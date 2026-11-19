"""Consultas de EAN em bases públicas, usadas por consulta_ean.py e consulta_ean_imagem.py."""

import os
import re

import requests

from config import carregar_env

carregar_env(sobrescrever=True)

SI5_API_TOKEN = os.getenv("SI5_API_TOKEN", "")
SI5_API_URL = os.getenv("SI5_API_URL", "https://api.si5.com.br/v1/produtos")


def exibir_produto(
    origem,
    nome,
    marca="Não informada",
    detalhe="Não informado",
    imagem="Não disponível",
):
    print("\n--- PRODUTO ENCONTRADO ---")
    print("Origem:", origem)
    print("Nome:", nome)
    print("Marca:", marca)
    print("Detalhe/Qtd:", detalhe)
    print("Imagem:", imagem)


def validar_codigo(numero):
    """Valida formato de EAN comum (8, 12, 13 ou 14 dígitos)."""
    numero = numero.strip()
    if not numero.isdigit():
        return False
    return len(numero) in (8, 12, 13, 14)


def buscar_open_facts(numero):
    bases = [
        ("Open Food Facts", "https://world.openfoodfacts.org/api/v3/product/"),
        ("Open Beauty Facts", "https://world.openbeautyfacts.org/api/v3/product/"),
        ("Open Products Facts", "https://world.openproductsfacts.org/api/v3/product/"),
        ("Open Pet Food Facts", "https://world.openpetfoodfacts.org/api/v3/product/"),
    ]
    parametros = {
        "product_type": "all",
        "fields": "product_name,product_name_pt,brands,quantity,image_url",
    }
    cabecalhos = {"User-Agent": "MeuLeitorDeCodigos/1.0 (Linux; Python)"}

    for nome_base, url in bases:
        try:
            resposta = requests.get(
                url + numero + ".json",
                params=parametros,
                headers=cabecalhos,
                timeout=8,
            )
            if resposta.status_code == 404:
                continue

            resposta.raise_for_status()
            dados = resposta.json()
            status = str(dados.get("status", ""))
            produto = dados.get("product") or {}

            if (status == "1" or status.startswith("success")) and produto:
                nome = (
                    produto.get("product_name_pt")
                    or produto.get("product_name")
                    or ""
                ).strip()
                if not nome:
                    continue

                exibir_produto(
                    origem=nome_base,
                    nome=nome,
                    marca=produto.get("brands") or "Não informada",
                    detalhe=produto.get("quantity") or "Não informada",
                    imagem=produto.get("image_url") or "Não disponível",
                )
                return True
        except requests.RequestException:
            continue

    return False


def buscar_upcitemdb(numero):
    """Endpoint gratuito de trial do UPCitemdb (sem API key)."""
    try:
        resposta = requests.get(
            "https://api.upcitemdb.com/prod/trial/lookup",
            params={"upc": numero},
            headers={
                "Accept": "application/json",
                "User-Agent": "MeuLeitorDeCodigos/1.0 (Linux; Python)",
            },
            timeout=8,
        )
        if resposta.status_code == 200:
            itens = resposta.json().get("items") or []
            if itens:
                item = itens[0]
                nome = item.get("title")
                if nome:
                    imagens = item.get("images") or []
                    exibir_produto(
                        origem="UPCitemdb",
                        nome=nome,
                        marca=item.get("brand") or "Não informada",
                        detalhe=item.get("size")
                        or item.get("weight")
                        or "Não informada",
                        imagem=imagens[0] if imagens else "Não disponível",
                    )
                    return True
    except requests.RequestException:
        pass

    return False


def buscar_si5_global(numero):
    """Consulta a API nacional da SI5 se o token gratuito estiver configurado."""
    if not SI5_API_TOKEN:
        return False

    url = f"{SI5_API_URL.rstrip('/')}/{numero}"
    cabecalhos = {
        "Authorization": f"Bearer {SI5_API_TOKEN}",
        "X-API-Key": SI5_API_TOKEN,
        "Accept": "application/json",
    }

    try:
        resposta = requests.get(url, headers=cabecalhos, timeout=8)
        if resposta.status_code == 200:
            dados = resposta.json()
            produto = (
                dados.get("data") or dados.get("produto") or dados
                if isinstance(dados, dict)
                else {}
            )
            if not isinstance(produto, dict):
                return False

            nome = (
                produto.get("descricao")
                or produto.get("nome")
                or produto.get("product_name")
            )
            if nome:
                ncm = produto.get("ncm")
                detalhe = f"NCM: {ncm}" if ncm else (produto.get("peso") or "Não informado")
                exibir_produto(
                    origem="SI5 Global",
                    nome=nome,
                    marca=produto.get("marca") or "Não informada",
                    detalhe=detalhe,
                    imagem=produto.get("foto") or produto.get("imagem") or "Não disponível",
                )
                return True
    except requests.RequestException:
        pass

    return False


def buscar_fallback_nacional(numero):
    """Tenta o nome do produto em diretório público caso não esteja nas APIs."""
    url = f"https://cosmos.bluesoft.com.br/produtos/{numero}"
    cabecalhos = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
        ),
        "Accept-Language": "pt-BR,pt;q=0.9",
    }

    try:
        resposta = requests.get(url, headers=cabecalhos, timeout=8)
        if resposta.status_code == 200:
            match = re.search(
                r"<title>(.*?)</title>", resposta.text, re.IGNORECASE | re.DOTALL
            )
            if match:
                titulo = match.group(1).strip()
                if "Cosmos" in titulo and not titulo.startswith("Just a moment"):
                    exibir_produto(
                        origem="Base Nacional Cosmos",
                        nome=titulo.split(" - ")[0].strip(),
                        detalhe=f"EAN: {numero}",
                        imagem=url,
                    )
                    return True
    except requests.RequestException:
        pass

    return False


def buscar_produto_ean(numero, titulo=True):
    """Consulta as fontes em ordem. Retorna True se alguma identificar o EAN."""
    if titulo:
        print("\nConsultando bases de dados...")

    if buscar_open_facts(numero):
        return True
    if buscar_upcitemdb(numero):
        return True
    if buscar_si5_global(numero):
        return True
    if buscar_fallback_nacional(numero):
        return True
    return False
