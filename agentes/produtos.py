"""
Agente de Produtos — pesquisa itens lucrativos para dropshipping tech.
Sugere produtos, calcula margem real e decide se vale adicionar à loja.
"""
import os
import json
from datetime import datetime
from anthropic import Anthropic
from dados.banco import registrar_resultado, obter_nicho

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SISTEMA = """Você é um especialista em dropshipping de tecnologia no mercado brasileiro.
Analise a categoria e sugira produtos lucrativos para vender via AliExpress → cliente Brasil.

Responda SEMPRE em JSON válido com esta estrutura:
{
  "produtos": [
    {
      "nome": "Nome do produto",
      "categoria": "categoria",
      "custo_aliexpress_brl": número,
      "preco_venda_sugerido": número,
      "margem_pct": número,
      "demanda": "ALTA|MÉDIA|BAIXA",
      "concorrencia": "ALTA|MÉDIA|BAIXA",
      "score_oportunidade": 0-100,
      "palavras_chave_busca": ["kw1","kw2"],
      "gancho_principal": "frase de venda",
      "publico_alvo": "descrição do público",
      "prazo_envio_dias": número
    }
  ],
  "melhor_produto": "nome do melhor produto",
  "resumo_mercado": "análise geral do momento"
}
Considere câmbio USD/BRL atual ~5.70. Frete AliExpress ~R$15-30 incluído no custo."""


def pesquisar(slug: str, categoria: str = "fones e audio") -> dict:
    """Pesquisa os melhores produtos para a categoria informada."""

    nicho = obter_nicho(slug)
    historico_produtos = []
    if nicho:
        registros = [r for r in nicho.get("registros", []) if r["tipo"] == "produtos"]
        historico_produtos = [r["conteudo"].get("melhor_produto") for r in registros[-3:]]

    prompt = f"""Categoria: {categoria}
Data: {datetime.now().strftime('%d/%m/%Y')}
Mercado: Brasil (dropshipping AliExpress)
Ticket alvo: R$ 80 a R$ 300

{f"Produtos já analisados antes (evite repetir): {historico_produtos}" if historico_produtos else ""}

Sugira os 5 melhores produtos desta categoria com maior margem e menor concorrência agora."""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        system=SISTEMA,
        messages=[{"role": "user", "content": prompt}],
    )

    texto = resp.content[0].text
    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0].strip()
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0].strip()

    resultado = json.loads(texto)
    resultado["categoria_pesquisada"] = categoria
    resultado["timestamp"] = datetime.now().isoformat()

    if nicho:
        registrar_resultado(slug, "produtos", resultado)

    return resultado


def calcular_margem(
    custo_produto: float,
    preco_venda: float,
    taxa_gateway: float = 3.5,
    custo_frete_incluido: float = 0.0,
) -> dict:
    """Calcula margem real de um produto dropshipping."""
    taxa_r = preco_venda * (taxa_gateway / 100)
    custo_total = custo_produto + custo_frete_incluido + taxa_r
    lucro = preco_venda - custo_total
    margem_pct = (lucro / preco_venda) * 100

    return {
        "preco_venda": preco_venda,
        "custo_produto": custo_produto,
        "taxa_gateway": round(taxa_r, 2),
        "custo_total": round(custo_total, 2),
        "lucro_por_venda": round(lucro, 2),
        "margem_pct": round(margem_pct, 1),
        "viavel": margem_pct >= 30,
        "recomendacao": (
            "✅ Excelente margem" if margem_pct >= 50 else
            "✅ Boa margem" if margem_pct >= 35 else
            "⚠️ Margem aceitável" if margem_pct >= 20 else
            "❌ Margem muito baixa — evitar"
        ),
    }
