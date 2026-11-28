"""Testes da exclusão de produtos (somente dashboard)."""

import sqlite3

import pytest

from api.config import settings

CHAVE_ESP32 = "chave-teste-123"
CHAVE_DASHBOARD = "chave-dashboard-teste"
EAN = "7891079013373"


def _existe(codigo):
    with sqlite3.connect(settings.DATABASE_PATH) as conn:
        return conn.execute(
            "SELECT 1 FROM produtos WHERE codigo = ?", (codigo,)
        ).fetchone() is not None


def _delete(client, codigo=EAN, chave=CHAVE_DASHBOARD):
    return client.delete(f"/produto/{codigo}", headers={"X-API-Key": chave})


def test_dashboard_apaga_produto(client):
    assert _existe(EAN)
    resposta = _delete(client)
    assert resposta.status_code == 200
    assert resposta.json() == {"codigo": EAN, "excluido": True}
    assert not _existe(EAN)


def test_esp32_nao_pode_apagar(client):
    resposta = _delete(client, chave=CHAVE_ESP32)
    assert resposta.status_code == 403
    assert resposta.json()["detail"] == "Este endpoint é exclusivo do dashboard"
    assert _existe(EAN)


def test_sem_chave_nao_pode_apagar(client):
    resposta = client.delete(f"/produto/{EAN}")
    assert resposta.status_code == 401
    assert _existe(EAN)


def test_produto_inexistente_retorna_404(client):
    resposta = _delete(client, codigo="0000000000000")
    assert resposta.status_code == 404
    assert resposta.json()["detail"] == "Produto não encontrado"


@pytest.mark.parametrize("codigo", ["1234567", "123456789012345", "78910790133a7"])
def test_ean_invalido_retorna_400(client, codigo):
    resposta = _delete(client, codigo=codigo)
    assert resposta.status_code == 400
    assert "EAN" in resposta.json()["detail"]


def test_exclusao_e_idempotente_no_estado(client):
    """Apagar duas vezes não apaga outros produtos nem muda o resultado."""
    primeira = _delete(client)
    segunda = _delete(client)
    assert primeira.status_code == 200
    assert segunda.status_code == 404
    assert not _existe(EAN)
