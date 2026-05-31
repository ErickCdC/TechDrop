"""
Agente Flyer — gera artes/banners para cada nicho.
Modo 1 (padrão): HTML renderizável (sem custo de API de imagem)
Modo 2 (premium): DALL-E 3 via OpenAI API
"""
import os
import json
from datetime import datetime
from pathlib import Path
from anthropic import Anthropic
from dados.banco import registrar_resultado, obter_nicho

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

OUTPUT_DIR = Path(__file__).parent.parent / "outputs" / "flyers"

PALETAS = {
    "saude":     {"bg": "#0f3460", "accent": "#e94560", "text": "#ffffff"},
    "financas":  {"bg": "#1a1a2e", "accent": "#f5a623", "text": "#ffffff"},
    "emagrecimento": {"bg": "#16213e", "accent": "#00b4d8", "text": "#ffffff"},
    "pets":      {"bg": "#2d6a4f", "accent": "#ffd166", "text": "#ffffff"},
    "default":   {"bg": "#212121", "accent": "#7c4dff", "text": "#ffffff"},
}


def _detectar_paleta(slug: str) -> dict:
    for chave in PALETAS:
        if chave in slug.lower():
            return PALETAS[chave]
    return PALETAS["default"]


SISTEMA_COPY_FLYER = """Você é um designer de copy para artes visuais.
Dado o nicho e o gancho, gere o texto ideal para um flyer.
Responda em JSON com: titulo (max 8 palavras), subtitulo (max 15 palavras),
cta_botao (max 4 palavras), rodape (max 10 palavras)."""


def _gerar_html(nicho: dict, slug: str, textos: dict) -> str:
    paleta = _detectar_paleta(slug)
    bg = paleta["bg"]
    accent = paleta["accent"]
    txt = paleta["text"]

    return f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;700;900&display=swap');
  * {{ margin: 0; padding: 0; box-sizing: border-box; }}
  body {{
    width: 1080px; height: 1080px;
    background: {bg};
    font-family: 'Inter', sans-serif;
    display: flex; align-items: center; justify-content: center;
    overflow: hidden;
  }}
  .card {{
    width: 900px; text-align: center; padding: 60px;
  }}
  .tag {{
    background: {accent}; color: {txt};
    display: inline-block; padding: 8px 24px;
    border-radius: 50px; font-size: 18px;
    font-weight: 700; letter-spacing: 2px;
    text-transform: uppercase; margin-bottom: 40px;
  }}
  h1 {{
    font-size: 72px; font-weight: 900;
    color: {txt}; line-height: 1.1;
    margin-bottom: 30px;
    text-shadow: 0 4px 20px rgba(0,0,0,0.4);
  }}
  h1 span {{ color: {accent}; }}
  p.sub {{
    font-size: 28px; color: {txt};
    opacity: 0.85; margin-bottom: 60px; line-height: 1.5;
  }}
  .btn {{
    background: {accent}; color: {txt};
    padding: 22px 60px; border-radius: 50px;
    font-size: 26px; font-weight: 700;
    display: inline-block; margin-bottom: 50px;
    box-shadow: 0 8px 30px rgba(0,0,0,0.3);
    text-transform: uppercase; letter-spacing: 1px;
  }}
  .rodape {{
    font-size: 18px; color: {txt}; opacity: 0.5;
  }}
  .deco {{
    position: absolute; width: 300px; height: 300px;
    background: {accent}; border-radius: 50%;
    opacity: 0.07; filter: blur(60px);
  }}
  .deco-1 {{ top: -80px; left: -80px; }}
  .deco-2 {{ bottom: -80px; right: -80px; }}
</style>
</head>
<body>
  <div class="deco deco-1"></div>
  <div class="deco deco-2"></div>
  <div class="card">
    <div class="tag">{nicho['nome']}</div>
    <h1>{textos['titulo']}</h1>
    <p class="sub">{textos['subtitulo']}</p>
    <div class="btn">{textos['cta_botao']}</div>
    <p class="rodape">{textos['rodape']}</p>
  </div>
</body>
</html>"""


def rodar(slug: str, gancho: str | None = None) -> dict:
    """Gera um flyer HTML para o nicho. Retorna caminho do arquivo."""

    nicho = obter_nicho(slug)
    if not nicho:
        raise ValueError(f"Nicho '{slug}' não encontrado.")

    metricas = nicho["metricas"]
    if not gancho:
        ganchos = metricas.get("ganchos_top", [])
        gancho = ganchos[0] if ganchos else nicho["descricao"]

    # Gera textos do flyer via Claude
    prompt = f"""Nicho: {nicho['nome']}
Descrição: {nicho['descricao']}
Gancho principal: {gancho}
Gere os textos para o flyer."""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        system=SISTEMA_COPY_FLYER,
        messages=[{"role": "user", "content": prompt}],
    )

    texto = resp.content[0].text
    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0].strip()
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0].strip()

    textos = json.loads(texto)

    # Gera HTML
    html = _gerar_html(nicho, slug, textos)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome_arquivo = f"{slug}_{timestamp}.html"
    caminho = OUTPUT_DIR / nome_arquivo
    caminho.write_text(html, encoding="utf-8")

    resultado = {
        "arquivo": str(caminho),
        "textos": textos,
        "gancho_usado": gancho,
    }
    registrar_resultado(slug, "flyer", resultado)
    return resultado
