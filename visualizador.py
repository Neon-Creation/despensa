"""Visualizador local da ESP32-CAM pela USB: stream ao vivo + rajada de fotos.

A câmera é comandada pela serial (COM5), sem depender do Wi-Fi da placa.
Um clique no botão verde — ou no botão físico do pino 42 — inicia uma sessão:
vários frames são capturados, salvos em ``imagens/fotos/<sessao>/`` e
decodificados localmente. A melhor foto é enviada uma única vez à API.

Uso:
    python visualizador.py [porta-serial] [porta-http]

Exemplo:
    python visualizador.py COM5 8090
"""

import io
import json
import queue
import re
import sys
import threading
import time
import urllib.request
from datetime import datetime
from urllib.parse import unquote
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import serial
from PIL import Image, ImageOps

from comum import ler_codigo_rapido_em_memoria

RAIZ = Path(__file__).resolve().parent
PORTA_SERIAL = sys.argv[1] if len(sys.argv) > 1 else "COM5"
PORTA_HTTP = int(sys.argv[2]) if len(sys.argv) > 2 else 8090
PASTA_FOTOS = RAIZ / "imagens" / "fotos"
PASTA_FOTOS.mkdir(parents=True, exist_ok=True)

QUADROS_POR_SESSAO = 5
INTERVALO_ENTRE_QUADROS_S = 0.2
CONFIRMACOES_PARA_PARAR = 2
DEBOUNCE_BOTAO_S = 1.5


def ler_config():
    """Lê a chave da ESP32 do .env sem expor valores em logs."""
    cfg = {}
    arquivo = RAIZ / ".env"
    if arquivo.is_file():
        for linha in arquivo.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                chave, valor = linha.split("=", 1)
                cfg[chave.strip()] = valor.strip().strip('"').strip("'")
    return cfg


CONFIG = ler_config()
API_URL = "http://localhost:8000/upload"
API_KEY = CONFIG.get("API_KEY_ESP32") or CONFIG.get("API_KEY") or ""

ULTIMA_SESSAO = {"resultado": None}


class LeitorSerial:
    """Único leitor da porta serial.

    Fica sempre de ouvido, mesmo sem navegador aberto, para receber o evento
    ``BOTAO`` da placa. Os comandos ``FOTO`` são serializados por ``trava`` —
    quem precisar de vários frames adquire a trava uma vez e pede todos.
    """

    def __init__(self, porta: str) -> None:
        self.ser = serial.Serial(porta, 921600, timeout=1)
        self.trava = threading.RLock()
        self.fila_quadros: queue.Queue = queue.Queue(maxsize=2)
        self.fila_eventos: queue.Queue = queue.Queue()
        self.geracao = 0
        self.ultimo_quadro = None
        self.vivo = True
        time.sleep(2)  # a abertura da porta reseta a ESP32
        self.ser.reset_input_buffer()
        threading.Thread(target=self._ler, daemon=True, name="leitor-serial").start()
        print(f"[visualizador] serial {porta} pronta")

    def escrever(self, dados: bytes) -> None:
        """Envia um comando/bipe para a placa."""
        try:
            with self.trava:
                self.ser.write(dados)
                self.ser.flush()
        except Exception as exc:
            print("[visualizador] falha ao escrever na serial:", exc)

    def _ler_exatamente(self, tamanho: int) -> bytes:
        dados = bytearray()
        while len(dados) < tamanho:
            parte = self.ser.read(tamanho - len(dados))
            if not parte:
                raise TimeoutError("timeout recebendo a imagem")
            dados.extend(parte)
        return bytes(dados)

    def _ler(self) -> None:
        while self.vivo:
            try:
                linha = self.ser.readline()
            except Exception as exc:
                print("[visualizador] erro na serial:", exc)
                time.sleep(0.5)
                continue
            if not linha:
                continue

            if b"BOTAO" in linha:
                self.fila_eventos.put("BOTAO")
                continue
            if b"INICIO_FOTO" not in linha:
                continue

            encontrado = re.search(rb"INICIO_FOTO\|[^|]*\|(\d+)", linha)
            if not encontrado:
                continue
            tamanho = int(encontrado.group(1))
            try:
                jpg = self._ler_exatamente(tamanho)
            except Exception as exc:
                print("[visualizador] frame falhou:", exc)
                continue
            # Um evento BOTAO no meio da transferência corrompe o JPEG;
            # descarta esse frame em vez de tentar decodificá-lo.
            if len(jpg) < 4 or jpg[:2] != b"\xff\xd8" or jpg[-2:] != b"\xff\xd9":
                print("[visualizador] frame corrompido descartado")
                continue

            limite = time.time() + 3
            while time.time() < limite:
                if b"FIM_FOTO" in self.ser.readline():
                    break

            self.geracao += 1
            self.ultimo_quadro = jpg
            while self.fila_quadros.full():
                try:
                    self.fila_quadros.get_nowait()
                except queue.Empty:
                    break
            self.fila_quadros.put((self.geracao, jpg))

    def capturar(self, timeout: float = 15) -> bytes:
        """Pede um frame e espera o novo (chamador deve segurar ``trava``)."""
        while not self.fila_quadros.empty():
            try:
                self.fila_quadros.get_nowait()
            except queue.Empty:
                break

        alvo = self.geracao + 1
        self.ser.write(b"FOTO\n")
        self.ser.flush()

        limite = time.time() + timeout
        while time.time() < limite:
            try:
                geracao, jpg = self.fila_quadros.get(timeout=0.2)
            except queue.Empty:
                continue
            if geracao >= alvo:
                return jpg
        raise TimeoutError("a placa não respondeu ao comando FOTO")


leitor = LeitorSerial(PORTA_SERIAL)


def enviar_para_api(jpg: bytes, sessao: str | None = None) -> dict:
    """Envia a foto para a FastAPI e devolve o JSON de resposta."""
    if not API_KEY:
        return {"erro": "API_KEY_ESP32 não configurada no .env"}
    limite = "----DespensaViewerBoundary"
    cabecalho = (
        f"--{limite}\r\n"
        'Content-Disposition: form-data; name="file"; filename="foto.jpg"\r\n'
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode()
    corpo = cabecalho + jpg
    if sessao:
        corpo += (
            f"\r\n--{limite}\r\n"
            'Content-Disposition: form-data; name="sessao"\r\n\r\n'
            f"{sessao}\r\n"
        ).encode()
    corpo += f"\r\n--{limite}--\r\n".encode()

    pedido = urllib.request.Request(
        API_URL,
        data=corpo,
        headers={
            "X-API-Key": API_KEY,
            "Content-Type": f"multipart/form-data; boundary={limite}",
        },
        method="POST",
    )
    with urllib.request.urlopen(pedido, timeout=25) as resposta:
        return json.loads(resposta.read().decode("utf-8"))


def decodificar(jpg: bytes) -> dict:
    """Tenta ler o EAN localmente, antes de gastar uma chamada na API."""
    try:
        imagem = Image.open(io.BytesIO(jpg))
        imagem = ImageOps.exif_transpose(imagem).convert("RGB")
        return ler_codigo_rapido_em_memoria(imagem)
    except Exception as exc:
        print("[visualizador] falha ao decodificar:", exc)
        return {}


def salvar_foto(jpg: bytes, sessao_id: str, indice: int) -> Path:
    pasta = PASTA_FOTOS / sessao_id
    pasta.mkdir(parents=True, exist_ok=True)
    destino = pasta / f"foto_{datetime.now():%H%M%S}_{indice:02d}.jpg"
    destino.write_bytes(jpg)
    return destino


def executar_sessao() -> dict:
    """Captura a rajada, decodifica localmente e envia uma única foto à API."""
    sessao_id = datetime.now().strftime("sessao_%Y%m%d_%H%M%S_%f")
    fotos: list[str] = []
    codigo = None
    melhor = None
    confirmacoes = 0

    with leitor.trava:
        for indice in range(QUADROS_POR_SESSAO):
            try:
                jpg = leitor.capturar()
            except Exception as exc:
                print("[visualizador] captura falhou:", exc)
                continue
            destino = salvar_foto(jpg, sessao_id, indice + 1)
            fotos.append(f"{sessao_id}/{destino.name}")
            print(f"[visualizador] salvo: {destino.name}")

            encontrados = decodificar(jpg)
            if encontrados:
                atual = next(iter(encontrados))
                confirmacoes += 1
                if melhor is None:
                    melhor, codigo = jpg, atual
                if confirmacoes >= CONFIRMACOES_PARA_PARAR:
                    codigo = atual
                    melhor = jpg
                    break
            if indice < QUADROS_POR_SESSAO - 1:
                time.sleep(INTERVALO_ENTRE_QUADROS_S)

    if melhor is None or not codigo:
        leitor.escrever(b"ERRO\n")
        resultado = {
            "ok": False,
            "erro": "Nenhum código encontrado nas fotos",
            "sessao": sessao_id,
            "fotos": fotos,
        }
    else:
        leitor.escrever(b"LIDO\n")
        try:
            api = enviar_para_api(melhor, sessao_id)
            print("[visualizador] API:", api)
        except Exception as exc:
            api = {"erro": str(exc)}
            print("[visualizador] API falhou:", exc)
        if api.get("acao") in ("contado", "cadastrado"):
            leitor.escrever(b"CADASTRADO\n")
        resultado = {
            "ok": True,
            "sessao": sessao_id,
            "fotos": fotos,
            "codigo": codigo,
            "api": api,
        }

    ULTIMA_SESSAO["resultado"] = resultado
    return resultado


def worker_botao() -> None:
    """Consome eventos BOTAO da placa sem bloquear o leitor serial."""
    ultimo_clique = 0.0
    while True:
        leitor.fila_eventos.get()
        agora = time.monotonic()
        if agora - ultimo_clique < DEBOUNCE_BOTAO_S:
            continue
        ultimo_clique = agora
        print("[visualizador] botão físico: iniciando rajada")
        try:
            executar_sessao()
        except Exception as exc:
            print("[visualizador] falha na sessão do botão:", exc)


threading.Thread(target=worker_botao, daemon=True, name="botao").start()


HTML = """<!doctype html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ESP32-CAM — ao vivo</title>
<style>
  :root { color-scheme: dark; }
  body { margin:0; padding:24px; background:#0e1117; color:#e6edf3;
         font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; }
  h1 { margin:0 0 4px; font-size:22px; }
  .sub { color:#8b949e; font-size:13px; margin-bottom:18px; }
  .painel { display:flex; gap:24px; flex-wrap:wrap; align-items:flex-start; }
  .video-wrap { flex:1 1 600px; min-width:320px; }
  #video { width:100%; background:#000; border-radius:12px; border:1px solid #30363d;
           aspect-ratio:4/3; object-fit:contain; }
  .mira { position:relative; display:inline-block; width:100%; }
  .mira .alvo { position:absolute; top:18%; left:14%; width:72%; height:64%;
                border:2px dashed #00ff66; box-sizing:border-box; pointer-events:none;
                border-radius:6px; box-shadow:0 0 0 9999px rgba(0,0,0,.35); }
  .controles { margin-top:16px; display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
  #bater { background:#2ea043; color:#fff; border:0; border-radius:10px;
            padding:14px 28px; font-size:17px; font-weight:700; cursor:pointer; }
  #bater:hover { background:#3fb950; }
  #bater:disabled { opacity:.6; cursor:wait; }
  #status { color:#8b949e; font-size:14px; }
  .galeria { flex:0 1 300px; }
  .galeria h2 { font-size:16px; margin:0 0 10px; }
  #fotos { display:grid; grid-template-columns:repeat(2,1fr); gap:8px; }
  #fotos img { width:100%; aspect-ratio:4/3; object-fit:cover; border-radius:6px;
               border:1px solid #30363d; background:#000; }
  .vazio { color:#8b949e; font-size:13px; }
</style>
</head>
<body>
  <h1>ESP32-CAM — visão ao vivo</h1>
  <div class="sub">Vídeo pela USB. Clique no botão verde ou no botão do pino 42: 5 fotos são salvas em
    <code>despensa/imagens/fotos/</code> e a melhor é enviada à API.</div>
  <div class="painel">
    <div class="video-wrap">
      <div class="mira"><img id="video" src="/stream" alt="Câmera ao vivo"><div class="alvo"></div></div>
      <div class="controles">
        <button id="bater">● Bater foto (rajada)</button>
        <span id="status">Conectando...</span>
      </div>
    </div>
    <div class="galeria">
      <h2>Últimas fotos</h2>
      <div id="fotos"><span class="vazio">Nenhuma foto salva ainda.</span></div>
    </div>
  </div>
<script>
const btn = document.getElementById('bater');
const status = document.getElementById('status');
const video = document.getElementById('video');
const fotos = document.getElementById('fotos');

video.addEventListener('load', () => { status.textContent = 'Ao vivo.'; });
video.addEventListener('error', () => { status.textContent = 'Sem sinal da câmera.'; });

function descrever(dados) {
  if (!dados) return 'Ao vivo.';
  if (!dados.ok) return `Erro: ${dados.erro}`;
  const api = dados.api || {};
  let texto = `${dados.fotos.length} fotos (${dados.sessao.slice(-12)})`;
  if (api.lido) {
    if (api.acao === 'contado') texto += ` | EAN ${api.codigo} contado (${api.quantidade})`;
    else if (api.acao === 'cadastrado') texto += ` | EAN ${api.codigo} cadastrado (${api.origem || 'auto'})`;
    else texto += ` | EAN ${api.codigo}: não cadastrado`;
  } else if (api.erro) texto += ` | API: ${api.erro}`;
  else texto += ` | ${api.mensagem || 'sem EAN na foto'}`;
  return texto;
}

async function carregar() {
  const r = await fetch('/files');
  const dados = await r.json();
  if (!dados.fotos.length) {
    fotos.innerHTML = '<span class="vazio">Nenhuma foto salva ainda.</span>';
    return;
  }
  fotos.innerHTML = dados.fotos.map(f =>
    `<img src="/foto/${encodeURIComponent(f.nome)}" title="${f.nome}">`
  ).join('');
}

btn.addEventListener('click', async () => {
  btn.disabled = true;
  status.textContent = 'Capturando rajada...';
  try {
    const r = await fetch('/snap');
    status.textContent = descrever(await r.json());
    await carregar();
  } catch (e) {
    status.textContent = 'Erro ao conectar: ' + e;
  } finally {
    btn.disabled = false;
  }
});

let ultimoResultado = null;
async function verificarBotao() {
  try {
    const r = await fetch('/status');
    const dados = await r.json();
    const atual = dados.ultima;
    if (atual && JSON.stringify(atual) !== ultimoResultado) {
      ultimoResultado = JSON.stringify(atual);
      status.textContent = descrever(atual);
      await carregar();
    }
  } catch (e) { /* stream ocupado */ }
}

carregar();
setInterval(carregar, 10000);
setInterval(verificarBotao, 1500);
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, formato, *args):
        print("[visualizador] " + (formato % args))

    def _json(self, dados, codigo=200):
        corpo = json.dumps(dados, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        # decodeURIComponent do navegador: "%2F" precisa virar "/" para
        # encontrar fotos dentro das subpastas de sessão.
        caminho = unquote(self.path.split("?", 1)[0])
        if caminho == "/":
            corpo = HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(corpo)))
            self.end_headers()
            self.wfile.write(corpo)
        elif caminho == "/stream":
            self._stream()
        elif caminho == "/snap":
            self._json(executar_sessao())
        elif caminho == "/files":
            arquivos = [
                {
                    "nome": p.relative_to(PASTA_FOTOS).as_posix(),
                    "bytes": p.stat().st_size,
                }
                for p in sorted(
                    PASTA_FOTOS.rglob("*.jpg"),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[:60]
            ]
            self._json({"fotos": arquivos})
        elif caminho.startswith("/foto/"):
            self._foto(caminho[len("/foto/"):])
        elif caminho == "/status":
            self._json(
                {
                    "serial": PORTA_SERIAL,
                    "pasta": str(PASTA_FOTOS),
                    "ultima": ULTIMA_SESSAO["resultado"],
                }
            )
        else:
            self.send_error(404)

    def _stream(self):
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        while True:
            try:
                with leitor.trava:
                    jpg = leitor.capturar()
                cabecalho = (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n"
                    b"Content-Length: " + str(len(jpg)).encode() + b"\r\n\r\n"
                )
                self.wfile.write(cabecalho + jpg + b"\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as exc:
                print("[visualizador] frame falhou:", exc)
                time.sleep(1)

    def _foto(self, nome):
        try:
            destino = (PASTA_FOTOS / nome).resolve()
            if PASTA_FOTOS.resolve() not in destino.parents:
                self.send_error(404)
                return
            dados = destino.read_bytes()
        except Exception:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(dados)))
        self.end_headers()
        self.wfile.write(dados)


if __name__ == "__main__":
    print(f"Visualizador: http://localhost:{PORTA_HTTP}/")
    print(f"Serial: {PORTA_SERIAL} | fotos: {PASTA_FOTOS}")
    ThreadingHTTPServer(("0.0.0.0", PORTA_HTTP), Handler).serve_forever()
