"""Leitura de código de barras em imagens recebidas pela API."""

import io

from PIL import Image, ImageOps

from api.services.database import registrar_ou_contar_retorna
from comum import ler_codigo_rapido_em_memoria


def processar_upload(conteudo_bytes: bytes) -> dict:
    """Decodifica o EAN da imagem em memória e contabiliza o produto.

    Nunca grava a imagem em disco: tudo é processado em memória.
    """
    try:
        imagem = Image.open(io.BytesIO(conteudo_bytes))
        imagem = ImageOps.exif_transpose(imagem).convert("RGB")
    except Exception as exc:
        return {"lido": False, "mensagem": f"Imagem inválida: {exc}"}

    codigos = ler_codigo_rapido_em_memoria(imagem)
    if not codigos:
        return {"lido": False, "mensagem": "Nenhum código de barras encontrado"}

    codigo = next(iter(codigos))
    resultado = registrar_ou_contar_retorna(codigo)
    return {"lido": True, "codigo": codigo, **resultado}
