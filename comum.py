"""Lógica compartilhada pelos leitores: banco, imagem, cadastro e buscas web."""

import re
import sqlite3
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from zbar_compat import garantir_zbar

decode = garantir_zbar()

from config import carregar_env

carregar_env(sobrescrever=True)

BANCO = Path(__file__).parent / "despensa.db"
PASTA_IMAGENS = Path(__file__).parent / "imagens"


def _decodificar_variante(imagem: Image.Image) -> dict:
    """Tenta o zbar numa única variante da imagem, retornando ``{ean: tipo}``."""
    try:
        codigos = decode(imagem)
    except Exception:
        return {}
    return {
        codigo.data.decode("utf-8", errors="replace"): codigo.type
        for codigo in codigos
    }


def ler_codigo_rapido_em_memoria(imagem: Image.Image) -> dict:
    """Decodifica EANs de uma imagem PIL já carregada em memória.

    Pipeline reforçada (a mesma do ``servidor_scanner.py``): cinza,
    autocontraste, nitidez, recorte central ampliado e binarização. O recorte
    vem primeiro porque é onde o código costuma estar quando o usuário aponta
    a câmera. Só depois tenta-se o quadro inteiro e as rotações.

    Nunca grava em disco. Retorna ``{codigo: tipo}`` com o primeiro código
    encontrado ou ``{}`` se nenhum for detectado.
    """
    cinza = ImageOps.grayscale(imagem)
    nitida = ImageOps.autocontrast(cinza, cutoff=2)
    nitida = nitida.filter(ImageFilter.UnsharpMask(radius=2, percent=250, threshold=2))

    variantes = [cinza, nitida]

    largura, altura = nitida.size
    caixa = (
        int(largura * 0.12),
        int(altura * 0.15),
        int(largura * 0.88),
        int(altura * 0.85),
    )
    recorte = nitida.crop(caixa)
    if recorte.width > 0 and recorte.height > 0:
        zoom = recorte.resize(
            (recorte.width * 2, recorte.height * 2), Image.Resampling.BILINEAR
        )
        zoom = zoom.filter(ImageFilter.SHARPEN)
        variantes.append(zoom)
        variantes.append(zoom.point(lambda p: 255 if p > 115 else 0))

    for variante in variantes:
        encontrados = _decodificar_variante(variante)
        if encontrados:
            return encontrados

    for angulo in (90, -90, 180):
        for variante in variantes[:3]:
            rotacionada = variante.rotate(angulo, expand=True, fillcolor=255)
            encontrados = _decodificar_variante(rotacionada)
            if encontrados:
                return encontrados

    return {}


def conectar_banco():
    """Abre o SQLite e garante o schema, incluindo a coluna foto_url."""
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

    colunas = [row[1] for row in conexao.execute("PRAGMA table_info(produtos)").fetchall()]
    if "foto_url" not in colunas:
        conexao.execute("ALTER TABLE produtos ADD COLUMN foto_url TEXT")

    conexao.commit()
    return conexao


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


def resolver_caminho(caminho_imagem):
    """Aceita caminho absoluto, relativo ao projeto ou dentro de imagens/."""
    caminho = Path(caminho_imagem).expanduser()

    if caminho.is_absolute() or caminho.is_file():
        return caminho

    return PASTA_IMAGENS / caminho


def ler_codigo(caminho_imagem, buscar_info=None, ao_encontrar=None):
    """Decodifica EANs da imagem e processa cada código encontrado.

    Sem callback, cadastra/conta o produto no banco. ``buscar_info`` informa
    como identificar automaticamente um produto novo; ``ao_encontrar``
    substitui o cadastro por outra ação (ex.: apenas consultar o EAN).
    """
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

    def processar(numero):
        if ao_encontrar:
            ao_encontrar(numero)
        else:
            registrar_ou_contar(numero, buscar_info=buscar_info)

    for numero, tipo in codigos_encontrados.items():
        print("\nCódigo identificado:", numero)
        print("Tipo:", tipo)
        processar(numero)


def registrar_ou_contar(codigo, buscar_info=None):
    """Conta um produto já cadastrado ou cadastra um novo.

    ``buscar_info`` é uma função opcional que tenta identificar o EAN
    automaticamente. Sem ela, os dados são pedidos manualmente.
    """
    conexao = conectar_banco()

    try:
        cursor = conexao.cursor()

        cursor.execute(
            "SELECT nome, marca, conteudo, quantidade FROM produtos WHERE codigo = ?",
            (codigo,)
        )
        produto = cursor.fetchone()

        if produto:
            # Produto já existe: NÃO consulta fontes, apenas incrementa a quantidade
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
            print("\nNovo produto identificado!")
            print("Código:", codigo)

            info = None
            if buscar_info:
                print("Buscando o produto...")
                info = buscar_info(codigo)

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
                if buscar_info:
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


# ---------------------------------------------------------------------------
# Fontes web usadas como etapas pelos leitores
# ---------------------------------------------------------------------------

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
    """Tenta capturar a imagem principal das URLs consultadas."""
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


def consultar_banco_local(codigo):
    """Reutiliza os dados de um produto já cadastrado no SQLite."""
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
    """Identifica o produto em bases públicas gratuitas pela web."""
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


def consultar_produto(codigo, etapas):
    """Percorre as etapas da mais barata para a mais cara e retorna o 1º resultado."""
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
