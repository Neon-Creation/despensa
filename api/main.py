"""Aplicação FastAPI: upload de fotos da ESP32 e healthcheck."""

import logging

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from api.auth import ClientInfo, verify_api_key, verify_sse_token
from api.config import settings
from api.schemas import (
    EanCodigo,
    EstoqueResponse,
    ProdutoResponse,
    ProdutoUpsert,
    UploadResponse,
)
from api.services.database import health_check_db
from api.services.estoque import get_estoque, get_produto, salvar_produto
from api.services.events import eventos, resposta_estoque
from api.services.scanner import processar_upload

logger = logging.getLogger(__name__)


def _rate_limit_key(request: Request) -> str:
    """Chave de rate limit: ``<ip>:<client_type>``.

    Separa os contadores por tipo de cliente, para que o dashboard não consuma
    a cota da ESP32 e vice-versa. Quando a chave ainda não foi resolvida
    (autenticação ainda não ocorreu), usa apenas o IP.
    """
    client_info: ClientInfo | None = getattr(request.state, "client_info", None)
    ip = request.client.host if request.client else "unknown"
    if client_info is not None:
        return f"{ip}:{client_info.client_type}"
    return ip


app = FastAPI(title="Despensa API", version="1.0.0")

# CORS aberto para depuração local (restringir em produção).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

limiter = Limiter(key_func=_rate_limit_key)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.exception_handler(RequestValidationError)
async def validacao_invalida(request: Request, exc: RequestValidationError):
    """EAN inválido no caminho retorna 400; demais validações seguem o padrão (422)."""
    if any(erro.get("loc", ())[:1] == ("path",) for erro in exc.errors()):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Código EAN inválido: use de 8 a 14 dígitos"},
        )
    return await request_validation_exception_handler(request, exc)


@app.post("/upload", response_model=UploadResponse)
@limiter.limit("10/minute")
async def upload_foto(
    request: Request,
    file: UploadFile = File(...),
    client: ClientInfo = Depends(verify_api_key),
):
    """Recebe uma imagem JPEG, lê o EAN e atualiza a quantidade no banco.

    Aceita apenas requisições da ESP32 (``client_type="esp32"``).
    """
    if client.client_type != "esp32":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este endpoint é exclusivo da ESP32",
        )

    request.state.client_info = client
    logger.info("upload client_type=%s", client.client_type)

    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Arquivo não é imagem",
        )

    conteudo = await file.read()
    limite_bytes = settings.MAX_UPLOAD_MB * 1024 * 1024
    if len(conteudo) > limite_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Arquivo excede {settings.MAX_UPLOAD_MB}MB",
        )

    return processar_upload(conteudo)


@app.get("/health")
async def health():
    """Reporta disponibilidade da API e do banco de dados."""
    db_ok = health_check_db()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "disconnected",
    }


@app.get("/estoque", response_model=EstoqueResponse)
@limiter.limit("30/minute")
async def listar_estoque(
    request: Request,
    client: ClientInfo = Depends(verify_api_key),
):
    """Lista todos os produtos do estoque ordenados por nome."""
    request.state.client_info = client
    logger.info("estoque client_type=%s", client.client_type)
    return get_estoque()


@app.get("/produto/{codigo}", response_model=ProdutoResponse)
@limiter.limit("30/minute")
async def obter_produto(
    request: Request,
    codigo: EanCodigo,
    client: ClientInfo = Depends(verify_api_key),
):
    """Retorna detalhes de um produto específico pelo código EAN."""
    request.state.client_info = client
    logger.info("get_produto codigo=%s client_type=%s", codigo, client.client_type)
    produto = get_produto(codigo)
    if not produto:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produto não encontrado")
    return produto


@app.put("/produto/{codigo}", response_model=ProdutoResponse)
@limiter.limit("30/minute")
async def cadastrar_produto(
    request: Request,
    codigo: EanCodigo,
    dados: ProdutoUpsert,
    client: ClientInfo = Depends(verify_api_key),
):
    """Cadastra um EAN novo ou atualiza os dados de um produto já existente.

    É o passo seguinte quando ``/upload`` responde ``acao: "erro"``: depois do
    cadastro, um novo upload do mesmo EAN incrementa a quantidade normalmente.
    """
    request.state.client_info = client
    logger.info("put_produto codigo=%s client_type=%s", codigo, client.client_type)
    return salvar_produto(codigo, dados)


@app.get("/events/estoque")
async def stream_estoque(
    request: Request,
    client: ClientInfo = Depends(verify_sse_token),
):
    """SSE: envia ``estoque-update`` quando a quantidade de um produto muda.

    Limite de 1 conexão simultânea por cliente (por IP): uma nova conexão
    enquanto a anterior estiver aberta recebe 429. A vaga é liberada quando o
    cliente desconecta.

    Autenticação via header ``X-API-Key`` ou query param ``token`` (para EventSource no browser).
    """
    request.state.client_info = client
    logger.info("sse_connect client_type=%s", client.client_type)

    cliente = request.client.host if request.client else "unknown"
    if not eventos.pode_conectar(cliente):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Já existe uma conexão SSE aberta para este cliente",
        )

    eventos.registrar_conexao(cliente)
    try:
        return resposta_estoque(cliente)
    except Exception:
        eventos.remover_conexao(cliente)
        raise


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
