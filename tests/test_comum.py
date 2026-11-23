"""Regressão da Fase 2: nova função em memória não altera os scripts CLI."""

import inspect

from PIL import Image, ImageOps

import comum
import recebe_foto

IMAGEM_MIOJO = "imagens/miojo.jpg"


def test_ler_codigo_em_memoria_igual_a_versao_arquivo():
    """A versão em memória deve detectar o mesmo EAN da versão em disco."""
    imagem = ImageOps.exif_transpose(Image.open(IMAGEM_MIOJO)).convert("RGB")

    em_memoria = comum.ler_codigo_rapido_em_memoria(imagem)
    em_arquivo = recebe_foto.ler_codigo_rapido(IMAGEM_MIOJO)

    assert em_memoria
    assert em_memoria == em_arquivo
    assert "7891079013373" in em_memoria


def test_registrar_ou_contar_permanece_interativo():
    """A função original dos scripts CLI não foi alterada (aceita buscar_info)."""
    assinatura = inspect.signature(comum.registrar_ou_contar)
    assert "codigo" in assinatura.parameters
    assert "buscar_info" in assinatura.parameters
