import os
import re
import sys
from PIL import Image, ImageEnhance, ImageOps
from pyzbar.pyzbar import decode
import requests

from config import carregar_env

carregar_env(sobrescrever=True)

# Chave gratuita da SI5 Global (opcional: defina no ambiente ou cole aqui após criar conta grátis)
SI5_API_TOKEN = os.getenv("SI5_API_TOKEN", "")
SI5_API_URL = os.getenv("SI5_API_URL", "https://api.si5.com.br/v1/produtos")


def exibir_produto(
    origem,
    nome,
    marca="Não informada",
    detalhe="Não informado",
    imagem="Não disponível",
):
  print(f"\n--- PRODUTO ENCONTRADO ({origem}) ---")
  print("Nome:", nome)
  print("Marca:", marca)
  print("Detalhe/Qtd:", detalhe)
  print("Imagem:", imagem)


def ler_codigo(caminho_imagem):
  try:
    imagem = Image.open(caminho_imagem)
    imagem = ImageOps.exif_transpose(imagem).convert("RGB")
  except Exception as e:
    print(f"\nErro ao abrir a imagem: {e}")
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
          resample=Image.Resampling.BICUBIC,
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
    buscar_produto(numero)


def buscar_produto(numero):
  # 1. Rede Open Facts (100% gratuitas e ilimitadas)
  if buscar_open_facts(numero):
    return

  # 2. UPCitemdb (Até 100 consultas/dia grátis sem precisar de chave)
  if buscar_upcitemdb(numero):
    return

  # 3. SI5 Global (Até 100 consultas/dia grátis com token de cadastro)
  if buscar_si5_global(numero):
    return

  # 4. Fallback Nacional via web (Bluesoft Cosmos)
  if buscar_fallback_nacional(numero):
    return

  print("\nProduto não encontrado em nenhuma das bases consultadas.")
  print("Código identificado:", numero)


def buscar_open_facts(numero):
  bases_open_facts = [
      (
          "Open Food Facts",
          f"https://world.openfoodfacts.org/api/v3/product/{numero}.json",
      ),
      (
          "Open Beauty Facts",
          f"https://world.openbeautyfacts.org/api/v3/product/{numero}.json",
      ),
      (
          "Open Products Facts",
          f"https://world.openproductsfacts.org/api/v3/product/{numero}.json",
      ),
      (
          "Open Pet Food Facts",
          f"https://world.openpetfoodfacts.org/api/v3/product/{numero}.json",
      ),
  ]

  parametros = {
      "product_type": "all",
      "fields": "product_name,product_name_pt,brands,quantity,image_url",
  }
  cabecalhos = {"User-Agent": "MeuLeitorDeCodigos/1.0 (Linux; Python)"}

  for nome_base, url in bases_open_facts:
    try:
      resposta = requests.get(
          url, params=parametros, headers=cabecalhos, timeout=8
      )
      if resposta.status_code == 404:
        continue

      resposta.raise_for_status()
      dados = resposta.json()

      status = str(dados.get("status", ""))
      if status.startswith("success") and dados.get("product"):
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
            imagem=produto.get("image_url") or "Não disponível",
        )
        return True
    except requests.RequestException:
      continue

  return False


def buscar_upcitemdb(numero):
  """Consulta o endpoint gratuito de trial do UPCitemdb (sem necessidade de API Key)."""
  url = "https://api.upcitemdb.com/prod/trial/lookup"
  cabecalhos = {
      "Accept": "application/json",
      "User-Agent": "MeuLeitorDeCodigos/1.0 (Linux; Python)",
  }

  try:
    resposta = requests.get(
        url, params={"upc": numero}, headers=cabecalhos, timeout=8
    )
    if resposta.status_code == 200:
      dados = resposta.json()
      itens = dados.get("items", [])
      if itens:
        item = itens[0]
        nome = item.get("title")
        if nome:
          imagens = item.get("images") or []
          exibir_produto(
              origem="UPCitemdb",
              nome=nome,
              marca=item.get("brand") or "Não informada",
              detalhe=item.get("size")
              or item.get("weight")
              or "Não informada",
              imagem=imagens[0] if imagens else "Não disponível",
          )
          return True
  except requests.RequestException:
    pass

  return False


def buscar_si5_global(numero):
  """Consulta a API nacional da SI5 Global caso o token gratuito esteja configurado."""
  if not SI5_API_TOKEN:
    return False

  url = f"{SI5_API_URL.rstrip('/')}/{numero}"
  cabecalhos = {
      "Authorization": f"Bearer {SI5_API_TOKEN}",
      "X-API-Key": SI5_API_TOKEN,
      "Accept": "application/json",
  }

  try:
    resposta = requests.get(url, headers=cabecalhos, timeout=8)
    if resposta.status_code == 200:
      dados = resposta.json()
      # Suporta retorno direto ou aninhado dentro de "data"/"produto"
      produto = (
          dados.get("data") or dados.get("produto") or dados
          if isinstance(dados, dict)
          else {}
      )
      nome = (
          produto.get("descricao")
          or produto.get("nome")
          or produto.get("product_name")
      )

      if nome:
        ncm = produto.get("ncm")
        detalhe = (
            f"NCM: {ncm}"
            if ncm
            else (produto.get("peso") or "Não informado")
        )
        exibir_produto(
            origem="SI5 Global",
            nome=nome,
            marca=produto.get("marca") or "Não informada",
            detalhe=detalhe,
            imagem=produto.get("foto")
            or produto.get("imagem")
            or "Não disponível",
        )
        return True
  except requests.RequestException:
    pass

  return False


def buscar_fallback_nacional(numero):
  """Tenta obter o nome do produto em diretórios públicos caso não esteja nas APIs."""
  url_cosmos = f"https://cosmos.bluesoft.com.br/produtos/{numero}"
  cabecalhos = {
      "User-Agent": (
          "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101"
          " Firefox/128.0"
      ),
      "Accept-Language": "pt-BR,pt;q=0.9",
  }

  try:
    resposta = requests.get(url_cosmos, headers=cabecalhos, timeout=8)
    if resposta.status_code == 200:
      match = re.search(
          r"<title>(.*?)</title>", resposta.text, re.IGNORECASE | re.DOTALL
      )
      if match:
        titulo = match.group(1).strip()
        if "Cosmos" in titulo and not titulo.startswith("Just a moment"):
          nome_limpo = titulo.split(" - ")[0].strip()
          exibir_produto(
              origem="Base Nacional Cosmos",
              nome=nome_limpo,
              detalhe=f"EAN {numero}",
              imagem=url_cosmos,
          )
          return True
  except requests.RequestException:
    pass

  return False


if __name__ == "__main__":
  if len(sys.argv) < 2:
    print("Uso: python3 api_codigo.py caminho_da_imagem")
  else:
    ler_codigo(sys.argv[1])