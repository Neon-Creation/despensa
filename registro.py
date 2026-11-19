"""Cadastro manual de produtos, sem IA."""

import sys

from comum import ler_codigo


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 registro.py caminho_da_imagem")
    else:
        ler_codigo(sys.argv[1])
