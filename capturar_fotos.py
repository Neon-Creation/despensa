"""Captura fotos de teste pela ESP32-CAM via USB e salva em imagens/.

Uso:
    python capturar_fotos.py [quantidade] [porta]

Exemplo:
    python capturar_fotos.py 3 COM5
"""

import re
import sys
import time
from datetime import datetime
from pathlib import Path

import serial

PORTA_PADRAO = "COM5"
BAUD_RATE = 921600
PASTA_SAIDA = Path(__file__).resolve().parent / "imagens"


def ler_exatamente(ser, tamanho):
    """Lê ``tamanho`` bytes da serial, mesmo que venham em pedaços."""
    dados = bytearray()
    while len(dados) < tamanho:
        parte = ser.read(tamanho - len(dados))
        if not parte:
            raise TimeoutError("timeout lendo os bytes da imagem")
        dados.extend(parte)
    return bytes(dados)


def capturar(ser, indice, total):
    print(f"[{indice}/{total}] solicitando foto...")
    ser.reset_input_buffer()
    ser.write(b"FOTO\n")
    ser.flush()

    tamanho = None
    while tamanho is None:
        linha = ser.readline()
        if b"INICIO_FOTO" in linha:
            texto = linha.decode("utf-8", errors="ignore")
            match = re.search(r"INICIO_FOTO\|[^|]*\|(\d+)", texto)
            if not match:
                raise ValueError(f"cabecalho invalido: {texto!r}")
            tamanho = int(match.group(1))

    dados = ler_exatamente(ser, tamanho)

    # Aguarda o fim da transferencia antes de enviar o proximo comando.
    while b"FIM_FOTO" not in ser.readline():
        pass

    PASTA_SAIDA.mkdir(parents=True, exist_ok=True)
    nome = f"foto_teste_{datetime.now():%Y%m%d_%H%M%S}_{indice:02d}.jpg"
    destino = PASTA_SAIDA / nome
    destino.write_bytes(dados)

    ok = dados[:2] == b"\xff\xd8" and dados[-2:] == b"\xff\xd9"
    print(f"[{indice}/{total}] salvo: {destino} ({len(dados)} bytes, JPEG {'valido' if ok else 'INVALIDO'})")
    return destino


def main():
    total = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    porta = sys.argv[2] if len(sys.argv) > 2 else PORTA_PADRAO

    print(f"Abrindo {porta} a {BAUD_RATE} baud...")
    with serial.Serial(porta, BAUD_RATE, timeout=15) as ser:
        time.sleep(3)  # a abertura da porta reseta a ESP32
        ser.reset_input_buffer()
        for indice in range(1, total + 1):
            capturar(ser, indice, total)
            if indice < total:
                time.sleep(2)
    print("Concluido.")


if __name__ == "__main__":
    main()
