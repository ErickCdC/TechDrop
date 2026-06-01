"""
Banco de produtos — agora usa a camada de DB real (Postgres/SQLite).
Sobrevive a deploys e reinícios.
"""
import uuid
from datetime import datetime

try:
    from servidor import db
except ImportError:
    import db

COLECAO = "produtos"


def listar() -> list[dict]:
    # Sem seed automático: a loja só mostra os produtos que VOCÊ cadastrou.
    # Nada de produtos fantasma voltando.
    return db.listar(COLECAO)


def obter(pid: str) -> dict | None:
    return db.get(COLECAO, pid)


def criar(dados: dict) -> dict:
    pid = str(uuid.uuid4())[:8]
    produto = {
        "id":            pid,
        "ativo":         dados.get("ativo", True),
        "criado_em":     datetime.now().isoformat(),
        "atualizado_em": datetime.now().isoformat(),
        **dados,
        "id":            pid,  # garante que não é sobrescrito
    }
    db.put(COLECAO, pid, produto)
    return produto


def atualizar(pid: str, dados: dict) -> dict | None:
    existente = db.get(COLECAO, pid)
    if not existente:
        # Produto sumiu (ex: banco reiniciou sem Postgres) — recria com o id
        # para que a edição nunca falhe e os dados não se percam.
        existente = {"id": pid, "criado_em": datetime.now().isoformat()}
    atualizado = {**existente, **dados, "id": pid, "atualizado_em": datetime.now().isoformat()}
    db.put(COLECAO, pid, atualizado)
    return atualizado


def deletar(pid: str) -> bool:
    return db.deletar(COLECAO, pid)
