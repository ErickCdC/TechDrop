# TechDrop Brasil

Loja de dropshipping automatizada (eletrônicos/gadgets do AliExpress) com catálogo
e marketing assistidos por IA (Claude), checkout via Mercado Pago e **fulfillment
automático**: quando o pagamento é aprovado, o pedido é criado sozinho no fornecedor.

---

## Arquitetura

| Componente | Pasta | Descrição |
|---|---|---|
| **Loja (backend)** | `servidor/` | API Flask: catálogo, checkout, webhook MP, admin, contas, fulfillment |
| **Loja (frontend)** | `site/` | HTML/CSS/JS estático servido pelo Flask |
| **Extensão Chrome** | `extensao/` | Captura produto no AliExpress → painel admin |
| **Agentes de marketing (CLI)** | `agentes/`, `main.py` | Spy/Copy/Flyer/Rentabilidade por nicho (subsistema legado) |
| **Persistência** | `dados/`, `servidor/db.py` | KV unificado: PostgreSQL (prod) ou SQLite (dev) |

Fluxo da venda: vitrine → checkout (CPF validado, cupom, cashback) → Mercado Pago →
webhook aprova → cria pedido no AliExpress DS API → e-mail + rastreio automáticos.

---

## Rodando localmente

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env             # e preencha as chaves
python -m servidor.app             # http://localhost:5000  (admin: /admin-panel/)
```

CLI de marketing (opcional): `python main.py`

Sem `DATABASE_URL`, usa SQLite local em `dados/techdrop.db`.

---

## Variáveis de ambiente

Veja `.env.example`. Resumo das principais:

| Variável | Obrigatória | Função |
|---|---|---|
| `ANTHROPIC_API_KEY` | sim | IA (curadoria, copy, descrições) |
| `MP_ACCESS_TOKEN` | sim (loja) | Mercado Pago (checkout/webhook) |
| `ADMIN_PASS` | **sim** | Senha do painel admin (sem ela o login fica bloqueado) |
| `JWT_SECRET` | recomendada | Assina tokens admin (se vazio, deriva de `ADMIN_PASS`) |
| `USER_JWT_SECRET` | recomendada | Assina tokens de cliente |
| `MP_WEBHOOK_SECRET` | recomendada | Valida assinatura do webhook MP |
| `DATABASE_URL` | prod | Postgres (Railway injeta automaticamente) |
| `CORS_ORIGINS` | não | Domínios extras p/ CORS (vírgula); `*` libera geral (dev) |
| `RESEND_API_KEY` | não | E-mails transacionais |
| `TRACK17_API_KEY` | não | Rastreio automático |
| `ALIEXPRESS_APP_KEY` / `ALIEXPRESS_APP_SECRET` | não | Fulfillment automático (DS API) |
| `LOJA_URL`, `LOJA_NOME`, `LOJA_EMAIL` | não | Identidade da loja |
| `USD_BRL`, `CPV_TRAFEGO`, `TAXA_GATEWAY`, `MARGEM_ALVO` | não | Precificação |

> **Nunca** comite o `.env` (já está no `.gitignore`). Gere segredos fortes com
> `python -c "import secrets; print(secrets.token_hex(32))"`.

---

## Deploy (Railway)

1. Adicione um banco **PostgreSQL** (a `DATABASE_URL` é injetada automaticamente).
2. Defina as variáveis de ambiente (mínimo: `ANTHROPIC_API_KEY`, `MP_ACCESS_TOKEN`,
   `ADMIN_PASS`, `JWT_SECRET`, `USER_JWT_SECRET`).
3. O deploy usa o `Procfile`: `gunicorn wsgi:app`.
4. Configure o webhook do Mercado Pago para `https://SEU_DOMINIO/api/webhook/mercadopago`.

---

## Segurança (implementado)

- Senhas de cliente com **PBKDF2-SHA256 + salt aleatório por usuário** (migração
  transparente de contas antigas no próximo login).
- Cabeçalhos de segurança em todas as respostas (`nosniff`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`, e `HSTS` sob HTTPS).
- **Content-Security-Policy** em modo *report-only* por padrão (não bloqueia, só
  registra violações). Defina `CSP_ENFORCE=1` para aplicar e `CSP_REPORT_URI` para
  coletar os relatórios.
- Cookie admin `HttpOnly` + `Secure` (em HTTPS) + `SameSite=Lax`.
- Revalidação de preços no servidor no checkout (anti-fraude).
- Validação de CPF (dígitos verificadores) e de assinatura do webhook MP.
- Limitador anti-brute-force nos logins (admin e cliente).
- CORS restrito à `LOJA_URL` por padrão.
- `ProxyFix` para HTTPS correto atrás do proxy do Railway.
