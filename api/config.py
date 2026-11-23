"""Configurações da API, carregadas de variáveis de ambiente e do .env da raiz."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# __file__ = <raiz>/api/config.py -> parent.parent = <raiz>
CAMINHO_ENV = Path(__file__).resolve().parent.parent / ".env"


class Settings(BaseSettings):
    """Parâmetros de execução da API.

    API_KEY: valor padrão existe apenas para permitir execução local imediata.
    Em produção, defina API_KEY no .env (ou variável de ambiente) com um valor
    forte gerado, por exemplo, com ``openssl rand -hex 32``.
    """

    API_KEY: str = "chave-teste-123"
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
