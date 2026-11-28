"""Configuração dos testes da API (banco isolado e cliente HTTP)."""

import itertools
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))

BANCO_ORIGINAL = RAIZ / "despensa.db"
BANCO_TESTE = Path(tempfile.gettempdir()) / "despensa_api_teste.db"

os.environ.setdefault("API_KEY", "chave-teste-123")
os.environ.setdefault("API_KEY_ESP32", "chave-teste-123")
os.environ.setdefault("API_KEY_DASHBOARD", "chave-dashboard-teste")
os.environ["DATABASE_PATH"] = str(BANCO_TESTE)

from api.config import settings  # noqa: E402

# Força o banco isolado mesmo que o .env defina DATABASE_PATH.
settings.DATABASE_PATH = str(BANCO_TESTE)
# Força chaves de teste para autenticação (sobrescreve valores do .env).
# Chaves diferentes permitem testar a restrição de client_type do DELETE.
settings.API_KEY_ESP32 = "chave-teste-123"
settings.API_KEY_DASHBOARD = "chave-dashboard-teste"

from api.main import app  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _copiar_banco():
    """Recria o banco de teste a partir do banco do projeto."""
    for sufixo in ("-wal", "-shm"):
        caminho = Path(str(BANCO_TESTE) + sufixo)
        if caminho.exists():
            caminho.unlink()
    shutil.copy(BANCO_ORIGINAL, BANCO_TESTE)


@pytest.fixture(autouse=True)
def banco_limpo():
    """Garante o mesmo estado inicial para cada teste."""
    _copiar_banco()
    yield


@pytest.fixture(autouse=True)
def lookup_desligado(monkeypatch):
    """Testes não devem consultar serviços externos sem mock explícito."""
    from api.services import scanner

    monkeypatch.setattr(scanner, "identificar_produto", lambda codigo: None)


_ips = itertools.count(1)


@pytest.fixture
def client():
    """Cliente HTTP com IP único por teste, isolando o contador de rate limit."""
    return TestClient(app, client=(f"10.0.0.{next(_ips)}", 50000))
