#!/bin/sh
# Garante o schema do SQLite no volume persistente antes de subir a API.
set -e

python3 - <<'PY'
import os
import sqlite3

caminho = os.environ.get("DATABASE_PATH", "/data/despensa.db")
os.makedirs(os.path.dirname(caminho) or ".", exist_ok=True)

conexao = sqlite3.connect(caminho)
conexao.execute("""
    CREATE TABLE IF NOT EXISTS produtos (
        codigo TEXT PRIMARY KEY,
        nome TEXT NOT NULL,
        marca TEXT,
        conteudo TEXT,
        foto_url TEXT,
        quantidade INTEGER NOT NULL DEFAULT 1
    )
""")
colunas = [row[1] for row in conexao.execute("PRAGMA table_info(produtos)")]
if "foto_url" not in colunas:
    conexao.execute("ALTER TABLE produtos ADD COLUMN foto_url TEXT")
conexao.commit()
conexao.close()
PY

exec "$@"
