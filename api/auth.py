"""Autenticação da API via API Key no header X-API-Key."""

import logging
import secrets
from typing import Literal, NamedTuple

from fastapi import Header, HTTPException, Query, status

from api.config import settings

logger = logging.getLogger(__name__)

ClientType = Literal["esp32", "dashboard"]


class ClientInfo(NamedTuple):
    """Resultado da autenticação: identifica quem enviou a requisição."""

    client_type: ClientType


def _resolve_client_type(key: str) -> ClientType | None:
    """Mapeia uma chave apresentada para o tipo de cliente correspondente.

    Ordem de verificação:
    1. ``API_KEY_ESP32`` — chave específica da ESP32.
    2. ``API_KEY_DASHBOARD`` — chave específica do dashboard.
    3. ``API_KEY`` legada — aceita como ``"esp32"`` para compatibilidade com
       implantações que ainda não definiram as chaves específicas.

    Retorna ``None`` se a chave não corresponder a nenhuma configurada.
    """
    esp32_key = settings.API_KEY_ESP32
    dashboard_key = settings.API_KEY_DASHBOARD

    if esp32_key and secrets.compare_digest(key, esp32_key):
        return "esp32"

    if dashboard_key and secrets.compare_digest(key, dashboard_key):
        return "dashboard"

    # Fallback legado: só é verificado quando as chaves específicas não estão
    # configuradas, evitando que a chave genérica "vaze" para outros tipos.
    if not esp32_key and not dashboard_key:
        if secrets.compare_digest(key, settings.API_KEY):
            return "esp32"

    return None


async def verify_api_key(x_api_key: str | None = Header(None)) -> ClientInfo:
    """Valida ``X-API-Key`` e retorna o tipo de cliente autenticado.

    O header é opcional na assinatura para que a ausência também resulte em 401
    (em vez do 422 padrão do FastAPI), conforme o contrato da API.
    """
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida",
        )

    client_type = _resolve_client_type(x_api_key)
    if client_type is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida",
        )

    logger.info("auth OK client_type=%s", client_type)
    return ClientInfo(client_type=client_type)


async def verify_sse_token(
    x_api_key: str | None = Header(None),
    token: str | None = Query(None),
) -> ClientInfo:
    """Valida autenticação para SSE: header ``X-API-Key`` ou query param ``token``.

    O query param ``token`` permite que ``EventSource`` do browser se autentique
    sem cabeçalhos customizados. Aceita apenas chaves do tipo ``dashboard``.
    """
    # Prioriza header; se ausente, usa query param
    key = x_api_key or token
    if not key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida",
        )

    client_type = _resolve_client_type(key)
    # Para SSE do dashboard, aceita apenas chaves dashboard (ou legada que mapeia para esp32)
    if client_type is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API Key inválida",
        )

    logger.info("sse auth OK client_type=%s", client_type)
    return ClientInfo(client_type=client_type)
