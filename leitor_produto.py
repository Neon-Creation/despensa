import os
import re
import sqlite3
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from google import genai
from google.genai import types
from PIL import Image, ImageEnhance, ImageOps
from pydantic import BaseModel
from pyzbar.pyzbar import decode

from config import carregar_env

carregar_env(sobrescrever=True)

BANCO = Path(__file__).parent / "despensa.db"
PASTA_IMAGENS = Path(__file__).parent / "imagens"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GOOGLE_CSE_KEY = os.environ.get("GOOGLE_CSE_KEY", "")
GOOGLE_CSE_CX = os.environ.get("GOOGLE_CSE_CX", "")


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
    """Links de grounding do Gemini costumam redirecionar via meta refresh."""
    meta = soup.find("meta", attrs={"http-equiv": re.compile("refresh", re.I)})
    if not meta:
        return ""

    conteudo = meta.get("content") or ""
    match = re.search(r"url\s*=\s*['\"]?(https?://[^'\"\s>]+)", conteudo, re.I)
    return match.group(1) if match else ""


def extrair_og_image(urls):
    """Tenta capturar a imagem principal (og:image) das URLs consultadas pelo Gemini."""
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

            # Os links do google_search costumam ser redirecionamentos do
            # vertexaisearch; segue o destino para tentar pegar a foto lá.
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


def _extrair_urls_fonte(response):
    """Coleta URIs de grounding com segurança entre versões do SDK."""
    candidatos = getattr(response, "candidates", None) or []
    if not candidatos:
        return []

    metadata = getattr(candidatos[0], "grounding_metadata", None)
    chunks = getattr(metadata, "grounding_chunks", None) or []

    urls = []
    for chunk in chunks:
        web = getattr(chunk, "web", None)
        uri = getattr(web, "uri", None) if web else None

        if not uri:
            contexto = getattr(chunk, "retrieved_context", None)
            uri = getattr(contexto, "uri", None) if contexto else None

        if isinstance(uri, str) and uri.startswith("http"):
            urls.append(uri)

    return urls


def consultar_produto_ia(codigo):
    """Consulta o código EAN na web usando Gemini + Google Search Grounding."""
    if not GEMINI_API_KEY:
        print("Aviso: GEMINI_API_KEY não configurada.")
        return None

    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        prompt = f"""
        Pesquise na web pelo código de barras EAN/GTIN: {codigo}.
        Identifique o produto exato vendido no Brasil correspondente a este código.
        Retorne:
        - encontrado: true se encontrar correspondência confiável, false caso contrário.
        - nome: nome limpo e completo do produto (sem abreviações de nota fiscal).
        - marca: fabricante ou marca principal.
        - conteudo: peso líquido ou volume (ex: 500g, 1L, 350ml). Se não souber, deixe vazio.
        """

        def gerar_com_retentativa():
            atraso = 2
            for tentativa in range(3):
                try:
                    return client.models.generate_content(
                        model="gemini-3.8-flash",
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            tools=[{"google_search": {}}],
                            response_mime_type="application/json",
                            response_schema=ProdutoIA,
                            temperature=0.1,
                        ),
                    )
                except Exception as e:
                    # 503 é pico de demanda e pode passar; 429 é cota esgotada,
                    # então não adianta esperar segundos: vai direto ao fallback.
                    if tentativa == 2 or "503" not in str(e):
                        raise
                    print(f"IA ocupada (503); tentando novamente em {atraso}s...")
                    time.sleep(atraso)
                    atraso *= 2

        response = gerar_com_retentativa()

        dados = ProdutoIA.model_validate_json(response.text)

        if not dados.encontrado or not dados.nome.strip():
            return None

        # Extrai URLs usadas como fonte pelo Gemini para tentar obter a foto
        foto_url = extrair_og_image(_extrair_urls_fonte(response))

        return {
            "nome": dados.nome.strip(),
            "marca": dados.marca.strip(),
            "conteudo": dados.conteudo.strip(),
            "foto_url": foto_url,
            "origem": "Gemini",
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


def consultar_google_imagens(codigo):
    """Busca o EAN no Google Imagens via Custom Search JSON API (opcional).

    Requer GOOGLE_CSE_KEY (uma chave de API com a Custom Search ativada) e
    GOOGLE_CSE_CX (o ID do mecanismo de busca configurado para a web inteira).
    Sem essas credenciais a função apenas retorna None.
    """
    if not GOOGLE_CSE_KEY or not GOOGLE_CSE_CX:
        return None

    try:
        resposta = requests.get(
            "https://www.googleapis.com/customsearch/v1",
            params={
                "key": GOOGLE_CSE_KEY,
                "cx": GOOGLE_CSE_CX,
                "q": codigo,
                "searchType": "image",
                "num": 5,
                "gl": "br",
                "hl": "pt",
            },
            timeout=10,
        )
        if resposta.status_code != 200:
            print(f"Google Imagens indisponível: HTTP {resposta.status_code}")
            return None

        for item in resposta.json().get("items") or []:
            nome = (item.get("title") or "").strip()
            if not nome:
                continue

            return {
                "nome": nome,
                "marca": "",
                "conteudo": "",
                "foto_url": item.get("link") or "",
                "origem": "Google Imagens",
            }
    except Exception as e:
        print(f"Erro ao consultar Google Imagens: {e}")

    return None


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

    Ordem: banco local -> sites web -> Google Imagens -> IA (Gemini).
    A IA só é chamada se nenhuma das etapas anteriores encontrar o produto.
    """
    etapas = [
        ("banco local", consultar_banco_local),
        ("sites web", consultar_sites_web),
        ("Google Imagens", consultar_google_imagens),
        ("IA (Gemini)", consultar_produto_ia),
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
        print("Uso: python3 leitor_produto.py caminho_da_imagem")
    else:
        ler_codigo(sys.argv[1])