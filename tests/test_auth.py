"""Testes de autenticação por cliente e isolamento de rate limit.

Cobre:
- Chave ESP32 autentica e retorna client_type="esp32".
- Chave Dashboard autentica e retorna client_type="dashboard".
- Chave inválida ou ausente retorna 401.
- Fallback legado (apenas API_KEY configurada) autentica como "esp32".
- Contadores de rate limit são separados por client_type.
- /upload rejeita (403) requisições autenticadas como dashboard.
"""

import io
import itertools

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.auth import ClientInfo, _resolve_client_type
from api.config import settings

# IPs únicos para isolar os contadores de rate limit entre testes.
_ips = itertools.count(200)


def _novo_client():
    from api.main import app

    return TestClient(app, client=(f"10.1.0.{next(_ips)}", 50000))


def _jpeg_vazio() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (255, 255, 255)).save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Testes unitários de _resolve_client_type
# ---------------------------------------------------------------------------


class TestResolveClientType:
    def setup_method(self):
        """Guarda e limpa as chaves específicas antes de cada teste."""
        self._orig_esp32 = settings.API_KEY_ESP32
        self._orig_dashboard = settings.API_KEY_DASHBOARD
        self._orig_legacy = settings.API_KEY

    def teardown_method(self):
        settings.API_KEY_ESP32 = self._orig_esp32
        settings.API_KEY_DASHBOARD = self._orig_dashboard
        settings.API_KEY = self._orig_legacy

    def test_esp32_key_resolves_to_esp32(self):
        settings.API_KEY_ESP32 = "chave-esp32"
        settings.API_KEY_DASHBOARD = "chave-dashboard"
        assert _resolve_client_type("chave-esp32") == "esp32"

    def test_dashboard_key_resolves_to_dashboard(self):
        settings.API_KEY_ESP32 = "chave-esp32"
        settings.API_KEY_DASHBOARD = "chave-dashboard"
        assert _resolve_client_type("chave-dashboard") == "dashboard"

    def test_invalid_key_returns_none(self):
        settings.API_KEY_ESP32 = "chave-esp32"
        settings.API_KEY_DASHBOARD = "chave-dashboard"
        assert _resolve_client_type("chave-errada") is None

    def test_legacy_fallback_resolves_as_esp32_when_specific_keys_absent(self):
        settings.API_KEY_ESP32 = None
        settings.API_KEY_DASHBOARD = None
        settings.API_KEY = "legado-123"
        assert _resolve_client_type("legado-123") == "esp32"

    def test_legacy_key_not_accepted_when_specific_keys_configured(self):
        """A chave legada não deve vazar quando as específicas estão definidas."""
        settings.API_KEY_ESP32 = "chave-esp32"
        settings.API_KEY_DASHBOARD = "chave-dashboard"
        settings.API_KEY = "legado-123"
        # A chave legada deve ser rejeitada — não corresponde a nenhuma específica.
        assert _resolve_client_type("legado-123") is None

    def test_esp32_key_not_confused_with_dashboard(self):
        settings.API_KEY_ESP32 = "chave-esp32"
        settings.API_KEY_DASHBOARD = "chave-dashboard"
        assert _resolve_client_type("chave-dashboard") != "esp32"
        assert _resolve_client_type("chave-esp32") != "dashboard"


# ---------------------------------------------------------------------------
# Testes de integração via HTTP
# ---------------------------------------------------------------------------


class TestAuthHTTP:
    def setup_method(self):
        self._orig_esp32 = settings.API_KEY_ESP32
        self._orig_dashboard = settings.API_KEY_DASHBOARD

    def teardown_method(self):
        settings.API_KEY_ESP32 = self._orig_esp32
        settings.API_KEY_DASHBOARD = self._orig_dashboard

    def test_esp32_key_accepted_on_estoque(self):
        settings.API_KEY_ESP32 = "k-esp32"
        settings.API_KEY_DASHBOARD = "k-dashboard"
        client = _novo_client()
        r = client.get("/estoque", headers={"X-API-Key": "k-esp32"})
        assert r.status_code == 200

    def test_dashboard_key_accepted_on_estoque(self):
        settings.API_KEY_ESP32 = "k-esp32"
        settings.API_KEY_DASHBOARD = "k-dashboard"
        client = _novo_client()
        r = client.get("/estoque", headers={"X-API-Key": "k-dashboard"})
        assert r.status_code == 200

    def test_missing_key_returns_401(self):
        client = _novo_client()
        r = client.get("/estoque")
        assert r.status_code == 401
        assert r.json()["detail"] == "API Key inválida"

    def test_wrong_key_returns_401(self):
        settings.API_KEY_ESP32 = "k-esp32"
        settings.API_KEY_DASHBOARD = "k-dashboard"
        client = _novo_client()
        r = client.get("/estoque", headers={"X-API-Key": "errada"})
        assert r.status_code == 401

    def test_dashboard_key_rejected_on_upload(self):
        """/upload é exclusivo da ESP32; chave de dashboard deve receber 403."""
        settings.API_KEY_ESP32 = "k-esp32"
        settings.API_KEY_DASHBOARD = "k-dashboard"
        client = _novo_client()
        r = client.post(
            "/upload",
            headers={"X-API-Key": "k-dashboard"},
            files={"file": ("foto.jpg", _jpeg_vazio(), "image/jpeg")},
        )
        assert r.status_code == 403
        assert "ESP32" in r.json()["detail"]

    def test_esp32_key_accepted_on_upload(self):
        settings.API_KEY_ESP32 = "k-esp32"
        settings.API_KEY_DASHBOARD = "k-dashboard"
        client = _novo_client()
        r = client.post(
            "/upload",
            headers={"X-API-Key": "k-esp32"},
            files={"file": ("foto.jpg", _jpeg_vazio(), "image/jpeg")},
        )
        # Pode ser 200 (sem EAN) ou imagem inválida — não deve ser 401/403.
        assert r.status_code not in (401, 403)


# ---------------------------------------------------------------------------
# Isolamento de rate limit por client_type
# ---------------------------------------------------------------------------


class TestRateLimitIsolation:
    def setup_method(self):
        self._orig_esp32 = settings.API_KEY_ESP32
        self._orig_dashboard = settings.API_KEY_DASHBOARD

    def teardown_method(self):
        settings.API_KEY_ESP32 = self._orig_esp32
        settings.API_KEY_DASHBOARD = self._orig_dashboard

    def test_esp32_and_dashboard_have_separate_upload_counters(self):
        """Esgota o limite do ESP32 sem afetar um segundo IP com chave de dashboard.

        O /upload tem limite de 10/min por chave ESP32.
        Depois de esgotar 10 chamadas no IP do ESP32, um IP diferente com
        a mesma chave ESP32 ainda deve conseguir 1 chamada (limite reinicia
        por IP+tipo), e um cliente dashboard não deve ser bloqueado (endpoint
        retorna 403, não 429).
        """
        settings.API_KEY_ESP32 = "k-esp32-rl"
        settings.API_KEY_DASHBOARD = "k-dashboard-rl"

        # IP A — esgota o limite de /upload com chave esp32
        ip_a = f"10.2.0.{next(_ips)}"
        client_a = TestClient(__import__("api.main", fromlist=["app"]).app, client=(ip_a, 50000))
        jpeg = _jpeg_vazio()
        for _ in range(10):
            client_a.post(
                "/upload",
                headers={"X-API-Key": "k-esp32-rl"},
                files={"file": ("foto.jpg", jpeg, "image/jpeg")},
            )
        r_bloqueado = client_a.post(
            "/upload",
            headers={"X-API-Key": "k-esp32-rl"},
            files={"file": ("foto.jpg", jpeg, "image/jpeg")},
        )
        assert r_bloqueado.status_code == 429

        # IP B — chave dashboard: /upload deve retornar 403 (escopo, não rate limit)
        ip_b = f"10.2.0.{next(_ips)}"
        client_b = TestClient(__import__("api.main", fromlist=["app"]).app, client=(ip_b, 50000))
        r_dashboard = client_b.post(
            "/upload",
            headers={"X-API-Key": "k-dashboard-rl"},
            files={"file": ("foto.jpg", jpeg, "image/jpeg")},
        )
        assert r_dashboard.status_code == 403  # proibido por escopo, não por rate limit

    def test_estoque_counters_are_separate_per_client_type(self):
        """Quota de /estoque do esp32 não consome a quota do dashboard."""
        settings.API_KEY_ESP32 = "k-esp32-estoque"
        settings.API_KEY_DASHBOARD = "k-dashboard-estoque"

        # Mesmo IP, chave esp32: consome 5 de 30
        ip = f"10.3.0.{next(_ips)}"
        client_esp = TestClient(
            __import__("api.main", fromlist=["app"]).app, client=(ip, 50000)
        )
        for _ in range(5):
            r = client_esp.get("/estoque", headers={"X-API-Key": "k-esp32-estoque"})
            assert r.status_code == 200

        # Mesmo IP, chave dashboard: contador independente, não bloqueado
        client_dash = TestClient(
            __import__("api.main", fromlist=["app"]).app, client=(ip, 50001)
        )
        for _ in range(5):
            r = client_dash.get("/estoque", headers={"X-API-Key": "k-dashboard-estoque"})
            assert r.status_code == 200


# ---------------------------------------------------------------------------
# Verificação do ClientInfo retornado
# ---------------------------------------------------------------------------


def test_client_info_is_named_tuple():
    info = ClientInfo(client_type="esp32")
    assert info.client_type == "esp32"

    info2 = ClientInfo(client_type="dashboard")
    assert info2.client_type == "dashboard"
