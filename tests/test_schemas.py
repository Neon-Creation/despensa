"""Testes dos modelos Pydantic da API: formato do EAN e docstring de ``total``."""

import pytest
from pydantic import ValidationError

from api.schemas import EstoqueResponse, ProdutoResponse, ProdutoUpsert

EANS_VALIDOS = [
    "12345678",  # EAN-8
    "036000291452",  # UPC-A (12 dígitos)
    "7891079013373",  # EAN-13
    "12345678901234",  # EAN-14
]

EANS_INVALIDOS = [
    "",
    "1234567",  # curto demais
    "123456789012345",  # longo demais
    "789107901337a",  # contém letra
    "7891-0790-1337",  # contém hífen
    " 7891079013373",  # contém espaço
]


@pytest.mark.parametrize("codigo", EANS_VALIDOS)
def test_produto_response_aceita_ean_valido(codigo):
    produto = ProdutoResponse(codigo=codigo, nome="Produto", quantidade=1)
    assert produto.codigo == codigo


@pytest.mark.parametrize("codigo", EANS_INVALIDOS)
def test_produto_response_rejeita_ean_invalido(codigo):
    with pytest.raises(ValidationError):
        ProdutoResponse(codigo=codigo, nome="Produto", quantidade=1)


def test_upsert_nao_adiciona_codigo_ao_corpo():
    """O EAN vem da URL; o corpo continua com os campos documentados."""
    dados = ProdutoUpsert(nome="Café")
    assert not hasattr(dados, "codigo")


def test_total_documentado_como_contagem_de_produtos():
    descricao = EstoqueResponse.model_fields["total"].description
    assert descricao == "Número de produtos únicos cadastrados"
