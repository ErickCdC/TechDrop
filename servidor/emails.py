"""
Serviço de e-mails automáticos via Resend.
Dispara em cada evento do pedido sem intervenção manual.
"""
import os
import httpx

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
LOJA_NOME      = os.getenv("LOJA_NOME",      "TechDrop Brasil")
LOJA_EMAIL     = os.getenv("LOJA_EMAIL",     "suporte@techdropbrasil.com.br")
LOJA_URL       = os.getenv("LOJA_URL",       "https://techdropbrasil.com.br")

def _enviar(destinatario: str, assunto: str, html: str) -> bool:
    if not RESEND_API_KEY:
        print(f"[EMAIL] RESEND_API_KEY não configurada — e-mail não enviado para {destinatario}")
        return False
    try:
        r = httpx.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": f"{LOJA_NOME} <onboarding@resend.dev>",
                  "to": [destinatario], "subject": assunto, "html": html},
            timeout=10,
        )
        ok = r.status_code in (200, 201)
        if not ok:
            print(f"[EMAIL] Erro Resend: {r.status_code} {r.text[:200]}")
        return ok
    except Exception as e:
        print(f"[EMAIL] Erro: {e}")
        return False


def _base(conteudo: str) -> str:
    return f"""
    <div style="font-family:Inter,sans-serif;max-width:560px;margin:0 auto;background:#fff;">
      <div style="background:#111827;padding:24px 32px;border-radius:12px 12px 0 0;text-align:center;">
        <h1 style="color:#3b82f6;font-size:22px;margin:0;">{LOJA_NOME}</h1>
      </div>
      <div style="padding:32px;border:1px solid #e5e7eb;border-top:none;border-radius:0 0 12px 12px;">
        {conteudo}
        <hr style="border:none;border-top:1px solid #e5e7eb;margin:24px 0;"/>
        <p style="color:#9ca3af;font-size:12px;text-align:center;">
          {LOJA_NOME} · {LOJA_URL}<br/>
          Em caso de dúvidas responda este e-mail.
        </p>
      </div>
    </div>"""


# ── CONFIRMAÇÃO DE PEDIDO ─────────────────────────────────────────────────────

def confirmacao_pedido(pedido: dict):
    cliente  = pedido.get("cliente", {})
    nome     = cliente.get("nome", "Cliente")
    email    = cliente.get("email", "")
    pid      = pedido.get("id", "")
    total    = pedido.get("total", 0)
    itens    = pedido.get("itens", [])

    if not email:
        return False

    itens_html = "".join(f"""
    <tr>
      <td style="padding:10px;border-bottom:1px solid #f3f4f6;">
        <img src="{i.get('imagem','')}" width="48" height="48"
             style="border-radius:6px;object-fit:cover;vertical-align:middle;margin-right:10px;"/>
        {i.get('titulo','—')}
      </td>
      <td style="padding:10px;border-bottom:1px solid #f3f4f6;text-align:right;white-space:nowrap;">
        <strong>R$ {i.get('preco_venda',0):.2f}</strong>
      </td>
    </tr>""" for i in itens)

    html = _base(f"""
    <h2 style="color:#111827;margin-bottom:8px;">Pedido confirmado! 🎉</h2>
    <p style="color:#6b7280;">Olá <strong>{nome}</strong>, recebemos seu pedido com sucesso.</p>

    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:16px;margin:20px 0;">
      <p style="margin:0;font-size:14px;">
        <strong>Pedido #</strong>{pid} · <strong>Total:</strong> R$ {total:.2f}<br/>
        <strong>Prazo de entrega:</strong> 15 a 25 dias úteis
      </p>
    </div>

    <table style="width:100%;border-collapse:collapse;margin-bottom:20px;">
      {itens_html}
    </table>

    <div style="background:#eff6ff;border-radius:8px;padding:16px;margin-bottom:20px;">
      <p style="margin:0;font-size:13px;color:#1d4ed8;">
        📦 Assim que seu pedido for despachado você receberá um e-mail com o código de rastreio.
      </p>
    </div>

    <p style="color:#6b7280;font-size:13px;">
      🛡️ Garantia de 7 dias após recebimento. Em caso de problemas, responda este e-mail.
    </p>""")

    return _enviar(email, f"✅ Pedido #{pid} confirmado — {LOJA_NOME}", html)


# ── RASTREIO DISPONÍVEL ───────────────────────────────────────────────────────

def notificar_rastreio(pedido: dict, codigo_rastreio: str):
    cliente = pedido.get("cliente", {})
    email   = cliente.get("email", "")
    nome    = cliente.get("nome", "Cliente")
    pid     = pedido.get("id", "")

    if not email:
        return False

    html = _base(f"""
    <h2 style="color:#111827;margin-bottom:8px;">Seu pedido foi despachado! 📦</h2>
    <p style="color:#6b7280;">Olá <strong>{nome}</strong>, seu pedido <strong>#{pid}</strong> está a caminho!</p>

    <div style="background:#eff6ff;border:1px solid #bfdbfe;border-radius:8px;padding:20px;margin:20px 0;text-align:center;">
      <p style="margin:0 0 8px;color:#6b7280;font-size:13px;">Código de rastreio</p>
      <p style="margin:0;font-size:22px;font-weight:900;color:#1d4ed8;letter-spacing:2px;">{codigo_rastreio}</p>
    </div>

    <p style="text-align:center;margin-bottom:20px;">
      <a href="https://www.17track.net/pt-br?nums={codigo_rastreio}"
         style="background:#3b82f6;color:#fff;padding:12px 28px;border-radius:8px;text-decoration:none;font-weight:700;">
        Rastrear meu pedido →
      </a>
    </p>

    <p style="color:#6b7280;font-size:13px;">
      ⏱️ O prazo de entrega é de <strong>15 a 25 dias úteis</strong>.<br/>
      Se não receber em 35 dias corridos, entre em contato para reembolso total.
    </p>""")

    return _enviar(email, f"📦 Pedido #{pid} despachado — rastreie agora", html)


# ── ENTREGUE ──────────────────────────────────────────────────────────────────

def notificar_entregue(pedido: dict):
    cliente = pedido.get("cliente", {})
    email   = cliente.get("email", "")
    nome    = cliente.get("nome", "Cliente")
    pid     = pedido.get("id", "")

    if not email:
        return False

    html = _base(f"""
    <h2 style="color:#111827;margin-bottom:8px;">Pedido entregue! 🎉</h2>
    <p style="color:#6b7280;">Olá <strong>{nome}</strong>, seu pedido <strong>#{pid}</strong> foi entregue!</p>

    <div style="background:#f0fdf4;border:1px solid #bbf7d0;border-radius:8px;padding:16px;margin:20px 0;">
      <p style="margin:0;font-size:14px;color:#166534;">
        ✅ Esperamos que você esteja adorando seu produto!<br/>
        Você tem <strong>7 dias</strong> para acionar a garantia em caso de problemas.
      </p>
    </div>

    <p style="color:#6b7280;font-size:13px;">
      Teve algum problema? Responda este e-mail descrevendo o que houve e envie fotos/vídeo.
      Nossa equipe avaliará e resolverá em até 48h.
    </p>""")

    return _enviar(email, f"🎉 Pedido #{pid} entregue — {LOJA_NOME}", html)


# ── CARRINHO ABANDONADO ───────────────────────────────────────────────────────

def recuperar_carrinho(pedido: dict):
    cliente = pedido.get("cliente", {})
    email   = cliente.get("email", "")
    nome    = cliente.get("nome", "").split()[0] if cliente.get("nome") else "Olá"
    itens   = pedido.get("itens", [])
    checkout_url = pedido.get("checkout_url", LOJA_URL)

    if not email:
        return False

    itens_html = "".join(f"""
    <tr>
      <td style="padding:10px;border-bottom:1px solid #f3f4f6;">
        <img src="{i.get('imagem','')}" width="48" height="48"
             style="border-radius:6px;object-fit:cover;vertical-align:middle;margin-right:10px;"/>
        {i.get('titulo','—')}
      </td>
      <td style="padding:10px;border-bottom:1px solid #f3f4f6;text-align:right;">
        <strong>R$ {i.get('preco_venda',0):.2f}</strong>
      </td>
    </tr>""" for i in itens)

    html = _base(f"""
    <h2 style="color:#111827;margin-bottom:8px;">{nome}, você esqueceu algo! 🛒</h2>
    <p style="color:#6b7280;">Seus produtos ainda estão te esperando. Garanta antes que o estoque acabe!</p>

    <table style="width:100%;border-collapse:collapse;margin:20px 0;">{itens_html}</table>

    <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:16px;margin-bottom:20px;text-align:center;">
      <p style="margin:0;font-size:14px;color:#92400e;">
        🎁 Use o cupom <strong style="font-size:16px;">VOLTA10</strong> e ganhe <strong>10% de desconto</strong>!
      </p>
    </div>

    <p style="text-align:center;margin-bottom:20px;">
      <a href="{checkout_url}" style="background:#2563eb;color:#fff;padding:14px 36px;border-radius:8px;text-decoration:none;font-weight:800;font-size:15px;">
        Finalizar minha compra →
      </a>
    </p>

    <p style="color:#9ca3af;font-size:12px;text-align:center;">
      🔒 Compra 100% segura · 🚚 Frete grátis · ↩️ 7 dias de garantia
    </p>""")

    return _enviar(email, f"🛒 {nome}, seus produtos estão esperando — 10% OFF", html)


# ── DISPUTA ABERTA ────────────────────────────────────────────────────────────

def notificar_disputa_aberta(pedido: dict, motivo: str):
    cliente = pedido.get("cliente", {})
    email   = cliente.get("email", "")
    nome    = cliente.get("nome", "Cliente")
    pid     = pedido.get("id", "")

    if not email:
        return False

    html = _base(f"""
    <h2 style="color:#111827;margin-bottom:8px;">Recebemos sua solicitação ⚙️</h2>
    <p style="color:#6b7280;">Olá <strong>{nome}</strong>, sua solicitação para o pedido <strong>#{pid}</strong> foi registrada.</p>

    <div style="background:#fffbeb;border:1px solid #fde68a;border-radius:8px;padding:16px;margin:20px 0;">
      <p style="margin:0;font-size:14px;color:#92400e;">
        <strong>Motivo:</strong> {motivo}<br/><br/>
        Estamos verificando com nosso fornecedor. O prazo de análise é de até <strong>5 dias úteis</strong>.
        Você receberá uma atualização por e-mail.
      </p>
    </div>

    <p style="color:#6b7280;font-size:13px;">
      ⚠️ O reembolso só é processado após confirmação do fornecedor.<br/>
      Isso garante que você receba o valor correto sem burocracia adicional.
    </p>""")

    return _enviar(email, f"⚙️ Solicitação #{pid} em análise — {LOJA_NOME}", html)
