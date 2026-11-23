"""Autenticação da API via API Key no header X-API-Key."""

import secrets
from typing import Optional

from fastapi import Header, HTTPException, status

from api.config import settings


async def verify_api_key(x_api_key: Optional[str] = Header(None)):
    """Valida a chave de API enviada no header ``X-API-Key``.

    O header é opcional na assinatura para que ausência também resulte em 401
    (em vez do 422 padrão do FastAPI), conforme o contrato da API.
    """
    if not x_api_key or not secrets.compare_digest(x_api_key, settings.API_KEY):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida",
        )
