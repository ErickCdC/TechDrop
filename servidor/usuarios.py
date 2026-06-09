"""
Contas de comprador — cadastro, login e tokens.
Usa o mesmo banco (Postgres/SQLite).
"""
import os
import hmac
import hashlib
import base64
import json
import uuid
import secrets
from datetime import datetime, timedelta

try:
    from servidor import db
except ImportError:
    import db

COLECAO     = "usuarios"
USER_SECRET = os.getenv("USER_JWT_SECRET", "") or os.getenv("JWT_SECRET", "") or "DEV-user-secret"
TOKEN_DIAS  = 30


_PBKDF2_ITER = 200_000  # custo de derivação (mais alto = mais resistente a brute-force)


def _hash_legado(senha: str) -> str:
    """Esquema ANTIGO: PBKDF2 com salt global (USER_SECRET).
    Mantido apenas para validar/migrar senhas de contas criadas antes da atualização."""
    return hashlib.pbkdf2_hmac("sha256", senha.encode(), USER_SECRET.encode(), 100_000).hex()


def _hash(senha: str, salt: bytes | None = None) -> str:
    """Esquema NOVO: PBKDF2-SHA256 com salt aleatório por usuário.
    Formato: pbkdf2_sha256$<iteracoes>$<salt_hex>$<hash_hex>"""
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), salt, _PBKDF2_ITER)
    return f"pbkdf2_sha256${_PBKDF2_ITER}${salt.hex()}${dk.hex()}"


def _verificar_senha(senha: str, armazenado: str) -> bool:
    """Valida a senha aceitando tanto o formato novo (com salt) quanto o legado."""
    if not armazenado:
        return False
    if armazenado.startswith("pbkdf2_sha256$"):
        try:
            _, iteracoes, salt_hex, hash_hex = armazenado.split("$")
            dk = hashlib.pbkdf2_hmac("sha256", senha.encode(), bytes.fromhex(salt_hex), int(iteracoes))
            return hmac.compare_digest(dk.hex(), hash_hex)
        except Exception:
            return False
    # legado: hash hex puro com salt global
    return hmac.compare_digest(armazenado, _hash_legado(senha))


def _canon_email(email: str) -> str:
    """
    Normaliza o e-mail para impedir contas duplicadas com o mesmo endereço real:
    - minúsculas e sem espaços
    - remove o sufixo +alias (ex: nome+promo@x.com -> nome@x.com) — vale para todos
    - Gmail/Googlemail ignoram pontos no nome (n.o.m.e@gmail.com == nome@gmail.com)
    """
    email = (email or "").strip().lower()
    if "@" not in email:
        return email
    local, _, dominio = email.partition("@")
    local = local.split("+", 1)[0]
    if dominio in ("gmail.com", "googlemail.com"):
        local = local.replace(".", "")
        dominio = "gmail.com"
    return f"{local}@{dominio}"


def _buscar_usuario(email: str):
    """Acha a conta pela chave canônica e, se não houver, pela chave antiga (raw)."""
    canon = _canon_email(email)
    u = db.get(COLECAO, canon)
    if u:
        return u, canon
    raw = (email or "").strip().lower()
    if raw and raw != canon:
        u = db.get(COLECAO, raw)
        if u:
            return u, raw
    return None, canon


def _criar_token(email: str) -> str:
    exp = (datetime.utcnow() + timedelta(days=TOKEN_DIAS)).isoformat()
    payload = base64.urlsafe_b64encode(json.dumps({"email": email, "exp": exp}).encode()).decode()
    sig = hmac.new(USER_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verificar_token(token: str) -> dict | None:
    try:
        payload, sig = token.rsplit(".", 1)
        if not hmac.compare_digest(sig, hmac.new(USER_SECRET.encode(), payload.encode(), hashlib.sha256).hexdigest()):
            return None
        dados = json.loads(base64.urlsafe_b64decode(payload).decode())
        if datetime.fromisoformat(dados["exp"]) < datetime.utcnow():
            return None
        return dados
    except Exception:
        return None


def cadastrar(nome: str, email: str, senha: str) -> dict:
    email_digitado = (email or "").strip().lower()
    email = _canon_email(email)
    if "@" not in email or "." not in email.split("@")[-1]:
        return {"ok": False, "erro": "Informe um e-mail válido."}
    # bloqueia conta duplicada: mesmo e-mail OU variação do mesmo endereço real
    # (canônica + forma digitada, p/ pegar também contas antigas)
    if db.get(COLECAO, email) or db.get(COLECAO, email_digitado):
        return {"ok": False, "erro": "Este e-mail já possui conta. Faça login."}
    if len(senha) < 6:
        return {"ok": False, "erro": "A senha deve ter pelo menos 6 caracteres."}
    usuario = {
        "id":        str(uuid.uuid4())[:8],
        "nome":      nome.strip(),
        "email":     email,                 # forma canônica (chave)
        "email_digitado": email_digitado,   # como o cliente escreveu
        "senha_hash": _hash(senha),
        "criado_em": datetime.now().isoformat(),
        "endereco":  {},
    }
    db.put(COLECAO, email, usuario)
    return {"ok": True, "token": _criar_token(email), "nome": usuario["nome"]}


def login(email: str, senha: str) -> dict:
    usuario, chave = _buscar_usuario(email)
    if not usuario or not _verificar_senha(senha, usuario.get("senha_hash", "")):
        return {"ok": False, "erro": "E-mail ou senha incorretos."}
    # Migração transparente: se a conta ainda usa o hash legado, regrava no formato novo
    if not usuario["senha_hash"].startswith("pbkdf2_sha256$"):
        usuario["senha_hash"] = _hash(senha)
        db.put(COLECAO, chave, usuario)
    return {"ok": True, "token": _criar_token(chave), "nome": usuario["nome"]}


def obter(email: str) -> dict | None:
    u, _ = _buscar_usuario(email)
    if u:
        u = {**u}
        u.pop("senha_hash", None)  # nunca expõe o hash
    return u


def atualizar_endereco(email: str, endereco: dict):
    u = db.get(COLECAO, email.lower().strip())
    if u:
        u["endereco"] = endereco
        db.put(COLECAO, email.lower().strip(), u)


def obter_saldo(email: str) -> float:
    u = db.get(COLECAO, email.lower().strip())
    return round(u.get("cashback", 0), 2) if u else 0.0


def adicionar_cashback(email: str, valor: float, motivo: str = "") -> float:
    email = email.lower().strip()
    u = db.get(COLECAO, email)
    if not u:
        return 0.0
    u["cashback"] = round(u.get("cashback", 0) + valor, 2)
    u.setdefault("cashback_historico", []).append({
        "valor": valor, "motivo": motivo, "data": datetime.now().isoformat()
    })
    db.put(COLECAO, email, u)
    return u["cashback"]


def usar_cashback(email: str, valor: float) -> bool:
    email = email.lower().strip()
    u = db.get(COLECAO, email)
    if not u or u.get("cashback", 0) < valor:
        return False
    u["cashback"] = round(u.get("cashback", 0) - valor, 2)
    u.setdefault("cashback_historico", []).append({
        "valor": -valor, "motivo": "Usado em compra", "data": datetime.now().isoformat()
    })
    db.put(COLECAO, email, u)
    return True


def ja_avaliou_produto(email: str, produto_id: str) -> bool:
    """Evita dar cashback duplicado pela mesma avaliação."""
    u = db.get(COLECAO, email.lower().strip())
    if not u:
        return False
    return produto_id in u.get("produtos_avaliados", [])


def marcar_avaliou(email: str, produto_id: str):
    email = email.lower().strip()
    u = db.get(COLECAO, email)
    if u:
        u.setdefault("produtos_avaliados", [])
        if produto_id not in u["produtos_avaliados"]:
            u["produtos_avaliados"].append(produto_id)
            db.put(COLECAO, email, u)


# ── INDIQUE E GANHE ─────────────────────────────────────────────────────────────

REF_INDEX = "ref_index"   # coleção: codigo -> email do indicador


def obter_ou_criar_ref(email: str) -> str:
    """Retorna (criando se preciso) o código de indicação do usuário."""
    email = email.lower().strip()
    u = db.get(COLECAO, email)
    if not u:
        return ""
    if u.get("ref_codigo"):
        return u["ref_codigo"]
    # gera código curto e único
    base = (u.get("nome", "") or email.split("@")[0])
    base = "".join(ch for ch in base.upper() if ch.isalnum())[:5] or "TECH"
    codigo = f"{base}{uuid.uuid4().hex[:4].upper()}"
    u["ref_codigo"] = codigo
    db.put(COLECAO, email, u)
    db.put(REF_INDEX, codigo, {"email": email})
    return codigo


def registrar_indicacao(email_indicado: str, ref_codigo: str) -> bool:
    """Marca que o indicado veio de um código (intenção). Crédito só na 1ª compra."""
    if not ref_codigo:
        return False
    email_indicado = email_indicado.lower().strip()
    ref = db.get(REF_INDEX, ref_codigo.strip().upper())
    if not ref:
        return False
    indicador = ref.get("email", "")
    if not indicador or indicador == email_indicado:
        return False   # não pode indicar a si mesmo
    u = db.get(COLECAO, email_indicado)
    if not u or u.get("indicado_por") or u.get("indicacao_creditada"):
        return False   # já tem indicador ou já foi creditado
    u["indicado_por"] = indicador
    db.put(COLECAO, email_indicado, u)
    return True


def creditar_indicacao(email_indicado: str, valor: float) -> dict:
    """
    Credita cashback para indicado e indicador na 1ª compra aprovada.
    Idempotente: só credita uma vez por indicado.
    """
    email_indicado = email_indicado.lower().strip()
    u = db.get(COLECAO, email_indicado)
    if not u:
        return {"ok": False}
    if u.get("indicacao_creditada"):
        return {"ok": False, "motivo": "ja_creditado"}
    indicador = u.get("indicado_por")
    if not indicador:
        return {"ok": False, "motivo": "sem_indicador"}
    # marca antes de creditar (evita corrida)
    u["indicacao_creditada"] = True
    db.put(COLECAO, email_indicado, u)
    adicionar_cashback(email_indicado, valor, "Bônus por indicação (1ª compra)")
    adicionar_cashback(indicador, valor, "Você indicou um amigo que comprou!")
    return {"ok": True, "indicador": indicador, "valor": valor}


# ── ENDEREÇOS SALVOS ───────────────────────────────────────────────────────────

def listar_enderecos(email: str) -> list[dict]:
    u = db.get(COLECAO, email.lower().strip())
    return (u or {}).get("enderecos", [])


def adicionar_endereco(email: str, endereco: dict) -> dict | None:
    email = email.lower().strip()
    u = db.get(COLECAO, email)
    if not u:
        return None
    u.setdefault("enderecos", [])
    novo = {
        "id":        str(uuid.uuid4())[:8],
        "apelido":   (endereco.get("apelido") or "Endereço").strip()[:30],
        "nome":      endereco.get("nome", ""),
        "cpf":       endereco.get("cpf", ""),
        "telefone":  endereco.get("telefone", ""),
        "cep":       endereco.get("cep", ""),
        "rua":       endereco.get("rua", ""),
        "numero":    endereco.get("numero", ""),
        "complemento": endereco.get("complemento", ""),
        "bairro":    endereco.get("bairro", ""),
        "cidade":    endereco.get("cidade", ""),
        "estado":    endereco.get("estado", ""),
        "principal": len(u["enderecos"]) == 0,  # primeiro vira principal
    }
    u["enderecos"].append(novo)
    db.put(COLECAO, email, u)
    return novo


def remover_endereco(email: str, end_id: str) -> bool:
    email = email.lower().strip()
    u = db.get(COLECAO, email)
    if not u:
        return False
    antes = len(u.get("enderecos", []))
    u["enderecos"] = [e for e in u.get("enderecos", []) if e.get("id") != end_id]
    # se removeu o principal, promove o primeiro
    if u["enderecos"] and not any(e.get("principal") for e in u["enderecos"]):
        u["enderecos"][0]["principal"] = True
    db.put(COLECAO, email, u)
    return len(u["enderecos"]) < antes


def definir_endereco_principal(email: str, end_id: str) -> bool:
    email = email.lower().strip()
    u = db.get(COLECAO, email)
    if not u:
        return False
    achou = False
    for e in u.get("enderecos", []):
        e["principal"] = (e.get("id") == end_id)
        achou = achou or e["principal"]
    if achou:
        db.put(COLECAO, email, u)
    return achou


def pedidos_do_usuario(email: str) -> list[dict]:
    """Retorna os pedidos vinculados à conta (por usuario_email ou email do cliente)."""
    email = email.lower().strip()
    pedidos = [p for p in db.listar("pedidos")
               if (p.get("usuario_email", "") or "").lower() == email
               or (p.get("cliente", {}).get("email", "") or "").lower() == email]
    return sorted(pedidos, key=lambda p: p.get("criado_em", ""), reverse=True)
