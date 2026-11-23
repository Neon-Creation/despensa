"""Camada de serviço para consultas de estoque."""

from typing import Optional

from api.services.database import listar_todos_produtos, buscar_produto_por_codigo
from api.schemas import ProdutoResponse, EstoqueResponse


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