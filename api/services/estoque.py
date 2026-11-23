"""Camada de serviço para consultas de estoque."""

from typing import Optional

from api.services.database import (
    listar_todos_produtos,
    buscar_produto_por_codigo,
    upsert_produto as upsert_produto_db,
)
from api.schemas import ProdutoResponse, EstoqueResponse, ProdutoUpsert


def get_estoque() -> EstoqueResponse:
    """Retorna todos os produtos do estoque com contagem total."""
    produtos = listar_todos_produtos()
    return EstoqueResponse(
        produtos=[ProdutoResponse(**p) for p in produtos],
        total=len(produtos)
    )


def get_produto(codigo: str) -> Optional[ProdutoResponse]:
    """Retorna um produto específico pelo código (EAN) ou None se não existir."""
    p = buscar_produto_por_codigo(codigo)
    return ProdutoResponse(**p) if p else None


def salvar_produto(codigo: str, dados: ProdutoUpsert) -> ProdutoResponse:
    """Cadastra ou atualiza um produto e devolve o registro resultante."""
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
    return ProdutoResponse(**produto)