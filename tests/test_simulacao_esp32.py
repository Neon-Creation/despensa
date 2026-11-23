"""Contrato HTTP usado pelo firmware da ESP32 (multipart montado à mão)."""

import sqlite3

import pytest

from api.config import settings
from simular_esp32 import BOUNDARY, interpretar_resposta, montar_multipart

CHAVE = "chave-teste-123"
IMAGEM_MIOJO = "imagens/miojo.jpg"
EAN_MIOJO = "7891079013373"


def test_multipart_do_firmware_e_aceito(client):
    with open(IMAGEM_MIOJO, "rb") as arquivo:
        jpeg = arquivo.read()

    with sqlite3.connect(settings.DATABASE_PATH) as conn:
        antes = conn.execute(
            "SELECT quantidade FROM produtos WHERE codigo = ?", (EAN_MIOJO,)
        ).fetchone()[0]

    resposta = client.post(
        "/upload",
        headers={
            "X-API-Key": CHAVE,
            "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
        },
        content=montar_multipart(jpeg),
    )

    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["lido"] is True
    assert dados["codigo"] == EAN_MIOJO
    assert dados["acao"] == "contado"
    assert dados["quantidade"] == antes + 1

    with sqlite3.connect(settings.DATABASE_PATH) as conn:
        depois = conn.execute(
            "SELECT quantidade FROM produtos WHERE codigo = ?", (EAN_MIOJO,)
        ).fetchone()[0]
    assert depois == antes + 1


@pytest.mark.parametrize(
    "json_resposta, lido, bipes",
    [
        (
            '{"lido":true,"codigo":"789","produto":"Arroz","acao":"contado",'
            '"quantidade":2,"mensagem":null}',
            True,
            "curto + longo",
        ),
        (
            '{"lido":true,"codigo":"789","acao":"erro","mensagem":"Produto não cadastrado"}',
            True,
            "3 curtos",
        ),
        (
            '{"lido":false,"mensagem":"Nenhum código de barras encontrado"}',
            False,
            "2 medios",
        ),
    ],
)
def test_interpretacao_da_resposta(json_resposta, lido, bipes):
    resultado = interpretar_resposta(json_resposta)
    assert resultado["lido"] is lido
    assert resultado["bipes"] == bipes
