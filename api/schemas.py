"""Modelos Pydantic da API."""

from typing import List, Optional

from pydantic import BaseModel, Field, field_validator


class UploadResponse(BaseModel):
    """Resultado do processamento de uma foto enviada pela ESP32."""

    lido: bool
    codigo: Optional[str] = None
    produto: Optional[str] = None
    acao: Optional[str] = None  # "contado" | "erro"
    quantidade: Optional[int] = None
    mensagem: Optional[str] = None


class ProdutoUpsert(BaseModel):
    """Dados para cadastrar ou atualizar um produto pelo EAN (via PUT)."""

    nome: str = Field(..., min_length=1, max_length=200)
    marca: Optional[str] = Field(default=None, max_length=200)
    conteudo: Optional[str] = Field(default=None, max_length=200)
    foto_url: Optional[str] = Field(default=None, max_length=500)
    quantidade: Optional[int] = Field(default=None, ge=0)

    @field_validator("nome")
    @classmethod
    def validar_nome(cls, valor: str) -> str:
        valor = valor.strip()
        if not valor:
            raise ValueError("O nome não pode ficar vazio")
        return valor

    @field_validator("marca", "conteudo", "foto_url")
    @classmethod
    def limpar_texto(cls, valor: Optional[str]) -> Optional[str]:
        return valor.strip() if valor else valor


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
