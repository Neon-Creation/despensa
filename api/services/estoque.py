"""Camada de serviço para consultas de estoque."""

import re
from typing import Optional

from api.schemas import EAN_PATTERN, EstoqueResponse, ProdutoResponse, ProdutoUpsert
from api.services.database import (
    buscar_produto_por_codigo,
    excluir_produto as excluir_produto_db,
    listar_todos_produtos,
)
from api.services.database import upsert_produto as upsert_produto_db
from api.services.events import eventos


def get_estoque() -> EstoqueResponse:
    """Retorna produtos com EAN válido; ``total`` conta produtos, não quantidades."""
    produtos = []
    for linha in listar_todos_produtos():
        if re.fullmatch(EAN_PATTERN, str(linha["codigo"])) is None:
            # Registros legados fora do padrão não são apagados do banco;
            # a correção é manual e está documentada no README.
            continue
        produtos.append(ProdutoResponse(**linha))
    return EstoqueResponse(produtos=produtos, total=len(produtos))


def get_produto(codigo: str) -> Optional[ProdutoResponse]:
    """Retorna um produto específico pelo código (EAN) ou None se não existir."""
    p = buscar_produto_por_codigo(codigo)
    return ProdutoResponse(**p) if p else None


def salvar_produto(codigo: str, dados: ProdutoUpsert) -> ProdutoResponse:
    """Cadastra ou atualiza um produto e devolve o registro resultante."""
    if re.fullmatch(EAN_PATTERN, codigo) is None:
        raise ValueError(f"Código EAN inválido: {codigo!r}")

    produto = upsert_produto_db(
        codigo,
        dados.nome,
        dados.marca,
        dados.conteudo,
        dados.foto_url,
        dados.quantidade,
    )
    if not produto:
        raise ValueError(f"Falha ao salvar o produto {codigo}")

    salvo = ProdutoResponse(**produto)
    eventos.notificar(
        codigo=salvo.codigo,
        quantidade=salvo.quantidade,
        acao="atualizado",
    )
    return salvo


def remover_produto(codigo: str) -> bool:
    """Exclui um produto pelo EAN e publica o evento SSE para o dashboard."""
    if re.fullmatch(EAN_PATTERN, codigo) is None:
        raise ValueError(f"Código EAN inválido: {codigo!r}")

    removido = excluir_produto_db(codigo)
    if removido:
        eventos.notificar(codigo=codigo, quantidade=0, acao="excluido")
    return removido