
import os
import re
import requests

from config import carregar_env

carregar_env(sobrescrever=True)

# Configuração da API SI5 Global
SI5_API_TOKEN = os.getenv("SI5_API_TOKEN", "")
SI5_API_URL = os.getenv(
    "SI5_API_URL",
    "https://api.si5.com.br/v1/produtos"
)


def exibir_produto(
    origem,
    nome,
    marca="Não informada",
    detalhe="Não informado",
    imagem="Não disponível"
):
    print("\n--- PRODUTO ENCONTRADO ---")
    print("Origem:", origem)
    print("Nome:", nome)
    print("Marca:", marca)
    print("Detalhe/Qtd:", detalhe)
    print("Imagem:", imagem)


def validar_codigo(numero):
    """
    Valida se o código possui apenas números
    e tamanho compatível com códigos de barras comuns.
    """
    numero = numero.strip()

    if not numero.isdigit():
        return False

    if len(numero) not in (8, 12, 13, 14):
        return False

    return True


def buscar_open_facts(numero):

    bases = [
        ("Open Food Facts",
         "https://world.openfoodfacts.org/api/v3/product/"),

        ("Open Beauty Facts",
         "https://world.openbeautyfacts.org/api/v3/product/"),

        ("Open Products Facts",
         "https://world.openproductsfacts.org/api/v3/product/"),

        ("Open Pet Food Facts",
         "https://world.openpetfoodfacts.org/api/v3/product/")
    ]

    parametros = {
        "fields": "product_name,product_name_pt,brands,quantity,image_url"
    }

    cabecalhos = {
        "User-Agent": "MeuLeitorDeCodigos/1.0"
    }

    for nome_base, url in bases:
        try:
            resposta = requests.get(
                url + numero + ".json",
                params=parametros,
                headers=cabecalhos,
                timeout=8
            )

            resposta.raise_for_status()
            dados = resposta.json()

            if dados.get("status") == 1 and dados.get("product"):

                produto = dados["product"]

                nome = (
                    produto.get("product_name_pt")
                    or produto.get("product_name")
                    or ""
                ).strip()

                if not nome:
                    continue

                exibir_produto(
                    origem=nome_base,
                    nome=nome,
                    marca=produto.get("brands") or "Não informada",
                    detalhe=produto.get("quantity") or "Não informada",
                    imagem=produto.get("image_url") or "Não disponível"
                )

                return True

        except requests.RequestException:
            continue

    return False


def buscar_upcitemdb(numero):

    url = "https://api.upcitemdb.com/prod/trial/lookup"

    try:
        resposta = requests.get(
            url,
            params={"upc": numero},
            headers={"Accept": "application/json"},
            timeout=8
        )

        if resposta.status_code == 200:

            dados = resposta.json()
            itens = dados.get("items", [])

            if itens:
                item = itens[0]

                imagens = item.get("images") or []

                exibir_produto(
                    origem="UPCitemdb",
                    nome=item.get("title") or "Não informado",
                    marca=item.get("brand") or "Não informada",
                    detalhe=(
                        item.get("size")
                        or item.get("weight")
                        or "Não informada"
                    ),
                    imagem=imagens[0] if imagens else "Não disponível"
                )

                return True

    except requests.RequestException:
        pass

    return False


def buscar_si5_global(numero):

    if not SI5_API_TOKEN:
        return False

    url = f"{SI5_API_URL.rstrip('/')}/{numero}"

    cabecalhos = {
        "Authorization": f"Bearer {SI5_API_TOKEN}",
        "X-API-Key": SI5_API_TOKEN,
        "Accept": "application/json"
    }

    try:
        resposta = requests.get(
            url,
            headers=cabecalhos,
            timeout=8
        )

        if resposta.status_code == 200:

            dados = resposta.json()

            produto = (
                dados.get("data")
                or dados.get("produto")
                or dados
            )

            if not isinstance(produto, dict):
                return False

            nome = (
                produto.get("descricao")
                or produto.get("nome")
                or produto.get("product_name")
            )

            if nome:
                exibir_produto(
                    origem="SI5 Global",
                    nome=nome,
                    marca=produto.get("marca") or "Não informada",
                    detalhe=produto.get("peso") or "Não informado",
                    imagem=(
                        produto.get("foto")
                        or produto.get("imagem")
                        or "Não disponível"
                    )
                )

                return True

    except requests.RequestException:
        pass

    return False


def buscar_fallback_nacional(numero):

    url = f"https://cosmos.bluesoft.com.br/produtos/{numero}"

    try:
        resposta = requests.get(
            url,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
            timeout=8
        )

        if resposta.status_code == 200:

            match = re.search(
                r"<title>(.*?)</title>",
                resposta.text,
                re.IGNORECASE | re.DOTALL
            )

            if match:
                titulo = match.group(1).strip()

                if "Cosmos" in titulo and not titulo.startswith("Just a moment"):

                    nome = titulo.split(" - ")[0].strip()

                    exibir_produto(
                        origem="Base Nacional Cosmos",
                        nome=nome,
                        detalhe=f"EAN: {numero}",
                        imagem=url
                    )

                    return True

    except requests.RequestException:
        pass

    return False


def buscar_produto(numero):

    print("\nConsultando bases de dados...")

    if buscar_open_facts(numero):
        return

    if buscar_upcitemdb(numero):
        return

    if buscar_si5_global(numero):
        return

    if buscar_fallback_nacional(numero):
        return

    print("\nProduto não encontrado.")
    print("Código de barras:", numero)


def main():

    print("=" * 40)
    print("       CONSULTA DE PRODUTOS")
    print("=" * 40)

    while True:

        numero = input(
            "\nDigite o código de barras (0 para sair): "
        ).strip()

        if numero == "0":
            print("\nPrograma encerrado.")
            break

        if not validar_codigo(numero):
            print("\nCódigo inválido!")
            print("Digite um código com 8, 12, 13 ou 14 números.")
            continue

        buscar_produto(numero)


if __name__ == "__main__":
    main()
