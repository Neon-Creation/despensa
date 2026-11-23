"""Compatibilidade para localizar a libzbar em macOS com Homebrew.

O pyzbar usa ``ctypes.util.find_library('zbar')``, que retorna ``None`` no
macOS Apple Silicon mesmo depois de ``brew install zbar`` (a biblioteca fica
em ``/opt/homebrew/lib``). Este módulo ajusta a busca antes de importar o
pyzbar. Em Linux/Windows ele não altera nada.
"""

import ctypes.util
import sys
from pathlib import Path

CANDIDATOS = (
    "/opt/homebrew/lib/libzbar.dylib",  # Apple Silicon
    "/usr/local/lib/libzbar.dylib",     # Intel macOS
)


def garantir_zbar():
    """Importa o pyzbar garantindo que a libzbar seja encontrada."""
    try:
        from pyzbar.pyzbar import decode  # noqa: F401
        return decode
    except ImportError:
        # Remove os imports parciais para poder tentar de novo após o ajuste.
        for nome in list(sys.modules):
            if nome == "pyzbar" or nome.startswith("pyzbar."):
                del sys.modules[nome]

    busca_original = ctypes.util.find_library

    def find_library(nome):
        if nome == "zbar":
            local = busca_original(nome)
            if local:
                return local
            for candidato in CANDIDATOS:
                if Path(candidato).is_file():
                    return candidato
        return busca_original(nome)

    ctypes.util.find_library = find_library

    from pyzbar.pyzbar import decode
    return decode
