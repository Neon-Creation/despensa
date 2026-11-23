"""Simula a ESP32-CAM localmente: captura -> POST /upload -> resposta da API.

O corpo multipart é montado exatamente como no firmware (``esp32_cam.ino``) e
a resposta é interpretada com as mesmas regras de bipes/mensagens seriais.
Por padrão uma API FastAPI é iniciada em segundo plano com uma cópia temporária
de ``despensa.db``, para não alterar o banco do projeto.

Exemplos:
    python3 simular_esp32.py imagens/miojo.jpg
    python3 simular_esp32.py imagens/miojo.jpg --url http://localhost:8000/upload
"""

import argparse
import json
import sqlite3
import sys
import tempfile
import threading
import time
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from zbar_compat import garantir_zbar  # noqa: E402

garantir_zbar()

BOUNDARY = "ESP32CAMBoundary"  # igual ao firmware


def montar_multipart(jpeg: bytes, nome_arquivo: str = "foto.jpg") -> bytes:
    """Reproduz o corpo montado por ``enviarFotoHTTP`` no firmware."""
    cabecalho = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{nome_arquivo}"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode("latin-1")
    rodape = f"\r\n--{BOUNDARY}--\r\n".encode("latin-1")
    return cabecalho + jpeg + rodape


def interpretar_resposta(texto: str) -> dict:
    """Equivalente ao ``processarRespostaHTTP`` do firmware."""
    dados = json.loads(texto)
    lido = bool(dados.get("lido", False))
    acao = dados.get("acao") or ""
    mensagem = dados.get("mensagem") or ""

    if not lido:
        return {"lido": False, "bipes": "2 medios", "serial": "", "mensagem": mensagem}

    if acao == "contado":
        return {
            "lido": True,
            "bipes": "curto + longo",
            "serial": "LIDO, CADASTRADO",
            "mensagem": f"contado: {dados.get('produto')} (qtd {dados.get('quantidade')})",
        }
    if acao == "erro":
        return {"lido": True, "bipes": "3 curtos", "serial": "LIDO", "mensagem": mensagem}
    return {"lido": True, "bipes": "curto", "serial": "LIDO", "mensagem": mensagem}


def descrever_resposta(texto: str) -> dict:
    """Interpreta e imprime o que a ESP32 faria (buzzer e porta serial)."""
    resultado = interpretar_resposta(texto)
    print("\nResposta recebida (JSON):", texto)
    print(f"ESP32: travaLeitura={str(resultado['lido']).lower()}")
    if resultado["serial"]:
        print(f"ESP32: serial -> {resultado['serial']}")
    print(f"ESP32: bip {resultado['bipes']} -> {resultado['mensagem']}")
    return resultado


def quantidade_no_banco(caminho_db: Path, codigo: str):
    with sqlite3.connect(caminho_db) as conn:
        linha = conn.execute(
            "SELECT quantidade FROM produtos WHERE codigo = ?", (codigo,)
        ).fetchone()
    return linha[0] if linha else None


def codigo_da_imagem(jpeg: bytes):
    """Decodifica localmente só para mostrar o antes/depois no banco."""
    import io

    from PIL import Image, ImageOps

    from comum import ler_codigo_rapido_em_memoria

    imagem = ImageOps.exif_transpose(Image.open(io.BytesIO(jpeg))).convert("RGB")
    return next(iter(ler_codigo_rapido_em_memoria(imagem)), None)


def iniciar_api_local(porta: int, caminho_db: Path):
    """Sobe o app FastAPI em thread separada, apontando para um banco temporário."""
    from api.config import settings

    settings.DATABASE_PATH = str(caminho_db)
    from api.main import app

    import uvicorn

    servidor = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=porta,
            log_level="warning",
            access_log=False,
        )
    )
    thread = threading.Thread(target=servidor.run, daemon=True)
    thread.start()
    while not servidor.started and thread.is_alive():
        time.sleep(0.05)
    if not servidor.started:
        raise RuntimeError("Não foi possível iniciar a API local")
    return servidor, thread


def resolver_imagem(nome: str) -> Path:
    caminho = Path(nome).expanduser()
    if caminho.is_file():
        return caminho
    return RAIZ / "imagens" / nome


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("imagem", nargs="?", default="imagens/miojo.jpg")
    parser.add_argument("--url", help="URL de uma API já rodando (padrão: sobe uma local)")
    parser.add_argument("--porta", type=int, default=8000)
    parser.add_argument("--chave", help="X-API-Key (padrão: API_KEY do .env)")
    parser.add_argument(
        "--cadastrar",
        action="store_true",
        help="se o EAN não estiver cadastrado, chama PUT /produto/{codigo} e reenvia a foto",
    )
    parser.add_argument("--nome", default="Produto identificado via API")
    parser.add_argument("--marca", default="")
    parser.add_argument("--conteudo", default="")
    args = parser.parse_args(argv)

    caminho_imagem = resolver_imagem(args.imagem)
    if not caminho_imagem.is_file():
        print(f"Imagem não encontrada: {caminho_imagem}", file=sys.stderr)
        return 2

    # Simula esp_camera_fb_get(): o JPEG sai da câmera já pronto para envio.
    jpeg = caminho_imagem.read_bytes()
    print(f"ESP32: foto capturada ({len(jpeg)} bytes) -> {caminho_imagem.name}")

    with tempfile.TemporaryDirectory(prefix="despensa_esp32_") as tmp:
        caminho_db = Path(tmp) / "despensa.db"
        servidor = thread = None
        if args.url:
            url = args.url
            caminho_db = RAIZ / "despensa.db"  # somente leitura, para referência
        else:
            # backup() inclui o conteúdo do WAL, diferente de um copy() simples.
            with sqlite3.connect(RAIZ / "despensa.db") as origem, sqlite3.connect(
                caminho_db
            ) as destino:
                origem.backup(destino)
            servidor, thread = iniciar_api_local(args.porta, caminho_db)
            url = f"http://127.0.0.1:{args.porta}/upload"
            print(f"API local pronta em {url}")

        from api.config import settings

        chave = args.chave or settings.API_KEY

        codigo = codigo_da_imagem(jpeg)
        antes = (
            quantidade_no_banco(caminho_db, codigo) if codigo and not args.url else None
        )
        print(f"EAN na foto: {codigo} | quantidade antes: {antes}")

        corpo = montar_multipart(jpeg)
        print(f"HTTP: enviando {len(corpo)} bytes...")
        resposta = requests.post(
            url,
            data=corpo,
            headers={
                "X-API-Key": chave,
                "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
            },
            timeout=30,
        )
        print(f"HTTP POST -> {resposta.status_code}")
        if resposta.status_code != 200:
            print(resposta.text)
            return 1

        descrever_resposta(resposta.text)
        if codigo and not args.url:
            print(f"EAN {codigo} | quantidade depois: {quantidade_no_banco(caminho_db, codigo)}")

        dados_primeira = json.loads(resposta.text)
        if args.cadastrar and dados_primeira.get("acao") == "erro" and dados_primeira.get("codigo"):
            ean = dados_primeira["codigo"]
            url_put = url.rsplit("/upload", 1)[0] + "/produto/" + ean
            print(f"\nProduto novo -> PUT {url_put}")
            cadastro = requests.put(
                url_put,
                headers={"X-API-Key": chave, "Content-Type": "application/json"},
                json={"nome": args.nome, "marca": args.marca, "conteudo": args.conteudo},
                timeout=30,
            )
            print(f"HTTP PUT -> {cadastro.status_code} {cadastro.text}")
            if cadastro.status_code == 200:
                print("\nReenviando a mesma foto apos o cadastro...")
                resposta2 = requests.post(
                    url,
                    data=corpo,
                    headers={
                        "X-API-Key": chave,
                        "Content-Type": f"multipart/form-data; boundary={BOUNDARY}",
                    },
                    timeout=30,
                )
                print(f"HTTP POST -> {resposta2.status_code}")
                descrever_resposta(resposta2.text)
                if not args.url:
                    print(f"EAN {ean} | quantidade final: {quantidade_no_banco(caminho_db, ean)}")

        if servidor is not None:
            servidor.should_exit = True
            thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
