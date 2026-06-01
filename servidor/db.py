"""
Camada de persistência unificada.
- Produção (Railway): usa PostgreSQL via DATABASE_URL
- Local (dev): usa SQLite em dados/techdrop.db

Modelo: tabela chave-valor com JSON, uma "coleção" por tipo.
"""
import os
import json
import threading
from pathlib import Path

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
_IS_POSTGRES = DATABASE_URL.startswith(("postgres://", "postgresql://"))
_lock = threading.Lock()
BACKEND = "PostgreSQL" if _IS_POSTGRES else "SQLite"

if _IS_POSTGRES:
    import psycopg2
    import psycopg2.extras
    _DSN = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    _PH = "%s"

    def _connect():
        # Railway exige SSL na conexão pública; tenta com, depois sem.
        try:
            return psycopg2.connect(_DSN, sslmode="require", connect_timeout=10)
        except Exception:
            return psycopg2.connect(_DSN, connect_timeout=10)
else:
    import sqlite3
    _DB_FILE = Path(__file__).parent.parent / "dados" / "techdrop.db"
    _DB_FILE.parent.mkdir(exist_ok=True)
    _PH = "?"

    def _connect():
        c = sqlite3.connect(str(_DB_FILE))
        c.row_factory = sqlite3.Row
        return c


def _run(fn):
    """Abre conexão, executa fn(cursor), commita e SEMPRE fecha."""
    conn = _connect()
    try:
        cur = conn.cursor()
        result = fn(cur)
        conn.commit()
        return result
    finally:
        conn.close()


def init_db():
    def _create(cur):
        if _IS_POSTGRES:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    colecao   TEXT NOT NULL,
                    chave     TEXT NOT NULL,
                    valor     JSONB NOT NULL,
                    criado_em TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (colecao, chave)
                )""")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_kv_colecao ON kv(colecao)")
        else:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS kv (
                    colecao   TEXT NOT NULL,
                    chave     TEXT NOT NULL,
                    valor     TEXT NOT NULL,
                    criado_em TEXT DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (colecao, chave)
                )""")
    _run(_create)


def put(colecao: str, chave: str, valor: dict):
    payload = json.dumps(valor, ensure_ascii=False)
    def _op(cur):
        if _IS_POSTGRES:
            cur.execute(
                "INSERT INTO kv (colecao, chave, valor) VALUES (%s, %s, %s) "
                "ON CONFLICT (colecao, chave) DO UPDATE SET valor = EXCLUDED.valor",
                (colecao, chave, payload))
        else:
            cur.execute(
                "INSERT INTO kv (colecao, chave, valor) VALUES (?, ?, ?) "
                "ON CONFLICT (colecao, chave) DO UPDATE SET valor = excluded.valor",
                (colecao, chave, payload))
    with _lock:
        _run(_op)


def get(colecao: str, chave: str) -> dict | None:
    def _op(cur):
        cur.execute(f"SELECT valor FROM kv WHERE colecao = {_PH} AND chave = {_PH}", (colecao, chave))
        row = cur.fetchone()
        if not row:
            return None
        v = row[0]
        return v if isinstance(v, dict) else json.loads(v)
    return _run(_op)


def listar(colecao: str) -> list[dict]:
    def _op(cur):
        cur.execute(f"SELECT valor FROM kv WHERE colecao = {_PH} ORDER BY criado_em DESC", (colecao,))
        out = []
        for row in cur.fetchall():
            v = row[0]
            out.append(v if isinstance(v, dict) else json.loads(v))
        return out
    return _run(_op)


def deletar(colecao: str, chave: str) -> bool:
    def _op(cur):
        cur.execute(f"DELETE FROM kv WHERE colecao = {_PH} AND chave = {_PH}", (colecao, chave))
        return cur.rowcount > 0
    with _lock:
        return _run(_op)


def encontrar(colecao: str, campo: str, valor) -> dict | None:
    for item in listar(colecao):
        if item.get(campo) == valor:
            return item
    return None


def status() -> dict:
    """Diagnóstico: qual banco está ativo e se está funcionando."""
    info = {"backend": BACKEND, "tem_database_url": bool(DATABASE_URL), "ok": False}
    try:
        def _op(cur):
            cur.execute("SELECT COUNT(*) FROM kv")
            return cur.fetchone()[0]
        info["total_registros"] = _run(_op)
        info["ok"] = True
    except Exception as e:
        info["erro"] = str(e)
    return info


# Inicializa ao importar
try:
    init_db()
    print(f"[DB] Inicializado — {BACKEND}" + (" (DATABASE_URL detectada)" if _IS_POSTGRES else " local — SEM Postgres!"))
except Exception as e:
    print(f"[DB] ERRO ao inicializar ({BACKEND}): {e}")
