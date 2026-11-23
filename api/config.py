"""Configurações da API, carregadas de variáveis de ambiente e do .env da raiz."""

from pathlib import Path
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict

# __file__ = <raiz>/api/config.py -> parent.parent = <raiz>
CAMINHO_ENV = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Parâmetros de execução da API.

    Chaves de API por cliente
    -------------------------
    Prefira usar ``API_KEY_ESP32`` e ``API_KEY_DASHBOARD`` em implantações novas.
    Cada chave identifica o tipo de cliente nas respostas de autenticação e nos
    logs, e separa os contadores de rate limit por tipo.

    ``API_KEY`` permanece como fallback de compatibilidade: se as chaves
    específicas não estiverem definidas, qualquer requisição que a apresente é
    autenticada como ``esp32``. Isso mantém implantações existentes funcionando
    sem alteração de configuração.

    Em produção, gere valores fortes com ``openssl rand -hex 32`` e defina as
    três variáveis no ``.env`` (ou no ambiente do container).
    """

    # Chave legada — usada como fallback quando as específicas não estão definidas.
    API_KEY: str = "chave-teste-123"

    # Chaves específicas por cliente (None = não configurada).
    API_KEY_ESP32: str | None = None
    API_KEY_DASHBOARD: str | None = None

    DATABASE_PATH: str = "/data/despensa.db"
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"
    MAX_UPLOAD_MB: int = 5

    model_config = SettingsConfigDict(
        env_file=str(CAMINHO_ENV),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
