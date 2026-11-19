"""Consulta interativa de EAN em fontes públicas."""

from fontes_ean import buscar_produto_ean, validar_codigo


def main():
    print("=" * 40)
    print("       CONSULTA DE PRODUTOS")
    print("=" * 40)

    while True:
        numero = input("\nDigite o código de barras (0 para sair): ").strip()

        if numero == "0":
            print("\nPrograma encerrado.")
            break

        if not validar_codigo(numero):
            print("\nCódigo inválido!")
            print("Digite um código com 8, 12, 13 ou 14 números.")
            continue

        if not buscar_produto_ean(numero):
            print("\nProduto não encontrado.")
            print("Código de barras:", numero)


if __name__ == "__main__":
    main()
