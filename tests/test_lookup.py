"""Testes da identificação automática de produtos pelo EAN."""

import sqlite3

import pytest

from api.config import settings
from api.services import produto_lookup, scanner
from api import main as api_main

CHAVE = "chave-teste-123"
CHAVE_DASHBOARD = "chave-dashboard-teste"
EAN_NOVO = "7899999999999"


def _upload(client, ean=EAN_NOVO, sessao=None, arquivo="imagens/miojo.jpg"):
    with open(arquivo, "rb") as handle:
        conteudo = handle.read()
    dados = {"sessao": sessao} if sessao else None
    return client.post(
        "/upload",
        headers={"X-API-Key": CHAVE},
        files={"file": ("foto.jpg", conteudo, "image/jpeg")},
        data=dados,
    )


def _quantidade(codigo):
    with sqlite3.connect(settings.DATABASE_PATH) as conn:
        linha = conn.execute(
            "SELECT quantidade FROM produtos WHERE codigo = ?", (codigo,)
        ).fetchone()
    return linha[0] if linha else None


@pytest.fixture
def ean_falso(monkeypatch):
    """Faz a decodificação devolver sempre o mesmo EAN novo."""
    monkeypatch.setattr(
        scanner, "ler_codigo_rapido_em_memoria", lambda imagem: {EAN_NOVO: "EAN13"}
    )
    return EAN_NOVO


def test_produto_novo_e_cadastrado_e_contado(client, monkeypatch, ean_falso):
    monkeypatch.setattr(
        scanner,
        "identificar_produto",
        lambda codigo: {
            "nome": "Produto Encontrado",
            "marca": "Marca Teste",
            "conteudo": "500g",
            "foto_url": "https://example.com/foto.jpg",
            "origem": "Open Food Facts",
        },
    )

    resposta = _upload(client)
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["acao"] == "cadastrado"
    assert dados["produto"] == "Produto Encontrado"
    assert dados["quantidade"] == 1
    assert dados["origem"] == "Open Food Facts"
    assert _quantidade(EAN_NOVO) == 1


def test_lookup_sem_resultado_continua_como_erro(client, monkeypatch, ean_falso):
    monkeypatch.setattr(scanner, "identificar_produto", lambda codigo: None)

    resposta = _upload(client)
    assert resposta.status_code == 200
    assert resposta.json()["acao"] == "erro"
    assert _quantidade(EAN_NOVO) is None


def test_produto_existente_nao_chama_lookup(client, monkeypatch):
    def nao_chamar(codigo):
        raise AssertionError("lookup não deveria ser chamado para produto existente")

    monkeypatch.setattr(scanner, "identificar_produto", nao_chamar)
    resposta = _upload(client, ean="7891079013373")
    assert resposta.status_code == 200
    assert resposta.json()["acao"] == "contado"


def test_mesma_sessao_nao_conta_duplicado(client, monkeypatch):
    with sqlite3.connect(settings.DATABASE_PATH) as conn:
        codigo = conn.execute("SELECT codigo FROM produtos LIMIT 1").fetchone()[0]
        quantidade_inicial = conn.execute(
            "SELECT quantidade FROM produtos WHERE codigo = ?", (codigo,)
        ).fetchone()[0]

    monkeypatch.setattr(
        scanner, "ler_codigo_rapido_em_memoria", lambda imagem: {codigo: "EAN13"}
    )

    primeira = _upload(client, ean=codigo, sessao="sessao-unica")
    segunda = _upload(client, ean=codigo, sessao="sessao-unica")
    assert primeira.status_code == segunda.status_code == 200
    assert primeira.json()["quantidade"] == segunda.json()["quantidade"]
    assert _quantidade(codigo) == quantidade_inicial + 1


def test_sugestao_preenchida(client, monkeypatch):
    monkeypatch.setattr(
        api_main,
        "identificar_produto",
        lambda codigo: {
            "nome": "Sugestão Automática",
            "marca": "Marca",
            "conteudo": "1L",
            "foto_url": "https://example.com/foto.jpg",
            "origem": "Cosmos",
        },
    )
    resposta = client.get(
        f"/produto/{EAN_NOVO}/sugestao", headers={"X-API-Key": CHAVE}
    )
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["encontrado"] is True
    assert dados["nome"] == "Sugestão Automática"
    assert dados["origem"] == "Cosmos"
    assert _quantidade(EAN_NOVO) is None, "consulta não deve cadastrar"


def test_sugestao_sem_resultado(client, monkeypatch):
    monkeypatch.setattr(api_main, "identificar_produto", lambda codigo: None)
    resposta = client.get(
        f"/produto/{EAN_NOVO}/sugestao", headers={"X-API-Key": CHAVE}
    )
    assert resposta.status_code == 200
    assert resposta.json() == {
        "encontrado": False,
        "nome": None,
        "marca": None,
        "conteudo": None,
        "foto_url": None,
        "origem": None,
    }


def test_identificar_ia_usa_groq_quando_fontes_rapidas_falham(client, monkeypatch):
    monkeypatch.setattr(settings, "PRODUTO_LOOKUP_GROQ", True)
    monkeypatch.setattr(api_main, "identificar_produto", lambda codigo: None)
    monkeypatch.setattr(
        api_main,
        "identificar_com_groq",
        lambda codigo: {
            "nome": "Produto pela IA",
            "marca": "Marca IA",
            "conteudo": "500g",
            "foto_url": "",
            "origem": "Groq",
        },
    )

    resposta = client.get(
        f"/produto/{EAN_NOVO}/identificar-ia", headers={"X-API-Key": CHAVE_DASHBOARD}
    )
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["encontrado"] is True
    assert dados["nome"] == "Produto pela IA"
    assert dados["origem"] == "Groq"
    assert _quantidade(EAN_NOVO) is None, "consulta não deve cadastrar"


def test_identificar_ia_fontes_rapidas_nao_chama_groq(client, monkeypatch):
    def nao_chamar(codigo):
        raise AssertionError("Groq não deveria ser chamado quando a fonte rápida encontra")

    monkeypatch.setattr(
        api_main,
        "identificar_produto",
        lambda codigo: {"nome": "Produto Rápido", "origem": "Cosmos"},
    )
    monkeypatch.setattr(api_main, "identificar_com_groq", nao_chamar)

    resposta = client.get(
        f"/produto/{EAN_NOVO}/identificar-ia", headers={"X-API-Key": CHAVE_DASHBOARD}
    )
    assert resposta.status_code == 200
    assert resposta.json()["origem"] == "Cosmos"


def test_identificar_ia_exclusivo_do_dashboard(client):
    resposta = client.get(
        f"/produto/{EAN_NOVO}/identificar-ia", headers={"X-API-Key": CHAVE}
    )
    assert resposta.status_code == 403
    assert resposta.json()["detail"] == "Este endpoint é exclusivo do dashboard"


def test_fontes_rapidas_com_resposta_mockada(monkeypatch):
    class Resposta:
        status_code = 200

        def json(self):
            return {
                "product": {
                    "product_name_pt": "Produto da Base",
                    "brands": "Marca Base",
                    "quantity": "500g",
                    "image_url": "https://example.com/foto.jpg",
                }
            }

    monkeypatch.setattr(
        produto_lookup.requests, "get", lambda *args, **kwargs: Resposta()
    )
    info = produto_lookup.identificar_produto(EAN_NOVO)
    assert info["nome"] == "Produto da Base"
    assert info["origem"] == "Open Food Facts"


def test_timeout_geral_nao_chama_outras_fontes(monkeypatch):
    monkeypatch.setattr(settings, "PRODUTO_LOOKUP_ATIVO", True)
    monkeypatch.setattr(settings, "PRODUTO_LOOKUP_TIMEOUT_S", 0.5)

    def lenta(codigo):
        raise AssertionError("fonte não deveria ser chamada com orçamento esgotado")

    monkeypatch.setattr(produto_lookup, "FONTES_RAPIDAS", (lenta,))
    # 1º valor define o limite; 2º já o ultrapassa, antes de chamar a fonte.
    valores = iter([1000.0, 1002.0])
    monkeypatch.setattr(produto_lookup.time, "monotonic", lambda: next(valores))
    assert produto_lookup.identificar_produto(EAN_NOVO) is None


def test_groq_desligado_nao_chama_ia(monkeypatch):
    monkeypatch.setattr(settings, "PRODUTO_LOOKUP_GROQ", False)
    assert produto_lookup.identificar_com_groq(EAN_NOVO) is None
