import os
import re
import sqlite3
import sys
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageEnhance, ImageOps
from pydantic import BaseModel
from pyzbar.pyzbar import decode

from config import carregar_env

carregar_env(sobrescrever=True)

BANCO = Path(__file__).parent / "despensa.db"
PASTA_IMAGENS = Path(__file__).parent / "imagens"
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = os.environ.get("GROQ_MODEL", "openai/gpt-oss-120b")
GROQ_API_URL = os.environ.get(
    "GROQ_API_URL", "https://api.groq.com/openai/v1/chat/completions"
)


class ProdutoIA(BaseModel):
    encontrado: bool
    nome: str
    marca: str
    conteudo: str


def conectar_banco():
    conexao = sqlite3.connect(BANCO)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            codigo TEXT PRIMARY KEY,
            nome TEXT NOT NULL,
            marca TEXT,
            conteudo TEXT,
            foto_url TEXT,
            quantidade INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Garante compatibilidade caso o despensa.db já exista sem a coluna foto_url
    colunas = [row[1] for row in conexao.execute("PRAGMA table_info(produtos)").fetchall()]
    if "foto_url" not in colunas:
        conexao.execute("ALTER TABLE produtos ADD COLUMN foto_url TEXT")

    conexao.commit()
    return conexao


def _og_image(soup):
    """Extrai a URL da imagem principal de og:image ou twitter:image."""
    for atributos in ({"property": "og:image"}, {"name": "twitter:image"}):
        meta = soup.find("meta", attrs=atributos)
        if meta and (meta.get("content") or "").startswith("http"):
            return meta["content"]
    return ""


def _meta_refresh(soup):
    """Alguns sites de resultados redirecionam via meta refresh."""
    meta = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
    if not meta:
        return ""

    conteudo = meta.get("content") or ""
    match = re.search(r"url\s*=\s*['\"]?(https?://[^'\"\s>]+)", conteudo, re.I)
    return match.group(1) if match else ""


def extrair_og_image(urls):
    """Tenta capturar a imagem principal (og:image) das URLs encontradas na busca."""
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in urls[:3]:
        try:
            resp = requests.get(url, headers=headers, timeout=5)
            if resp.status_code != 200:
                continue

            soup = BeautifulSoup(resp.text, "html.parser")
            imagem = _og_image(soup)
            if imagem:
                return imagem

            # Segue redirecionamentos via meta refresh para tentar pegar a foto.
            destino = _meta_refresh(soup) or resp.url
            if destino.startswith("http") and destino not in (url, resp.url):
                resp_destino = requests.get(destino, headers=headers, timeout=5)
                if resp_destino.status_code == 200:
                    imagem = _og_image(
                        BeautifulSoup(resp_destino.text, "html.parser")
                    )
                    if imagem:
                        return imagem
        except Exception:
            continue
    return ""


def buscar_resultados_web(codigo):
    """Busca o EAN no DuckDuckGo (HTML, sem chave) e devolve trechos + URLs."""
    cabecalhos = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9",
    }
    resposta = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": f'"{codigo}"'},
        headers=cabecalhos,
        timeout=12,
    )
    resposta.raise_for_status()
    sopa = BeautifulSoup(resposta.text, "html.parser")

    trechos = []
    urls = []
    for bloco in sopa.select(".result")[:6]:
        titulo = bloco.select_one(".result__a")
        if not titulo:
            continue

        resumo = bloco.select_one(".result__snippet")
        trechos.append(
            f"- {titulo.get_text(' ', strip=True)}. "
            f"{resumo.get_text(' ', strip=True) if resumo else ''}".strip()
        )

        href = titulo.get("href") or ""
        destino = href
        if "uddg=" in href:
            destino = unquote(parse_qs(urlparse(href).query).get("uddg", [href])[0])
        if destino.startswith("http") and destino not in urls:
            urls.append(destino)

    return "\n".join(trechos), urls


def _parsear_json(texto):
    """Aceita JSON puro ou envolto em blocos de markdown."""
    texto = (texto or "").strip()
    if texto.startswith("```"):
        texto = texto.split("```", 2)[1]
        if texto.lstrip().lower().startswith("json"):
            texto = texto.lstrip()[4:]
    inicio = texto.find("{")
    fim = texto.rfind("}")
    if inicio == -1 or fim == -1:
        raise ValueError("resposta da IA não continha JSON")
    return ProdutoIA.model_validate_json(texto[inicio : fim + 1])


def consultar_produto_ia(codigo):
    """Identifica o EAN com Groq (gratuito) usando trechos de busca na web."""
    if not GROQ_API_KEY:
        print("Aviso: GROQ_API_KEY não configurada. Crie uma chave gratuita em")
        print("       https://console.groq.com/keys e cole no arquivo .env.")
        return None

    try:
        trechos, urls = buscar_resultados_web(codigo)
        if not trechos:
            print("Busca web sem resultados para este código.")
            return None

        prompt = f"""
Você identifica produtos brasileiros de supermercado pelo código EAN/GTIN.
Código pesquisado: {codigo}

Trechos encontrados na web:
{trechos}

Usando somente essas informações, devolva:
- encontrado: true se os trechos identificam claramente o produto; false se não houver correspondência confiável.
- nome: nome limpo e completo do produto em português (sem abreviações de nota fiscal).
- marca: fabricante ou marca principal.
- conteudo: peso líquido ou volume (ex: 500g, 1L, 85g). Se não souber, deixe vazio.

Responda apenas com um objeto JSON válido, sem markdown e sem explicações.
"""

        corpo = {
            "model": GROQ_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "Você responde sempre com JSON válido e nada mais.",
                },
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0,
            "max_tokens": 400,
        }
        cabecalhos = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json",
        }
        resposta = requests.post(
            GROQ_API_URL, headers=cabecalhos, json=corpo, timeout=30
        )

        # Alguns modelos aceitos no Groq não suportam response_format.
        if resposta.status_code == 400 and "response_format" in resposta.text:
            corpo.pop("response_format")
            resposta = requests.post(
                GROQ_API_URL, headers=cabecalhos, json=corpo, timeout=30
            )

        if resposta.status_code == 401:
            print("Groq: chave inválida (401). Confira GROQ_API_KEY no .env.")
            return None
        if resposta.status_code == 429:
            print("Groq: limite de requisições atingido (429). Tente em instantes.")
            return None
        resposta.raise_for_status()

        conteudo = resposta.json()["choices"][0]["message"]["content"]
        dados = _parsear_json(conteudo)

        if not dados.encontrado or not dados.nome.strip():
            return None

        return {
            "nome": dados.nome.strip(),
            "marca": dados.marca.strip(),
            "conteudo": dados.conteudo.strip(),
            "foto_url": extrair_og_image(urls),
            "origem": "Groq",
        }

    except Exception as e:
        print(f"Erro ao consultar IA: {e}")
        return None


def consultar_banco_local(codigo):
    """1ª etapa: reutiliza os dados de um produto já cadastrado no SQLite."""
    conexao = conectar_banco()

    try:
        linha = conexao.execute(
            "SELECT nome, marca, conteudo, foto_url FROM produtos WHERE codigo = ?",
            (codigo,),
        ).fetchone()
    finally:
        conexao.close()

    if not linha:
        return None

    nome, marca, conteudo, foto_url = linha
    if not nome:
        return None

    return {
        "nome": nome,
        "marca": marca or "",
        "conteudo": conteudo or "",
        "foto_url": foto_url or "",
        "origem": "Banco local",
    }


def consultar_sites_web(codigo):
    """2ª etapa: identifica o produto em bases públicas gratuitas pela web."""
    headers = {"User-Agent": "Despensa/1.0 (Linux; Python)"}
    parametros = {
        "fields": "product_name,product_name_pt,brands,quantity,image_url",
        "product_type": "all",
    }
    fontes = [
        ("Open Food Facts", f"https://world.openfoodfacts.org/api/v3/product/{codigo}.json"),
        ("Open Beauty Facts", f"https://world.openbeautyfacts.org/api/v3/product/{codigo}.json"),
        ("Open Products Facts", f"https://world.openproductsfacts.org/api/v3/product/{codigo}.json"),
    ]

    for origem, url in fontes:
        try:
            resposta = requests.get(url, params=parametros, headers=headers, timeout=10)
            if resposta.status_code != 200:
                continue

            produto = resposta.json().get("product") or {}
            nome = (
                produto.get("product_name_pt") or produto.get("product_name") or ""
            ).strip()
            if not nome:
                continue

            return {
                "nome": nome,
                "marca": (produto.get("brands") or "").strip(),
                "conteudo": (produto.get("quantity") or "").strip(),
                "foto_url": produto.get("image_url") or "",
                "origem": origem,
            }
        except Exception:
            continue

    # Última opção gratuita: UPCitemdb (trial sem chave)
    try:
        resposta = requests.get(
            "https://api.upcitemdb.com/prod/trial/lookup",
            params={"upc": codigo},
            headers={**headers, "Accept": "application/json"},
            timeout=10,
        )
        itens = resposta.json().get("items") or []
        if itens:
            item = itens[0]
            nome = (item.get("title") or "").strip()
            if nome:
                imagens = item.get("images") or []
                return {
                    "nome": nome,
                    "marca": (item.get("brand") or "").strip(),
                    "conteudo": (item.get("size") or item.get("weight") or "").strip(),
                    "foto_url": imagens[0] if imagens else "",
                    "origem": "UPCitemdb",
                }
    except Exception:
        pass

    return None


def consultar_produto(codigo):
    """Busca o produto em etapas, da fonte mais barata para a mais cara.

    Ordem: banco local -> sites web -> IA (Groq).
    A IA só é chamada se nenhuma das etapas anteriores encontrar o produto.
    """
    etapas = [
        ("banco local", consultar_banco_local),
        ("sites web", consultar_sites_web),
        ("IA (Groq)", consultar_produto_ia),
    ]

    for nome, funcao in etapas:
        print(f"Buscando em {nome}...")
        try:
            info = funcao(codigo)
        except Exception as e:
            print(f"Falha em {nome}: {e}")
            info = None

        if info:
            return info

    return None


def pedir_texto(mensagem, obrigatorio=False, aviso="O campo não pode ficar vazio."):
    """Lê texto do usuário e retorna None quando não há entrada disponível."""
    while True:
        try:
            valor = input(mensagem).strip()
        except EOFError:
            print("\nEntrada de dados indisponível (sem terminal interativo).")
            return None

        if not obrigatorio or valor:
            return valor

        print(aviso)


def registrar_ou_contar(codigo):
    conexao = conectar_banco()

    try:
        cursor = conexao.cursor()

        cursor.execute(
            "SELECT nome, marca, conteudo, quantidade FROM produtos WHERE codigo = ?",
            (codigo,)
        )

        produto = cursor.fetchone()

        if produto:
            # Produto já existe: NÃO chama a IA, apenas incrementa a quantidade
            cursor.execute(
                "UPDATE produtos SET quantidade = quantidade + 1 WHERE codigo = ?",
                (codigo,)
            )

            conexao.commit()

            cursor.execute(
                "SELECT nome, quantidade FROM produtos WHERE codigo = ?",
                (codigo,)
            )

            nome, quantidade = cursor.fetchone()

            print("\nProduto já cadastrado!")
            print("Nome:", nome)
            print("Quantidade na despensa:", quantidade)

        else:
            # Novo produto: chama a IA para descobrir nome, marca, conteúdo e foto
            print("\nNovo produto identificado!")
            print("Código:", codigo)
            print("Buscando o produto...")

            info = consultar_produto(codigo)

            if info:
                nome = info["nome"]
                marca = info["marca"]
                conteudo = info["conteudo"]
                foto_url = info["foto_url"]

                print(f"\nDados encontrados ({info.get('origem', 'IA')}):")
                print("Nome:", nome)
                print("Marca:", marca or "-")
                print("Conteúdo:", conteudo or "-")
                print("Foto URL:", foto_url or "Não encontrada")
            else:
                print("\nNenhuma fonte conseguiu identificar o produto automaticamente.")
                nome = pedir_texto(
                    "Nome do produto: ",
                    obrigatorio=True,
                    aviso="O nome não pode ficar vazio.",
                )
                if nome is None:
                    print("Produto não cadastrado (nome não informado).")
                    return

                marca = pedir_texto("Marca: ") or ""
                conteudo = pedir_texto(
                    "Conteúdo da embalagem (peso ou volume): "
                ) or ""
                foto_url = ""

            cursor.execute(
                """
                INSERT INTO produtos (codigo, nome, marca, conteudo, foto_url, quantidade)
                VALUES (?, ?, ?, ?, ?, 1)
                """,
                (codigo, nome, marca, conteudo, foto_url)
            )

            conexao.commit()

            print("\nProduto cadastrado com sucesso!")
            print("Quantidade na despensa: 1")

    finally:
        conexao.close()


def resolver_caminho(caminho_imagem):
    """Aceita caminho absoluto, relativo ao projeto ou dentro de imagens/."""
    caminho = Path(caminho_imagem).expanduser()

    if caminho.is_absolute() or caminho.is_file():
        return caminho

    return PASTA_IMAGENS / caminho


def ler_codigo(caminho_imagem):
    caminho = resolver_caminho(caminho_imagem)

    try:
        imagem = Image.open(caminho)
        imagem = ImageOps.exif_transpose(imagem).convert("RGB")
    except Exception as e:
        print("\nErro ao abrir a imagem:", e)
        return

    cinza = ImageOps.grayscale(imagem)
    contraste = ImageEnhance.Contrast(cinza).enhance(2)

    versoes = [imagem, cinza, contraste]
    codigos_encontrados = {}

    print("\nIniciando leitura...")

    angulos = [0] + [a for a in range(-90, 91, 5) if a != 0]

    for angulo in angulos:
        for versao in versoes:
            cor_fundo = (255, 255, 255) if versao.mode == "RGB" else 255

            rotacionada = versao.rotate(
                angulo,
                expand=True,
                fillcolor=cor_fundo,
                resample=Image.Resampling.BICUBIC
            )

            codigos = decode(rotacionada)

            for codigo in codigos:
                numero = codigo.data.decode("utf-8", errors="replace")
                codigos_encontrados[numero] = codigo.type

            if codigos_encontrados:
                break

        if codigos_encontrados:
            break

    if not codigos_encontrados:
        print("\nNenhum código encontrado.")
        print("Verifique o enquadramento e a qualidade da fotografia.")
        return

    for numero, tipo in codigos_encontrados.items():
        print("\nCódigo identificado:", numero)
        print("Tipo:", tipo)

        registrar_ou_contar(numero)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 leitor.py caminho_da_imagem")
    else:
        ler_codigo(sys.argv[1])