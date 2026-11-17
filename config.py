"""Configuração compartilhada do projeto.

Carrega as variáveis do arquivo .env que fica ao lado dos scripts.
Não sobrescreve variáveis já definidas no ambiente (útil em servidores/CI).
"""

import os
from pathlib import Path

CAMINHO_ENV = Path(__file__).parent / ".env"


def carregar_env(caminho=None, sobrescrever=False):
    """Lê um arquivo .env simples (CHAVE=valor) e exporta para o ambiente.

    Com ``sobrescrever=True`` o .env passa a ser a fonte principal da chave:
    basta colar uma chave nova no arquivo e rodar o script novamente.
    """
    caminho = Path(caminho) if caminho is not None else CAMINHO_ENV

    if not caminho.is_file():
        return

    for linha in caminho.read_text(encoding="utf-8").splitlines():
        linha = linha.strip()
        if not linha or linha.startswith("#") or "=" not in linha:
            continue

        chave, valor = linha.split("=", 1)
        chave = chave.strip()
        valor = valor.strip().strip('"').strip("'")

        if chave and valor and (sobrescrever or chave not in os.environ):
            os.environ[chave] = valor
