"""
Agente de Rentabilidade — decide se vale manter, ajustar ou trocar o produto.
Analisa CPL, ticket médio, taxa de conversão e margem real.
"""
import os
import json
from datetime import datetime
from anthropic import Anthropic
from dados.banco import obter_nicho, registrar_resultado, atualizar_metricas

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SISTEMA = """Você é um analista financeiro especialista em negócios digitais brasileiros.
Analise os dados do produto e responda em JSON com:
{
  "veredicto": "MANTER" | "AJUSTAR" | "TROCAR",
  "score_rentabilidade": 0-100,
  "margem_real_pct": número,
  "ponto_equilibrio_vendas": número,
  "lucro_estimado_mes": número,
  "problemas": ["lista de problemas detectados"],
  "acoes_recomendadas": ["lista de ações concretas"],
  "melhor_preco_sugerido": número,
  "resumo": "frase direta sobre a situação"
}
Seja objetivo e direto. Pense no mercado brasileiro."""


def analisar(
    slug: str,
    preco_venda: float,
    custo_por_lead: float = 5.0,
    taxa_conversao_pct: float = 2.0,
    vendas_mes: int = 0,
    custo_plataforma_pct: float = 9.9,  # Hotmart padrão
    custo_trafego_mes: float = 0.0,
) -> dict:
    """
    Analisa se o produto é rentável e o que fazer.

    Parâmetros:
        preco_venda         — Preço atual do produto (R$)
        custo_por_lead      — Quanto paga por cada lead/clique (R$)
        taxa_conversao_pct  — % de visitantes que compram
        vendas_mes          — Quantas vendas fez no mês (0 = ainda não vendeu)
        custo_plataforma_pct — Taxa da plataforma (Hotmart=9.9%, Kiwify=9.99%)
        custo_trafego_mes   — Gasto total com anúncios no mês (R$)
    """
    nicho = obter_nicho(slug)
    if not nicho:
        raise ValueError(f"Nicho '{slug}' não encontrado.")

    # Cálculos base
    taxa_plataforma = preco_venda * (custo_plataforma_pct / 100)
    receita_liquida = preco_venda - taxa_plataforma
    leads_necessarios = 100 / taxa_conversao_pct  # leads por venda
    custo_por_venda = leads_necessarios * custo_por_lead
    margem_por_venda = receita_liquida - custo_por_venda
    margem_pct = (margem_por_venda / preco_venda) * 100 if preco_venda > 0 else 0

    receita_mes = vendas_mes * preco_venda
    custo_total_mes = (vendas_mes * custo_por_venda) + custo_trafego_mes
    lucro_mes = receita_mes - custo_total_mes - (receita_mes * custo_plataforma_pct / 100)

    prompt = f"""Produto: {nicho['nome']}
Nicho: {nicho['descricao']}
Data: {datetime.now().strftime('%d/%m/%Y')}

DADOS FINANCEIROS:
- Preço de venda: R$ {preco_venda:.2f}
- Taxa da plataforma: R$ {taxa_plataforma:.2f} ({custo_plataforma_pct}%)
- Receita líquida por venda: R$ {receita_liquida:.2f}
- Custo por lead: R$ {custo_por_lead:.2f}
- Taxa de conversão: {taxa_conversao_pct}%
- Leads necessários por venda: {leads_necessarios:.0f}
- Custo por venda adquirida: R$ {custo_por_venda:.2f}
- Margem por venda: R$ {margem_por_venda:.2f} ({margem_pct:.1f}%)
- Vendas no mês: {vendas_mes}
- Gasto com tráfego no mês: R$ {custo_trafego_mes:.2f}
- Receita bruta do mês: R$ {receita_mes:.2f}
- Lucro estimado do mês: R$ {lucro_mes:.2f}

Analise criticamente e dê o veredicto."""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=SISTEMA,
        messages=[{"role": "user", "content": prompt}],
    )

    texto = resp.content[0].text
    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0].strip()
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0].strip()

    resultado = json.loads(texto)
    resultado["dados_entrada"] = {
        "preco_venda": preco_venda,
        "custo_por_lead": custo_por_lead,
        "taxa_conversao_pct": taxa_conversao_pct,
        "vendas_mes": vendas_mes,
        "lucro_mes_calculado": round(lucro_mes, 2),
    }
    resultado["timestamp"] = datetime.now().isoformat()

    registrar_resultado(slug, "rentabilidade", resultado)
    return resultado
