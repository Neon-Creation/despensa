"""Testes locais da API — Fase 5.1 a 5.6 do plano api-esp32-cloud."""

import io
import pytest
import sqlite3

from PIL import Image, ImageOps

from api.config import settings
from api.services import scanner

IMAGEM_MIOJO = "imagens/miojo.jpg"
CHAVE = "chave-teste-123"


def _bytes_da_imagem(imagem):
    buffer = io.BytesIO()
    imagem.save(buffer, format="JPEG")
    return buffer.getvalue()


def _conteudo_miojo():
    with open(IMAGEM_MIOJO, "rb") as arquivo:
        return arquivo.read()


def _codigo_da_imagem(conteudo):
    imagem = ImageOps.exif_transpose(Image.open(io.BytesIO(conteudo))).convert("RGB")
    return next(iter(scanner.ler_codigo_rapido_em_memoria(imagem)))


def _upload(client, caminho=IMAGEM_MIOJO, chave=CHAVE, tipo="image/jpeg", conteudo=None):
    conteudo = _conteudo_miojo() if conteudo is None else conteudo
    nome = caminho.split("/")[-1]
    return client.post(
        "/upload",
        headers={"X-API-Key": chave},
        files={"file": (nome, conteudo, tipo)},
    )


@pytest.fixture
def api_key():
    """Chave de API válida para testes."""
    return CHAVE


@pytest.fixture
def produto_cadastrado():
    """Retorna um produto que existe no banco de teste."""
    with sqlite3.connect(settings.DATABASE_PATH) as conn:
        linha = conn.execute(
            "SELECT codigo, nome FROM produtos LIMIT 1"
        ).fetchone()
    if linha is None:
        pytest.skip("Nenhum produto cadastrado no banco de teste")
    return {"codigo": linha[0], "nome": linha[1]}


# --- 5.1 Healthcheck -------------------------------------------------------
def test_health(client):
    resposta = client.get("/health")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok", "database": "connected"}


# --- 5.2 Autenticação ------------------------------------------------------
def test_auth_sem_header(client):
    resposta = client.post("/upload", files={"file": ("miojo.jpg", b"x", "image/jpeg")})
    assert resposta.status_code == 401
    assert resposta.json()["detail"] == "API Key inválida"


def test_auth_chave_errada(client):
    assert _upload(client, chave="errada").status_code == 401


def test_auth_chave_correta(client):
    assert _upload(client).status_code == 200


# --- 5.3 Validação de arquivo ----------------------------------------------
def test_upload_nao_imagem(client):
    resposta = _upload(client, caminho="teste.txt", tipo="text/plain", conteudo=b"teste")
    assert resposta.status_code == 400
    assert resposta.json()["detail"] == "Arquivo não é imagem"


def test_upload_grande_demais(client):
    conteudo = b"0" * ((settings.MAX_UPLOAD_MB + 1) * 1024 * 1024)
    resposta = _upload(client, conteudo=conteudo)
    assert resposta.status_code == 413
    assert "excede" in resposta.json()["detail"]


# --- 5.4 Rate limit --------------------------------------------------------
def test_rate_limit(client):
    codigos = [_upload(client).status_code for _ in range(11)]
    assert codigos[:10] == [200] * 10
    assert codigos[10] == 429


# --- 5.5 Cenários de negócio -----------------------------------------------
def test_produto_existente(client):
    codigo = _codigo_da_imagem(_conteudo_miojo())

    with sqlite3.connect(settings.DATABASE_PATH) as conn:
        linha = conn.execute(
            "SELECT nome FROM produtos WHERE codigo = ?", (codigo,)
        ).fetchone()
    assert linha is not None, "Use uma imagem com EAN já cadastrado no banco"

    resposta = _upload(client)
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["lido"] is True
    assert dados["codigo"] == codigo
    assert dados["produto"] == linha[0]
    assert dados["acao"] == "contado"
    assert isinstance(dados["quantidade"], int) and dados["quantidade"] > 0


def test_produto_novo(client, monkeypatch):
    monkeypatch.setattr(
        scanner, "ler_codigo_rapido_em_memoria", lambda imagem: {"7899999999999": "EAN13"}
    )
    resposta = _upload(client)
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["lido"] is True
    assert dados["codigo"] == "7899999999999"
    assert dados["produto"] is None
    assert dados["acao"] == "erro"
    assert dados["quantidade"] == 0
    assert "não cadastrado" in dados["mensagem"]


def test_sem_codigo_de_barras(client, monkeypatch):
    monkeypatch.setattr(scanner, "ler_codigo_rapido_em_memoria", lambda imagem: {})
    resposta = _upload(client)
    assert resposta.status_code == 200
    assert resposta.json() == {
        "lido": False,
        "codigo": None,
        "produto": None,
        "acao": None,
        "quantidade": None,
        "mensagem": "Nenhum código de barras encontrado",
    }


def test_imagem_real_sem_codigo(client):
    """Imagem gerada em memória, sem nenhum código de barras."""
    imagem = Image.new("RGB", (64, 64), (255, 255, 255))
    resposta = _upload(client, conteudo=_bytes_da_imagem(imagem))
    assert resposta.status_code == 200
    assert resposta.json()["lido"] is False


# --- 5.6 Persistência ------------------------------------------------------
def test_persistencia_incremento(client):
    codigo = _codigo_da_imagem(_conteudo_miojo())

    def quantidade():
        with sqlite3.connect(settings.DATABASE_PATH) as conn:
            return conn.execute(
                "SELECT quantidade FROM produtos WHERE codigo = ?", (codigo,)
            ).fetchone()[0]

    antes = quantidade()
    for _ in range(3):
        assert _upload(client).status_code == 200
    assert quantidade() == antes + 3


# --- 5.7 Endpoints de Estoque (Dashboard) ----------------------------------
def test_listar_estoque(client, api_key):
    resposta = client.get("/estoque", headers={"X-API-Key": api_key})
    assert resposta.status_code == 200
    dados = resposta.json()
    assert "produtos" in dados and "total" in dados
    assert isinstance(dados["produtos"], list)
    assert dados["total"] == len(dados["produtos"])
    if dados["produtos"]:
        produto = dados["produtos"][0]
        assert "codigo" in produto
        assert "nome" in produto
        assert "quantidade" in produto


def test_listar_estoque_sem_auth(client):
    resposta = client.get("/estoque")
    assert resposta.status_code == 401


def test_obter_produto_existente(client, api_key, produto_cadastrado):
    resposta = client.get(
        f"/produto/{produto_cadastrado['codigo']}", headers={"X-API-Key": api_key}
    )
    assert resposta.status_code == 200
    dados = resposta.json()
    assert dados["codigo"] == produto_cadastrado["codigo"]
    assert dados["nome"] == produto_cadastrado["nome"]
    assert "quantidade" in dados


def test_obter_produto_inexistente(client, api_key):
    resposta = client.get("/produto/0000000000000", headers={"X-API-Key": api_key})
    assert resposta.status_code == 404
    assert resposta.json()["detail"] == "Produto não encontrado"


def test_estoque_rate_limit(client, api_key):
    # /estoque tem limite de 30/min, testamos alguns para garantir que não bloqueia
    for _ in range(5):
        resposta = client.get("/estoque", headers={"X-API-Key": api_key})
        assert resposta.status_code == 200


# --- 5.8 Validação de formato do EAN ----------------------------------------
EANS_INVALIDOS = ["1234567", "123456789012345", "789107901337a", "7891-0790-1337"]


@pytest.mark.parametrize("codigo", EANS_INVALIDOS)
def test_obter_produto_com_ean_invalido_retorna_400(client, api_key, codigo):
    resposta = client.get(f"/produto/{codigo}", headers={"X-API-Key": api_key})
    assert resposta.status_code == 400
    assert "EAN" in resposta.json()["detail"]


@pytest.mark.parametrize("codigo", EANS_INVALIDOS)
def test_cadastrar_produto_com_ean_invalido_retorna_400(client, api_key, codigo):
    resposta = client.put(
        f"/produto/{codigo}",
        headers={"X-API-Key": api_key},
        json={"nome": "Produto teste"},
    )
    assert resposta.status_code == 400
    assert "EAN" in resposta.json()["detail"]
