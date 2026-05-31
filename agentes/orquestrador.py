"""
Orquestrador central — coordena todos os agentes para cada nicho.
Pode rodar manualmente ou em modo agendado (a cada N horas).
"""
import os
import json
import schedule
import time
from datetime import datetime
from pathlib import Path
from dados.banco import listar_nichos, obter_nicho
from agentes import spy, copy, flyer

CICLO_HORAS = int(os.getenv("CICLO_HORAS", "6"))
RELATORIO_DIR = Path(__file__).parent.parent / "outputs" / "relatorios"


def _salvar_relatorio(slug: str, dados: dict):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = RELATORIO_DIR / f"{slug}_{ts}.json"
    caminho.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(caminho)


def ciclo_nicho(slug: str) -> dict:
    """Roda todos os agentes para um nicho e retorna o relatório."""
    nicho = obter_nicho(slug)
    if not nicho:
        return {"erro": f"Nicho '{slug}' não encontrado"}

    print(f"\n[{datetime.now().strftime('%H:%M')}] Iniciando ciclo: {nicho['nome']}")
    relatorio = {"slug": slug, "nome": nicho["nome"], "timestamp": datetime.now().isoformat()}

    # 1. Spy — análise de mercado
    try:
        print(f"  → Agente Spy...")
        relatorio["spy"] = spy.rodar(slug, nicho["nome"], nicho["descricao"])
        print(f"  ✓ Spy concluído")
    except Exception as e:
        relatorio["spy"] = {"erro": str(e)}
        print(f"  ✗ Spy falhou: {e}")

    # 2. Copy — 3 formatos diferentes
    relatorio["copies"] = {}
    for formato, tom in [("post", "direto"), ("stories", "urgente"), ("anuncio", "emocional")]:
        try:
            print(f"  → Copy [{formato}/{tom}]...")
            relatorio["copies"][formato] = copy.rodar(slug, formato=formato, tom=tom)
            print(f"  ✓ Copy {formato} concluído")
        except Exception as e:
            relatorio["copies"][formato] = {"erro": str(e)}

    # 3. Flyer — arte automática
    try:
        print(f"  → Agente Flyer...")
        relatorio["flyer"] = flyer.rodar(slug)
        print(f"  ✓ Flyer gerado: {relatorio['flyer']['arquivo']}")
    except Exception as e:
        relatorio["flyer"] = {"erro": str(e)}
        print(f"  ✗ Flyer falhou: {e}")

    # Salva relatório
    caminho = _salvar_relatorio(slug, relatorio)
    relatorio["relatorio_salvo"] = caminho
    print(f"  → Relatório: {caminho}")

    return relatorio


def ciclo_todos() -> list[dict]:
    """Roda o ciclo completo para todos os nichos cadastrados."""
    nichos = listar_nichos()
    if not nichos:
        print("Nenhum nicho cadastrado. Use o dashboard para adicionar.")
        return []

    print(f"\n{'='*50}")
    print(f"CICLO AUTOMÁTICO — {len(nichos)} nicho(s)")
    print(f"{'='*50}")

    resultados = []
    for slug in nichos:
        resultado = ciclo_nicho(slug)
        resultados.append(resultado)

    print(f"\n✓ Ciclo completo. Próximo em {CICLO_HORAS}h.")
    return resultados


def iniciar_agendamento():
    """Inicia o loop agendado. Bloqueia o processo."""
    print(f"Agendamento iniciado — ciclo a cada {CICLO_HORAS} hora(s).")
    ciclo_todos()  # Roda imediatamente na inicialização

    schedule.every(CICLO_HORAS).hours.do(ciclo_todos)
    while True:
        schedule.run_pending()
        time.sleep(60)
