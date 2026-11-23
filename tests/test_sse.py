"""Testes do canal SSE com um servidor HTTP real.

O TestClient do Starlette espera a resposta ASGI terminar; como uma conexão SSE
é indefinida por design, ele trava. Aqui subimos o app com uvicorn em thread e
usamos um cliente HTTP com ``stream=True``, como o dashboard fará.
"""

import json
import socket
import threading
import time

import pytest
import requests
import uvicorn

from api.main import app
from api.services import events

CHAVE = "chave-teste-123"
EAN_MIOJO = "7891079013373"


@pytest.fixture
def base_url():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        porta = sock.getsockname()[1]

    servidor = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=porta,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=servidor.run, daemon=True)
    thread.start()
    while not servidor.started and thread.is_alive():
        time.sleep(0.02)
    if not servidor.started:
        raise RuntimeError("Falha ao iniciar o servidor de teste")

    yield f"http://127.0.0.1:{porta}"

    servidor.should_exit = True
    thread.join(timeout=5)


def _esperar(predicado, timeout=3.0):
    inicio = time.monotonic()
    while time.monotonic() - inicio < timeout:
        if predicado():
            return True
        time.sleep(0.02)
    return False


def _upload(base_url):
    with open("imagens/miojo.jpg", "rb") as arquivo:
        conteudo = arquivo.read()
    return requests.post(
        f"{base_url}/upload",
        headers={"X-API-Key": CHAVE},
        files={"file": ("miojo.jpg", conteudo, "image/jpeg")},
        timeout=10,
    )


def _proximo_evento(linhas):
    """Ignora pings/retry e devolve o JSON do próximo ``estoque-update``."""
    while True:
        linha = next(linhas)
        if linha.startswith("event: estoque-update"):
            dado = next(linhas)
            assert dado.startswith("data: ")
            return json.loads(dado[len("data: "):])


def test_sse_recebe_evento_apos_upload(base_url):
    with requests.get(
        f"{base_url}/events/estoque",
        headers={"X-API-Key": CHAVE},
        stream=True,
        timeout=(5, 10),
    ) as resposta:
        assert resposta.status_code == 200
        assert resposta.headers["Content-Type"].startswith("text/event-stream")
        assert _esperar(lambda: events.eventos.quantidade_inscritos == 1)

        upload = _upload(base_url)
        assert upload.status_code == 200
        assert upload.json()["acao"] == "contado"

        evento = _proximo_evento(resposta.iter_lines(decode_unicode=True))

    assert evento["codigo"] == EAN_MIOJO
    assert evento["acao"] == "incrementado"
    assert isinstance(evento["quantidade"], int)
    assert evento["quantidade"] >= 2
    assert _esperar(lambda: events.eventos.quantidade_inscritos == 0)


def test_sse_recebe_evento_apos_put(base_url):
    with requests.get(
        f"{base_url}/events/estoque",
        headers={"X-API-Key": CHAVE},
        stream=True,
        timeout=(5, 10),
    ) as resposta:
        assert resposta.status_code == 200
        assert _esperar(lambda: events.eventos.quantidade_inscritos == 1)

        cadastro = requests.put(
            f"{base_url}/produto/7899999999999",
            headers={"X-API-Key": CHAVE},
            json={"nome": "Produto via SSE"},
            timeout=10,
        )
        assert cadastro.status_code == 200
        evento = _proximo_evento(resposta.iter_lines(decode_unicode=True))

    assert evento["codigo"] == "7899999999999"
    assert evento["acao"] == "atualizado"
    assert evento["quantidade"] == 1
    assert _esperar(lambda: events.eventos.quantidade_inscritos == 0)


def test_sse_exige_api_key(base_url):
    resposta = requests.get(f"{base_url}/events/estoque", timeout=10)
    assert resposta.status_code == 401


def test_sse_apenas_uma_conexao_por_cliente(base_url):
    with requests.get(
        f"{base_url}/events/estoque",
        headers={"X-API-Key": CHAVE},
        stream=True,
        timeout=(5, 10),
    ) as primeira:
        assert primeira.status_code == 200
        assert _esperar(lambda: events.eventos.quantidade_inscritos == 1)

        segunda = requests.get(
            f"{base_url}/events/estoque",
            headers={"X-API-Key": CHAVE},
            stream=True,
            timeout=(5, 10),
        )
        assert segunda.status_code == 429
        assert "conexão SSE" in segunda.json()["detail"]
        segunda.close()

    assert _esperar(lambda: events.eventos.quantidade_inscritos == 0)
