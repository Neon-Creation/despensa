"""Aplicação FastAPI: upload de fotos da ESP32 e healthcheck."""

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from api.auth import verify_api_key
from api.config import settings
from api.schemas import UploadResponse, EstoqueResponse, ProdutoResponse, ProdutoUpsert
from api.services.database import health_check_db
from api.services.estoque import get_estoque, get_produto, salvar_produto
from api.services.scanner import processar_upload

app = FastAPI(title="Despensa API", version="1.0.0")

# CORS aberto para depuração local (restringir em produção).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Rate limit: 10 requisições por minuto por IP.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.post("/upload", response_model=UploadResponse, dependencies=[Depends(verify_api_key)])
@limiter.limit("10/minute")
async def upload_foto(request: Request, file: UploadFile = File(...)):
    """Recebe uma imagem JPEG, lê o EAN e atualiza a quantidade no banco."""
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


@app.get("/estoque", response_model=EstoqueResponse, dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def listar_estoque(request: Request):
    """Lista todos os produtos do estoque ordenados por nome."""
    return get_estoque()


@app.get("/produto/{codigo}", response_model=ProdutoResponse, dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def obter_produto(request: Request, codigo: str):
    """Retorna detalhes de um produto específico pelo código EAN."""
    produto = get_produto(codigo)
    if not produto:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Produto não encontrado")
    return produto


@app.put("/produto/{codigo}", response_model=ProdutoResponse, dependencies=[Depends(verify_api_key)])
@limiter.limit("30/minute")
async def cadastrar_produto(request: Request, codigo: str, dados: ProdutoUpsert):
    """Cadastra um EAN novo ou atualiza os dados de um produto já existente.

    É o passo seguinte quando ``/upload`` responde ``acao: "erro"``: depois do
    cadastro, um novo upload do mesmo EAN incrementa a quantidade normalmente.
    """
    return salvar_produto(codigo, dados)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host=settings.HOST,
        port=settings.PORT,
        log_level=settings.LOG_LEVEL.lower(),
    )
