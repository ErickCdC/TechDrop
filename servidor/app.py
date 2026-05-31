"""
Servidor backend — TechDrop Brasil
Gerencia carrinho, checkout Mercado Pago e pedidos automáticos.
"""
import os
import json
import uuid
import hmac
import hashlib
from datetime import datetime
from pathlib import Path
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import httpx
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__, static_folder="../site", static_url_path="")
CORS(app)

MP_ACCESS_TOKEN   = os.getenv("MP_ACCESS_TOKEN", "")
MP_WEBHOOK_SECRET = os.getenv("MP_WEBHOOK_SECRET", "")
LOJA_URL          = os.getenv("LOJA_URL", "http://localhost:5000")

PEDIDOS_DIR = Path(__file__).parent.parent / "dados" / "pedidos"
PEDIDOS_DIR.mkdir(exist_ok=True)

MP_HEADERS = {
    "Authorization": f"Bearer {MP_ACCESS_TOKEN}",
    "Content-Type":  "application/json",
    "X-Idempotency-Key": "",
}


# ── SITE ESTÁTICO ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ── CARRINHO ──────────────────────────────────────────────────────────────────

@app.route("/api/produtos", methods=["GET"])
def listar_produtos():
    """Retorna o catálogo de produtos com preços e links de checkout."""
    try:
        with open(Path(__file__).parent.parent / "dados" / "produtos.json", encoding="utf-8") as f:
            produtos = json.load(f)
        return jsonify({"ok": True, "produtos": produtos})
    except FileNotFoundError:
        return jsonify({"ok": False, "erro": "Catálogo não encontrado. Rode o agente AliExpress primeiro."})


# ── CRIAR PREFERÊNCIA DE PAGAMENTO (MERCADO PAGO) ────────────────────────────

@app.route("/api/checkout", methods=["POST"])
def criar_checkout():
    """
    Recebe o carrinho e cria uma preferência de pagamento no Mercado Pago.
    Retorna o link de checkout (Pix, cartão, boleto).
    """
    if not MP_ACCESS_TOKEN:
        return jsonify({"ok": False, "erro": "MP_ACCESS_TOKEN não configurado no .env"}), 400

    body = request.json
    itens   = body.get("itens", [])
    cliente = body.get("cliente", {})

    if not itens:
        return jsonify({"ok": False, "erro": "Carrinho vazio"}), 400

    pedido_id = str(uuid.uuid4())[:8].upper()

    # Monta preferência para o Mercado Pago
    preference = {
        "external_reference": pedido_id,
        "items": [
            {
                "id":          item["id"],
                "title":       item["titulo"],
                "quantity":    item.get("quantidade", 1),
                "unit_price":  float(item["preco_venda"]),
                "currency_id": "BRL",
            }
            for item in itens
        ],
        "payer": {
            "name":  cliente.get("nome", ""),
            "email": cliente.get("email", ""),
        },
        "back_urls": {
            "success": f"{LOJA_URL}/pedido-confirmado.html?id={pedido_id}",
            "failure": f"{LOJA_URL}/?checkout=falhou",
            "pending": f"{LOJA_URL}/pedido-pendente.html?id={pedido_id}",
        },
        "auto_return":        "approved",
        "notification_url":   f"{LOJA_URL}/api/webhook/mercadopago",
        "statement_descriptor": "TECHDROP BRASIL",
        "expires":             False,
    }

    headers = {**MP_HEADERS, "X-Idempotency-Key": pedido_id}
    resp = httpx.post(
        "https://api.mercadopago.com/checkout/preferences",
        json=preference,
        headers=headers,
        timeout=15,
    )

    if resp.status_code != 201:
        return jsonify({"ok": False, "erro": resp.text}), 400

    mp_data = resp.json()

    # Salva pedido localmente
    pedido = {
        "id":           pedido_id,
        "mp_id":        mp_data.get("id"),
        "status":       "aguardando_pagamento",
        "criado_em":    datetime.now().isoformat(),
        "cliente":      cliente,
        "itens":        itens,
        "total":        sum(i["preco_venda"] * i.get("quantidade", 1) for i in itens),
        "checkout_url": mp_data.get("init_point"),
    }
    _salvar_pedido(pedido)

    return jsonify({
        "ok":           True,
        "pedido_id":    pedido_id,
        "checkout_url": mp_data.get("init_point"),     # URL completa (Pix/cartão/boleto)
        "checkout_pix": mp_data.get("point_of_interaction", {}).get("transaction_data", {}).get("qr_code"),
    })


# ── WEBHOOK MERCADO PAGO ──────────────────────────────────────────────────────

@app.route("/api/webhook/mercadopago", methods=["POST"])
def webhook_mp():
    """
    Recebe notificações do Mercado Pago.
    Quando pagamento aprovado: registra pedido e dispara agente de fulfillment.
    """
    data = request.json or {}
    tipo = data.get("type") or data.get("action", "")

    if tipo not in ("payment", "payment.updated"):
        return jsonify({"ok": True}), 200

    payment_id = (data.get("data", {}).get("id")
                  or data.get("id"))
    if not payment_id:
        return jsonify({"ok": True}), 200

    # Consulta o pagamento no MP
    resp = httpx.get(
        f"https://api.mercadopago.com/v1/payments/{payment_id}",
        headers=MP_HEADERS,
        timeout=10,
    )
    if resp.status_code != 200:
        return jsonify({"ok": False}), 400

    payment = resp.json()
    status        = payment.get("status")
    pedido_id     = payment.get("external_reference")
    valor_pago    = payment.get("transaction_amount")
    metodo        = payment.get("payment_type_id")

    pedido = _carregar_pedido(pedido_id)
    if not pedido:
        return jsonify({"ok": True}), 200

    pedido["status"]       = status
    pedido["payment_id"]   = payment_id
    pedido["valor_pago"]   = valor_pago
    pedido["metodo_pgto"]  = metodo
    pedido["pago_em"]      = datetime.now().isoformat()
    _salvar_pedido(pedido)

    if status == "approved":
        _processar_pedido_aprovado(pedido)

    return jsonify({"ok": True}), 200


# ── CONSULTAR STATUS DO PEDIDO ────────────────────────────────────────────────

@app.route("/api/pedido/<pedido_id>", methods=["GET"])
def consultar_pedido(pedido_id):
    pedido = _carregar_pedido(pedido_id.upper())
    if not pedido:
        return jsonify({"ok": False, "erro": "Pedido não encontrado"}), 404
    return jsonify({"ok": True, "pedido": pedido})


# ── FULFILLMENT AUTOMÁTICO ────────────────────────────────────────────────────

def _processar_pedido_aprovado(pedido: dict):
    """
    Chamado quando pagamento é aprovado.
    Registra o pedido para processamento manual (ou automático com AliExpress API).
    """
    pedido["status"] = "pagamento_aprovado"
    pedido["proximos_passos"] = []

    for item in pedido["itens"]:
        pedido["proximos_passos"].append({
            "produto":      item["titulo"],
            "link_ali":     item.get("link_aliexpress", ""),
            "endereco":     pedido.get("cliente", {}).get("endereco", "Coletar no checkout"),
            "instrucao":    f"Comprar no AliExpress e enviar para o endereço do cliente.",
            "status":       "pendente_compra",
        })

    _salvar_pedido(pedido)

    # Salva em fila de fulfillment
    fila_path = PEDIDOS_DIR / "fila_fulfillment.json"
    fila = []
    if fila_path.exists():
        with open(fila_path, encoding="utf-8") as f:
            fila = json.load(f)
    fila.append({"pedido_id": pedido["id"], "criado_em": datetime.now().isoformat()})
    with open(fila_path, "w", encoding="utf-8") as f:
        json.dump(fila, f, ensure_ascii=False, indent=2)

    print(f"\n✅ PEDIDO APROVADO: {pedido['id']} — R$ {pedido['total']:.2f}")
    print(f"   Cliente: {pedido['cliente'].get('nome', 'N/A')} | {pedido['cliente'].get('email', '')}")
    for p in pedido["proximos_passos"]:
        print(f"   → Comprar: {p['produto']}")
        if p["link_ali"]:
            print(f"     Link AliExpress: {p['link_ali']}")


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _salvar_pedido(pedido: dict):
    path = PEDIDOS_DIR / f"{pedido['id']}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(pedido, f, ensure_ascii=False, indent=2)


def _carregar_pedido(pedido_id: str) -> dict | None:
    path = PEDIDOS_DIR / f"{pedido_id}.json"
    if not path.exists():
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\nTechDrop Brasil - servidor rodando em http://localhost:{port}")
    print(f"   Site:  http://localhost:{port}/")
    print(f"   Admin: http://localhost:{port}/admin/\n")
    app.run(host="0.0.0.0", port=port, debug=False)
