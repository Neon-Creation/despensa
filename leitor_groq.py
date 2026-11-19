"""Leitor principal com Groq: imagem -> banco -> web -> DuckDuckGo + Groq."""

import os
import sys
from urllib.parse import parse_qs, unquote, urlparse

import requests
from bs4 import BeautifulSoup
from pydantic import BaseModel

from comum import (
    consultar_banco_local,
    consultar_produto,
    consultar_sites_web,
    extrair_og_image,
    ler_codigo,
)
from config import carregar_env

carregar_env(sobrescrever=True)

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
    return ProdutoIA.model_validate_json(texto[inicio: fim + 1])


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


ETAPAS = [
    ("banco local", consultar_banco_local),
    ("sites web", consultar_sites_web),
    ("IA (Groq)", consultar_produto_ia),
]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 leitor_groq.py caminho_da_imagem")
    else:
        ler_codigo(
            sys.argv[1],
            buscar_info=lambda codigo: consultar_produto(codigo, ETAPAS),
        )
