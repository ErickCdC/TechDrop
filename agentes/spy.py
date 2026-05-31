"""
Agente Spy — analisa mercado, concorrentes e tendências de qualquer nicho.
Retroalimenta o banco com melhores horários, ganchos e produtos detectados.
"""
import os
from datetime import datetime
from anthropic import Anthropic
from dados.banco import registrar_resultado, atualizar_metricas, historico_nicho

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SISTEMA = """Você é um especialista em análise de mercado digital brasileiro.
Sua missão: para o nicho informado, gere uma análise detalhada contendo:
1. Top 3 dores/desejos do público-alvo
2. Melhores horários para postar (baseado em comportamento do nicho)
3. Top 5 ganchos de entrada que mais convertem nesse nicho
4. 3 produtos/ofertas que estão funcionando no mercado
5. Nível de concorrência e oportunidade (1-10)
6. Uma frase de posicionamento diferenciado

Responda SEMPRE em JSON válido com as chaves:
dores, melhores_horarios, ganchos_top, produtos_funcionando,
concorrencia_score, oportunidade_score, posicionamento.
Seja específico para o contexto brasileiro."""


def rodar(slug: str, nome: str, descricao: str) -> dict:
    """Executa o agente spy para um nicho e salva os resultados."""

    # Inclui histórico para retroalimentação
    historico = historico_nicho(slug, tipo="spy", limite=5)
    contexto_historico = ""
    if historico:
        contexto_historico = f"\n\nHistórico anterior deste nicho (use para evoluir a análise):\n{historico}"

    prompt = f"""Nicho: {nome}
Descrição: {descricao}
Data atual: {datetime.now().strftime('%d/%m/%Y %H:%M')}
{contexto_historico}

Faça a análise completa deste nicho para o mercado brasileiro."""

    resposta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=SISTEMA,
        messages=[{"role": "user", "content": prompt}],
    )

    import json
    texto = resposta.content[0].text
    # Extrai JSON da resposta (pode vir com markdown)
    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0].strip()
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0].strip()

    resultado = json.loads(texto)

    # Salva no banco e atualiza métricas aprendidas
    registrar_resultado(slug, "spy", resultado)
    atualizar_metricas(slug, "melhores_horarios", resultado.get("melhores_horarios", []))
    atualizar_metricas(slug, "ganchos_top", resultado.get("ganchos_top", []))
    atualizar_metricas(slug, "produtos_ativos", resultado.get("produtos_funcionando", []))

    return resultado
