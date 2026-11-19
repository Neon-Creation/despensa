"""Lê o EAN de uma imagem e consulta bases públicas."""

import sys

from comum import ler_codigo
from fontes_ean import buscar_produto_ean


def buscar_produto(numero):
    if not buscar_produto_ean(numero, titulo=False):
        print("\nProduto não encontrado em nenhuma das bases consultadas.")
        print("Código identificado:", numero)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 consulta_ean_imagem.py caminho_da_imagem")
    else:
        ler_codigo(sys.argv[1], ao_encontrar=buscar_produto)
