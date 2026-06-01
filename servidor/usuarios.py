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
from datetime import datetime, timedelta

try:
    from servidor import db
except ImportError:
    import db

COLECAO     = "usuarios"
USER_SECRET = os.getenv("USER_JWT_SECRET", "") or os.getenv("JWT_SECRET", "") or "DEV-user-secret"
TOKEN_DIAS  = 30


def _hash(senha: str) -> str:
    # PBKDF2 com salt fixo derivado do secret (suficiente p/ esta escala)
    return hashlib.pbkdf2_hmac("sha256", senha.encode(), USER_SECRET.encode(), 100_000).hex()


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
    email = email.lower().strip()
    if db.get(COLECAO, email):
        return {"ok": False, "erro": "Este e-mail já está cadastrado."}
    if len(senha) < 6:
        return {"ok": False, "erro": "A senha deve ter pelo menos 6 caracteres."}
    usuario = {
        "id":        str(uuid.uuid4())[:8],
        "nome":      nome.strip(),
        "email":     email,
        "senha_hash": _hash(senha),
        "criado_em": datetime.now().isoformat(),
        "endereco":  {},
    }
    db.put(COLECAO, email, usuario)
    return {"ok": True, "token": _criar_token(email), "nome": usuario["nome"]}


def login(email: str, senha: str) -> dict:
    email = email.lower().strip()
    usuario = db.get(COLECAO, email)
    if not usuario or not hmac.compare_digest(usuario["senha_hash"], _hash(senha)):
        return {"ok": False, "erro": "E-mail ou senha incorretos."}
    return {"ok": True, "token": _criar_token(email), "nome": usuario["nome"]}


def obter(email: str) -> dict | None:
    u = db.get(COLECAO, email.lower().strip())
    if u:
        u = {**u}
        u.pop("senha_hash", None)  # nunca expõe o hash
    return u


def atualizar_endereco(email: str, endereco: dict):
    u = db.get(COLECAO, email.lower().strip())
    if u:
        u["endereco"] = endereco
        db.put(COLECAO, email.lower().strip(), u)


def pedidos_do_usuario(email: str) -> list[dict]:
    """Retorna os pedidos vinculados ao e-mail do comprador."""
    email = email.lower().strip()
    pedidos = [p for p in db.listar("pedidos")
               if (p.get("cliente", {}).get("email", "") or "").lower() == email]
    return sorted(pedidos, key=lambda p: p.get("criado_em", ""), reverse=True)
