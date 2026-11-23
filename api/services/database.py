"""Acesso ao SQLite usado pela API (WAL, health check e contagem de produtos)."""

import sqlite3
from contextlib import contextmanager
from typing import Optional

from api.config import settings


@contextmanager
def get_db():
    """Abre uma conexão SQLite com WAL e garante o schema da tabela produtos.

    O ``PRAGMA journal_mode=WAL`` melhora a concorrência de leitura/escrita e o
    ``busy_timeout`` evita falhas quando outra conexão está escrevendo. O
    schema é criado aqui porque o volume de produção começa vazio.
    """
    conn = sqlite3.connect(settings.DATABASE_PATH, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS produtos (
                codigo TEXT PRIMARY KEY,
                nome TEXT NOT NULL,
                marca TEXT,
                conteudo TEXT,
                foto_url TEXT,
                quantidade INTEGER NOT NULL DEFAULT 1
            )
        """)

        colunas = {
            row[1] for row in conn.execute("PRAGMA table_info(produtos)").fetchall()
        }
        if "foto_url" not in colunas:
            conn.execute("ALTER TABLE produtos ADD COLUMN foto_url TEXT")

        conn.commit()
        yield conn
    finally:
        conn.close()


def health_check_db() -> bool:
    """Retorna True se o banco estiver acessível."""
    try:
        with get_db() as conn:
            conn.execute("SELECT 1")
        return True
    except Exception:
        return False


def registrar_ou_contar_retorna(codigo: str) -> dict:
    """Conta um produto existente; para produto novo, retorna erro sem cadastrar.

    A API não tem terminal interativo para pedir nome/marca, então produtos
    novos devem ser cadastrados via ``PUT /produto/{codigo}`` (ou pelos scripts
    CLI locais).
    """
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT nome, quantidade FROM produtos WHERE codigo = ?",
            (codigo,),
        )
        produto = cursor.fetchone()

        if produto:
            cursor.execute(
                "UPDATE produtos SET quantidade = quantidade + 1 WHERE codigo = ?",
                (codigo,),
            )
            conn.commit()

            cursor.execute(
                "SELECT nome, quantidade FROM produtos WHERE codigo = ?",
                (codigo,),
            )
            nome, quantidade = cursor.fetchone()
            return {"acao": "contado", "produto": nome, "quantidade": quantidade}

        return {
            "acao": "erro",
            "produto": None,
            "quantidade": 0,
            "mensagem": "Produto não cadastrado. Cadastre via PUT /produto/{codigo}.",
        }


def upsert_produto(
    codigo: str,
    nome: str,
    marca: Optional[str] = None,
    conteudo: Optional[str] = None,
    foto_url: Optional[str] = None,
    quantidade: Optional[int] = None,
) -> Optional[dict]:
    """Cadastra um produto novo ou atualiza os dados de um já existente.

    Em produto novo, ``quantidade`` começa em 1 (ou no valor informado). Em
    produto existente, os metadados são atualizados e a quantidade só muda se
    ``quantidade`` for enviado — assim corrigir o cadastro não zera a contagem.
    """
    codigo = codigo.strip()
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM produtos WHERE codigo = ?", (codigo,))
        existe = cursor.fetchone() is not None

        if existe:
            cursor.execute(
                """
                UPDATE produtos
                SET nome = ?, marca = ?, conteudo = ?, foto_url = ?,
                    quantidade = COALESCE(?, quantidade)
                WHERE codigo = ?
                """,
                (nome, marca, conteudo, foto_url, quantidade, codigo),
            )
        else:
            cursor.execute(
                """
                INSERT INTO produtos (codigo, nome, marca, conteudo, foto_url, quantidade)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    codigo,
                    nome,
                    marca,
                    conteudo,
                    foto_url,
                    1 if quantidade is None else quantidade,
                ),
            )
        conn.commit()

        cursor.execute(
            """
            SELECT codigo, nome, marca, conteudo, quantidade
            FROM produtos WHERE codigo = ?
            """,
            (codigo,),
        )
        linha = cursor.fetchone()
        if not linha:
            return None
        return {
            "codigo": linha[0],
            "nome": linha[1],
            "marca": linha[2],
            "conteudo": linha[3],
            "quantidade": linha[4],
        }


def listar_todos_produtos() -> list[dict]:
    """Retorna todos os produtos ordenados por nome."""
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT codigo, nome, marca, conteudo, quantidade
            FROM produtos ORDER BY nome
        """)
        return [
            {"codigo": r[0], "nome": r[1], "marca": r[2], "conteudo": r[3], "quantidade": r[4]}
            for r in cursor.fetchall()
        ]


def buscar_produto_por_codigo(codigo: str) -> Optional[dict]:
    """Retorna produto único ou None se não existir."""
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT codigo, nome, marca, conteudo, quantidade
            FROM produtos WHERE codigo = ?
        """, (codigo,))
        row = cursor.fetchone()
        if row:
            return {"codigo": row[0], "nome": row[1], "marca": row[2], "conteudo": row[3], "quantidade": row[4]}
        return None
