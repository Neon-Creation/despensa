"""Barramento de eventos SSE (Server-Sent Events) para o estoque.

Cada conexão SSE recebe uma fila ``asyncio.Queue`` própria. As mudanças de
estoque publicam um evento em todas as filas ativas; quando o cliente fecha a
conexão, o gerador remove a fila e libera a vaga do cliente.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from sse_starlette.sse import EventSourceResponse

PING_SEGUNDOS = 15
TAMANHO_MAX_FILA = 50


class BarramentoEventos:
    """Publica eventos para assinantes SSE e limita a 1 conexão por cliente."""

    def _publicar_evento_nomeado(self, nome: str, dados: dict) -> None:
        """Publica um evento SSE com nome a partir de código síncrono."""
        if not self._inscritos:
            return
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        evento = {
            "event": nome,
            "data": json.dumps(dados, ensure_ascii=False),
        }

        def agendar() -> None:
            asyncio.create_task(self._publicar_bruto(evento))

        try:
            loop.call_soon_threadsafe(agendar)
        except RuntimeError:
            pass

    def __init__(self) -> None:
        self._inscritos: set[asyncio.Queue[dict]] = set()
        self._conexoes: set[str] = set()
        self._loop: asyncio.AbstractEventLoop | None = None

    @property
    def quantidade_inscritos(self) -> int:
        return len(self._inscritos)

    def pode_conectar(self, cliente: str) -> bool:
        """Retorna False se esse cliente já tiver uma conexão SSE aberta."""
        return cliente not in self._conexoes

    def registrar_conexao(self, cliente: str) -> None:
        self._conexoes.add(cliente)

    def remover_conexao(self, cliente: str) -> None:
        self._conexoes.discard(cliente)

    def inscrever(self) -> asyncio.Queue[dict]:
        """Cria a fila da conexão atual. Deve ser chamado dentro do event loop."""
        self._loop = asyncio.get_running_loop()
        fila: asyncio.Queue[dict] = asyncio.Queue(maxsize=TAMANHO_MAX_FILA)
        self._inscritos.add(fila)
        return fila

    def dessinscrever(self, fila: asyncio.Queue[dict]) -> None:
        self._inscritos.discard(fila)

    async def publicar(self, evento: dict) -> None:
        """Entrega o evento a todos os assinantes conectados."""
        for fila in tuple(self._inscritos):
            try:
                fila.put_nowait(evento)
            except asyncio.QueueFull:
                # Cliente lento: descarta o evento mais antigo para não travar.
                try:
                    fila.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    fila.put_nowait(evento)
                except asyncio.QueueFull:
                    pass

    def notificar(self, *, codigo: str, quantidade: int, acao: str) -> None:
        """Publica a partir de código síncrono (endpoint ou threadpool).

        O agendamento usa ``call_soon_threadsafe`` para funcionar tanto quando
        a chamada vem do event loop quanto de uma worker thread. Sem assinantes,
        não faz nada.
        """
        if not self._inscritos:
            return
        loop = self._loop
        if loop is None or not loop.is_running():
            return

        evento = {"codigo": codigo, "quantidade": quantidade, "acao": acao}

        def agendar() -> None:
            asyncio.create_task(self.publicar(evento))

        try:
            loop.call_soon_threadsafe(agendar)
        except RuntimeError:
            # Loop encerrado (shutdown): não há mais ninguém para notificar.
            pass

    def notificar_produto_nao_cadastrado(self, codigo: str) -> None:
        """Publica evento SSE 'produto-nao-cadastrado' a partir de código síncrono."""
        self._publicar_evento_nomeado("produto-nao-cadastrado", {"codigo": codigo})

    def notificar_produto_identificando(self, codigo: str) -> None:
        """Avisa que a IA começou a identificar o EAN em background."""
        self._publicar_evento_nomeado("produto-identificando", {"codigo": codigo})

    def notificar_produto_nao_identificado(self, codigo: str) -> None:
        """Avisa que a IA terminou sem encontrar o produto."""
        self._publicar_evento_nomeado("produto-nao-identificado", {"codigo": codigo})

    def notificar_produto_identificado(self, codigo: str, info: dict) -> None:
        """Avisa que a identificação em background concluiu o cadastro."""
        self._publicar_evento_nomeado(
            "produto-identificado",
            {"codigo": codigo, "origem": info.get("origem"), "nome": info.get("nome")},
        )

    async def _publicar_bruto(self, evento: dict) -> None:
        """Publica evento já formatado para SSE (com campos 'event' e 'data')."""
        for fila in tuple(self._inscritos):
            try:
                fila.put_nowait(evento)
            except asyncio.QueueFull:
                try:
                    fila.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    fila.put_nowait(evento)
                except asyncio.QueueFull:
                    pass


eventos = BarramentoEventos()


async def gerador_estoque(cliente: str) -> AsyncIterator[dict]:
    """Emite eventos SSE: ``estoque-update`` e ``produto-nao-cadastrado``."""
    fila = eventos.inscrever()
    try:
        while True:
            try:
                evento = await asyncio.wait_for(fila.get(), timeout=PING_SEGUNDOS)
            except asyncio.TimeoutError:
                # O EventSourceResponse já envia o comentário de keep-alive.
                continue
            # Eventos de _publicar_bruto já têm 'event' e 'data'; estoque-update é formatado aqui.
            if "event" in evento and "data" in evento:
                yield evento
            else:
                yield {
                    "event": "estoque-update",
                    "data": json.dumps(evento, ensure_ascii=False),
                }
    finally:
        eventos.dessinscrever(fila)
        eventos.remover_conexao(cliente)


def resposta_estoque(cliente: str) -> EventSourceResponse:
    """Monta a resposta SSE com keep-alive e sem cache."""
    return EventSourceResponse(
        gerador_estoque(cliente),
        ping=PING_SEGUNDOS,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
