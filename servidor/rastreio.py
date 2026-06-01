"""
Serviço de rastreio automático via 17track API.
Registra pacotes, monitora status e notifica clientes automaticamente.
"""
import os
import httpx
from datetime import datetime

TRACK17_KEY = os.getenv("TRACK17_API_KEY", "")
BASE_URL    = "https://api.17track.net/track/v2"

HEADERS = {
    "17token":    TRACK17_KEY,
    "Content-Type": "application/json",
}

# Mapeamento de status 17track → português
STATUS_MAP = {
    0:   ("Não encontrado",      "aguardando"),
    10:  ("Em trânsito",         "transito"),
    20:  ("Expirado",            "problema"),
    30:  ("Entrega pendente",    "transito"),
    35:  ("Não entregue",        "problema"),
    40:  ("Entregue",            "entregue"),
    50:  ("Entregue",            "entregue"),
    -1:  ("Inválido",            "problema"),
}


def registrar_rastreio(codigo: str, transportadora: str = "") -> bool:
    """Registra um código de rastreio no 17track para monitoramento."""
    if not TRACK17_KEY:
        print("[RASTREIO] TRACK17_API_KEY não configurada")
        return False
    try:
        payload = [{"number": codigo}]
        if transportadora:
            payload[0]["carrier"] = transportadora
        r = httpx.post(f"{BASE_URL}/register",
                       headers=HEADERS, json=payload, timeout=10)
        data = r.json()
        ok = data.get("code") == 0
        if not ok:
            print(f"[RASTREIO] Erro ao registrar {codigo}: {data}")
        return ok
    except Exception as e:
        print(f"[RASTREIO] Erro: {e}")
        return False


def consultar_status(codigo: str) -> dict:
    """Consulta o status atual de um rastreio."""
    if not TRACK17_KEY:
        return {"status": "indisponivel", "descricao": "API não configurada"}
    try:
        r = httpx.post(f"{BASE_URL}/gettrackinfo",
                       headers=HEADERS,
                       json=[{"number": codigo}],
                       timeout=10)
        data = r.json()
        if data.get("code") != 0:
            return {"status": "erro", "descricao": str(data)}

        track = data.get("data", {}).get("accepted", [{}])[0]
        track_info = track.get("track", {})
        status_code = track_info.get("e", 0)
        status_label, status_key = STATUS_MAP.get(status_code, ("Desconhecido", "transito"))

        eventos = track_info.get("z", [])
        ultimo  = eventos[0] if eventos else {}

        return {
            "codigo":     codigo,
            "status":     status_key,
            "descricao":  status_label,
            "ultimo_evento": ultimo.get("z", ""),
            "data_evento":   ultimo.get("a", ""),
            "entregue":   status_code in (40, 50),
            "problema":   status_code in (20, 35, -1),
        }
    except Exception as e:
        return {"status": "erro", "descricao": str(e)}


def verificar_todos_pedidos(pedidos_dir) -> list[dict]:
    """
    Verifica status de todos os pedidos em rastreio.
    Retorna lista de pedidos com status atualizado.
    Chamado automaticamente pelo agendador.
    """
    import json
    from pathlib import Path

    atualizados = []
    pasta = Path(pedidos_dir)

    for arq in pasta.glob("*.json"):
        if arq.name == "fila_fulfillment.json":
            continue
        try:
            with open(arq, encoding="utf-8") as f:
                pedido = json.load(f)

            rastreio = pedido.get("rastreio", "")
            if not rastreio:
                continue
            status = pedido.get("status", "")
            if status in ("entregue", "reembolsado", "cancelado"):
                continue

            # Consulta status atual
            info = consultar_status(rastreio)
            pedido["rastreio_info"] = info
            pedido["rastreio_checado_em"] = datetime.now().isoformat()

            # Atualiza status se entregue
            if info.get("entregue") and status != "entregue":
                pedido["status"] = "entregue"
                pedido["entregue_em"] = datetime.now().isoformat()
                atualizados.append({"pedido": pedido, "evento": "entregue"})
            elif info.get("problema") and status not in ("problema", "disputa"):
                atualizados.append({"pedido": pedido, "evento": "problema"})

            with open(arq, "w", encoding="utf-8") as f:
                json.dump(pedido, f, ensure_ascii=False, indent=2)

        except Exception as e:
            print(f"[RASTREIO] Erro ao verificar {arq.name}: {e}")

    return atualizados
