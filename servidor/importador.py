"""
Importador automático de produtos AliExpress.
Cola o link → extrai fotos, vídeos, variantes, preço → precifica → pronto.
"""
import re
import json
import os
import httpx
from anthropic import Anthropic

client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

USD_BRL          = float(os.getenv("USD_BRL",        "5.70"))
CUSTO_TRAFEGO    = float(os.getenv("CPV_TRAFEGO",    "15.0"))
TAXA_GATEWAY_PCT = float(os.getenv("TAXA_GATEWAY",   "3.5"))
MARGEM_ALVO      = float(os.getenv("MARGEM_ALVO",    "35.0"))

HEADERS = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer":         "https://www.aliexpress.com/",
}


def _extrair_id(url: str) -> str | None:
    m = re.search(r"/item/(\d+)", url)
    return m.group(1) if m else None


def _calcular_preco(custo_usd: float) -> dict:
    custo_brl   = custo_usd * USD_BRL * 1.12
    divisor     = 1 - (TAXA_GATEWAY_PCT + MARGEM_ALVO) / 100
    preco_venda = (custo_brl + CUSTO_TRAFEGO) / divisor
    preco_venda = round(preco_venda / 10) * 10 - 1
    taxa_gw     = preco_venda * TAXA_GATEWAY_PCT / 100
    lucro       = preco_venda - custo_brl - CUSTO_TRAFEGO - taxa_gw
    margem      = (lucro / preco_venda) * 100
    preco_de    = round(preco_venda * 1.65 / 10) * 10
    return {
        "preco_venda":  round(preco_venda, 2),
        "preco_de":     round(preco_de, 2),
        "custo_brl":    round(custo_brl, 2),
        "lucro_venda":  round(lucro, 2),
        "margem_pct":   round(margem, 1),
        "parcelamento": f"3x de R$ {preco_venda/3:.2f}".replace(".", ","),
    }


def _fetch_pagina(url: str) -> str:
    """Busca o HTML da página do produto."""
    # Normaliza URL
    if "aliexpress.com" not in url:
        raise ValueError("URL deve ser do AliExpress")
    url_limpa = url.split("?")[0]  # remove parâmetros de rastreio para URL canônica
    pid = _extrair_id(url)
    if pid:
        url_limpa = f"https://www.aliexpress.com/item/{pid}.html"

    r = httpx.get(url_limpa, headers=HEADERS, timeout=20, follow_redirects=True)
    return r.text


def _extrair_dados_json(html: str) -> dict:
    """Extrai o JSON de dados do produto embutido na página."""
    padroes = [
        r'window\.runParams\s*=\s*(\{.+?\});\s*(?:var|window)',
        r'"data"\s*:\s*(\{"root":.+?\})\s*[,;]',
        r'window\._dida_data_\s*=\s*(\{.+?\});',
    ]
    for padrao in padroes:
        m = re.search(padrao, html, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except Exception:
                continue
    return {}


def _extrair_imagens(html: str, dados: dict) -> list[str]:
    """Extrai todas as imagens do produto."""
    imgs = set()

    # Tenta extrair de JSON embutido
    txt = json.dumps(dados)
    for m in re.finditer(r'"(https://ae\d+\.alicdn\.com/[^"]+\.(?:jpg|png|webp)[^"]*)"', txt):
        url = m.group(1).split("_")[0] + ".jpg"  # pega versão grande
        if "logo" not in url.lower():
            imgs.add(url)

    # Fallback: regex no HTML
    for m in re.finditer(r'"(//ae\d+\.alicdn\.com/kf/[^"]+\.jpg[^"]*)"', html):
        imgs.add("https:" + m.group(1).split('"')[0])

    # Limita a 8 imagens únicas
    resultado = []
    for img in list(imgs)[:8]:
        clean = re.sub(r'_\d+x\d+.*', '', img) + "_480x480.jpg"
        resultado.append(clean)
    return resultado[:8]


def _extrair_variantes(html: str) -> list[dict]:
    """Extrai variantes (cores, tamanhos etc)."""
    variantes = []
    m = re.search(r'"skuPropertyList"\s*:\s*(\[.+?\])\s*[,}]', html, re.DOTALL)
    if m:
        try:
            props = json.loads(m.group(1))
            for prop in props:
                nome = prop.get("skuPropertyName", "")
                valores = [v.get("propertyValueDisplayName", v.get("propertyValueName", ""))
                           for v in prop.get("skuPropertyValues", [])]
                if nome and valores:
                    variantes.append({"nome": nome, "opcoes": valores})
        except Exception:
            pass
    return variantes[:3]  # máx 3 tipos de variante


def _extrair_preco_usd(html: str) -> float:
    """Extrai o preço em USD ou BRL e converte."""
    # Preço em BRL
    m = re.search(r'"discountPrice"\s*:\s*"?([\d.]+)"?', html)
    if m:
        preco_brl = float(m.group(1))
        return preco_brl / USD_BRL

    # Preço direto USD
    m = re.search(r'"formatedActivityPrice"\s*:\s*"US \$([\d.]+)"', html)
    if m:
        return float(m.group(1))

    m = re.search(r'"minActivityAmount"\s*:\s*"?([\d.]+)"?', html)
    if m:
        return float(m.group(1))

    return 0.0


def _extrair_video(html: str) -> str | None:
    m = re.search(r'"videoUrl"\s*:\s*"([^"]+\.mp4[^"]*)"', html)
    if m:
        url = m.group(1).replace("\\u002F", "/")
        return url if url.startswith("http") else "https:" + url
    return None


SISTEMA_IA = """Você é um especialista em e-commerce brasileiro.
Dado o HTML/dados de um produto AliExpress, extraia e melhore as informações para uma loja brasileira.

Retorne JSON com:
{
  "titulo": "nome comercial atrativo em português (máx 60 chars)",
  "descricao": "descrição de venda em português, 2-3 frases destacando benefícios",
  "categoria": "fones|powerbanks|carregadores|acessorios|gamer",
  "badge": "Mais vendido|Novo|Top rated|Oferta|Em alta (escolha o mais adequado)",
  "ganchos": ["gancho 1 para copy", "gancho 2", "gancho 3"],
  "bullets": ["benefício 1", "benefício 2", "benefício 3", "benefício 4", "benefício 5"]
}
Seja direto, comercial e focado no consumidor brasileiro."""


def _melhorar_com_ia(titulo_raw: str, desc_raw: str, variantes: list) -> dict:
    prompt = f"""Produto AliExpress para loja dropshipping brasileira:
Título original: {titulo_raw}
Descrição: {desc_raw[:500]}
Variantes: {variantes}

Melhore para o mercado brasileiro."""

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=800,
        system=SISTEMA_IA,
        messages=[{"role": "user", "content": prompt}],
    )
    texto = resp.content[0].text
    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0].strip()
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0].strip()
    return json.loads(texto)


def importar_produto(url: str) -> dict:
    """
    Pipeline completo:
    1. Busca página do produto
    2. Extrai dados (imagens, variantes, preço)
    3. Melhora com IA
    4. Calcula precificação
    5. Retorna tudo pronto
    """
    pid = _extrair_id(url)
    if not pid:
        raise ValueError("URL inválida. Use um link de produto AliExpress.")

    print(f"  Importando produto {pid}...")
    html = _fetch_pagina(url)

    # Extrai título
    titulo_raw = ""
    m = re.search(r'"subject"\s*:\s*"([^"]+)"', html)
    if m:
        titulo_raw = m.group(1)
    if not titulo_raw:
        m = re.search(r'<title>([^<]+)</title>', html)
        titulo_raw = m.group(1) if m else "Produto AliExpress"

    # Extrai demais dados
    dados_json = _extrair_dados_json(html)
    imagens    = _extrair_imagens(html, dados_json)
    variantes  = _extrair_variantes(html)
    preco_usd  = _extrair_preco_usd(html)
    video_url  = _extrair_video(html)

    # Descrição raw
    desc_raw = ""
    m = re.search(r'"description"\s*:\s*"([^"]{20,500})"', html)
    if m:
        desc_raw = m.group(1)

    print(f"  → {len(imagens)} imagens, {len(variantes)} variantes, US$ {preco_usd:.2f}")
    print("  Melhorando com IA...")

    # Melhora com Claude
    ia = _melhorar_com_ia(titulo_raw, desc_raw, variantes)

    # Precificação
    preco = _calcular_preco(preco_usd) if preco_usd > 0 else {
        "preco_venda": 0, "preco_de": 0, "custo_brl": 0,
        "lucro_venda": 0, "margem_pct": 0, "parcelamento": "",
    }

    return {
        "ok": True,
        "produto_id": pid,
        "url_original": url,
        "titulo":    ia.get("titulo", titulo_raw[:60]),
        "descricao": ia.get("descricao", desc_raw[:200]),
        "categoria": ia.get("categoria", "acessorios"),
        "badge":     ia.get("badge", ""),
        "ganchos":   ia.get("ganchos", []),
        "bullets":   ia.get("bullets", []),
        "imagens":   imagens,
        "imagem":    imagens[0] if imagens else "",
        "video":     video_url,
        "variantes": variantes,
        "preco_usd": preco_usd,
        "link_aliexpress": f"https://www.aliexpress.com/item/{pid}.html",
        **preco,
    }
