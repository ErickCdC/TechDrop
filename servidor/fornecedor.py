"""
Engine de fulfillment automático.
Quando o pagamento é aprovado, cria o pedido no fornecedor via API OFICIAL,
usando nome + endereço do cliente. Ativa sozinho quando as chaves existem.

Provedores suportados:
  - AliExpress Dropshipping API (oficial) — aliexpress.ds.order.create
  - Fallback: fila manual (sem quebrar nada)

NUNCA usa robô/scraping para comprar — só API oficial (regras da plataforma).
"""
import os
import re
import time
import hmac
import hashlib
import json
import httpx
from datetime import datetime

try:
    from servidor import db
except ImportError:
    import db

# ── Credenciais (defina no Railway) ────────────────────────────────────────────
ALI_DS_APP_KEY    = os.getenv("ALIEXPRESS_APP_KEY", "")
ALI_DS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET", "")

API_URL  = "https://api-sg.aliexpress.com/sync"
IOP_URL  = "https://api-sg.aliexpress.com/rest"


def _token_salvo() -> dict | None:
    return db.get("_sistema", "aliexpress_token")


def get_token() -> str:
    t = _token_salvo()
    return (t or {}).get("access_token", "") or os.getenv("ALIEXPRESS_DS_TOKEN", "")


def automacao_ativa() -> bool:
    return bool(ALI_DS_APP_KEY and ALI_DS_APP_SECRET and get_token())


# ── OAuth: troca o code pelo access_token (IOP gateway) ────────────────────────

def _iop_sign(api_path: str, params: dict, secret: str) -> str:
    base = api_path + "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest().upper()


def trocar_code_por_token(code: str) -> dict:
    """Troca o code do OAuth pelo access_token e salva no banco."""
    if not (ALI_DS_APP_KEY and ALI_DS_APP_SECRET):
        return {"ok": False, "erro": "Defina ALIEXPRESS_APP_KEY e ALIEXPRESS_APP_SECRET no Railway"}
    api_path = "/auth/token/create"
    params = {
        "app_key":     ALI_DS_APP_KEY,
        "timestamp":   str(int(time.time() * 1000)),
        "sign_method": "sha256",
        "code":        code,
    }
    params["sign"] = _iop_sign(api_path, params, ALI_DS_APP_SECRET)
    try:
        r = httpx.post(IOP_URL + api_path, data=params, timeout=20)
        data = r.json()
        token = data.get("access_token")
        if not token:
            return {"ok": False, "erro": f"AliExpress: {data}"}
        registro = {
            "access_token":  token,
            "refresh_token": data.get("refresh_token", ""),
            "expires_in":    data.get("expires_in", 0),
            "obtido_em":     datetime.now().isoformat(),
            "account":       data.get("account", ""),
        }
        db.put("_sistema", "aliexpress_token", registro)
        return {"ok": True, "account": registro["account"]}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def _sign(params: dict, secret: str) -> str:
    base = "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest().upper()


def _product_id(link: str) -> str:
    m = re.search(r"/item/(\d+)", link or "")
    return m.group(1) if m else ""


def _montar_endereco(cliente: dict) -> dict:
    e = cliente.get("endereco", {}) or {}
    return {
        "contact_person": cliente.get("nome", ""),
        "full_name":      cliente.get("nome", ""),
        "mobile_no":      re.sub(r"\D", "", cliente.get("telefone", "")),
        "phone_country":  "+55",
        "country":        "BR",
        "province":       e.get("estado", ""),
        "city":           e.get("cidade", ""),
        "address":        f"{e.get('rua','')}, {e.get('numero','')} {e.get('complemento','')}".strip(),
        "address2":       e.get("bairro", ""),
        "zip":            re.sub(r"\D", "", e.get("cep", "")),
        "cpf":            re.sub(r"\D", "", cliente.get("cpf", "")),  # exigido p/ Brasil
    }


def criar_pedido_automatico(pedido: dict) -> dict:
    """
    Tenta criar o pedido no AliExpress via API oficial.
    Retorna {ok, ali_order_id|erro, automatico}.
    Se a API não estiver configurada, devolve {ok:False, manual:True}.
    """
    if not automacao_ativa():
        return {"ok": False, "manual": True,
                "motivo": "API AliExpress DS ainda não configurada (aguardando aprovação)"}

    cliente  = pedido.get("cliente", {})
    endereco = _montar_endereco(cliente)
    if not endereco["cpf"]:
        return {"ok": False, "manual": True, "motivo": "CPF obrigatório para envio ao Brasil"}

    # Monta os itens (cada produto precisa do product_id do AliExpress)
    product_items = []
    for item in pedido.get("itens", []):
        pid = _product_id(item.get("link_aliexpress", ""))
        if not pid:
            return {"ok": False, "manual": True,
                    "motivo": f"Produto '{item.get('titulo')}' sem link AliExpress válido"}
        product_items.append({
            "product_id":       pid,
            "product_count":    item.get("quantidade", 1),
            "sku_attr":         item.get("sku_attr", ""),   # variante (cor/tam) se houver
            "logistics_service_name": "",
            "order_memo":       "Pedido via loja",
        })

    param_order = {
        "logistics_address": endereco,
        "product_items":     product_items,
    }

    params = {
        "method":      "aliexpress.ds.order.create",
        "app_key":     ALI_DS_APP_KEY,
        "access_token": get_token(),
        "timestamp":   str(int(time.time() * 1000)),
        "format":      "json",
        "v":           "2.0",
        "sign_method": "sha256",
        "param_place_order_request4_open_api_d_t_o": json.dumps(param_order, ensure_ascii=False),
    }
    params["sign"] = _sign(params, ALI_DS_APP_SECRET)

    try:
        r = httpx.post(API_URL, data=params, timeout=30)
        data = r.json()
        resp = (data.get("aliexpress_ds_order_create_response", {})
                    .get("result", {}))
        if resp.get("is_success") or resp.get("order_list"):
            ali_id = (resp.get("order_list", {}).get("number", [""])[0]
                      if isinstance(resp.get("order_list"), dict) else "")
            return {"ok": True, "automatico": True, "ali_order_id": ali_id, "raw": resp}
        return {"ok": False, "manual": True, "motivo": f"AliExpress recusou: {data}"}
    except Exception as e:
        return {"ok": False, "manual": True, "motivo": f"Erro de conexão: {e}"}
