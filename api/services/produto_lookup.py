"""Identificação automática de produtos a partir de um EAN.

As fontes rápidas (Open Food Facts, UPCitemdb e Cosmos) são consultadas dentro
de um orçamento de tempo curto, para não atrasar o upload da ESP32. O Groq é
uma fonte opcional, mais lenta, executada em background pelo endpoint.

Nenhuma função deste módulo escreve no banco: elas apenas devolvem os dados
encontrados em ``{nome, marca, conteudo, foto_url, origem}``.
"""

from __future__ import annotations

import logging
import time

import requests
from bs4 import BeautifulSoup

from api.config import settings

logger = logging.getLogger(__name__)

TIMEOUT_FONTE_S = 2.5
USER_AGENT = "Despensa/1.0 (consulta de EAN)"

BASES_ABERTAS = (
    ("Open Food Facts", "https://world.openfoodfacts.org/api/v3/product/"),
    ("Open Beauty Facts", "https://world.openbeautyfacts.org/api/v3/product/"),
    ("Open Products Facts", "https://world.openproductsfacts.org/api/v3/product/"),
)


def _limpar(valor) -> str:
    """Normaliza um campo de texto vindo de uma API externa."""
    if valor is None:
        return ""
    return " ".join(str(valor).split()).strip()


def _resultado(origem: str, produto: dict) -> dict | None:
    """Monta o dicionário padrão, exigindo ao menos um nome."""
    nome = _limpar(
        produto.get("product_name_pt")
        or produto.get("product_name")
        or produto.get("title")
        or produto.get("nome")
        or produto.get("descricao")
    )
    if not nome:
        return None
    return {
        "nome": nome[:200],
        "marca": _limpar(produto.get("brands") or produto.get("brand") or produto.get("marca"))[:200],
        "conteudo": _limpar(
            produto.get("quantity") or produto.get("size") or produto.get("weight") or produto.get("peso")
        )[:200],
        "foto_url": _limpar(produto.get("image_url") or produto.get("foto") or produto.get("imagem"))[:500],
        "origem": origem,
    }


def consultar_bases_abertas(codigo: str) -> dict | None:
    """Consulta as bases Open Food Facts e derivadas."""
    parametros = {
        "fields": "product_name,product_name_pt,brands,quantity,image_url",
        "product_type": "all",
    }
    for origem, url in BASES_ABERTAS:
        try:
            resposta = requests.get(
                f"{url}{codigo}.json",
                params=parametros,
                headers={"User-Agent": USER_AGENT},
                timeout=TIMEOUT_FONTE_S,
            )
            if resposta.status_code != 200:
                continue
            produto = (resposta.json() or {}).get("product") or {}
            encontrado = _resultado(origem, produto)
            if encontrado:
                return encontrado
        except Exception as exc:
            logger.debug("lookup %s falhou para %s: %s", origem, codigo, exc)
    return None


def consultar_upcitemdb(codigo: str) -> dict | None:
    """Consulta o endpoint gratuito de trial do UPCitemdb."""
    try:
        resposta = requests.get(
            "https://api.upcitemdb.com/prod/trial/lookup",
            params={"upc": codigo},
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            timeout=TIMEOUT_FONTE_S,
        )
        if resposta.status_code != 200:
            return None
        itens = (resposta.json() or {}).get("items") or []
        if itens:
            imagens = itens[0].get("images") or []
            dados = dict(itens[0])
            if imagens:
                dados["image_url"] = imagens[0]
            return _resultado("UPCitemdb", dados)
    except Exception as exc:
        logger.debug("lookup UPCitemdb falhou para %s: %s", codigo, exc)
    return None


def consultar_cosmos(codigo: str) -> dict | None:
    """Tenta identificar o produto pelo título da página do Cosmos."""
    try:
        resposta = requests.get(
            f"https://cosmos.bluesoft.com.br/produtos/{codigo}",
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0"
                ),
                "Accept-Language": "pt-BR,pt;q=0.9",
            },
            timeout=TIMEOUT_FONTE_S,
        )
        if resposta.status_code != 200:
            return None
        titulo = BeautifulSoup(resposta.text, "html.parser").title
        texto = _limpar(titulo.get_text() if titulo else "")
        # O Cloudflare devolve "Just a moment..." em vez do produto.
        if not texto or "Cosmos" not in texto or texto.lower().startswith("just a moment"):
            return None
        return _resultado("Cosmos", {"nome": texto.split(" - ")[0].strip()})
    except Exception as exc:
        logger.debug("lookup Cosmos falhou para %s: %s", codigo, exc)
    return None


FONTES_RAPIDAS = (consultar_bases_abertas, consultar_upcitemdb, consultar_cosmos)


def identificar_produto(codigo: str) -> dict | None:
    """Percorre as fontes rápidas até encontrar um nome confiável.

    Respeita ``settings.PRODUTO_LOOKUP_TIMEOUT_S`` como orçamento total.
    """
    if not settings.PRODUTO_LOOKUP_ATIVO:
        return None

    limite = time.monotonic() + max(0.5, settings.PRODUTO_LOOKUP_TIMEOUT_S)
    for fonte in FONTES_RAPIDAS:
        if time.monotonic() >= limite:
            logger.info("orçamento de lookup esgotado para %s", codigo)
            break
        try:
            info = fonte(codigo)
        except Exception as exc:
            logger.warning("lookup %s levantou exceção para %s: %s", fonte.__name__, codigo, exc)
            info = None
        if info and info.get("nome"):
            logger.info("produto %s identificado por %s", codigo, info["origem"])
            return info
    return None


def identificar_com_groq(codigo: str) -> dict | None:
    """Última fonte, opcional e lenta. Chamada em background.

    O import é lazy para que o container não precise do módulo quando o Groq
    estiver desligado (evita dependência de `leitor_groq.py` nesse caso).
    """
    if not settings.PRODUTO_LOOKUP_GROQ:
        return None
    try:
        from leitor_groq import consultar_produto_ia

        info = consultar_produto_ia(codigo)
        if info and info.get("nome"):
            return _resultado(info.get("origem", "Groq"), info)
    except Exception as exc:
        logger.warning("lookup Groq falhou para %s: %s", codigo, exc)
    return None
