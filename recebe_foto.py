"""Recebe fotos da ESP32-CAM pela serial, lê o EAN e registra no banco."""

import os
import time

import serial
from PIL import Image, ImageEnhance, ImageOps
from pyzbar.pyzbar import decode

from comum import registrar_ou_contar

# --- CONFIGURAÇÕES ---
PORTA_SERIAL = '/dev/ttyACM0'
BAUD_RATE = 921600


# --- LEITURA DE IMAGEM OTIMIZADA PARA TEMPO REAL ---
def ler_codigo_rapido(caminho_imagem):
    try:
        imagem = Image.open(caminho_imagem)
        imagem = ImageOps.exif_transpose(imagem).convert("RGB")
    except Exception:
        return {}

    cinza = ImageOps.grayscale(imagem)
    contraste = ImageEnhance.Contrast(cinza).enhance(2)
    versoes = [cinza, contraste, imagem]

    codigos_encontrados = {}

    # Ângulos retos apenas, para garantir velocidade em tempo real
    angulos_rapidos = [0, 90, -90, 180]

    for angulo in angulos_rapidos:
        for versao in versoes:
            cor_fundo = (255, 255, 255) if versao.mode == "RGB" else 255
            if angulo != 0:
                rotacionada = versao.rotate(
                    angulo,
                    expand=True,
                    fillcolor=cor_fundo
                )
            else:
                rotacionada = versao

            codigos = decode(rotacionada)
            for codigo in codigos:
                numero = codigo.data.decode("utf-8", errors="replace")
                codigos_encontrados[numero] = codigo.type

            # Retorna imediatamente ao achar o primeiro código válido
            if codigos_encontrados:
                return codigos_encontrados

    return codigos_encontrados


# --- LOOP PRINCIPAL DO SCANNER ---
def iniciar_scanner():
    try:
        ser = serial.Serial(PORTA_SERIAL, BAUD_RATE, timeout=5)
        print(f"\n📡 Conectado à ESP32 ({PORTA_SERIAL}).")
        print("🟢 SEGURE O BOTÃO para iniciar o escaneamento contínuo...\n")

        while True:
            linha = ser.readline()

            if b'INICIO_FOTO' in linha:
                try:
                    texto = linha.decode('utf-8', errors='ignore').strip()
                    partes = texto.split('|')

                    if len(partes) >= 3:
                        caminho_arquivo = partes[1]
                        tamanho_foto = int(partes[2])

                        # Lê a imagem da porta serial e salva
                        dados_imagem = ser.read(tamanho_foto)
                        os.makedirs(os.path.dirname(caminho_arquivo), exist_ok=True)
                        with open(caminho_arquivo, 'wb') as f:
                            f.write(dados_imagem)

                        print(".", end="", flush=True)

                        # Tenta ler o código na foto recém-chegada
                        codigos = ler_codigo_rapido(caminho_arquivo)

                        if codigos:
                            # 1. Trava a câmera e dá o bipe curto de "lido"
                            ser.write(b'LIDO\n')
                            ser.flush()

                            print("\n\n🎯 CÓDIGO LIDO COM SUCESSO! Câmera pausada.")

                            # 2. Faz o cadastro/contagem no banco
                            for numero in codigos:
                                registrar_ou_contar(numero)

                            # 3. Bipe longo do buzzer para confirmar o cadastro
                            ser.write(b'CADASTRADO\n')
                            ser.flush()

                            # 4. Limpeza do buffer serial
                            time.sleep(0.5)
                            ser.reset_input_buffer()

                            print("\n🟢 SOLTE E APERTE O BOTÃO NOVAMENTE para ler o próximo produto...")

                except Exception as e:
                    print(f"\nErro no frame atual: {e}")

    except serial.SerialException as e:
        print(f"\n❌ Erro de conexão: {e}")
        print("Verifique se o Monitor Serial da Arduino IDE está fechado.")
    except KeyboardInterrupt:
        print("\n\nEncerrando scanner inteligente...")
        if 'ser' in locals() and ser.is_open:
            ser.close()


if __name__ == "__main__":
    iniciar_scanner()
