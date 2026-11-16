import io
import logging
import threading
import time
import unicodedata
from flask import Flask, Response, jsonify, request
from PIL import Image, ImageFilter, ImageOps
from pyzbar.pyzbar import ZBarSymbol, decode
import requests
from werkzeug.serving import WSGIRequestHandler

WSGIRequestHandler.protocol_version = "HTTP/1.1"
app = Flask(__name__)
logging.getLogger("werkzeug").setLevel(logging.ERROR)

PRODUTOS_CADASTRADOS = {
    "7896806700069": "talco . Moca",
    "7891910000197": "Acucar Uniao 1kg",
    "7894900011517": "Coca-Cola 2L",
    "7891000053508": "Achocolatado Nescau",
}

TIPOS_ACEITOS = [ZBarSymbol.EAN13, ZBarSymbol.EAN8, ZBarSymbol.UPCA, ZBarSymbol.UPCE]
NOME_MAX = 19
CACHE_ONLINE = {}

ultimo_jpeg = None
contador_quadros = 0
novo_quadro = threading.Condition()

stat_lock = threading.Lock()
stat_inicio = time.time()
stat_quadros = 0
stat_decode_ms = 0.0


def limpar_nome(texto):
    s = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode("ascii")
    s = s.replace('"', "").replace("\\", "")
    return " ".join(s.split())[:NOME_MAX]


def buscar_api(numero_ean):
    try:
        url = f"https://br.openfoodfacts.org/api/v2/product/{numero_ean}.json"
        resp = requests.get(url, timeout=2.5)
        dados = resp.json() if resp.status_code == 200 else {}
        prod = dados.get("product", {}) if dados.get("status") == 1 else {}
        nome_bruto = prod.get("product_name_pt") or prod.get("product_name") or ""
        return limpar_nome(nome_bruto)
    except Exception as e:
        print(f"Erro na consulta online: {e}")
        return ""


def decodificar_produto_por_numero(numero_ean):
    if numero_ean in PRODUTOS_CADASTRADOS:
        return limpar_nome(PRODUTOS_CADASTRADOS[numero_ean])
    if numero_ean in CACHE_ONLINE:
        return CACHE_ONLINE[numero_ean]

    nome = buscar_api(numero_ean)
    if nome:
        CACHE_ONLINE[numero_ean] = nome
        return nome
    return f"Item {numero_ean[-6:]}"


def registrar_estatistica(decode_ms):
    global stat_inicio, stat_quadros, stat_decode_ms
    with stat_lock:
        stat_quadros += 1
        stat_decode_ms += decode_ms
        agora = time.time()
        decorrido = agora - stat_inicio
        if decorrido < 2.0:
            return
        fps = stat_quadros / decorrido
        medio = stat_decode_ms / stat_quadros
        print(f"\n[TURBO] {fps:.1f} FPS recebidos | Processamento Python: {medio:.1f} ms/frame")
        stat_inicio = agora
        stat_quadros = 0
        stat_decode_ms = 0.0


def tentar_ler_codigo_inteligente(img_cinza):
    img_nitida = ImageOps.autocontrast(img_cinza, cutoff=2)
    img_nitida = img_nitida.filter(ImageFilter.UnsharpMask(radius=2, percent=250, threshold=2))
    res = decode(img_nitida, symbols=TIPOS_ACEITOS)
    if res:
        return res

    w, h = img_nitida.size
    caixa = (int(w * 0.15), int(h * 0.20), int(w * 0.85), int(h * 0.80))
    recorte = img_nitida.crop(caixa)
    zoom = recorte.resize((recorte.width * 2, recorte.height * 2), Image.Resampling.BILINEAR)
    zoom = zoom.filter(ImageFilter.SHARPEN)
    res = decode(zoom, symbols=TIPOS_ACEITOS)
    if res:
        return res

    bw = recorte.point(lambda p: 255 if p > 115 else 0)
    return decode(bw, symbols=TIPOS_ACEITOS)


def gerar_stream():
    ultimo_visto = 0
    while True:
        with novo_quadro:
            novo_quadro.wait_for(lambda: contador_quadros != ultimo_visto, timeout=15)
            jpeg = ultimo_jpeg
            ultimo_visto = contador_quadros
        if not jpeg:
            continue
        yield (
            b"--quadro\r\nContent-Type: image/jpeg\r\nContent-Length: "
            + str(len(jpeg)).encode()
            + b"\r\n\r\n"
            + jpeg
            + b"\r\n"
        )


@app.route("/", methods=["GET"])
def index():
    html = (
        "<body style='margin:0;background:#111;color:#eee;font-family:sans-serif;text-align:center'>"
        "<h3>Servidor TURBO online - Camera ao Vivo</h3>"
        "<p>Posicione o codigo na mira verde (a ~15-25 cm da camera).</p>"
        "<div style='position:relative;display:inline-block'>"
        "<img src='/stream' style='width:640px;max-width:95vw;border:2px solid #444;border-radius:8px'>"
        "<div style='position:absolute;top:20%;left:15%;width:70%;height:60%;"
        "border:2px dashed #00ff66;box-sizing:border-box;pointer-events:none;"
        "box-shadow: 0 0 0 9999px rgba(0,0,0,0.35);border-radius:6px'></div>"
        "</div></body>"
    )
    return Response(html, mimetype="text/html")


@app.route("/stream", methods=["GET"])
def stream():
    resp = Response(gerar_stream(), mimetype="multipart/x-mixed-replace; boundary=quadro")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/frame", methods=["GET"])
def frame():
    if ultimo_jpeg is None:
        return "", 404
    resp = Response(ultimo_jpeg, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/scan", methods=["POST"])
def scan_frame():
    global ultimo_jpeg, contador_quadros

    dados = request.get_data(cache=False)
    if not dados:
        return jsonify({"lido": False}), 400

    with novo_quadro:
        ultimo_jpeg = dados
        contador_quadros += 1
        novo_quadro.notify_all()

    try:
        t0 = time.perf_counter()
        imagem = Image.open(io.BytesIO(dados)).convert("L")
        resultados = tentar_ler_codigo_inteligente(imagem)
        registrar_estatistica((time.perf_counter() - t0) * 1000)

        if not resultados:
            return jsonify({"lido": False})

        numeracao = resultados[0].data.decode("utf-8")
        nome_produto = decodificar_produto_por_numero(numeracao)
        print(f"\n[BIP!] {numeracao} -> {nome_produto}")

        return jsonify({"lido": True, "numeracao": numeracao, "produto": nome_produto})

    except Exception as e:
        print(f"\n[ERRO] {type(e).__name__}: {e}")
        return jsonify({"lido": False}), 500


if __name__ == "__main__":
    print("Scanner TURBO ativo na porta 5000 (HTTP/1.1 Keep-Alive)! Aguardando ESP32...")
    app.run(host="0.0.0.0", port=5000, threaded=True)
