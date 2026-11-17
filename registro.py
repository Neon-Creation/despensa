import sqlite3
import sys
from pathlib import Path
from PIL import Image, ImageEnhance, ImageOps
from pyzbar.pyzbar import decode


BANCO = Path(__file__).parent / "despensa.db"
PASTA_IMAGENS = Path(__file__).parent / "imagens"


def conectar_banco():
    conexao = sqlite3.connect(BANCO)

    conexao.execute("""
        CREATE TABLE IF NOT EXISTS produtos (
            codigo TEXT PRIMARY KEY,
            nome TEXT NOT NULL,
            marca TEXT,
            conteudo TEXT,
            quantidade INTEGER NOT NULL DEFAULT 1
        )
    """)

    conexao.commit()
    return conexao


def pedir_texto(mensagem, obrigatorio=False, aviso="O campo não pode ficar vazio."):
    """Lê texto do usuário e retorna None quando não há entrada disponível."""
    while True:
        try:
            valor = input(mensagem).strip()
        except EOFError:
            print("\nEntrada de dados indisponível (sem terminal interativo).")
            return None

        if not obrigatorio or valor:
            return valor

        print(aviso)


def registrar_ou_contar(codigo):
    conexao = conectar_banco()

    try:
        cursor = conexao.cursor()

        cursor.execute(
            "SELECT nome, marca, conteudo, quantidade FROM produtos WHERE codigo = ?",
            (codigo,)
        )

        produto = cursor.fetchone()

        if produto:
            cursor.execute(
                "UPDATE produtos SET quantidade = quantidade + 1 WHERE codigo = ?",
                (codigo,)
            )

            conexao.commit()

            cursor.execute(
                "SELECT nome, quantidade FROM produtos WHERE codigo = ?",
                (codigo,)
            )

            nome, quantidade = cursor.fetchone()

            print("\nProduto já cadastrado!")
            print("Nome:", nome)
            print("Quantidade na despensa:", quantidade)

        else:
            print("\nNovo produto identificado!")
            print("Código:", codigo)

            nome = pedir_texto(
                "Nome do produto: ",
                obrigatorio=True,
                aviso="O nome não pode ficar vazio.",
            )
            if nome is None:
                print("Produto não cadastrado (nome não informado).")
                return

            marca = pedir_texto("Marca: ") or ""
            conteudo = pedir_texto(
                "Conteúdo da embalagem (peso ou volume): "
            ) or ""

            cursor.execute(
                """
                INSERT INTO produtos (codigo, nome, marca, conteudo, quantidade)
                VALUES (?, ?, ?, ?, 1)
                """,
                (codigo, nome, marca, conteudo)
            )

            conexao.commit()

            print("\nProduto cadastrado com sucesso!")
            print("Quantidade na despensa: 1")

    finally:
        conexao.close()


def resolver_caminho(caminho_imagem):
    """Aceita caminho absoluto, relativo ao projeto ou dentro de imagens/."""
    caminho = Path(caminho_imagem).expanduser()

    if caminho.is_absolute() or caminho.is_file():
        return caminho

    return PASTA_IMAGENS / caminho


def ler_codigo(caminho_imagem):
    caminho = resolver_caminho(caminho_imagem)

    try:
        imagem = Image.open(caminho)
        imagem = ImageOps.exif_transpose(imagem).convert("RGB")
    except Exception as e:
        print("\nErro ao abrir a imagem:", e)
        return

    cinza = ImageOps.grayscale(imagem)
    contraste = ImageEnhance.Contrast(cinza).enhance(2)

    versoes = [imagem, cinza, contraste]
    codigos_encontrados = {}

    print("\nIniciando leitura...")

    angulos = [0] + [a for a in range(-90, 91, 5) if a != 0]

    for angulo in angulos:
        for versao in versoes:
            cor_fundo = (255, 255, 255) if versao.mode == "RGB" else 255

            rotacionada = versao.rotate(
                angulo,
                expand=True,
                fillcolor=cor_fundo,
                resample=Image.Resampling.BICUBIC
            )

            codigos = decode(rotacionada)

            for codigo in codigos:
                numero = codigo.data.decode("utf-8", errors="replace")
                codigos_encontrados[numero] = codigo.type

            if codigos_encontrados:
                break

        if codigos_encontrados:
            break

    if not codigos_encontrados:
        print("\nNenhum código encontrado.")
        print("Verifique o enquadramento e a qualidade da fotografia.")
        return

    for numero, tipo in codigos_encontrados.items():
        print("\nCódigo identificado:", numero)
        print("Tipo:", tipo)

        registrar_ou_contar(numero)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 registro.py caminho_da_imagem")
    else:
        ler_codigo(sys.argv[1])
