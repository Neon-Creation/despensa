"""Modelos Pydantic da API."""

from typing import List, Optional

from pydantic import BaseModel


class UploadResponse(BaseModel):
    """Resultado do processamento de uma foto enviada pela ESP32."""

    lido: bool
    codigo: Optional[str] = None
    produto: Optional[str] = None
    acao: Optional[str] = None  # "contado" | "erro"
    mensagem: Optional[str] = None


class ProdutoResponse(BaseModel):
    """Dados de um produto no estoque."""

    codigo: str
    nome: str
    marca: Optional[str] = None
    conteudo: Optional[str] = None
    quantidade: int


class EstoqueResponse(BaseModel):
    """Lista completa de produtos do estoque."""

    produtos: List[ProdutoResponse]
    total: int
