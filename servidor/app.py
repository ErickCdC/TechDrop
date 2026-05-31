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
from flask import Flask, request, jsonify, send_from_directory, make_response
from flask_cors import CORS
import httpx
from dotenv import load_dotenv
try:
    from servidor.auth import login_required, verificar_credenciais
    from servidor import produtos_db
    from servidor.importador import importar_produto
except ImportError:
    from auth import login_required, verificar_credenciais
    import produtos_db
    from importador import importar_produto

load_dotenv()

app = Flask(__name__, static_folder="../site", static_url_path="")
CORS(app, supports_credentials=True)

UPLOADS_DIR = Path(__file__).parent / "uploads"
UPLOADS_DIR.mkdir(exist_ok=True)

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


# ── ADMIN AUTH ────────────────────────────────────────────────────────────────

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.json or {}
    token = verificar_credenciais(data.get("user",""), data.get("senha",""))
    if not token:
        return jsonify({"ok": False, "erro": "Usuário ou senha incorretos"}), 401
    resp = make_response(jsonify({"ok": True, "token": token}))
    resp.set_cookie("admin_token", token, httponly=True, samesite="Lax", max_age=86400)
    return resp

@app.route("/api/admin/logout", methods=["POST"])
def admin_logout():
    resp = make_response(jsonify({"ok": True}))
    resp.delete_cookie("admin_token")
    return resp

@app.route("/api/admin/me", methods=["GET"])
@login_required
def admin_me():
    return jsonify({"ok": True, "user": os.getenv("ADMIN_USER", "admin")})


# ── ADMIN PRODUTOS ─────────────────────────────────────────────────────────────

@app.route("/api/admin/produtos", methods=["GET"])
@login_required
def admin_listar_produtos():
    return jsonify({"ok": True, "produtos": produtos_db.listar()})

@app.route("/api/admin/produtos", methods=["POST"])
@login_required
def admin_criar_produto():
    dados = request.json or {}
    produto = produtos_db.criar(dados)
    return jsonify({"ok": True, "produto": produto}), 201

@app.route("/api/admin/produtos/<pid>", methods=["GET"])
@login_required
def admin_obter_produto(pid):
    p = produtos_db.obter(pid)
    if not p:
        return jsonify({"ok": False, "erro": "Não encontrado"}), 404
    return jsonify({"ok": True, "produto": p})

@app.route("/api/admin/produtos/<pid>", methods=["PUT"])
@login_required
def admin_atualizar_produto(pid):
    dados = request.json or {}
    p = produtos_db.atualizar(pid, dados)
    if not p:
        return jsonify({"ok": False, "erro": "Não encontrado"}), 404
    return jsonify({"ok": True, "produto": p})

@app.route("/api/admin/produtos/<pid>", methods=["DELETE"])
@login_required
def admin_deletar_produto(pid):
    ok = produtos_db.deletar(pid)
    return jsonify({"ok": ok})

@app.route("/api/admin/importar", methods=["POST"])
@login_required
def admin_importar_produto():
    """Importa produto completo a partir de uma URL do AliExpress."""
    url = (request.json or {}).get("url", "").strip()
    if not url:
        return jsonify({"ok": False, "erro": "URL não informada"}), 400
    try:
        dados = importar_produto(url)
        return jsonify(dados)
    except ValueError as e:
        return jsonify({"ok": False, "erro": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "erro": f"Erro ao importar: {str(e)}"}), 500

@app.route("/api/admin/produtos/upload-foto", methods=["POST"])
@login_required
def admin_upload_foto():
    if "foto" not in request.files:
        return jsonify({"ok": False, "erro": "Nenhuma foto enviada"}), 400
    foto = request.files["foto"]
    ext  = foto.filename.rsplit(".", 1)[-1].lower()
    if ext not in ("jpg", "jpeg", "png", "webp"):
        return jsonify({"ok": False, "erro": "Formato inválido. Use JPG, PNG ou WEBP"}), 400
    nome = f"{uuid.uuid4().hex[:12]}.{ext}"
    foto.save(UPLOADS_DIR / nome)
    url = f"/uploads/{nome}"
    return jsonify({"ok": True, "url": url})

@app.route("/uploads/<path:nome>")
def servir_upload(nome):
    return send_from_directory(UPLOADS_DIR, nome)


# ── ADMIN PEDIDOS ──────────────────────────────────────────────────────────────

@app.route("/api/admin/pedidos/<pedido_id>/reembolsar", methods=["POST"])
@login_required
def admin_reembolsar(pedido_id):
    """Reembolsa um pedido via Mercado Pago."""
    pedido = _carregar_pedido(pedido_id.upper())
    if not pedido:
        return jsonify({"ok": False, "erro": "Pedido não encontrado"}), 404
    payment_id = pedido.get("payment_id")
    if not payment_id:
        return jsonify({"ok": False, "erro": "Pedido sem payment_id"}), 400
    try:
        r = httpx.post(
            f"https://api.mercadopago.com/v1/payments/{payment_id}/refunds",
            headers={**MP_HEADERS, "X-Idempotency-Key": f"refund-{pedido_id}"},
            json={},
            timeout=15,
        )
        if r.status_code in (200, 201):
            pedido["status"] = "reembolsado"
            _salvar_pedido(pedido)
            return jsonify({"ok": True, "msg": "Reembolso solicitado com sucesso"})
        return jsonify({"ok": False, "erro": r.text}), 400
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 500


@app.route("/api/admin/pedidos/<pedido_id>/fulfillment", methods=["POST"])
@login_required
def admin_marcar_fulfillment(pedido_id):
    """Marca pedido como comprado no AliExpress."""
    pedido = _carregar_pedido(pedido_id.upper())
    if not pedido:
        return jsonify({"ok": False, "erro": "Pedido não encontrado"}), 404
    pedido["status"] = "comprado_aliexpress"
    pedido["comprado_em"] = datetime.now().isoformat()
    pedido["rastreio"] = (request.json or {}).get("rastreio", "")
    _salvar_pedido(pedido)
    return jsonify({"ok": True})


@app.route("/api/admin/pedidos", methods=["GET"])
@login_required
def admin_listar_pedidos():
    pedidos = []
    for arq in sorted(PEDIDOS_DIR.glob("*.json"), reverse=True)[:50]:
        if arq.name == "fila_fulfillment.json":
            continue
        with open(arq, encoding="utf-8") as f:
            pedidos.append(json.load(f))
    return jsonify({"ok": True, "pedidos": pedidos})


# ── ADMIN MÉTRICAS ─────────────────────────────────────────────────────────────

@app.route("/api/admin/metricas", methods=["GET"])
@login_required
def admin_metricas():
    pedidos = []
    for arq in PEDIDOS_DIR.glob("*.json"):
        if arq.name == "fila_fulfillment.json":
            continue
        with open(arq, encoding="utf-8") as f:
            pedidos.append(json.load(f))

    aprovados   = [p for p in pedidos if p.get("status") == "approved"]
    receita     = sum(p.get("total", 0) for p in aprovados)
    ticket_med  = receita / len(aprovados) if aprovados else 0

    # Vendas por hora
    vendas_hora = {str(h).zfill(2): 0 for h in range(24)}
    for p in aprovados:
        hora = p.get("pago_em", "")[:13].split("T")[-1][:2]
        if hora in vendas_hora:
            vendas_hora[hora] += 1

    melhor_hora = max(vendas_hora, key=vendas_hora.get) if any(vendas_hora.values()) else "18"

    return jsonify({
        "ok": True,
        "total_pedidos":   len(pedidos),
        "pedidos_aprovados": len(aprovados),
        "receita_total":   round(receita, 2),
        "ticket_medio":    round(ticket_med, 2),
        "melhor_hora":     melhor_hora,
        "vendas_por_hora": vendas_hora,
        "produtos_ativos": len([p for p in produtos_db.listar() if p.get("ativo")]),
    })


# ── PRODUTOS PÚBLICO ───────────────────────────────────────────────────────────

@app.route("/api/produtos", methods=["GET"])
def listar_produtos():
    ativos = [p for p in produtos_db.listar() if p.get("ativo", True)]
    return jsonify({"ok": True, "produtos": ativos})


# ── ADMIN PÁGINAS ──────────────────────────────────────────────────────────────

@app.route("/admin-panel/")
@app.route("/admin-panel")
def admin_panel():
    return send_from_directory(app.static_folder, "admin-panel.html")

@app.route("/admin-panel/login")
def admin_login_page():
    return send_from_directory(app.static_folder, "admin-login.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\nTechDrop Brasil - servidor rodando em http://localhost:{port}")
    print(f"   Site:  http://localhost:{port}/")
    print(f"   Admin: http://localhost:{port}/admin-panel/\n")
    app.run(host="0.0.0.0", port=port, debug=False)
