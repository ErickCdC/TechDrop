"""
Sistema de cupons avançado.
Campos: tipo, valor, mínimo de compra, máximo de usos, validade,
desconto máximo (para %), segmento de cliente, ativo.
"""
from datetime import datetime

try:
    from servidor import db
except ImportError:
    import db

COLECAO = "cupons"

SEGMENTOS = {
    "todos":          "Todos os clientes",
    "primeira_compra":"Apenas primeira compra",
    "logados":        "Apenas clientes logados",
}


def _semear():
    if db.get("_sistema", "cupons_semeados"):
        return
    padrao = [
        {"codigo": "BEMVINDO10", "tipo": "percent", "valor": 10, "ativo": True, "min_compra": 0,
         "max_usos": 0, "desconto_max": 0, "segmento": "primeira_compra", "validade": "",
         "descricao": "10% na primeira compra"},
    ]
    for c in padrao:
        salvar(c)
    db.put("_sistema", "cupons_semeados", {"feito": True})


def _normalizar(c: dict) -> dict:
    return {
        "codigo":       (c.get("codigo") or "").strip().upper(),
        "tipo":         c.get("tipo", "percent"),          # percent | fixo
        "valor":        float(c.get("valor", 0) or 0),
        "ativo":        bool(c.get("ativo", True)),
        "min_compra":   float(c.get("min_compra", 0) or 0),
        "max_usos":     int(c.get("max_usos", 0) or 0),    # 0 = ilimitado
        "desconto_max": float(c.get("desconto_max", 0) or 0),  # teto p/ % (0 = sem teto)
        "segmento":     c.get("segmento", "todos"),
        "validade":     c.get("validade", ""),             # "YYYY-MM-DD" ou vazio
        "descricao":    (c.get("descricao") or "").strip(),
        "usos":         int(c.get("usos", 0) or 0),
        "criado_em":    c.get("criado_em") or datetime.now().isoformat(),
    }


def listar() -> list[dict]:
    _semear()
    return sorted(db.listar(COLECAO), key=lambda x: x.get("criado_em", ""), reverse=True)


def obter(codigo: str) -> dict | None:
    return db.get(COLECAO, (codigo or "").strip().upper())


def salvar(cupom: dict) -> dict:
    c = _normalizar(cupom)
    if not c["codigo"]:
        raise ValueError("Código obrigatório")
    # preserva usos existentes ao editar
    existente = db.get(COLECAO, c["codigo"])
    if existente:
        c["usos"] = existente.get("usos", 0)
        c["criado_em"] = existente.get("criado_em", c["criado_em"])
    db.put(COLECAO, c["codigo"], c)
    return c


def deletar(codigo: str) -> bool:
    return db.deletar(COLECAO, (codigo or "").strip().upper())


def validar(codigo: str, subtotal: float, email: str = "", logado: bool = False) -> dict:
    _semear()
    codigo = (codigo or "").strip().upper()
    if not codigo:
        return {"ok": False, "erro": "Informe um cupom"}
    c = db.get(COLECAO, codigo)
    if not c or not c.get("ativo"):
        return {"ok": False, "erro": "Cupom inválido ou inativo"}

    # Validade
    if c.get("validade"):
        try:
            if datetime.now().date() > datetime.fromisoformat(c["validade"]).date():
                return {"ok": False, "erro": "Cupom expirado"}
        except Exception:
            pass

    # Limite de usos
    if c.get("max_usos", 0) > 0 and c.get("usos", 0) >= c["max_usos"]:
        return {"ok": False, "erro": "Cupom esgotado"}

    # Mínimo de compra
    if subtotal < c.get("min_compra", 0):
        return {"ok": False, "erro": f"Cupom válido para compras acima de R$ {c['min_compra']:.0f}"}

    # Segmento
    seg = c.get("segmento", "todos")
    if seg == "logados" and not logado:
        return {"ok": False, "erro": "Cupom exclusivo para clientes logados. Faça login."}
    if seg == "primeira_compra" and email:
        # verifica se já tem pedido aprovado
        pedidos = [p for p in db.listar("pedidos")
                   if (p.get("cliente", {}).get("email", "") or "").lower() == email.lower()
                   and p.get("status") in ("approved", "pagamento_aprovado", "comprado_aliexpress", "entregue")]
        if pedidos:
            return {"ok": False, "erro": "Cupom válido apenas na primeira compra"}

    # Cálculo
    if c["tipo"] == "percent":
        desconto = subtotal * c["valor"] / 100
        if c.get("desconto_max", 0) > 0:
            desconto = min(desconto, c["desconto_max"])
    else:
        desconto = min(c["valor"], subtotal)
    desconto = round(desconto, 2)

    return {
        "ok": True, "codigo": codigo, "desconto": desconto,
        "descricao": c.get("descricao", ""),
        "total_com_desconto": round(subtotal - desconto, 2),
    }


def registrar_uso(codigo: str):
    c = db.get(COLECAO, (codigo or "").strip().upper())
    if c:
        c["usos"] = c.get("usos", 0) + 1
        db.put(COLECAO, c["codigo"], c)
