"""
Sistema de disputas e reembolso escalável.
Segue o fluxo correto: cliente abre → aguarda AliExpress → reembolsa.
"""
import uuid
from datetime import datetime, timedelta

try:
    from servidor import db
except ImportError:
    import db

COLECAO = "disputas"

# Políticas de reembolso automático
DIAS_SEM_ENTREGA_REEMBOLSO = 35  # reembolso automático se não entregou
DIAS_ANALISE_DISPUTA       = 5   # prazo para analisar disputa

MOTIVOS_VALIDOS = {
    "nao_recebido":      "Produto não recebido",
    "produto_defeito":   "Produto com defeito",
    "produto_diferente": "Produto diferente do anunciado",
    "nao_entregou":      "Não foi entregue no prazo",
}


def abrir_disputa(pedido_id: str, pedido: dict, motivo: str, descricao: str, evidencias: list = []) -> dict:
    """Abre uma disputa para um pedido."""
    disputa = {
        "id":            str(uuid.uuid4())[:8].upper(),
        "pedido_id":     pedido_id,
        "motivo":        motivo,
        "motivo_label":  MOTIVOS_VALIDOS.get(motivo, motivo),
        "descricao":     descricao,
        "evidencias":    evidencias,
        "status":        "aberta",
        "aberta_em":     datetime.now().isoformat(),
        "prazo_analise": (datetime.now() + timedelta(days=DIAS_ANALISE_DISPUTA)).isoformat(),
        "cliente":       pedido.get("cliente", {}),
        "total_pedido":  pedido.get("total", 0),
        "rastreio":      pedido.get("rastreio", ""),
        "historico": [
            {
                "data":    datetime.now().isoformat(),
                "acao":    "Disputa aberta pelo cliente",
                "detalhe": descricao,
            }
        ],
    }

    # Verifica se é caso de reembolso automático (sem entrega em 35 dias)
    criado_em = pedido.get("criado_em", "")
    if criado_em and motivo == "nao_recebido":
        dias = (datetime.now() - datetime.fromisoformat(criado_em)).days
        if dias >= DIAS_SEM_ENTREGA_REEMBOLSO:
            disputa["reembolso_automatico"] = True
            disputa["reembolso_motivo"] = f"Pedido não entregue após {dias} dias (política: {DIAS_SEM_ENTREGA_REEMBOLSO} dias)"

    _salvar_disputa(disputa)
    return disputa


def listar_disputas(status: str = None) -> list[dict]:
    disputas = db.listar(COLECAO)
    if status:
        disputas = [d for d in disputas if d.get("status") == status]
    return sorted(disputas, key=lambda x: x.get("aberta_em", ""), reverse=True)


def atualizar_disputa(disputa_id: str, acao: str, detalhe: str = "", novo_status: str = None) -> dict | None:
    disputa = db.get(COLECAO, disputa_id)
    if not disputa:
        return None

    disputa["historico"].append({
        "data":    datetime.now().isoformat(),
        "acao":    acao,
        "detalhe": detalhe,
    })
    if novo_status:
        disputa["status"] = novo_status

    _salvar_disputa(disputa)
    return disputa


def aprovar_reembolso(disputa_id: str, valor: float = None) -> dict | None:
    """Marca disputa como aprovada para reembolso (após AliExpress confirmar)."""
    return atualizar_disputa(
        disputa_id,
        acao="Reembolso aprovado após confirmação do fornecedor",
        detalhe=f"Valor: R$ {valor:.2f}" if valor else "",
        novo_status="reembolso_aprovado",
    )


def verificar_reembolsos_automaticos(pedidos_dir=None) -> list[dict]:
    """
    Verifica pedidos que passaram do prazo e gera reembolso automático.
    Chamado pelo agendador diariamente. Lê do banco.
    """
    candidatos = []
    for pedido in db.listar("pedidos"):
        try:
            if pedido.get("status") not in ("comprado_aliexpress", "pagamento_aprovado", "approved"):
                continue
            criado = datetime.fromisoformat(pedido.get("criado_em", datetime.now().isoformat()))
            dias   = (datetime.now() - criado).days
            if dias >= DIAS_SEM_ENTREGA_REEMBOLSO and not pedido.get("disputa_aberta"):
                candidatos.append({"pedido": pedido, "dias": dias})
        except Exception:
            continue
    return candidatos


def _salvar_disputa(disputa: dict):
    db.put(COLECAO, disputa["id"], disputa)
