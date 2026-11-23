"""Testes do cadastro via PUT e do fluxo completo upload -> PUT -> upload."""

import sqlite3

from api.config import settings
from api.services import scanner

CHAVE = "chave-teste-123"
CODIGO_NOVO = "7899999999999"


def _jpeg_miojo():
    with open("imagens/miojo.jpg", "rb") as arquivo:
        return arquivo.read()


def _put(client, codigo, json):
    return client.put(f"/produto/{codigo}", headers={"X-API-Key": CHAVE}, json=json)


def _qtd(codigo):
    with sqlite3.connect(settings.DATABASE_PATH) as conn:
        linha = conn.execute(
            "SELECT quantidade FROM produtos WHERE codigo = ?", (codigo,)
        ).fetchone()
    return linha[0] if linha else None


def test_put_cadastra_e_upload_passa_a_contar(client, monkeypatch):
    """O fluxo que faltava: upload -> 'erro' -> PUT -> upload -> 'contado'."""
    monkeypatch.setattr(
        scanner, "ler_codigo_rapido_em_memoria", lambda imagem: {CODIGO_NOVO: "EAN13"}
    )
    arquivo = {"file": ("foto.jpg", _jpeg_miojo(), "image/jpeg")}

    primeira = client.post(
        "/upload", headers={"X-API-Key": CHAVE}, files=arquivo
    )
    assert primeira.json()["acao"] == "erro"
    assert _qtd(CODIGO_NOVO) is None

    cadastro = _put(
        client,
        CODIGO_NOVO,
        {"nome": " Café teste ", "marca": "Marca X", "conteudo": "500 g"},
    )
    assert cadastro.status_code == 200
    dados = cadastro.json()
    assert dados["codigo"] == CODIGO_NOVO
    assert dados["nome"] == "Café teste"
    assert dados["marca"] == "Marca X"
    assert dados["conteudo"] == "500 g"
    assert dados["quantidade"] == 1
    assert _qtd(CODIGO_NOVO) == 1

    segunda = client.post(
        "/upload", headers={"X-API-Key": CHAVE}, files=arquivo
    )
    assert segunda.status_code == 200
    assert segunda.json()["acao"] == "contado"
    assert segunda.json()["produto"] == "Café teste"
    assert segunda.json()["quantidade"] == 2
    assert _qtd(CODIGO_NOVO) == 2


def test_put_atualiza_metadados_sem_zerar_quantidade(client):
    with sqlite3.connect(settings.DATABASE_PATH) as conn:
        codigo = conn.execute("SELECT codigo FROM produtos LIMIT 1").fetchone()[0]
        conn.execute("UPDATE produtos SET quantidade = 7 WHERE codigo = ?", (codigo,))
        conn.commit()

    resposta = _put(client, codigo, {"nome": "Nome corrigido"})
    assert resposta.status_code == 200
    assert resposta.json()["nome"] == "Nome corrigido"
    assert resposta.json()["quantidade"] == 7
    assert _qtd(codigo) == 7


def test_put_com_quantidade_explicita(client):
    resposta = _put(
        client,
        CODIGO_NOVO,
        {"nome": "Produto zerado", "quantidade": 0},
    )
    assert resposta.status_code == 200
    assert resposta.json()["quantidade"] == 0
    assert _qtd(CODIGO_NOVO) == 0


def test_put_exige_autenticacao(client):
    resposta = client.put(
        f"/produto/{CODIGO_NOVO}", json={"nome": "Teste"}
    )
    assert resposta.status_code == 401


def test_put_valida_nome(client):
    assert _put(client, CODIGO_NOVO, {"marca": "Sem nome"}).status_code == 422
    assert _put(client, CODIGO_NOVO, {"nome": "   "}).status_code == 422
    assert _put(client, CODIGO_NOVO, {"nome": "Ok", "quantidade": -1}).status_code == 422
