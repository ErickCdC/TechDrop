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
    from servidor import db
    from servidor.auth import login_required, verificar_credenciais
    from servidor import produtos_db
    from servidor.importador import importar_produto
    from servidor.emails import (confirmacao_pedido, notificar_rastreio,
                                  notificar_entregue, notificar_disputa_aberta,
                                  recuperar_carrinho)
    from servidor.rastreio import registrar_rastreio, consultar_status, verificar_todos_pedidos
    from servidor.disputas import (abrir_disputa, listar_disputas,
                                    atualizar_disputa, aprovar_reembolso,
                                    verificar_reembolsos_automaticos)
    from servidor import usuarios
    from servidor import cupons
    from servidor import avaliacoes
    from servidor.emails import pedir_avaliacao
except ImportError:
    import db
    from auth import login_required, verificar_credenciais
    import produtos_db
    from importador import importar_produto
    from emails import (confirmacao_pedido, notificar_rastreio,
                        notificar_entregue, notificar_disputa_aberta,
                        recuperar_carrinho)
    from rastreio import registrar_rastreio, consultar_status, verificar_todos_pedidos
    from disputas import (abrir_disputa, listar_disputas,
                          atualizar_disputa, aprovar_reembolso,
                          verificar_reembolsos_automaticos)
    import usuarios
    import cupons
    import avaliacoes
    from emails import pedir_avaliacao

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

@app.route("/api/cupom/validar", methods=["POST"])
def validar_cupom():
    d = request.json or {}
    return jsonify(cupons.validar(d.get("codigo", ""), float(d.get("subtotal", 0))))


@app.route("/api/checkout", methods=["POST"])
def criar_checkout():
    """
    Recebe o carrinho e cria uma preferência de pagamento no Mercado Pago.
    Valida preços no servidor (anti-fraude) e aplica cupom.
    """
    if not MP_ACCESS_TOKEN:
        return jsonify({"ok": False, "erro": "MP_ACCESS_TOKEN não configurado no .env"}), 400

    body = request.json
    itens   = body.get("itens", [])
    cliente = body.get("cliente", {})
    cupom_codigo = body.get("cupom", "")

    if not itens:
        return jsonify({"ok": False, "erro": "Carrinho vazio"}), 400

    # ── ANTI-FRAUDE: revalida preços contra o banco ───────────────────────────
    catalogo = {p["id"]: p for p in produtos_db.listar()}
    for item in itens:
        prod = catalogo.get(item.get("id"))
        if prod:
            # força o preço real do servidor, ignora o que veio do navegador
            item["preco_venda"] = prod["preco_venda"]
            item["titulo"]      = prod.get("titulo", item.get("titulo"))
            item["link_aliexpress"] = prod.get("link_aliexpress", "")

    subtotal = sum(i["preco_venda"] * i.get("quantidade", 1) for i in itens)

    # ── CUPOM ──────────────────────────────────────────────────────────────────
    desconto = 0
    cupom_aplicado = None
    if cupom_codigo:
        res = cupons.validar(cupom_codigo, subtotal)
        if res.get("ok"):
            desconto = res["desconto"]
            cupom_aplicado = res["codigo"]

    pedido_id = str(uuid.uuid4())[:8].upper()

    # Monta itens da preferência
    mp_items = [
        {
            "id":          item["id"],
            "title":       item["titulo"],
            "quantity":    item.get("quantidade", 1),
            "unit_price":  float(item["preco_venda"]),
            "currency_id": "BRL",
        }
        for item in itens
    ]
    # Desconto entra como item negativo
    if desconto > 0:
        mp_items.append({
            "id": "desconto", "title": f"Desconto ({cupom_aplicado})",
            "quantity": 1, "unit_price": -float(desconto), "currency_id": "BRL",
        })

    preference = {
        "external_reference": pedido_id,
        "items": mp_items,
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

    # Salva pedido no banco
    pedido = {
        "id":           pedido_id,
        "mp_id":        mp_data.get("id"),
        "status":       "aguardando_pagamento",
        "criado_em":    datetime.now().isoformat(),
        "cliente":      cliente,
        "itens":        itens,
        "subtotal":     round(subtotal, 2),
        "desconto":     desconto,
        "cupom":        cupom_aplicado,
        "total":        round(subtotal - desconto, 2),
        "checkout_url": mp_data.get("init_point"),
        "email_recuperacao_enviado": False,
    }
    _salvar_pedido(pedido)
    if cupom_aplicado:
        cupons.registrar_uso(cupom_aplicado)

    return jsonify({
        "ok":           True,
        "pedido_id":    pedido_id,
        "checkout_url": mp_data.get("init_point"),     # URL completa (Pix/cartão/boleto)
        "checkout_pix": mp_data.get("point_of_interaction", {}).get("transaction_data", {}).get("qr_code"),
    })


# ── WEBHOOK MERCADO PAGO ──────────────────────────────────────────────────────

def _validar_assinatura_mp(req, data_id: str) -> bool:
    """
    Valida a assinatura x-signature do Mercado Pago.
    Se MP_WEBHOOK_SECRET não estiver configurado, pula (mas re-verificamos
    o pagamento via API mesmo assim, então não é furo crítico).
    """
    if not MP_WEBHOOK_SECRET:
        return True  # sem secret configurado — confia na reverificação via API
    try:
        sig = req.headers.get("x-signature", "")
        req_id = req.headers.get("x-request-id", "")
        partes = dict(p.strip().split("=", 1) for p in sig.split(",") if "=" in p)
        ts, v1 = partes.get("ts", ""), partes.get("v1", "")
        manifest = f"id:{data_id};request-id:{req_id};ts:{ts};"
        esperado = hmac.new(MP_WEBHOOK_SECRET.encode(), manifest.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(esperado, v1)
    except Exception:
        return False


@app.route("/api/webhook/mercadopago", methods=["POST"])
def webhook_mp():
    """
    Recebe notificações do Mercado Pago.
    Valida assinatura + re-verifica o pagamento via API antes de aprovar.
    """
    data = request.json or {}
    tipo = data.get("type") or data.get("action", "")

    if tipo not in ("payment", "payment.updated"):
        return jsonify({"ok": True}), 200

    payment_id = (data.get("data", {}).get("id")
                  or data.get("id"))
    if not payment_id:
        return jsonify({"ok": True}), 200

    # Validação de assinatura (defesa em profundidade)
    if not _validar_assinatura_mp(request, str(payment_id)):
        print(f"[WEBHOOK] Assinatura inválida para payment {payment_id}")
        return jsonify({"ok": False, "erro": "assinatura inválida"}), 401

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
        # E-mail automático de confirmação
        try:
            confirmacao_pedido(pedido)
        except Exception as e:
            print(f"[EMAIL] Erro ao enviar confirmação: {e}")

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
    print(f"[PEDIDO] Aprovado: {pedido['id']} - R$ {pedido.get('total',0):.2f}")


# ── HELPERS (agora usam o banco de dados) ──────────────────────────────────────

def _salvar_pedido(pedido: dict):
    db.put("pedidos", pedido["id"], pedido)


def _carregar_pedido(pedido_id: str) -> dict | None:
    return db.get("pedidos", pedido_id)


def _listar_pedidos() -> list[dict]:
    pedidos = db.listar("pedidos")
    return sorted(pedidos, key=lambda p: p.get("criado_em", ""), reverse=True)


# ── AVALIAÇÕES (REVIEWS) ───────────────────────────────────────────────────────

@app.route("/api/avaliacoes/destaque", methods=["GET"])
def avaliacoes_destaque():
    avaliacoes._semear()
    return jsonify({"ok": True, "avaliacoes": avaliacoes.listar_destaque(6)})

@app.route("/api/avaliacoes/produto/<produto_id>", methods=["GET"])
def avaliacoes_produto(produto_id):
    return jsonify({
        "ok": True,
        "avaliacoes": avaliacoes.listar_por_produto(produto_id),
        "resumo": avaliacoes.media_produto(produto_id),
    })

@app.route("/api/avaliacoes/upload-foto", methods=["POST"])
def avaliacao_upload_foto():
    """Upload público de foto de avaliação (com validação)."""
    if "foto" not in request.files:
        return jsonify({"ok": False, "erro": "Nenhuma foto"}), 400
    foto = request.files["foto"]
    ext = (foto.filename.rsplit(".", 1)[-1] or "").lower()
    if ext not in ("jpg", "jpeg", "png", "webp"):
        return jsonify({"ok": False, "erro": "Formato inválido"}), 400
    # Limita tamanho (~5MB)
    foto.seek(0, 2); tamanho = foto.tell(); foto.seek(0)
    if tamanho > 5 * 1024 * 1024:
        return jsonify({"ok": False, "erro": "Foto muito grande (máx 5MB)"}), 400
    nome = f"rev_{uuid.uuid4().hex[:12]}.{ext}"
    foto.save(UPLOADS_DIR / nome)
    return jsonify({"ok": True, "url": f"/uploads/{nome}"})

@app.route("/api/avaliacoes", methods=["POST"])
def criar_avaliacao():
    """Cliente envia avaliação (validada por token do pedido)."""
    d = request.json or {}
    pedido_id = d.get("pedido_id", "").upper()
    token     = d.get("token", "")
    produto_id= d.get("produto_id", "")
    if not pedido_id or not avaliacoes.validar_token(pedido_id, token):
        return jsonify({"ok": False, "erro": "Link de avaliação inválido"}), 403
    av = avaliacoes.criar(produto_id, d)
    return jsonify({"ok": True, "avaliacao": av}), 201

@app.route("/api/avaliacoes/pedido/<pedido_id>", methods=["GET"])
def avaliacao_dados_pedido(pedido_id):
    """Retorna os itens do pedido para a página de avaliação (valida token)."""
    token = request.args.get("token", "")
    pedido_id = pedido_id.upper()
    if not avaliacoes.validar_token(pedido_id, token):
        return jsonify({"ok": False, "erro": "Link inválido"}), 403
    pedido = _carregar_pedido(pedido_id)
    if not pedido:
        return jsonify({"ok": False, "erro": "Pedido não encontrado"}), 404
    return jsonify({"ok": True, "itens": pedido.get("itens", []), "cliente_nome": pedido.get("cliente", {}).get("nome", "")})


# ── ADMIN AVALIAÇÕES ───────────────────────────────────────────────────────────

@app.route("/api/admin/avaliacoes", methods=["GET"])
@login_required
def admin_listar_avaliacoes():
    return jsonify({"ok": True, "avaliacoes": avaliacoes.listar_todas()})

@app.route("/api/admin/avaliacoes/<av_id>/aprovar", methods=["POST"])
@login_required
def admin_aprovar_avaliacao(av_id):
    aprovado = (request.json or {}).get("aprovado", True)
    ok = avaliacoes.definir_aprovacao(av_id, aprovado)
    return jsonify({"ok": ok})

@app.route("/api/admin/avaliacoes/<av_id>", methods=["DELETE"])
@login_required
def admin_deletar_avaliacao(av_id):
    return jsonify({"ok": avaliacoes.deletar(av_id)})

@app.route("/api/admin/avaliacoes/importar", methods=["POST"])
@login_required
def admin_importar_avaliacoes():
    """Importa avaliações reais do AliExpress (vindas da extensão)."""
    d = request.json or {}
    produto_id = d.get("produto_id", "")
    reviews    = d.get("reviews", [])
    if not produto_id or not reviews:
        return jsonify({"ok": False, "erro": "produto_id e reviews obrigatórios"}), 400
    importadas = avaliacoes.importar_aliexpress(produto_id, reviews)
    return jsonify({"ok": True, "importadas": importadas})


# ── CONTA DO COMPRADOR ─────────────────────────────────────────────────────────

@app.route("/api/conta/cadastrar", methods=["POST"])
def conta_cadastrar():
    d = request.json or {}
    r = usuarios.cadastrar(d.get("nome",""), d.get("email",""), d.get("senha",""))
    code = 201 if r.get("ok") else 400
    return jsonify(r), code

@app.route("/api/conta/login", methods=["POST"])
def conta_login():
    d = request.json or {}
    r = usuarios.login(d.get("email",""), d.get("senha",""))
    return jsonify(r), (200 if r.get("ok") else 401)

@app.route("/api/conta/me", methods=["GET"])
def conta_me():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    dados = usuarios.verificar_token(token)
    if not dados:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401
    u = usuarios.obter(dados["email"])
    return jsonify({"ok": True, "usuario": u})

@app.route("/api/conta/pedidos", methods=["GET"])
def conta_pedidos():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    dados = usuarios.verificar_token(token)
    if not dados:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401
    pedidos = usuarios.pedidos_do_usuario(dados["email"])
    # Enriquece com status de rastreio
    for p in pedidos:
        if p.get("rastreio") and not p.get("rastreio_info"):
            try:
                p["rastreio_info"] = consultar_status(p["rastreio"])
            except Exception:
                pass
    return jsonify({"ok": True, "pedidos": pedidos})


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
    pedido = _carregar_pedido(pedido_id.upper())
    if not pedido:
        return jsonify({"ok": False, "erro": "Pedido não encontrado"}), 404
    rastreio = (request.json or {}).get("rastreio", pedido.get("rastreio", ""))
    pedido["status"]      = "comprado_aliexpress"
    pedido["comprado_em"] = datetime.now().isoformat()
    pedido["rastreio"]    = rastreio
    _salvar_pedido(pedido)
    # Registra rastreio no 17track e notifica cliente automaticamente
    if rastreio:
        try:
            registrar_rastreio(rastreio)
            notificar_rastreio(pedido, rastreio)
        except Exception as e:
            print(f"[RASTREIO] {e}")
    return jsonify({"ok": True})


# ── RASTREIO ───────────────────────────────────────────────────────────────────

@app.route("/api/rastreio/<codigo>", methods=["GET"])
def rastreio_publico(codigo):
    """Rota pública para o cliente consultar o rastreio."""
    info = consultar_status(codigo)
    return jsonify({"ok": True, "rastreio": info})

@app.route("/api/admin/rastreio/verificar-todos", methods=["POST"])
@login_required
def admin_verificar_rastreios():
    """Verifica todos os pedidos em rastreio e atualiza status."""
    atualizados = verificar_todos_pedidos(str(PEDIDOS_DIR))
    for item in atualizados:
        pedido = item["pedido"]
        if item["evento"] == "entregue":
            try: notificar_entregue(pedido)
            except: pass
        _salvar_pedido(pedido)
    return jsonify({"ok": True, "atualizados": len(atualizados)})


# ── DISPUTAS ───────────────────────────────────────────────────────────────────

@app.route("/api/disputa", methods=["POST"])
def abrir_disputa_cliente():
    """Cliente abre disputa publicamente (sem login)."""
    data = request.json or {}
    pedido_id = data.get("pedido_id", "").upper()
    motivo    = data.get("motivo", "")
    descricao = data.get("descricao", "")
    if not pedido_id or not motivo:
        return jsonify({"ok": False, "erro": "pedido_id e motivo são obrigatórios"}), 400
    pedido = _carregar_pedido(pedido_id)
    if not pedido:
        return jsonify({"ok": False, "erro": "Pedido não encontrado"}), 404
    disputa = abrir_disputa(pedido_id, pedido, motivo, descricao)
    pedido["disputa_aberta"] = disputa["id"]
    pedido["status"] = "disputa"
    _salvar_pedido(pedido)
    try: notificar_disputa_aberta(pedido, disputa["motivo_label"])
    except: pass
    return jsonify({"ok": True, "disputa_id": disputa["id"]})

@app.route("/api/admin/disputas", methods=["GET"])
@login_required
def admin_listar_disputas():
    status = request.args.get("status")
    return jsonify({"ok": True, "disputas": listar_disputas(status)})

@app.route("/api/admin/disputas/<disputa_id>/aprovar-reembolso", methods=["POST"])
@login_required
def admin_aprovar_reembolso(disputa_id):
    """Admin aprova reembolso após AliExpress confirmar."""
    data  = request.json or {}
    valor = data.get("valor")
    disp  = aprovar_reembolso(disputa_id, valor)
    if not disp:
        return jsonify({"ok": False, "erro": "Disputa não encontrada"}), 404
    # Agora processa o reembolso no MP
    pedido = _carregar_pedido(disp["pedido_id"])
    if pedido and pedido.get("payment_id"):
        try:
            r = httpx.post(
                f"https://api.mercadopago.com/v1/payments/{pedido['payment_id']}/refunds",
                headers={**MP_HEADERS, "X-Idempotency-Key": f"refund-{disputa_id}"},
                json={},
                timeout=15,
            )
            if r.status_code in (200, 201):
                pedido["status"] = "reembolsado"
                _salvar_pedido(pedido)
        except Exception as e:
            print(f"[REEMBOLSO] {e}")
    return jsonify({"ok": True, "disputa": disp})

@app.route("/api/admin/disputas/<disputa_id>/atualizar", methods=["POST"])
@login_required
def admin_atualizar_disputa(disputa_id):
    data = request.json or {}
    disp = atualizar_disputa(disputa_id, data.get("acao",""), data.get("detalhe",""), data.get("status"))
    if not disp:
        return jsonify({"ok": False, "erro": "Disputa não encontrada"}), 404
    return jsonify({"ok": True, "disputa": disp})


# ── AGENDADOR AUTOMÁTICO (com eleição de líder p/ não duplicar entre workers) ──

import threading, time as _time, uuid as _uuid
from datetime import timedelta as _td

_WORKER_ID = _uuid.uuid4().hex[:8]

def _sou_lider() -> bool:
    """
    Eleição de líder via banco: só UM worker roda o agendador.
    Evita e-mails duplicados quando há múltiplos workers do gunicorn.
    """
    try:
        lock = db.get("_sistema", "scheduler_lock")
        agora = datetime.utcnow()
        if lock:
            expira = datetime.fromisoformat(lock["expira"])
            if lock["owner"] != _WORKER_ID and expira > agora:
                return False  # outro worker é o líder e está ativo
        # Assume/renova a liderança por 20 minutos
        db.put("_sistema", "scheduler_lock", {
            "owner":  _WORKER_ID,
            "expira": (agora + _td(minutes=20)).isoformat(),
        })
        return True
    except Exception:
        return False

def _job_rastreio():
    while True:
        _time.sleep(6 * 3600)
        if not _sou_lider():
            continue
        try:
            print("[AGENDADOR] Verificando rastreios...")
            atualizados = verificar_todos_pedidos()
            for item in atualizados:
                pedido = item["pedido"]
                if item["evento"] == "entregue" and not pedido.get("avaliacao_solicitada"):
                    notificar_entregue(pedido)
                    # Pede avaliação com link assinado
                    link = f"{LOJA_URL}/avaliar?pedido={pedido['id']}&token={avaliacoes.token_avaliacao(pedido['id'])}"
                    pedir_avaliacao(pedido, link)
                    pedido["avaliacao_solicitada"] = True
                _salvar_pedido(pedido)
            print(f"[AGENDADOR] {len(atualizados)} rastreio(s) atualizados")
        except Exception as e:
            print(f"[AGENDADOR] Erro: {e}")

def _job_reembolsos():
    while True:
        _time.sleep(24 * 3600)
        if not _sou_lider():
            continue
        try:
            print("[AGENDADOR] Verificando reembolsos automáticos...")
            for c in verificar_reembolsos_automaticos():
                pedido = c["pedido"]
                disputa = abrir_disputa(
                    pedido["id"], pedido, "nao_recebido",
                    f"Reembolso automático — pedido não entregue após {c['dias']} dias")
                pedido["disputa_aberta"] = disputa["id"]
                pedido["status"] = "disputa"
                _salvar_pedido(pedido)
                notificar_disputa_aberta(pedido, "Prazo de entrega excedido")
                print(f"[AGENDADOR] Disputa automática para #{pedido['id']}")
        except Exception as e:
            print(f"[AGENDADOR] Erro reembolsos: {e}")

def _job_carrinho_abandonado():
    """Envia e-mail de recuperação 1h após checkout não finalizado."""
    while True:
        _time.sleep(30 * 60)  # checa a cada 30min
        if not _sou_lider():
            continue
        try:
            agora = datetime.now()
            for pedido in db.listar("pedidos"):
                if pedido.get("status") != "aguardando_pagamento":
                    continue
                if pedido.get("email_recuperacao_enviado"):
                    continue
                if not pedido.get("cliente", {}).get("email"):
                    continue
                criado = datetime.fromisoformat(pedido.get("criado_em", agora.isoformat()))
                horas = (agora - criado).total_seconds() / 3600
                if 1 <= horas <= 48:  # entre 1h e 48h
                    recuperar_carrinho(pedido)
                    pedido["email_recuperacao_enviado"] = True
                    _salvar_pedido(pedido)
                    print(f"[CARRINHO] Recuperação enviada para #{pedido['id']}")
        except Exception as e:
            print(f"[CARRINHO] Erro: {e}")

threading.Thread(target=_job_rastreio,            daemon=True).start()
threading.Thread(target=_job_reembolsos,          daemon=True).start()
threading.Thread(target=_job_carrinho_abandonado, daemon=True).start()


@app.route("/api/admin/pedidos", methods=["GET"])
@login_required
def admin_listar_pedidos():
    return jsonify({"ok": True, "pedidos": _listar_pedidos()[:50]})


# ── ADMIN MÉTRICAS ─────────────────────────────────────────────────────────────

@app.route("/api/admin/metricas", methods=["GET"])
@login_required
def admin_metricas():
    pedidos = _listar_pedidos()

    aprovados   = [p for p in pedidos if p.get("status") in ("approved", "pagamento_aprovado", "comprado_aliexpress", "entregue")]
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
    # Enriquece com a média real de avaliações
    for p in ativos:
        resumo = avaliacoes.media_produto(p["id"])
        if resumo["total"] > 0:
            p["avaliacao"] = resumo["media"]
            p["vendas"]    = resumo["total"]  # mostra nº de avaliações reais
            p["tem_reviews_reais"] = True
    return jsonify({"ok": True, "produtos": ativos})


# ── ADMIN PÁGINAS ──────────────────────────────────────────────────────────────

@app.route("/admin-panel/")
@app.route("/admin-panel")
def admin_panel():
    return send_from_directory(app.static_folder, "admin-panel.html")

@app.route("/admin-panel/login")
def admin_login_page():
    return send_from_directory(app.static_folder, "admin-login.html")

@app.route("/minha-conta")
@app.route("/minha-conta.html")
def minha_conta_page():
    return send_from_directory(app.static_folder, "minha-conta.html")

@app.route("/avaliar")
@app.route("/avaliar.html")
def avaliar_page():
    return send_from_directory(app.static_folder, "avaliar.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\nTechDrop Brasil - servidor rodando em http://localhost:{port}")
    print(f"   Site:  http://localhost:{port}/")
    print(f"   Admin: http://localhost:{port}/admin-panel/\n")
    app.run(host="0.0.0.0", port=port, debug=False)
