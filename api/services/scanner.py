"""Leitura de código de barras em imagens recebidas pela API."""

from __future__ import annotations

import io
import logging
import threading
import time

from PIL import Image, ImageOps

from api.config import settings
from api.services.database import (
    inserir_se_novo,
    produto_existe,
    registrar_ou_contar_retorna,
)
from api.services.events import eventos
from api.services.produto_lookup import identificar_produto
from comum import ler_codigo_rapido_em_memoria

logger = logging.getLogger(__name__)

TTL_SESSAO_S = 120


class CacheSessao:
    """Evita contar duas vezes a mesma sessão de captura (burst ou retry)."""

    def __init__(self, ttl: float = TTL_SESSAO_S) -> None:
        self._ttl = ttl
        self._dados: dict[str, tuple[float, dict]] = {}
        self._trava = threading.Lock()

    def obter(self, chave: str) -> dict | None:
        with self._trava:
            item = self._dados.get(chave)
            if item is None:
                return None
            registrado, resultado = item
            if time.monotonic() - registrado > self._ttl:
                del self._dados[chave]
                return None
            return resultado

    def guardar(self, chave: str, resultado: dict) -> None:
        with self._trava:
            agora = time.monotonic()
            for antiga, (registrado, _) in list(self._dados.items()):
                if agora - registrado > self._ttl:
                    del self._dados[antiga]
            self._dados[chave] = (agora, resultado)


cache_sessao = CacheSessao()


def processar_upload(conteudo_bytes: bytes, sessao: str | None = None) -> dict:
    """Decodifica o EAN, identifica o produto quando novo e contabiliza.

    Nunca grava a imagem em disco: tudo é processado em memória. O campo
    ``_agendar_groq`` é sinal interno para o endpoint agendar a fonte lenta em
    background; ele é removido antes da resposta HTTP.
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

    if sessao:
        anterior = cache_sessao.obter(sessao)
        if anterior is not None:
            logger.info("sessão %s repetida; reaproveitando resultado", sessao)
            return anterior

    info = None
    agendar_groq = False
    if not produto_existe(codigo):
        info = identificar_produto(codigo)
        if not info and settings.PRODUTO_LOOKUP_GROQ:
            agendar_groq = True

    resultado = registrar_ou_contar_retorna(codigo, info=info)
    resposta = {"lido": True, "codigo": codigo, **resultado}

    if resultado.get("acao") == "contado":
        eventos.notificar(
            codigo=codigo,
            quantidade=resultado["quantidade"],
            acao="incrementado",
        )
    elif resultado.get("acao") == "cadastrado":
        eventos.notificar(
            codigo=codigo,
            quantidade=resultado["quantidade"],
            acao="cadastrado",
        )
    elif resultado.get("acao") == "erro":
        eventos.notificar_produto_nao_cadastrado(codigo)

    if agendar_groq:
        resposta["_agendar_groq"] = True

    if sessao:
        cache_sessao.guardar(sessao, resposta)
    return resposta


_groq_em_andamento: set[str] = set()
_groq_trava = threading.Lock()


def identificar_groq_em_background(codigo: str) -> None:
    """Tenta o Groq para um EAN não identificado e cadastra se encontrar.

    Rodada via ``BackgroundTasks``. Publica eventos SSE no início, no sucesso
    e na falha, para que o dashboard possa mostrar e remover o card de
    "identificando" sem ficar preso. Bursts com o mesmo EAN não disparam
    tarefas duplicadas enquanto uma já está em andamento.
    """
    from api.services.produto_lookup import identificar_com_groq

    with _groq_trava:
        if codigo in _groq_em_andamento:
            logger.info("identificação de %s já em andamento", codigo)
            return
        _groq_em_andamento.add(codigo)

    try:
        if produto_existe(codigo):
            eventos.notificar_produto_identificado(
                codigo, {"origem": "Banco local", "nome": ""}
            )
            return

        info = identificar_com_groq(codigo)
        if not info or not info.get("nome"):
            logger.info("Groq não identificou o EAN %s", codigo)
            eventos.notificar_produto_nao_identificado(codigo)
            return

        salvo = inserir_se_novo(
            codigo,
            info["nome"],
            info.get("marca"),
            info.get("conteudo"),
            info.get("foto_url"),
        )
        if not salvo:
            eventos.notificar_produto_nao_identificado(codigo)
            return

        logger.info("EAN %s identificado em background por %s", codigo, info.get("origem"))
        eventos.notificar(
            codigo=codigo,
            quantidade=salvo["quantidade"],
            acao="cadastrado",
        )
        eventos.notificar_produto_identificado(codigo, info)
    except Exception as exc:
        logger.warning("falha na identificação em background de %s: %s", codigo, exc)
        eventos.notificar_produto_nao_identificado(codigo)
    finally:
        with _groq_trava:
            _groq_em_andamento.discard(codigo)
