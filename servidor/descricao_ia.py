"""
Agente de IA para gerar descrição de produto.
Usa SOMENTE as informações coletadas do anúncio (título + especificações).
Não inventa dados. Gera headline + descrição de fácil leitura.
"""
import os
import json
from anthropic import Anthropic


def _client():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or not key.startswith("sk-"):
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        try:
            with open(env_path, encoding="utf-8-sig") as f:
                for line in f:
                    if "ANTHROPIC_API_KEY" in line and "sk-" in line:
                        key = line.split("=", 1)[1].strip()
                        break
        except Exception:
            pass
    return Anthropic(api_key=key)


SISTEMA = """Você é um copywriter de e-commerce brasileiro especializado em produtos de tecnologia.

REGRA ABSOLUTA: use SOMENTE as informações fornecidas (título, especificações e o
TEXTO DE DESCRIÇÃO já existente do anúncio). Aproveite ao máximo o texto de descrição
fornecido — reescreva-o de forma mais clara e organizada, mas sem inventar nada novo.
NUNCA invente especificações, números, materiais ou recursos que não foram informados.
Se uma informação não foi dada, não a mencione.

Gere uma descrição de produto que seja:
- Fácil de ler (frases curtas, escaneável)
- Detalhada (aproveita todas as specs reais fornecidas)
- Persuasiva mas honesta

Responda SEMPRE em JSON válido:
{
  "headline": "uma frase de impacto curta destacando o principal benefício (máx 12 palavras)",
  "descricao_html": "descrição em HTML simples: um parágrafo de abertura + uma lista <ul><li> com os destaques baseados nas especificações reais. Use apenas <p>, <ul>, <li>, <strong>."
}"""


def gerar(titulo: str, especificacoes: list, descricao_atual: str = "") -> dict:
    specs_txt = "\n".join(f"- {e.get('nome')}: {e.get('valor')}" for e in (especificacoes or []))
    prompt = f"""Produto: {titulo}

Especificações reais coletadas do anúncio:
{specs_txt or "(nenhuma especificação estruturada)"}

Texto de descrição já existente no anúncio (use como base principal, reescrevendo melhor):
{descricao_atual[:2000] or "(vazio)"}

Gere a headline e a descrição usando SOMENTE essas informações — aproveitando e
reorganizando o texto existente acima, sem inventar nada."""

    resp = _client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=900,
        system=SISTEMA,
        messages=[{"role": "user", "content": prompt}],
    )
    txt = resp.content[0].text
    if "```json" in txt:
        txt = txt.split("```json")[1].split("```")[0].strip()
    elif "```" in txt:
        txt = txt.split("```")[1].split("```")[0].strip()
    return json.loads(txt)
