"""
Camada de persistência unificada.
- Produção (Railway): usa PostgreSQL via DATABASE_URL
- Local (dev): usa SQLite em dados/techdrop.db

Modelo: tabela chave-valor com JSON, uma "coleção" por tipo
(produtos, pedidos, disputas, usuarios). Flexível e à prova de deploy.
"""
import os
import json
import threading
from datetime import datetime
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "")
_IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
_lock = threading.Lock()

# ── CONEXÃO ────────────────────────────────────────────────────────────────────

if _IS_POSTGRES:
    import psycopg2
    import psycopg2.extras

    # Railway às vezes entrega postgres:// — psycopg2 quer postgresql://
    _DSN = DATABASE_URL.replace("postgres://", "postgresql://", 1)

    def _conn():
        return psycopg2.connect(_DSN, sslmode="require")

    _PH = "%s"           # placeholder
else:
    import sqlite3
    _DB_FILE = Path(__file__).parent.parent / "dados" / "techdrop.db"
    _DB_FILE.parent.mkdir(exist_ok=True)

    def _conn():
        c = sqlite3.connect(str(_DB_FILE))
        c.row_factory = sqlite3.Row
        return c

    _PH = "?"


def init_db():
    """Cria a tabela kv se não existir."""
    with _lock, _conn() as c:
        cur = c.cursor()
        if _IS_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    colecao    TEXT NOT NULL,
                    chave      TEXT NOT NULL,
                    valor      JSONB NOT NULL,
                    criado_em  TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (colecao, chave)
                )
            """)
            cur.execute("CREATE INDEX IF NOT EXISTS idx_kv_colecao ON kv(colecao)")
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    colecao    TEXT NOT NULL,
                    chave      TEXT NOT NULL,
                    valor      TEXT NOT NULL,
                    criado_em  TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (colecao, chave)
                )
            """)
        c.commit()


# ── OPERAÇÕES ──────────────────────────────────────────────────────────────────

def put(colecao: str, chave: str, valor: dict):
    """Insere ou atualiza um registro."""
    dados = json.dumps(valor, ensure_ascii=False) if not _IS_POSTGRES else valor
    with _lock, _conn() as c:
        cur = c.cursor()
        if _IS_POSTGRES:
            cur.execute(
                "INSERT INTO kv (colecao, chave, valor) VALUES (%s, %s, %s) "
                "ON CONFLICT (colecao, chave) DO UPDATE SET valor = EXCLUDED.valor",
                (colecao, chave, json.dumps(valor, ensure_ascii=False)),
            )
        else:
            cur.execute(
                "INSERT INTO kv (colecao, chave, valor) VALUES (?, ?, ?) "
                "ON CONFLICT (colecao, chave) DO UPDATE SET valor = excluded.valor",
                (colecao, chave, dados),
            )
        c.commit()


def get(colecao: str, chave: str) -> dict | None:
    with _conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT valor FROM kv WHERE colecao = {_PH} AND chave = {_PH}", (colecao, chave))
        row = cur.fetchone()
        if not row:
            return None
        valor = row[0]
        return valor if isinstance(valor, dict) else json.loads(valor)


def listar(colecao: str) -> list[dict]:
    with _conn() as c:
        cur = c.cursor()
        cur.execute(f"SELECT valor FROM kv WHERE colecao = {_PH} ORDER BY criado_em DESC", (colecao,))
        out = []
        for row in cur.fetchall():
            valor = row[0]
            out.append(valor if isinstance(valor, dict) else json.loads(valor))
        return out


def deletar(colecao: str, chave: str) -> bool:
    with _lock, _conn() as c:
        cur = c.cursor()
        cur.execute(f"DELETE FROM kv WHERE colecao = {_PH} AND chave = {_PH}", (colecao, chave))
        afetados = cur.rowcount
        c.commit()
        return afetados > 0


def encontrar(colecao: str, campo: str, valor) -> dict | None:
    """Busca o primeiro registro onde data[campo] == valor."""
    for item in listar(colecao):
        if item.get(campo) == valor:
            return item
    return None


# Inicializa ao importar
try:
    init_db()
    print(f"[DB] Inicializado — {'PostgreSQL' if _IS_POSTGRES else 'SQLite local'}")
except Exception as e:
    print(f"[DB] Erro ao inicializar: {e}")
