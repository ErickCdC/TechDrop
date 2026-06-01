"""
Importador automático de produtos AliExpress.
Usa API direta do AliExpress + Claude para extrair e melhorar dados.
"""
import re
import json
import os
import httpx
from anthropic import Anthropic

def _get_client():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key or not key.startswith("sk-"):
        # Lê diretamente com utf-8-sig para remover BOM
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        try:
            with open(env_path, encoding="utf-8-sig") as f:
                for line in f:
                    if "ANTHROPIC_API_KEY" in line and "sk-" in line:
                        key = line.split("=", 1)[1].strip()
                        os.environ["ANTHROPIC_API_KEY"] = key
                        break
        except Exception:
            pass
    return Anthropic(api_key=key)

USD_BRL          = float(os.getenv("USD_BRL",        "5.70"))
CUSTO_TRAFEGO    = float(os.getenv("CPV_TRAFEGO",    "10.0"))
TAXA_GATEWAY_PCT = float(os.getenv("TAXA_GATEWAY",   "3.5"))
MARGEM_ALVO      = float(os.getenv("MARGEM_ALVO",    "25.0"))

HEADERS_BROWSER = {
    "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "pt-BR,pt;q=0.9",
    "Referer":         "https://www.aliexpress.com/",
}


def _extrair_id(url: str) -> str | None:
    m = re.search(r"/item/(\d+)", url)
    return m.group(1) if m else None


def _calcular_preco(custo_usd: float) -> dict:
    custo_brl   = custo_usd * USD_BRL * 1.05
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


def _fetch_api_produto(pid: str) -> dict:
    """
    Tenta buscar dados via endpoints JSON do AliExpress.
    Menos sujeito ao bloqueio anti-bot que o HTML.
    """
    endpoints = [
        f"https://www.aliexpress.com/fn/ae-detail-api-service/product?productId={pid}&country=BR&currency=BRL&language=pt_BR",
        f"https://www.aliexpress.com/fn/superApeContent/product/detail?productId={pid}&country=BR&currency=BRL",
        f"https://aedetailservice.aliexpress.com/detail/detail.json?productId={pid}&country=BR&currency=BRL",
    ]
    for url in endpoints:
        try:
            r = httpx.get(url, headers=HEADERS_BROWSER, timeout=15, follow_redirects=True)
            if r.status_code == 200:
                data = r.json()
                if data and isinstance(data, dict):
                    return data
        except Exception:
            continue
    return {}


def _extrair_de_api(dados: dict) -> dict:
    """Extrai campos de qualquer formato de resposta da API."""
    result = {"titulo": "", "imagens": [], "preco_usd": 0.0, "variantes": [], "video": None}
    if not dados:
        return result
    txt = json.dumps(dados)

    # Imagens
    imgs = set()
    for m in re.finditer(r'"(https://ae\d+\.alicdn\.com/kf/[^"]+\.jpg[^"]*)"', txt):
        url = re.sub(r'_\d+x\d+[^.]*', '', m.group(1))
        imgs.add(url + "_480x480.jpg" if "_480x480" not in url else url)
    for m in re.finditer(r'"(//ae\d+\.alicdn\.com/kf/[^"]+\.jpg[^"]*)"', txt):
        url = "https:" + re.sub(r'_\d+x\d+[^.]*', '', m.group(1))
        imgs.add(url + "_480x480.jpg" if "_480x480" not in url else url)
    result["imagens"] = list(imgs)[:8]

    # Preço
    for chave in ['"salePrice"', '"discountPrice"', '"activityPrice"', '"minActivityAmount"']:
        m = re.search(chave + r'\s*:\s*["\']?([\d.]+)', txt)
        if m:
            val = float(m.group(1))
            # Se for BRL (>50) converte para USD
            result["preco_usd"] = val / USD_BRL if val > 50 else val
            break

    # Título
    for chave in ['"subject"', '"title"', '"productTitle"']:
        m = re.search(chave + r'\s*:\s*"([^"]{10,200})"', txt)
        if m:
            result["titulo"] = m.group(1)
            break

    # Variantes
    variantes = []
    for m in re.finditer(r'"skuPropertyName"\s*:\s*"([^"]+)"', txt):
        nome = m.group(1)
        pos = m.start()
        vals_m = re.findall(r'"propertyValueDisplayName"\s*:\s*"([^"]+)"', txt[pos:pos+2000])
        if vals_m:
            variantes.append({"nome": nome, "opcoes": list(dict.fromkeys(vals_m))[:12]})
    result["variantes"] = variantes[:3]

    # Video
    m = re.search(r'"videoUrl"\s*:\s*"([^"]+\.mp4[^"]*)"', txt)
    if m:
        result["video"] = m.group(1).replace("\\u002F", "/")

    return result


SISTEMA_PRODUTO = """Você é um expert em e-commerce brasileiro e produto tech/eletrônicos.

Dado o ID e URL de um produto AliExpress, gere dados realistas e comerciais para uma loja de dropshipping brasileira.

Retorne JSON com:
{
  "titulo": "nome comercial atrativo em português (máx 60 chars)",
  "descricao": "descrição de venda, 2-3 frases focadas em benefícios para o consumidor brasileiro",
  "categoria": "fones|powerbanks|carregadores|acessorios|gamer",
  "badge": "Mais vendido|Novo|Top rated|Oferta|Em alta",
  "ganchos": ["3 ganchos de copy persuasivos em português"],
  "bullets": ["5 benefícios do produto em português"],
  "preco_usd_estimado": número (estimativa do custo em USD no AliExpress),
  "imagens_busca": ["3 URLs de imagens de banco de imagens que representem bem este produto"]
}

Para as imagens use URLs do Unsplash no formato:
https://images.unsplash.com/photo-XXXXXXXXXXX?w=400&h=400&fit=crop&q=80

Escolha fotos profissionais que realmente representem o tipo de produto."""


def _gerar_com_ia(pid: str, url: str, dados_parciais: dict) -> dict:
    """Usa Claude para gerar/completar dados do produto."""
    prompt = f"""Produto AliExpress:
ID: {pid}
URL: {url}
Título extraído: {dados_parciais.get('titulo', 'não disponível')}
Preço USD extraído: {dados_parciais.get('preco_usd', 0)}
Imagens encontradas: {len(dados_parciais.get('imagens', []))}
Variantes: {dados_parciais.get('variantes', [])}

Gere os dados completos para este produto."""

    resp = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        system=SISTEMA_PRODUTO,
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
    1. Extrai ID do produto
    2. Tenta API do AliExpress
    3. Claude preenche/melhora tudo
    4. Calcula precificação completa
    """
    pid = _extrair_id(url)
    if not pid:
        raise ValueError("URL inválida. Use um link de produto AliExpress com /item/NUMERO")

    print(f"Importando produto {pid}...")

    # Tenta API
    dados_api = _fetch_api_produto(pid)
    dados_parciais = _extrair_de_api(dados_api)

    print(f"API: {len(dados_parciais['imagens'])} imagens, preco US${dados_parciais['preco_usd']:.2f}")
    print("Gerando com IA...")

    # Claude melhora e preenche gaps
    ia = _gerar_com_ia(pid, url, dados_parciais)

    # Mescla: dados reais têm prioridade sobre IA
    imagens = dados_parciais["imagens"] if dados_parciais["imagens"] else ia.get("imagens_busca", [])
    preco_usd = dados_parciais["preco_usd"] if dados_parciais["preco_usd"] > 0 else ia.get("preco_usd_estimado", 15.0)
    variantes = dados_parciais["variantes"] if dados_parciais["variantes"] else []

    preco = _calcular_preco(preco_usd)

    return {
        "ok":       True,
        "produto_id": pid,
        "url_original": url,
        "titulo":    ia.get("titulo", dados_parciais.get("titulo", "")[:60]),
        "descricao": ia.get("descricao", ""),
        "categoria": ia.get("categoria", "acessorios"),
        "badge":     ia.get("badge", ""),
        "ganchos":   ia.get("ganchos", []),
        "bullets":   ia.get("bullets", []),
        "imagens":   imagens,
        "imagem":    imagens[0] if imagens else "",
        "video":     dados_parciais.get("video"),
        "variantes": variantes,
        "preco_usd": preco_usd,
        "link_aliexpress": f"https://www.aliexpress.com/item/{pid}.html",
        **preco,
    }
