"""
Agente Copy — gera textos, ganchos e CTAs otimizados por horário e nicho.
Aprende com o histórico de copies anteriores de cada nicho.
"""
import os
from datetime import datetime
from anthropic import Anthropic
from dados.banco import registrar_resultado, obter_nicho

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SISTEMA = """Você é um copywriter expert em vendas digitais para o mercado brasileiro.
Crie copies altamente persuasivos adaptados ao horário do dia e ao nicho.
Use gatilhos mentais, linguagem coloquial brasileira e urgência real.
Responda SEMPRE em JSON válido com as chaves:
gancho, legenda_curta, legenda_longa, cta, stories_texto, headline_site."""

HORARIOS = {
    range(6, 9):   "manhã cedo — público acordando, motivação e energia",
    range(9, 12):  "manhã — público produtivo, foco em soluções e resultados",
    range(12, 14): "almoço — público relaxado, conteúdo leve e rápido",
    range(14, 18): "tarde — público cansado, foco em transformação e escapismo",
    range(18, 21): "fim do dia — público em casa, decisões de compra e sonhos",
    range(21, 24): "noite — público livre, maior taxa de conversão, emoção",
}


def _contexto_horario() -> str:
    hora = datetime.now().hour
    for intervalo, desc in HORARIOS.items():
        if hora in intervalo:
            return desc
    return "madrugada — público insone, copy mais íntimo e direto"


def rodar(
    slug: str,
    formato: str = "post",  # post | stories | email | anuncio
    tom: str = "direto",    # direto | emocional | educativo | urgente
) -> dict:
    """Gera copy otimizado para o nicho no horário atual."""

    nicho = obter_nicho(slug)
    if not nicho:
        raise ValueError(f"Nicho '{slug}' não encontrado.")

    metricas = nicho["metricas"]
    ganchos_aprendidos = metricas.get("ganchos_top", [])
    produtos = metricas.get("produtos_ativos", [])
    horario_ctx = _contexto_horario()

    # Monta contexto de aprendizado
    contexto = f"""Nicho: {nicho['nome']}
Descrição: {nicho['descricao']}
Horário atual: {datetime.now().strftime('%H:%M')} ({horario_ctx})
Formato: {formato}
Tom: {tom}"""

    if ganchos_aprendidos:
        contexto += f"\nGanchos que já funcionaram neste nicho: {ganchos_aprendidos}"
    if produtos:
        contexto += f"\nProdutos em destaque: {produtos}"

    resposta = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=SISTEMA,
        messages=[{"role": "user", "content": contexto}],
    )

    import json
    texto = resposta.content[0].text
    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0].strip()
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0].strip()

    resultado = json.loads(texto)
    resultado["formato"] = formato
    resultado["tom"] = tom
    resultado["horario_geracao"] = datetime.now().strftime("%H:%M")

    registrar_resultado(slug, "copy", resultado)
    return resultado
