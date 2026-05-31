"""
Banco de aprendizado — armazena e retroalimenta dados de cada nicho.
Usa JSON local; troque por Supabase/SQLite se quiser escalar.
"""
import json
import os
from datetime import datetime
from pathlib import Path

CAMINHO = Path(__file__).parent / "banco.json"


def _carregar() -> dict:
    if not CAMINHO.exists():
        return {"nichos": {}}
    with open(CAMINHO, encoding="utf-8") as f:
        return json.load(f)


def _salvar(dados: dict):
    with open(CAMINHO, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)


# ── Nichos ────────────────────────────────────────────────────────────────────

def listar_nichos() -> list[str]:
    return list(_carregar()["nichos"].keys())


def adicionar_nicho(slug: str, nome: str, descricao: str):
    dados = _carregar()
    if slug not in dados["nichos"]:
        dados["nichos"][slug] = {
            "nome": nome,
            "descricao": descricao,
            "criado_em": datetime.now().isoformat(),
            "registros": [],
            "metricas": {
                "copies_gerados": 0,
                "flyers_gerados": 0,
                "melhores_horarios": [],
                "ganchos_top": [],
                "produtos_ativos": [],
            },
        }
        _salvar(dados)
        return True
    return False  # já existe


def obter_nicho(slug: str) -> dict | None:
    return _carregar()["nichos"].get(slug)


# ── Registros (aprendizado) ────────────────────────────────────────────────────

def registrar_resultado(slug: str, tipo: str, conteudo: dict):
    """Salva um resultado (copy, flyer, análise) e atualiza métricas."""
    dados = _carregar()
    nicho = dados["nichos"].get(slug)
    if not nicho:
        raise ValueError(f"Nicho '{slug}' não encontrado.")

    registro = {
        "tipo": tipo,
        "timestamp": datetime.now().isoformat(),
        "horario": datetime.now().strftime("%H:%M"),
        "conteudo": conteudo,
    }
    nicho["registros"].append(registro)

    # Atualiza contadores
    if tipo == "copy":
        nicho["metricas"]["copies_gerados"] += 1
    elif tipo == "flyer":
        nicho["metricas"]["flyers_gerados"] += 1

    _salvar(dados)


def atualizar_metricas(slug: str, campo: str, valor):
    """Atualiza um campo de métricas diretamente."""
    dados = _carregar()
    nicho = dados["nichos"].get(slug)
    if nicho:
        nicho["metricas"][campo] = valor
        _salvar(dados)


def historico_nicho(slug: str, tipo: str | None = None, limite: int = 20) -> list:
    """Retorna os últimos registros, opcionalmente filtrados por tipo."""
    nicho = obter_nicho(slug)
    if not nicho:
        return []
    registros = nicho["registros"]
    if tipo:
        registros = [r for r in registros if r["tipo"] == tipo]
    return registros[-limite:]
