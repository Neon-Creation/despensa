"""Leitor principal com Gemini: imagem -> banco -> web -> IA."""

import os
import sys
import time

import requests
from google import genai
from google.genai import types
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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
GOOGLE_CSE_KEY = os.environ.get("GOOGLE_CSE_KEY", "")
GOOGLE_CSE_CX = os.environ.get("GOOGLE_CSE_CX", "")


class ProdutoIA(BaseModel):
    encontrado: bool
    nome: str
    marca: str
    conteudo: str


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
    """Consulta o EAN na web usando Gemini + Google Search Grounding."""
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
                    # 503 é pico de demanda; 429 é cota esgotada, vai ao fallback.
                    if tentativa == 2 or "503" not in str(e):
                        raise
                    print(f"IA ocupada (503); tentando novamente em {atraso}s...")
                    time.sleep(atraso)
                    atraso *= 2

        response = gerar_com_retentativa()

        dados = ProdutoIA.model_validate_json(response.text)

        if not dados.encontrado or not dados.nome.strip():
            return None

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


def consultar_google_imagens(codigo):
    """Busca o EAN no Google Imagens via Custom Search JSON API (opcional)."""
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


ETAPAS = [
    ("banco local", consultar_banco_local),
    ("sites web", consultar_sites_web),
    ("Google Imagens", consultar_google_imagens),
    ("IA (Gemini)", consultar_produto_ia),
]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 leitor_gemini.py caminho_da_imagem")
    else:
        ler_codigo(
            sys.argv[1],
            buscar_info=lambda codigo: consultar_produto(codigo, ETAPAS),
        )
