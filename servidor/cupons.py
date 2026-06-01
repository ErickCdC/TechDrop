"""
Sistema de cupons de desconto.
Cupons ficam no banco; alguns padrão são criados na primeira execução.
"""
from datetime import datetime

try:
    from servidor import db
except ImportError:
    import db

COLECAO = "cupons"


def _semear():
    if db.listar(COLECAO):
        return
    padrao = [
        {"codigo": "TECHDROP10", "tipo": "percent", "valor": 10, "ativo": True, "min_compra": 0,   "descricao": "10% de desconto"},
        {"codigo": "PRIMEIRA15", "tipo": "percent", "valor": 15, "ativo": True, "min_compra": 150, "descricao": "15% acima de R$150"},
        {"codigo": "FRETE20",    "tipo": "fixo",    "valor": 20, "ativo": True, "min_compra": 100, "descricao": "R$20 off acima de R$100"},
        {"codigo": "VOLTA10",    "tipo": "percent", "valor": 10, "ativo": True, "min_compra": 0,   "descricao": "10% recuperação de carrinho"},
    ]
    for c in padrao:
        c["criado_em"] = datetime.now().isoformat()
        c["usos"] = 0
        db.put(COLECAO, c["codigo"], c)


def validar(codigo: str, subtotal: float) -> dict:
    """Valida o cupom e retorna o desconto aplicável."""
    _semear()
    codigo = (codigo or "").strip().upper()
    if not codigo:
        return {"ok": False, "erro": "Informe um cupom"}
    cupom = db.get(COLECAO, codigo)
    if not cupom or not cupom.get("ativo"):
        return {"ok": False, "erro": "Cupom inválido ou expirado"}
    if subtotal < cupom.get("min_compra", 0):
        return {"ok": False, "erro": f"Cupom válido para compras acima de R$ {cupom['min_compra']:.0f}"}

    if cupom["tipo"] == "percent":
        desconto = round(subtotal * cupom["valor"] / 100, 2)
    else:
        desconto = min(cupom["valor"], subtotal)

    return {
        "ok": True,
        "codigo": codigo,
        "desconto": desconto,
        "descricao": cupom.get("descricao", ""),
        "total_com_desconto": round(subtotal - desconto, 2),
    }


def registrar_uso(codigo: str):
    cupom = db.get(COLECAO, (codigo or "").strip().upper())
    if cupom:
        cupom["usos"] = cupom.get("usos", 0) + 1
        db.put(COLECAO, cupom["codigo"], cupom)


def listar() -> list[dict]:
    _semear()
    return db.listar(COLECAO)


def salvar(cupom: dict):
    cupom["codigo"] = cupom["codigo"].strip().upper()
    if "criado_em" not in cupom:
        cupom["criado_em"] = datetime.now().isoformat()
    cupom.setdefault("usos", 0)
    db.put(COLECAO, cupom["codigo"], cupom)
