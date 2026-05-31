"""
Autenticação admin via JWT.
"""
import os
import json
import hmac
import hashlib
import base64
from datetime import datetime, timedelta
from functools import wraps
from flask import request, jsonify

ADMIN_USER   = os.getenv("ADMIN_USER",     "admin")
ADMIN_PASS   = os.getenv("ADMIN_PASS",     "techdrop2026")
JWT_SECRET   = os.getenv("JWT_SECRET",     "techdrop-secret-mude-isso")
TOKEN_HORAS  = int(os.getenv("TOKEN_HORAS", "24"))


def _hash_senha(senha: str) -> str:
    return hashlib.sha256(senha.encode()).hexdigest()


def _criar_token(user: str) -> str:
    expira = (datetime.utcnow() + timedelta(hours=TOKEN_HORAS)).isoformat()
    payload = json.dumps({"user": user, "exp": expira})
    payload_b64 = base64.urlsafe_b64encode(payload.encode()).decode()
    sig = hmac.new(JWT_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{sig}"


def _verificar_token(token: str) -> dict | None:
    try:
        payload_b64, sig = token.rsplit(".", 1)
        sig_esperado = hmac.new(JWT_SECRET.encode(), payload_b64.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, sig_esperado):
            return None
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        if datetime.fromisoformat(payload["exp"]) < datetime.utcnow():
            return None
        return payload
    except Exception:
        return None


def login_required(f):
    """Decorator para proteger rotas admin."""
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.headers.get("Authorization", "").replace("Bearer ", "")
        if not token:
            token = request.cookies.get("admin_token", "")
        if not token or not _verificar_token(token):
            return jsonify({"ok": False, "erro": "Não autorizado"}), 401
        return f(*args, **kwargs)
    return decorated


def verificar_credenciais(user: str, senha: str) -> str | None:
    """Verifica login e retorna token se correto."""
    if user == ADMIN_USER and _hash_senha(senha) == _hash_senha(ADMIN_PASS):
        return _criar_token(user)
    return None
