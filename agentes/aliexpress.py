"""
Agente AliExpress — busca produtos reais, calcula margem e atualiza o site.

Usa a AliExpress Affiliate API (oficial e gratuita).
Registro em: https://portals.aliexpress.com/
"""
import os
import json
import hmac
import hashlib
import time
import httpx
from datetime import datetime
from anthropic import Anthropic

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

ALIEXPRESS_APP_KEY    = os.getenv("ALIEXPRESS_APP_KEY", "")
ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET", "")
ALIEXPRESS_TRACKING   = os.getenv("ALIEXPRESS_TRACKING_ID", "")

USD_BRL  = float(os.getenv("USD_BRL", "5.70"))
MARGEM_MINIMA = 35  # % mínima para considerar o produto


# ── ASSINATURA API ALIEXPRESS ─────────────────────────────────────────────────

def _sign(params: dict, secret: str) -> str:
    """Gera assinatura HMAC-SHA256 para a API do AliExpress."""
    sorted_params = sorted(params.items())
    base = "".join(f"{k}{v}" for k, v in sorted_params)
    return hmac.new(secret.encode(), base.encode(), hashlib.sha256).hexdigest().upper()


def _call_api(method: str, extra: dict) -> dict:
    """Chama a API do AliExpress com os parâmetros fornecidos."""
    url = "https://api-sg.aliexpress.com/sync"
    params = {
        "method":       method,
        "app_key":      ALIEXPRESS_APP_KEY,
        "timestamp":    str(int(time.time() * 1000)),
        "format":       "json",
        "v":            "2.0",
        "sign_method":  "sha256",
        **extra,
    }
    params["sign"] = _sign(params, ALIEXPRESS_APP_SECRET)
    resp = httpx.get(url, params=params, timeout=15)
    return resp.json()


# ── BUSCA DE PRODUTOS ─────────────────────────────────────────────────────────

def buscar_produtos_api(keywords: str, min_preco_usd=5, max_preco_usd=50, paginas=1) -> list[dict]:
    """Busca produtos reais via AliExpress Affiliate API."""
    if not ALIEXPRESS_APP_KEY:
        raise ValueError("ALIEXPRESS_APP_KEY não configurada no .env")

    produtos = []
    for page in range(1, paginas + 1):
        data = _call_api("aliexpress.affiliate.product.query", {
            "keywords":             keywords,
            "min_sale_price":       str(int(min_preco_usd * 100)),
            "max_sale_price":       str(int(max_preco_usd * 100)),
            "sort":                 "SALE_PRICE_ASC",
            "page_no":              str(page),
            "page_size":            "20",
            "fields":               "product_id,product_title,product_main_image_url,sale_price,original_price,evaluate_rate,lastest_volume,product_detail_url,promotion_link",
            "tracking_id":          ALIEXPRESS_TRACKING,
        })

        items = (
            data.get("aliexpress_affiliate_product_query_response", {})
                .get("resp_result", {})
                .get("result", {})
                .get("products", {})
                .get("product", [])
        )
        produtos.extend(items)

    return produtos


# ── FILTRO E MARGEM ───────────────────────────────────────────────────────────

def _calcular_margem(preco_usd: float, multiplicador: float = 2.5) -> dict:
    """Calcula preço de venda sugerido e margem real em BRL."""
    custo_brl   = preco_usd * USD_BRL * 1.10   # +10% frete/variação
    venda_brl   = round(custo_brl * multiplicador, -1)  # arredonda p/ dezena
    taxa_gateway = venda_brl * 0.035
    lucro       = venda_brl - custo_brl - taxa_gateway
    margem_pct  = (lucro / venda_brl) * 100
    return {
        "custo_brl":       round(custo_brl, 2),
        "preco_venda":     round(venda_brl, 2),
        "preco_de":        round(venda_brl * 1.6, 2),  # preço "riscado"
        "lucro_por_venda": round(lucro, 2),
        "margem_pct":      round(margem_pct, 1),
        "parcelamento":    f"3x de R$ {round(venda_brl/3, 2):.2f}".replace(".", ","),
    }


def filtrar_e_rankear(produtos_raw: list[dict]) -> list[dict]:
    """Filtra por qualidade e rankeia por score de oportunidade."""
    resultado = []
    for p in produtos_raw:
        try:
            preco_usd = float(p.get("sale_price", "0").replace(",", ""))
            avaliacao = float(p.get("evaluate_rate", "0").replace("%", "")) / 20  # converte % para 5 estrelas
            vendas    = int(p.get("lastest_volume", 0))
            imagem    = p.get("product_main_image_url", "")
            titulo    = p.get("product_title", "")[:80]
            link      = p.get("promotion_link") or p.get("product_detail_url", "")

            if not imagem or preco_usd < 1:
                continue

            margem = _calcular_margem(preco_usd)
            if margem["margem_pct"] < MARGEM_MINIMA:
                continue

            score = (avaliacao * 20) + min(vendas / 10, 30) + margem["margem_pct"] * 0.5
            resultado.append({
                "id":          p.get("product_id"),
                "titulo":      titulo,
                "imagem":      imagem,
                "link":        link,
                "preco_usd":   preco_usd,
                "avaliacao":   round(avaliacao, 1),
                "vendas":      vendas,
                "score":       round(score, 1),
                **margem,
            })
        except Exception:
            continue

    return sorted(resultado, key=lambda x: x["score"], reverse=True)


# ── CURADORIA COM CLAUDE ──────────────────────────────────────────────────────

SISTEMA_CURADOR = """Você é um curador de produtos para dropshipping brasileiro.
Dado uma lista de produtos do AliExpress, selecione os 6 melhores para uma loja tech.
Considere: imagem de qualidade, título claro, boa margem, alta demanda no Brasil.
Renomeie os títulos para português brasileiro, sem jargões chineses.
Responda em JSON: lista de objetos com id, titulo_pt, gancho (frase de venda curta)."""


def curar_com_ia(produtos: list[dict]) -> list[dict]:
    """Usa Claude para selecionar e renomear os melhores produtos."""
    if not produtos:
        return []

    lista = [{"id": p["id"], "titulo": p["titulo"], "margem": p["margem_pct"], "score": p["score"]}
             for p in produtos[:20]]

    resp = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        system=SISTEMA_CURADOR,
        messages=[{"role": "user", "content": f"Produtos:\n{json.dumps(lista, ensure_ascii=False)}"}],
    )

    texto = resp.content[0].text
    if "```json" in texto:
        texto = texto.split("```json")[1].split("```")[0].strip()
    elif "```" in texto:
        texto = texto.split("```")[1].split("```")[0].strip()

    curadoria = {item["id"]: item for item in json.loads(texto)}

    # Mescla curadoria com dados financeiros
    resultado = []
    for p in produtos:
        if str(p["id"]) in curadoria or p["id"] in curadoria:
            chave = str(p["id"]) if str(p["id"]) in curadoria else p["id"]
            p["titulo_pt"] = curadoria[chave].get("titulo_pt", p["titulo"])
            p["gancho"]    = curadoria[chave].get("gancho", "")
            resultado.append(p)
        if len(resultado) >= 6:
            break

    return resultado


# ── ATUALIZA O SITE ───────────────────────────────────────────────────────────

SITE_PATH = os.path.join(os.path.dirname(__file__), "..", "site", "index.html")

_BADGE_MAP = ["🔥 Top", "Novo", "Top rated", "Oferta", "Em alta", ""]

def atualizar_site(produtos: list[dict]):
    """Substitui os produtos no index.html com os dados reais."""
    with open(SITE_PATH, encoding="utf-8") as f:
        html = f.read()

    # Gera novo bloco de produtos
    cards = []
    for i, p in enumerate(produtos[:6]):
        badge = _BADGE_MAP[i % len(_BADGE_MAP)]
        badge_html = f'<span class="badge-hot">{badge}</span>' if badge else ""
        estrelas = "★" * int(p["avaliacao"]) + "☆" * (5 - int(p["avaliacao"]))
        cards.append(f"""
    <div class="prod-card">
      <div class="prod-img-wrap">
        <img src="{p['imagem']}" alt="{p['titulo_pt']}" loading="lazy"/>
        {badge_html}
        <span class="frete-badge">Frete grátis</span>
      </div>
      <div class="prod-info">
        <h3>{p['titulo_pt']}</h3>
        <div class="stars-row"><span class="stars">{estrelas}</span><span class="stars-count">{p['avaliacao']} ({p['vendas']})</span></div>
        <div class="preco-wrap">
          <div class="preco-de">R$ {p['preco_de']:.2f}</div>
          <div class="preco-por">R$ {int(p['preco_venda'])}<small>,00</small></div>
          <div class="preco-parcel">{p['parcelamento']} sem juros</div>
          <a href="{p['link']}" target="_blank" rel="noopener" class="btn-comprar">Comprar agora</a>
        </div>
      </div>
    </div>""")

    novo_grid = '\n'.join(cards)

    # Substitui apenas a seção de produtos
    import re
    html = re.sub(
        r'(<div class="produtos-grid" id="produtos">)(.*?)(</div>\s*</div>\s*<!-- COMO FUNCIONA -->)',
        rf'\1\n{novo_grid}\n  </div>\n\n  </div>\n\n<!-- COMO FUNCIONA -->',
        html, flags=re.DOTALL
    )

    with open(SITE_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"✓ Site atualizado com {len(produtos)} produtos reais.")


# ── PIPELINE COMPLETO ─────────────────────────────────────────────────────────

CATEGORIAS_TECH = [
    ("wireless headphones anc", 8, 40),
    ("power bank 20000mah fast charge", 5, 25),
    ("gan charger 65w usb c", 5, 20),
    ("tws earbuds bluetooth 5.3", 5, 25),
    ("wireless silent mouse 2.4g", 3, 15),
    ("ring light led selfie", 5, 20),
]


def rodar_pipeline():
    """Pipeline completo: busca → filtra → cura → atualiza site."""
    print(f"\n{'='*50}")
    print(f"AGENTE ALIEXPRESS — {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print(f"{'='*50}")

    todos_produtos = []
    for keywords, min_p, max_p in CATEGORIAS_TECH:
        print(f"  Buscando: {keywords}...")
        try:
            raw = buscar_produtos_api(keywords, min_p, max_p)
            filtrados = filtrar_e_rankear(raw)
            print(f"  → {len(raw)} encontrados, {len(filtrados)} com boa margem")
            todos_produtos.extend(filtrados[:5])
        except Exception as e:
            print(f"  ✗ Erro: {e}")

    if not todos_produtos:
        print("\n⚠️ Nenhum produto encontrado. Verifique as chaves da API no .env")
        return []

    print(f"\n  Curando {len(todos_produtos)} produtos com IA...")
    produtos_finais = curar_com_ia(todos_produtos)

    print(f"  Atualizando site com {len(produtos_finais)} produtos...")
    atualizar_site(produtos_finais)

    # Salva relatório
    relatorio = {
        "timestamp": datetime.now().isoformat(),
        "total_encontrados": len(todos_produtos),
        "produtos_selecionados": len(produtos_finais),
        "produtos": produtos_finais,
    }
    relatorio_path = os.path.join(os.path.dirname(__file__), "..", "outputs", "relatorios",
                                  f"aliexpress_{datetime.now().strftime('%Y%m%d_%H%M')}.json")
    with open(relatorio_path, "w", encoding="utf-8") as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=2)

    print(f"  Relatório: {relatorio_path}")
    return produtos_finais
