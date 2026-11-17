import serial
import time
import os
import sqlite3
import sys
from pathlib import Path
from PIL import Image, ImageEnhance, ImageOps
from pyzbar.pyzbar import decode

# --- CONFIGURAÇÕES ---
PORTA_SERIAL = '/dev/ttyACM0' 
BAUD_RATE = 921600
BANCO = Path(__file__).parent / "despensa.db"

# --- FUNÇÕES DE BANCO DE DADOS ---
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

def registrar_ou_contar(codigo):
    conexao = conectar_banco()
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

        print(f"\n📦 Produto já cadastrado: {nome}")
        print(f"🔄 Nova quantidade na despensa: {quantidade}")
    else:
        print(f"\n✨ Novo produto identificado! Código: {codigo}")
        nome = input("Nome do produto: ").strip()
        while not nome:
            print("O nome não pode ficar vazio.")
            nome = input("Nome do produto: ").strip()

        marca = input("Marca: ").strip()
        conteudo = input("Conteúdo da embalagem (peso ou volume): ").strip()

        cursor.execute(
            """
            INSERT INTO produtos (codigo, nome, marca, conteudo, quantidade) 
            VALUES (?, ?, ?, ?, 1)
            """,
            (codigo, nome, marca, conteudo)
        )
        conexao.commit()
        print("\n✅ Produto cadastrado com sucesso! Quantidade: 1")

    conexao.close()

# --- FUNÇÃO DE LEITURA DE IMAGEM OTIMIZADA ---
def ler_codigo_rapido(caminho_imagem):
    try:
        imagem = Image.open(caminho_imagem)
        imagem = ImageOps.exif_transpose(imagem).convert("RGB")
    except Exception as e:
        return {}

    cinza = ImageOps.grayscale(imagem)
    contraste = ImageEnhance.Contrast(cinza).enhance(2)
    versoes = [cinza, contraste, imagem]
    
    codigos_encontrados = {}
    
    # Reduzido para ângulos retos para garantir velocidade em tempo real
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
                            
                        print(".", end="", flush=True) # Feedback visual de que está lendo fotos
                        
                        # Tenta ler o código na foto recém-chegada
                        codigos = ler_codigo_rapido(caminho_arquivo)
                        
                        if codigos:
                            # 1. Trava a câmera e dá o bipe curto de "lido"
                            ser.write(b'LIDO\n')
                            ser.flush()
                            
                            print("\n\n🎯 CÓDIGO LIDO COM SUCESSO! Câmera pausada.")
                            
                            # 2. Faz o input do usuário e registra no banco
                            for numero, tipo in codigos.items():
                                registrar_ou_contar(numero)
                            
                            # 3. ENVIA O COMANDO DE SUCESSO PARA O BIPE LONGO DO BUZZER
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