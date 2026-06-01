"""
Sistema de avaliações (reviews) reais com foto.
Clientes avaliam após a compra; admin pode ocultar avaliações impróprias.
"""
import uuid
import hmac
import hashlib
import os
from datetime import datetime

try:
    from servidor import db
except ImportError:
    import db

COLECAO = "avaliacoes"
SECRET  = os.getenv("JWT_SECRET", "") or os.getenv("ADMIN_PASS", "") or "review-secret"


def token_avaliacao(pedido_id: str) -> str:
    """Token simples que autoriza avaliar um pedido específico."""
    return hmac.new(SECRET.encode(), f"review::{pedido_id}".encode(), hashlib.sha256).hexdigest()[:16]


def validar_token(pedido_id: str, token: str) -> bool:
    return hmac.compare_digest(token_avaliacao(pedido_id), token or "")


def criar(produto_id: str, dados: dict) -> dict:
    av = {
        "id":         str(uuid.uuid4())[:8],
        "produto_id": produto_id,
        "pedido_id":  dados.get("pedido_id", ""),
        "nome":       (dados.get("nome", "Cliente") or "Cliente").strip()[:40],
        "nota":       max(1, min(5, int(dados.get("nota", 5)))),
        "texto":      (dados.get("texto", "") or "").strip()[:600],
        "foto":       dados.get("foto", ""),
        "aprovado":   True,   # auto-aprova; admin pode ocultar
        "criado_em":  datetime.now().isoformat(),
    }
    db.put(COLECAO, av["id"], av)
    return av


def listar_por_produto(produto_id: str, apenas_aprovadas=True) -> list[dict]:
    todas = db.listar(COLECAO)
    out = [a for a in todas if a.get("produto_id") == produto_id and (a.get("aprovado") or not apenas_aprovadas)]
    return sorted(out, key=lambda a: a.get("criado_em", ""), reverse=True)


def listar_destaque(limite=6) -> list[dict]:
    """Melhores avaliações (com foto e nota alta) para a home."""
    todas = [a for a in db.listar(COLECAO) if a.get("aprovado")]
    # Prioriza as que têm foto e nota >= 4
    com_foto = [a for a in todas if a.get("foto") and a.get("nota", 0) >= 4]
    sem_foto = [a for a in todas if not a.get("foto") and a.get("nota", 0) >= 4]
    ordenadas = sorted(com_foto, key=lambda a: a.get("criado_em",""), reverse=True) + \
                sorted(sem_foto, key=lambda a: a.get("criado_em",""), reverse=True)
    return ordenadas[:limite]


def listar_todas() -> list[dict]:
    return sorted(db.listar(COLECAO), key=lambda a: a.get("criado_em", ""), reverse=True)


def definir_aprovacao(av_id: str, aprovado: bool) -> bool:
    av = db.get(COLECAO, av_id)
    if not av:
        return False
    av["aprovado"] = aprovado
    db.put(COLECAO, av_id, av)
    return True


def deletar(av_id: str) -> bool:
    return db.deletar(COLECAO, av_id)


def media_produto(produto_id: str) -> dict:
    avs = listar_por_produto(produto_id)
    if not avs:
        return {"media": 0, "total": 0}
    media = sum(a["nota"] for a in avs) / len(avs)
    return {"media": round(media, 1), "total": len(avs)}


def importar_aliexpress(produto_id: str, reviews: list[dict]) -> int:
    """
    Importa avaliações reais vindas da extensão (AliExpress feedback API).
    Cada review: {nome, pais, nota, texto, fotos[], data}.
    Evita duplicar pelo conjunto nome+texto.
    """
    existentes = {(a.get("nome",""), a.get("texto","")[:50])
                  for a in db.listar(COLECAO) if a.get("produto_id") == produto_id}
    importadas = 0
    for r in reviews:
        nome  = (r.get("nome") or "Cliente").strip()[:40]
        texto = (r.get("texto") or "").strip()[:600]
        if (nome, texto[:50]) in existentes:
            continue
        if not texto and not r.get("fotos"):
            continue  # ignora reviews vazias sem foto
        av = {
            "id":         str(uuid.uuid4())[:8],
            "produto_id": produto_id,
            "pedido_id":  "",
            "nome":       nome,
            "pais":       r.get("pais", ""),
            "nota":       max(1, min(5, int(r.get("nota", 5)))),
            "texto":      texto,
            "foto":       (r.get("fotos") or [""])[0],
            "fotos":      r.get("fotos", []),
            "origem":     "aliexpress",
            "aprovado":   True,
            "criado_em":  r.get("data") or datetime.now().isoformat(),
        }
        db.put(COLECAO, av["id"], av)
        existentes.add((nome, texto[:50]))
        importadas += 1
    return importadas


def _semear():
    """Sem avaliações fantasma. A loja só mostra avaliações reais."""
    return
