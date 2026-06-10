"""
Servidor backend — TechDrop Brasil
Gerencia carrinho, checkout Mercado Pago e pedidos automáticos.
"""
import os
import re
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
                                  notificar_cancelamento, recuperar_carrinho)
    from servidor.rastreio import registrar_rastreio, consultar_status, verificar_todos_pedidos
    from servidor.disputas import (abrir_disputa, listar_disputas,
                                    atualizar_disputa, aprovar_reembolso,
                                    verificar_reembolsos_automaticos)
    from servidor import usuarios
    from servidor import cupons
    from servidor import avaliacoes
    from servidor.emails import pedir_avaliacao
    from servidor import config_site
except ImportError:
    import db
    from auth import login_required, verificar_credenciais
    import produtos_db
    from importador import importar_produto
    from emails import (confirmacao_pedido, notificar_rastreio,
                        notificar_entregue, notificar_disputa_aberta,
                        notificar_cancelamento, recuperar_carrinho)
    from rastreio import registrar_rastreio, consultar_status, verificar_todos_pedidos
    from disputas import (abrir_disputa, listar_disputas,
                          atualizar_disputa, aprovar_reembolso,
                          verificar_reembolsos_automaticos)
    import usuarios
    import cupons
    import avaliacoes
    from emails import pedir_avaliacao
    import config_site

load_dotenv()

app = Flask(__name__, static_folder="../site", static_url_path="")

# Atrás do proxy do Railway/Heroku: confia em X-Forwarded-Proto/Host para que
# request.is_secure, cookies "secure" e HSTS funcionem corretamente.
try:
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
except Exception:
    pass

# CORS: por padrão restringe à própria loja (LOJA_URL). Defina CORS_ORIGINS
# (separado por vírgula) para liberar domínios extras. "*" libera geral (dev).
_cors_origins = os.getenv("CORS_ORIGINS", "").strip()
if _cors_origins == "*":
    CORS(app, supports_credentials=True)
else:
    _origins = [o.strip() for o in _cors_origins.split(",") if o.strip()]
    _loja = os.getenv("LOJA_URL", "").strip()
    if _loja:
        _origins.append(_loja)
    # Extensao do Chrome (importador AliExpress) chama a API de origem
    # chrome-extension://<id> — libera esse esquema sempre.
    _origins.append(re.compile(r"^chrome-extension://"))
    CORS(app, supports_credentials=True, origins=_origins or "*")


# ── Content-Security-Policy ───────────────────────────────────────────────────
# Reflete os terceiros realmente usados (Google Fonts, Unsplash/alicdn, Mercado
# Pago, pixels Meta/TikTok/Google, ViaCEP, 17track). Por padrão vai em modo
# REPORT-ONLY: o navegador apenas registra violações no console, sem bloquear nada
# (não quebra pixels nem scripts inline). Defina CSP_ENFORCE=1 para passar a aplicar
# de fato, e CSP_REPORT_URI para receber os relatórios de violação.
_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https:; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "img-src 'self' data: blob: https:; "
    "font-src 'self' https://fonts.gstatic.com data:; "
    "connect-src 'self' https:; "
    "frame-src 'self' https:; "
    "media-src 'self' https:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self' https:; "
    "frame-ancestors 'self'"
)
_CSP_ENFORCE   = os.getenv("CSP_ENFORCE", "") == "1"
_CSP_REPORT_URI = os.getenv("CSP_REPORT_URI", "").strip()
if _CSP_REPORT_URI:
    _CSP += f"; report-uri {_CSP_REPORT_URI}"
_CSP_HEADER = "Content-Security-Policy" if _CSP_ENFORCE else "Content-Security-Policy-Report-Only"


@app.after_request
def _aplicar_headers_seguranca(resp):
    """Cabeçalhos de segurança aplicados a todas as respostas."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
    resp.headers.setdefault(_CSP_HEADER, _CSP)
    # HSTS só quando servido por HTTPS (evita travar o dev local em http)
    if request.is_secure:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    return resp


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
# (a rota "/" é tratada em oauth_aliexpress, que serve o index ou captura o code)


# ── CARRINHO ──────────────────────────────────────────────────────────────────

# ── CRIAR PREFERÊNCIA DE PAGAMENTO (MERCADO PAGO) ────────────────────────────

@app.route("/api/cupom/validar", methods=["POST"])
def validar_cupom():
    d = request.json or {}
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = usuarios.verificar_token(token) if token else None
    email = (user or {}).get("email", "") or d.get("email", "")
    return jsonify(cupons.validar(d.get("codigo", ""), float(d.get("subtotal", 0)),
                                  email=email, logado=bool(user)))


# ── ADMIN CUPONS ───────────────────────────────────────────────────────────────

@app.route("/api/admin/cupons", methods=["GET"])
@login_required
def admin_listar_cupons():
    return jsonify({"ok": True, "cupons": cupons.listar()})

@app.route("/api/admin/cupons", methods=["POST"])
@login_required
def admin_salvar_cupom():
    try:
        c = cupons.salvar(request.json or {})
        return jsonify({"ok": True, "cupom": c})
    except ValueError as e:
        return jsonify({"ok": False, "erro": str(e)}), 400

@app.route("/api/admin/cupons/<codigo>", methods=["DELETE"])
@login_required
def admin_deletar_cupom(codigo):
    return jsonify({"ok": cupons.deletar(codigo)})


def _cpf_valido(cpf: str) -> bool:
    """Valida CPF com os dígitos verificadores (algoritmo oficial)."""
    cpf = re.sub(r"\D", "", cpf or "")
    if len(cpf) != 11 or cpf == cpf[0] * 11:
        return False
    for i in (9, 10):
        soma = sum(int(cpf[n]) * ((i + 1) - n) for n in range(i))
        dig = (soma * 10) % 11
        dig = 0 if dig == 10 else dig
        if dig != int(cpf[i]):
            return False
    return True


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

    # ── CPF obrigatório e válido (exigido pelo AliExpress p/ envio ao Brasil) ──
    if not _cpf_valido(cliente.get("cpf", "")):
        return jsonify({"ok": False, "erro": "CPF inválido. Verifique os números informados.", "campo": "cpf"}), 400

    # ── ANTI-FRAUDE: revalida preços contra o banco ───────────────────────────
    catalogo = {p["id"]: p for p in produtos_db.listar()}
    for item in itens:
        prod = catalogo.get(item.get("id"))
        if prod:
            # Preço base do produto
            preco = prod["preco_venda"]
            # Preço por combinação (SKU capturado do AliExpress)
            sku_preco = 0
            sku_attr = item.get("sku_attr", "")
            if sku_attr:
                for s in prod.get("skus", []):
                    if s.get("sku_attr") == sku_attr and s.get("preco_venda", 0) > 0:
                        sku_preco = s["preco_venda"]
                        break
            # Preço por opção definido pelo lojista (tem prioridade)
            opt_preco = 0
            sel = item.get("sel_labels", []) or []
            for v in prod.get("variantes", []):
                for o in (v.get("opcoes") or []):
                    if isinstance(o, dict) and o.get("label") in sel and o.get("preco_venda", 0) > 0:
                        opt_preco = max(opt_preco, o["preco_venda"])
            if opt_preco > 0:
                preco = opt_preco
            elif sku_preco > 0:
                preco = sku_preco
            item["preco_venda"]     = preco   # força o preço real do servidor
            item["titulo"]          = prod.get("titulo", item.get("titulo"))
            item["link_aliexpress"] = prod.get("link_aliexpress", "")

    subtotal = sum(i["preco_venda"] * i.get("quantidade", 1) for i in itens)

    # ── CUPOM ──────────────────────────────────────────────────────────────────
    desconto = 0
    cupom_aplicado = None
    _tok = request.headers.get("Authorization", "").replace("Bearer ", "")
    _user = usuarios.verificar_token(_tok) if _tok else None
    if cupom_codigo:
        res = cupons.validar(cupom_codigo, subtotal,
                             email=cliente.get("email", ""), logado=bool(_user))
        if res.get("ok"):
            desconto = res["desconto"]
            cupom_aplicado = res["codigo"]

    # ── CASHBACK ────────────────────────────────────────────────────────────────
    cashback_usado = 0
    usar_cashback = float(body.get("cashback", 0) or 0)
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user_data = usuarios.verificar_token(token) if token else None
    if usar_cashback > 0 and user_data:
        cfg = config_site.obter()
        saldo = usuarios.obter_saldo(user_data["email"])
        max_uso = (subtotal - desconto) * float(cfg.get("cashback_uso_max_pct", 30)) / 100
        cashback_usado = min(usar_cashback, saldo, max_uso)
        cashback_usado = round(cashback_usado, 2)
        if cashback_usado > 0:
            usuarios.usar_cashback(user_data["email"], cashback_usado)

    desconto_total = desconto + cashback_usado
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
    # Desconto (cupom) entra como item negativo
    if desconto > 0:
        mp_items.append({
            "id": "desconto", "title": f"Desconto ({cupom_aplicado})",
            "quantity": 1, "unit_price": -float(desconto), "currency_id": "BRL",
        })
    # Cashback entra como item negativo
    if cashback_usado > 0:
        mp_items.append({
            "id": "cashback", "title": "Cashback aplicado",
            "quantity": 1, "unit_price": -float(cashback_usado), "currency_id": "BRL",
        })

    # Pagador completo (CPF + nome) — necessário p/ liberar o Pix no MP
    _nome_partes = (cliente.get("nome", "") or "").strip().split(" ", 1)
    _cpf_num = re.sub(r"\D", "", cliente.get("cpf", ""))
    _end = cliente.get("endereco", {}) or {}
    preference = {
        "external_reference": pedido_id,
        "items": mp_items,
        "payer": {
            "name":    _nome_partes[0],
            "surname": _nome_partes[1] if len(_nome_partes) > 1 else "",
            "email":   cliente.get("email", ""),
            "identification": {"type": "CPF", "number": _cpf_num},
            "phone": {"number": re.sub(r"\D", "", cliente.get("telefone", ""))},
            "address": {
                "zip_code":     re.sub(r"\D", "", _end.get("cep", "")),
                "street_name":  _end.get("rua", ""),
                "street_number": _end.get("numero", ""),
            },
        },
        "back_urls": {
            "success": f"{LOJA_URL}/pedido-confirmado.html?id={pedido_id}",
            "failure": f"{LOJA_URL}/?checkout=falhou",
            "pending": f"{LOJA_URL}/pedido-confirmado.html?id={pedido_id}",
        },
        "notification_url":   f"{LOJA_URL}/api/webhook/mercadopago",
        "statement_descriptor": (config_site.obter().get("loja_nome", "LOJA")[:22]).upper(),
        "expires":             False,
    }

    # ── FILTRA o método escolhido (Pix / Cartão / Boleto) ──────────────────────
    metodo = body.get("metodo_pagamento", "")
    todos = ["credit_card", "debit_card", "ticket", "bank_transfer", "atm", "prepaid_card"]
    permitidos = {
        "pix":    ["bank_transfer", "account_money"],
        "cartao": ["credit_card", "debit_card"],
        "boleto": ["ticket"],
    }.get(metodo)
    if permitidos:
        preference["payment_methods"] = {
            "excluded_payment_types": [{"id": t} for t in todos if t not in permitidos],
            "installments": 12,
        }

    # auto_return só é aceito pelo MP com URL pública https
    if LOJA_URL.startswith("https://"):
        preference["auto_return"] = "approved"

    headers = {**MP_HEADERS, "X-Idempotency-Key": pedido_id}
    resp = httpx.post(
        "https://api.mercadopago.com/checkout/preferences",
        json=preference,
        headers=headers,
        timeout=15,
    )

    if resp.status_code != 201:
        # Retorna o erro detalhado do MP para diagnóstico
        try:
            err = resp.json()
            msg = err.get("message") or err.get("error") or resp.text
        except Exception:
            msg = resp.text
        print(f"[MP] Erro {resp.status_code}: {msg}")
        return jsonify({"ok": False, "erro": f"Mercado Pago: {msg}"}), 400

    mp_data = resp.json()

    # Salva pedido no banco — VINCULADO à conta do usuário
    pedido = {
        "id":           pedido_id,
        "mp_id":        mp_data.get("id"),
        "status":       "aguardando_pagamento",
        "criado_em":    datetime.now().isoformat(),
        "usuario_email": (_user or {}).get("email", "") or cliente.get("email", ""),
        "cliente":      cliente,
        "itens":        itens,
        "subtotal":     round(subtotal, 2),
        "desconto":     desconto,
        "cupom":        cupom_aplicado,
        "cashback_usado": cashback_usado,
        "total":        round(subtotal - desconto_total, 2),
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

    # ── FULFILLMENT AUTOMÁTICO via AliExpress DS API ──────────────────────────
    try:
        from servidor import fornecedor
    except ImportError:
        import fornecedor
    try:
        res = fornecedor.criar_pedido_automatico(pedido)
        if res.get("ok"):
            pedido["status"] = "comprado_aliexpress"
            pedido["ali_order_id"] = res.get("ali_order_id", "")
            pedido["fulfillment"] = "automatico"
            pedido["comprado_em"] = datetime.now().isoformat()
            print(f"[FULFILLMENT] AUTOMÁTICO ✓ Pedido {pedido['id']} -> AliExpress {res.get('ali_order_id')}")
        else:
            pedido["fulfillment"] = "manual"
            pedido["fulfillment_motivo"] = res.get("motivo", "")
            print(f"[FULFILLMENT] Manual: {res.get('motivo')}")
    except Exception as e:
        pedido["fulfillment"] = "manual"
        print(f"[FULFILLMENT] Erro: {e}")

    # ── INDIQUE E GANHE: credita na 1ª compra aprovada (idempotente) ──────────
    try:
        cfg_ind = config_site.obter()
        if cfg_ind.get("indicacao_ativa", True):
            email_comprador = (pedido.get("usuario_email")
                               or pedido.get("cliente", {}).get("email", "")).lower()
            if email_comprador and usuarios.obter(email_comprador):
                res_ind = usuarios.creditar_indicacao(email_comprador, float(cfg_ind.get("indicacao_valor", 15)))
                if res_ind.get("ok"):
                    print(f"[INDICACAO] Creditado R$ {res_ind['valor']} p/ {email_comprador} e {res_ind['indicador']}")
    except Exception as e:
        print(f"[INDICACAO] erro: {e}")

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
    # Cashback se o comprador tiver conta cadastrada
    resp = {"ok": True, "avaliacao": av}
    pedido = _carregar_pedido(pedido_id)
    email = (pedido or {}).get("cliente", {}).get("email", "")
    cfg = config_site.obter()
    if email and cfg.get("cashback_ativo") and usuarios.obter(email) \
       and not usuarios.ja_avaliou_produto(email, produto_id):
        valor = float(cfg.get("cashback_por_avaliacao", 5))
        usuarios.adicionar_cashback(email, valor, "Avaliação pós-compra")
        usuarios.marcar_avaliou(email, produto_id)
        resp["cashback_ganho"] = valor
    return jsonify(resp), 201

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

@app.route("/api/admin/avaliacoes/remover-duplicadas", methods=["POST"])
@login_required
def admin_remover_duplicadas():
    n = avaliacoes.remover_duplicadas()
    return jsonify({"ok": True, "removidas": n})

@app.route("/api/admin/avaliacoes/lote", methods=["POST"])
@login_required
def admin_avaliacoes_lote():
    d = request.json or {}
    n = avaliacoes.acao_em_lote(d.get("ids", []), d.get("acao", ""))
    return jsonify({"ok": True, "afetadas": n})

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
    if _login_bloqueado("conta"):
        return jsonify({"ok": False, "erro": "Muitas tentativas. Aguarde alguns minutos."}), 429
    d = request.json or {}
    r = usuarios.login(d.get("email",""), d.get("senha",""))
    if r.get("ok"):
        _limpar_falhas_login("conta")
    else:
        _registrar_falha_login("conta")
    return jsonify(r), (200 if r.get("ok") else 401)

@app.route("/api/conta/me", methods=["GET"])
def conta_me():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    dados = usuarios.verificar_token(token)
    if not dados:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401
    u = usuarios.obter(dados["email"])
    return jsonify({"ok": True, "usuario": u})

@app.route("/api/conta/pedido/<pid>/cancelar", methods=["POST"])
def conta_cancelar_pedido(pid):
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    dados = usuarios.verificar_token(token)
    if not dados:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401
    pedido = _carregar_pedido(pid.upper())
    if not pedido:
        return jsonify({"ok": False, "erro": "Pedido não encontrado"}), 404
    # Só o dono pode cancelar, e só se ainda não pagou
    dono = (pedido.get("usuario_email", "") or pedido.get("cliente", {}).get("email", "")).lower()
    if dono != dados["email"].lower():
        return jsonify({"ok": False, "erro": "Sem permissão"}), 403
    if pedido.get("status") not in ("aguardando_pagamento", "pending"):
        return jsonify({"ok": False, "erro": "Este pedido não pode ser cancelado"}), 400
    pedido["status"] = "cancelado"
    pedido["cancelado_em"] = datetime.now().isoformat()
    _salvar_pedido(pedido)
    return jsonify({"ok": True})


def _user_do_token():
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    return usuarios.verificar_token(token)

@app.route("/api/conta/enderecos", methods=["GET"])
def conta_listar_enderecos():
    d = _user_do_token()
    if not d:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401
    return jsonify({"ok": True, "enderecos": usuarios.listar_enderecos(d["email"])})

@app.route("/api/conta/enderecos", methods=["POST"])
def conta_add_endereco():
    d = _user_do_token()
    if not d:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401
    novo = usuarios.adicionar_endereco(d["email"], request.json or {})
    return jsonify({"ok": bool(novo), "endereco": novo}), (201 if novo else 400)

@app.route("/api/conta/enderecos/<eid>", methods=["DELETE"])
def conta_del_endereco(eid):
    d = _user_do_token()
    if not d:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401
    return jsonify({"ok": usuarios.remover_endereco(d["email"], eid)})

@app.route("/api/conta/enderecos/<eid>/principal", methods=["POST"])
def conta_principal_endereco(eid):
    d = _user_do_token()
    if not d:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401
    return jsonify({"ok": usuarios.definir_endereco_principal(d["email"], eid)})


@app.route("/api/conta/indicacao", methods=["GET"])
def conta_indicacao():
    d = _user_do_token()
    if not d:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401
    cfg = config_site.obter()
    if not cfg.get("indicacao_ativa", True):
        return jsonify({"ok": True, "ativo": False})
    codigo = usuarios.obter_ou_criar_ref(d["email"])
    u = usuarios.obter(d["email"]) or {}
    # conta quantos amigos foram creditados a partir deste indicador
    indicados = len([x for x in db.listar("usuarios")
                     if (x.get("indicado_por", "") or "").lower() == d["email"].lower()
                     and x.get("indicacao_creditada")])
    return jsonify({
        "ok": True, "ativo": True,
        "codigo": codigo,
        "link": f"{LOJA_URL}/?ref={codigo}",
        "valor": float(cfg.get("indicacao_valor", 15)),
        "indicados": indicados,
        "saldo": usuarios.obter_saldo(d["email"]),
    })

@app.route("/api/indicacao/registrar", methods=["POST"])
def indicacao_registrar():
    d = _user_do_token()
    if not d:
        return jsonify({"ok": False, "erro": "Não autenticado"}), 401
    ref = (request.json or {}).get("ref", "")
    ok = usuarios.registrar_indicacao(d["email"], ref)
    return jsonify({"ok": ok})


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

# ── Anti-brute-force (limitador leve, em memória por worker) ──────────────────
from collections import defaultdict
import time as _time

_tentativas_login = defaultdict(list)   # ip -> [timestamps de falhas]
_LOGIN_JANELA_SEG  = 900   # 15 min
_LOGIN_MAX_FALHAS  = 10    # falhas permitidas na janela


def _ip_cliente() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "?"


def _login_bloqueado(escopo: str) -> bool:
    chave = f"{escopo}:{_ip_cliente()}"
    agora = _time.time()
    _tentativas_login[chave] = [t for t in _tentativas_login[chave] if agora - t < _LOGIN_JANELA_SEG]
    return len(_tentativas_login[chave]) >= _LOGIN_MAX_FALHAS


def _registrar_falha_login(escopo: str):
    _tentativas_login[f"{escopo}:{_ip_cliente()}"].append(_time.time())


def _limpar_falhas_login(escopo: str):
    _tentativas_login.pop(f"{escopo}:{_ip_cliente()}", None)


@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    if _login_bloqueado("admin"):
        return jsonify({"ok": False, "erro": "Muitas tentativas. Aguarde alguns minutos."}), 429
    data = request.json or {}
    token = verificar_credenciais(data.get("user",""), data.get("senha",""))
    if not token:
        _registrar_falha_login("admin")
        return jsonify({"ok": False, "erro": "Usuário ou senha incorretos"}), 401
    _limpar_falhas_login("admin")
    resp = make_response(jsonify({"ok": True, "token": token}))
    resp.set_cookie("admin_token", token, httponly=True, samesite="Lax",
                    secure=request.is_secure, max_age=86400)
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

@app.route("/api/admin/gerar-descricao", methods=["POST"])
@login_required
def admin_gerar_descricao():
    """Agente de IA: gera headline + descrição usando só os dados do anúncio."""
    try:
        from servidor import descricao_ia
    except ImportError:
        import descricao_ia
    d = request.json or {}
    try:
        res = descricao_ia.gerar(d.get("titulo", ""), d.get("especificacoes", []), d.get("descricao", ""))
        return jsonify({"ok": True, **res})
    except Exception as e:
        return jsonify({"ok": False, "erro": str(e)}), 500

@app.route("/api/admin/produtos", methods=["POST"])
@login_required
def admin_criar_produto():
    dados = request.json or {}
    # Anti-duplicação: se já existe produto com o mesmo link AliExpress, atualiza
    link = (dados.get("link_aliexpress") or "").split("?")[0]
    if link:
        for p in produtos_db.listar():
            if (p.get("link_aliexpress") or "").split("?")[0] == link:
                atualizado = produtos_db.atualizar(p["id"], dados)
                return jsonify({"ok": True, "produto": atualizado, "duplicado": True}), 200
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

@app.route("/api/admin/factory-reset", methods=["POST"])
@login_required
def admin_factory_reset():
    """
    RESET DE FÁBRICA — apaga produtos, pedidos, avaliações, disputas, usuários,
    endereços, cupons e marcadores. MANTÉM config do site e token AliExpress.
    Comando oculto: rode factoryReset() no console do painel admin.
    """
    confirmacao = (request.json or {}).get("confirmacao", "")
    if confirmacao != "ZERAR TUDO":
        return jsonify({"ok": False, "erro": "Confirmação inválida"}), 400

    resultado = {}
    for col in ["produtos", "pedidos", "avaliacoes", "disputas", "usuarios", "cupons"]:
        resultado[col] = db.apagar_colecao(col)

    # Remove marcadores de seed (mas mantém config_site e aliexpress_token)
    for chave in ["produtos_semeados", "avaliacoes_semeadas", "cupons_semeados", "scheduler_lock"]:
        db.deletar("_sistema", chave)

    print(f"[RESET] Reset de fábrica executado: {resultado}")
    return jsonify({"ok": True, "removidos": resultado})


@app.route("/api/admin/limpar-tudo", methods=["POST"])
@login_required
def admin_limpar_tudo():
    """Apaga TODOS os produtos e avaliações (limpeza geral)."""
    n_prod = 0
    for p in produtos_db.listar():
        if produtos_db.deletar(p["id"]):
            n_prod += 1
    n_av = 0
    for a in avaliacoes.listar_todas():
        if avaliacoes.deletar(a["id"]):
            n_av += 1
    return jsonify({"ok": True, "produtos_removidos": n_prod, "avaliacoes_removidas": n_av})

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


@app.route("/api/admin/pedidos/<pedido_id>/cancelar", methods=["POST"])
@login_required
def admin_cancelar_pedido(pedido_id):
    """
    Cancelamento completo (anti-erro): reembolsa o cliente no Mercado Pago,
    tenta cancelar o pedido no AliExpress, marca como cancelado e avisa o cliente.
    Retorna um relatório por etapa — nada trava se uma das etapas falhar.
    """
    pedido = _carregar_pedido(pedido_id.upper())
    if not pedido:
        return jsonify({"ok": False, "erro": "Pedido não encontrado"}), 404

    if pedido.get("status") == "cancelado":
        return jsonify({"ok": True, "msg": "Pedido já estava cancelado",
                        "etapas": pedido.get("cancelamento", {})})

    motivo = (request.json or {}).get("motivo", "").strip()
    etapas = {"reembolso": "—", "aliexpress": "—", "email": "—"}

    # 1) Reembolso no Mercado Pago (se houve pagamento e ainda não foi reembolsado)
    payment_id = pedido.get("payment_id")
    if pedido.get("status") == "reembolsado":
        etapas["reembolso"] = "Já havia sido reembolsado"
    elif payment_id:
        try:
            r = httpx.post(
                f"https://api.mercadopago.com/v1/payments/{payment_id}/refunds",
                headers={**MP_HEADERS, "X-Idempotency-Key": f"cancel-{pedido_id}"},
                json={}, timeout=15,
            )
            if r.status_code in (200, 201):
                etapas["reembolso"] = "✓ Reembolso solicitado ao Mercado Pago"
            else:
                etapas["reembolso"] = f"⚠️ Falhou no MP: {r.text[:160]}"
        except Exception as e:
            etapas["reembolso"] = f"⚠️ Erro ao reembolsar: {e}"
    else:
        etapas["reembolso"] = "Sem pagamento registrado (nada a reembolsar)"

    # 2) Cancelamento no AliExpress (se o pedido já foi enviado para lá)
    ali_id = pedido.get("ali_order_id")
    if ali_id:
        try:
            try:
                from servidor import fornecedor
            except ImportError:
                import fornecedor
            res = fornecedor.cancelar_pedido_aliexpress(ali_id)
            etapas["aliexpress"] = ("✓ Cancelado no AliExpress" if res.get("ok")
                                    else f"⚠️ {res.get('motivo', 'cancele manualmente no painel do AliExpress')}")
        except Exception as e:
            etapas["aliexpress"] = f"⚠️ Erro: {e} — cancele manualmente no painel do AliExpress"
    else:
        etapas["aliexpress"] = "Compra não foi enviada ao AliExpress (nada a cancelar lá)"

    # 3) Atualiza o pedido
    reembolsou = etapas["reembolso"].startswith("✓") or "Já havia" in etapas["reembolso"]
    pedido["status"]        = "cancelado"
    pedido["cancelado_em"]  = datetime.now().isoformat()
    pedido["cancel_motivo"] = motivo
    pedido["cancelamento"]  = etapas
    _salvar_pedido(pedido)

    # 4) Avisa o cliente
    try:
        ok = notificar_cancelamento(pedido, motivo, reembolsado=bool(payment_id))
        etapas["email"] = "✓ Cliente avisado por e-mail" if ok else "⚠️ E-mail não enviado (verifique RESEND_API_KEY)"
    except Exception as e:
        etapas["email"] = f"⚠️ Erro no e-mail: {e}"

    pedido["cancelamento"] = etapas
    _salvar_pedido(pedido)
    print(f"[CANCELAMENTO] Pedido {pedido_id} cancelado · {etapas}")
    return jsonify({"ok": True, "etapas": etapas})


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

def _job_token_aliexpress():
    """Renova o access_token do AliExpress proativamente (1x por dia)."""
    while True:
        _time.sleep(24 * 3600)
        if not _sou_lider():
            continue
        try:
            from servidor import fornecedor
        except ImportError:
            import fornecedor
        try:
            fornecedor.get_token()  # dispara renovação se estiver perto de expirar
        except Exception as e:
            print(f"[ALI TOKEN] job: {e}")

threading.Thread(target=_job_rastreio,            daemon=True).start()
threading.Thread(target=_job_reembolsos,          daemon=True).start()
threading.Thread(target=_job_carrinho_abandonado, daemon=True).start()
threading.Thread(target=_job_token_aliexpress,    daemon=True).start()


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

    # ── LUCRO LÍQUIDO REAL ────────────────────────────────────────────────────
    cfg = config_site.obter()
    taxa_mp_pct = float(cfg.get("taxa_mp_pct", 4.99) or 0)
    trafego_pad = float(cfg.get("custo_trafego_pedido", 10) or 0)

    # mapa id -> custo em BRL (do catálogo atual)
    custo_por_id = {}
    for p in produtos_db.listar():
        c = p.get("custo_brl")
        if not c and p.get("preco_usd"):
            c = float(p["preco_usd"]) * float(cfg.get("custo_cambio", 5.70)) * 1.05
        custo_por_id[str(p.get("id"))] = float(c or 0)

    custo_produtos = 0.0
    for p in aprovados:
        for item in p.get("itens", []):
            qtd = item.get("quantidade", 1) or 1
            custo = custo_por_id.get(str(item.get("id")), 0)
            if not custo:   # produto saiu do catálogo: estima 35% do preço de venda
                custo = float(item.get("preco_venda", 0)) * 0.35
            custo_produtos += custo * qtd

    taxa_mp   = receita * taxa_mp_pct / 100
    # gasto com anúncio: valor informado pelo admin (total) ou estimativa por pedido
    gasto_reg = db.get("_sistema", "gasto_anuncios") or {}
    gasto_anuncios = float(gasto_reg.get("total", 0) or 0)
    if not gasto_anuncios:
        gasto_anuncios = trafego_pad * len(aprovados)   # estimativa
        gasto_estimado = True
    else:
        gasto_estimado = False

    lucro_liquido = receita - custo_produtos - taxa_mp - gasto_anuncios

    return jsonify({
        "ok": True,
        "total_pedidos":   len(pedidos),
        "pedidos_aprovados": len(aprovados),
        "receita_total":   round(receita, 2),
        "ticket_medio":    round(ticket_med, 2),
        "melhor_hora":     melhor_hora,
        "vendas_por_hora": vendas_hora,
        "produtos_ativos": len([p for p in produtos_db.listar() if p.get("ativo")]),
        # lucro real
        "custo_produtos":  round(custo_produtos, 2),
        "taxa_mp":         round(taxa_mp, 2),
        "gasto_anuncios":  round(gasto_anuncios, 2),
        "gasto_estimado":  gasto_estimado,
        "lucro_liquido":   round(lucro_liquido, 2),
        "margem_liquida":  round(lucro_liquido / receita * 100, 1) if receita else 0,
    })


@app.route("/api/admin/gasto-anuncios", methods=["POST"])
@login_required
def admin_set_gasto_anuncios():
    """Admin informa quanto gastou com anúncios (total acumulado)."""
    valor = float((request.json or {}).get("total", 0) or 0)
    db.put("_sistema", "gasto_anuncios", {"total": valor,
                                          "atualizado_em": datetime.now().isoformat()})
    return jsonify({"ok": True, "total": valor})


# ── PRODUTOS PÚBLICO ───────────────────────────────────────────────────────────

@app.route("/")
@app.route("/oauth/aliexpress/callback")
def oauth_aliexpress():
    """
    Recebe o code do AliExpress e troca pelo access_token automaticamente.
    Se não houver code, serve a loja normalmente.
    """
    code = request.args.get("code", "")
    if not code:
        return send_from_directory(app.static_folder, "index.html")
    try:
        from servidor import fornecedor
    except ImportError:
        import fornecedor
    res = fornecedor.trocar_code_por_token(code)
    if res.get("ok"):
        return f"""<html><body style="font-family:sans-serif;text-align:center;padding:60px;background:#f0fdf4;">
        <h1 style="color:#16a34a;">✅ AliExpress conectado!</h1>
        <p>Conta: <strong>{res.get('account','')}</strong></p>
        <p>A automação de pedidos está ATIVA. Agora cada venda paga é comprada
        automaticamente no AliExpress com o endereço do cliente.</p>
        <a href="/admin-panel/" style="background:#2563eb;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;">Ir para o painel</a>
        </body></html>"""
    return f"""<html><body style="font-family:sans-serif;text-align:center;padding:60px;">
        <h1 style="color:#dc2626;">Erro ao conectar</h1>
        <p>{res.get('erro','')}</p>
        <p>Verifique se ALIEXPRESS_APP_KEY e ALIEXPRESS_APP_SECRET estão no Railway.</p>
        </body></html>""", 400


@app.route("/api/admin/aliexpress/status", methods=["GET"])
@login_required
def admin_aliexpress_status():
    try:
        from servidor import fornecedor
    except ImportError:
        import fornecedor
    return jsonify({"ok": True, "ativo": fornecedor.automacao_ativa(),
                    "token": bool(fornecedor.get_token())})


@app.route("/api/status-db", methods=["GET"])
def status_db():
    """Diagnóstico: mostra qual banco está ativo (para checar o Postgres)."""
    return jsonify(db.status())

@app.route("/api/config", methods=["GET"])
def get_config():
    """Configurações públicas do site (lidas pelo front)."""
    return jsonify({"ok": True, "config": config_site.obter()})


# ── LEADS (captura de e-mail pelo popup) ───────────────────────────────────────

@app.route("/api/lead", methods=["POST"])
def capturar_lead():
    """Salva e-mail capturado no popup (para remarketing)."""
    d = request.json or {}
    email = (d.get("email") or "").strip().lower()
    if "@" not in email or "." not in email:
        return jsonify({"ok": False, "erro": "E-mail inválido"}), 400
    existente = db.get("leads", email)
    if not existente:
        db.put("leads", email, {
            "email":     email,
            "origem":    d.get("origem", "popup"),
            "criado_em": datetime.now().isoformat(),
        })
    return jsonify({"ok": True})

@app.route("/api/admin/leads", methods=["GET"])
@login_required
def admin_listar_leads():
    leads = sorted(db.listar("leads"), key=lambda x: x.get("criado_em", ""), reverse=True)
    return jsonify({"ok": True, "leads": leads, "total": len(leads)})


# ── USUÁRIOS (visão completa no admin) ─────────────────────────────────────────

@app.route("/api/admin/usuarios", methods=["GET"])
@login_required
def admin_listar_usuarios():
    """Lista completa de clientes com dados agregados (sem expor senha)."""
    todos   = db.listar("usuarios")
    pedidos = _listar_pedidos()
    aprovados_status = ("approved", "pagamento_aprovado", "comprado_aliexpress", "entregue")

    # pré-agrupa pedidos por e-mail do cliente
    por_email = {}
    for p in pedidos:
        em = (p.get("usuario_email") or p.get("cliente", {}).get("email", "") or "").lower()
        if em:
            por_email.setdefault(em, []).append(p)

    lista = []
    for u in todos:
        email = (u.get("email") or "").lower()
        peds  = por_email.get(email, [])
        aprov = [p for p in peds if p.get("status") in aprovados_status]
        gasto = sum(p.get("total", 0) for p in aprov)
        lista.append({
            "nome":        u.get("nome", ""),
            "email":       email,
            "criado_em":   u.get("criado_em", ""),
            "cashback":    round(u.get("cashback", 0), 2),
            "enderecos":   len(u.get("enderecos", []) or ([u["endereco"]] if u.get("endereco") else [])),
            "telefone":    (u.get("endereco", {}) or {}).get("telefone", ""),
            "ref_codigo":  u.get("ref_codigo", ""),
            "indicado_por": u.get("indicado_por", ""),
            "indicacao_creditada": bool(u.get("indicacao_creditada")),
            "total_pedidos":   len(peds),
            "pedidos_aprovados": len(aprov),
            "total_gasto":     round(gasto, 2),
            "ultimo_pedido":   max((p.get("criado_em", "") for p in peds), default=""),
        })

    # ordena por mais recente cadastro
    lista.sort(key=lambda x: x.get("criado_em", ""), reverse=True)

    # quantos clientes cada indicador trouxe (creditados)
    indicacoes = {}
    for u in lista:
        ind = (u["indicado_por"] or "").lower()
        if ind and u["indicacao_creditada"]:
            indicacoes[ind] = indicacoes.get(ind, 0) + 1
    for u in lista:
        u["indicou"] = indicacoes.get(u["email"], 0)

    resumo = {
        "total":          len(lista),
        "com_compra":     len([u for u in lista if u["pedidos_aprovados"] > 0]),
        "receita_total":  round(sum(u["total_gasto"] for u in lista), 2),
        "cashback_total": round(sum(u["cashback"] for u in lista), 2),
    }
    return jsonify({"ok": True, "usuarios": lista, "resumo": resumo})


@app.route("/api/admin/usuarios/<email>", methods=["GET"])
@login_required
def admin_detalhe_usuario(email):
    """Detalhe de um cliente: dados, endereços, cashback e histórico de pedidos."""
    email = email.lower()
    u = db.get("usuarios", email)
    if not u:
        return jsonify({"ok": False, "erro": "Cliente não encontrado"}), 404
    u = {**u}
    u.pop("senha_hash", None)
    peds = [p for p in _listar_pedidos()
            if (p.get("usuario_email") or p.get("cliente", {}).get("email", "") or "").lower() == email]
    peds.sort(key=lambda p: p.get("criado_em", ""), reverse=True)
    return jsonify({"ok": True, "usuario": u, "pedidos": peds})

@app.route("/api/admin/usuarios/<email>", methods=["DELETE"])
@login_required
def admin_deletar_usuario(email):
    """Exclui a conta de um cliente (os pedidos dele permanecem no histórico)."""
    ok = usuarios.deletar(email)
    return jsonify({"ok": ok, "erro": None if ok else "Cliente não encontrado"})

@app.route("/api/admin/config", methods=["GET"])
@login_required
def admin_get_config():
    return jsonify({"ok": True, "config": config_site.obter()})

@app.route("/api/admin/config", methods=["POST"])
@login_required
def admin_salvar_config():
    novos = request.json or {}
    return jsonify({"ok": True, "config": config_site.salvar(novos)})


@app.route("/api/upsell", methods=["GET"])
def get_upsell():
    """Produtos marcados como sugestão para o carrinho/checkout."""
    sugeridos = [p for p in produtos_db.listar()
                 if p.get("ativo", True) and p.get("sugerir_carrinho")]
    return jsonify({"ok": True, "produtos": sugeridos[:6]})

@app.route("/api/produto/<pid>", methods=["GET"])
def get_produto_publico(pid):
    """Produto individual + avaliações para a página de detalhe."""
    p = produtos_db.obter(pid)
    if not p or not p.get("ativo", True):
        return jsonify({"ok": False, "erro": "Produto não encontrado"}), 404
    resumo = avaliacoes.media_produto(pid)
    if resumo["total"] > 0:
        p["avaliacao"] = resumo["media"]
    return jsonify({
        "ok": True,
        "produto": p,
        "avaliacoes": avaliacoes.listar_por_produto(pid),
        "resumo": resumo,
    })

@app.route("/api/avaliacao-publica", methods=["POST"])
def avaliacao_publica():
    """Avaliação pela página do produto — SÓ com conta criada. Dá cashback."""
    d = request.json or {}
    produto_id = d.get("produto_id", "")
    if not produto_id:
        return jsonify({"ok": False, "erro": "produto inválido"}), 400

    # Exige conta de comprador
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    dados_user = usuarios.verificar_token(token)
    if not dados_user:
        return jsonify({"ok": False, "erro": "Faça login para avaliar", "precisa_login": True}), 401

    email = dados_user["email"]
    user = usuarios.obter(email)
    d["nome"] = user.get("nome", "Cliente")  # nome real (mascarado na exibição)
    d["pedido_id"] = ""
    av = avaliacoes.criar(produto_id, d)
    avaliacoes.definir_aprovacao(av["id"], False)  # pendente de moderação

    # Cashback (uma vez por produto)
    resp = {"ok": True}
    cfg = config_site.obter()
    if cfg.get("cashback_ativo") and not usuarios.ja_avaliou_produto(email, produto_id):
        valor = float(cfg.get("cashback_por_avaliacao", 5))
        novo = usuarios.adicionar_cashback(email, valor, f"Avaliação do produto")
        usuarios.marcar_avaliou(email, produto_id)
        resp["cashback_ganho"] = valor
        resp["cashback_total"] = novo
    return jsonify(resp)


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

@app.route("/produto")
@app.route("/produto.html")
def produto_page():
    return send_from_directory(app.static_folder, "produto.html")

@app.route("/info")
@app.route("/info.html")
def info_page():
    return send_from_directory(app.static_folder, "info.html")

@app.route("/robots.txt")
def robots():
    txt = f"User-agent: *\nAllow: /\nDisallow: /admin-panel\nDisallow: /api/\nSitemap: {LOJA_URL}/sitemap.xml\n"
    return app.response_class(txt, mimetype="text/plain")

@app.route("/sitemap.xml")
def sitemap():
    urls = [f"{LOJA_URL}/", f"{LOJA_URL}/info?p=sobre", f"{LOJA_URL}/info?p=privacidade",
            f"{LOJA_URL}/info?p=termos", f"{LOJA_URL}/info?p=trocas", f"{LOJA_URL}/info?p=entrega",
            f"{LOJA_URL}/rastrear"]
    for p in produtos_db.listar():
        if p.get("ativo", True):
            urls.append(f"{LOJA_URL}/produto?id={p['id']}")
    xml = '<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
    xml += "".join(f"  <url><loc>{u}</loc></url>\n" for u in urls)
    xml += "</urlset>"
    return app.response_class(xml, mimetype="application/xml")

@app.route("/rastrear")
@app.route("/rastrear.html")
def rastrear_page():
    return send_from_directory(app.static_folder, "rastrear.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\nTechDrop Brasil - servidor rodando em http://localhost:{port}")
    print(f"   Site:  http://localhost:{port}/")
    print(f"   Admin: http://localhost:{port}/admin-panel/\n")
    app.run(host="0.0.0.0", port=port, debug=False)
