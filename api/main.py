"""Aplicação FastAPI: upload de fotos da ESP32 e healthcheck."""

import asyncio
import logging

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
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
    ExclusaoResponse,
    ProdutoResponse,
    ProdutoUpsert,
    SugestaoResponse,
    UploadResponse,
)
from api.services.database import health_check_db, produto_existe
from api.services.estoque import get_estoque, get_produto, remover_produto, salvar_produto
from api.services.events import eventos, resposta_estoque
from api.services.produto_lookup import identificar_com_groq, identificar_produto
from api.services.scanner import identificar_groq_em_background, processar_upload

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
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    sessao: str | None = Form(default=None),
    client: ClientInfo = Depends(verify_api_key),
):
    """Recebe uma imagem JPEG, lê o EAN e atualiza a quantidade no banco.

    Aceita apenas requisições da ESP32 (``client_type="esp32"``). O campo
    opcional ``sessao`` identifica o burst: repetições da mesma sessão não
    contam duas vezes.
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

    # O lookup consulta serviços externos: roda fora do event loop.
    resultado = await asyncio.to_thread(processar_upload, conteudo, sessao)
    if resultado.pop("_agendar_groq", False):
        eventos.notificar_produto_identificando(resultado["codigo"])
        background_tasks.add_task(identificar_groq_em_background, resultado["codigo"])
    return resultado


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


@app.get("/produto/{codigo}/sugestao", response_model=SugestaoResponse)
@limiter.limit("10/minute")
async def sugerir_produto(
    request: Request,
    codigo: EanCodigo,
    client: ClientInfo = Depends(verify_api_key),
):
    """Consulta fontes públicas e sugere dados para um EAN não cadastrado.

    Não grava nada; o dashboard usa o resultado para preencher o formulário de
    cadastro quando a API não conseguiu identificar o produto no upload.
    """
    request.state.client_info = client
    logger.info("sugestao codigo=%s client_type=%s", codigo, client.client_type)

    if produto_existe(codigo):
        return SugestaoResponse(encontrado=False)

    info = await asyncio.to_thread(identificar_produto, codigo)
    if not info:
        return SugestaoResponse(encontrado=False)
    return SugestaoResponse(
        encontrado=True,
        nome=info.get("nome"),
        marca=info.get("marca"),
        conteudo=info.get("conteudo"),
        foto_url=info.get("foto_url"),
        origem=info.get("origem"),
    )


@app.get("/produto/{codigo}/identificar-ia", response_model=SugestaoResponse)
@limiter.limit("5/minute")
async def identificar_produto_com_ia(
    request: Request,
    codigo: EanCodigo,
    client: ClientInfo = Depends(verify_api_key),
):
    """Identifica o produto sob demanda, usando fontes rápidas e depois Groq.

    É a busca acionada pelo botão "Buscar com IA" do cadastro manual. Pode
    demorar alguns segundos; não grava nada no banco.
    """
    if client.client_type != "dashboard":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este endpoint é exclusivo do dashboard",
        )

    request.state.client_info = client
    logger.info("identificar_ia codigo=%s client_type=%s", codigo, client.client_type)

    if produto_existe(codigo):
        return SugestaoResponse(encontrado=False)

    def buscar() -> dict | None:
        info = identificar_produto(codigo)
        if not info and settings.PRODUTO_LOOKUP_GROQ:
            info = identificar_com_groq(codigo)
        return info

    info = await asyncio.to_thread(buscar)
    if not info:
        return SugestaoResponse(encontrado=False)
    return SugestaoResponse(
        encontrado=True,
        nome=info.get("nome"),
        marca=info.get("marca"),
        conteudo=info.get("conteudo"),
        foto_url=info.get("foto_url"),
        origem=info.get("origem"),
    )


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


@app.delete("/produto/{codigo}", response_model=ExclusaoResponse)
@limiter.limit("30/minute")
async def excluir_produto(
    request: Request,
    codigo: EanCodigo,
    client: ClientInfo = Depends(verify_api_key),
):
    """Apaga um produto do estoque. Operação exclusiva do dashboard.

    A ESP32 nunca pode excluir: apenas a chave do dashboard é aceita.
    """
    if client.client_type != "dashboard":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Este endpoint é exclusivo do dashboard",
        )

    request.state.client_info = client
    logger.info("delete_produto codigo=%s client_type=%s", codigo, client.client_type)
    if not remover_produto(codigo):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produto não encontrado")
    return ExclusaoResponse(codigo=codigo)


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
