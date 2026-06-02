"""
Configurações editáveis do site (lojinha personalizável).
Tudo fica no banco; o admin edita e o site lê em tempo real.
"""
try:
    from servidor import db
except ImportError:
    import db

CHAVE = "config_site"

PADRAO = {
    # Marca
    "loja_nome":      "TechDrop",
    "cor_principal":  "#2563eb",
    "cor_destaque":   "#1d4ed8",
    "whatsapp":       "",          # ex: 5511999999999

    # Barra superior
    "topbar_texto":   "Frete grátis em todos os pedidos acima de R$ 99",
    "topbar_ativa":   True,

    # Hero
    "hero_titulo":    "Tecnologia que cabe no seu dia a dia",
    "hero_subtitulo": "Fones, carregadores e acessórios selecionados, com entrega para todo o Brasil.",
    "hero_cta":       "Ver produtos",
    "hero_imagem":    "https://images.unsplash.com/photo-1468495244123-6c6c332eeece?w=900&h=600&fit=crop&q=80",

    # Banner promocional
    "promo_ativa":    True,
    "promo_titulo":   "Ofertas da semana",
    "promo_texto":    "Descontos selecionados em acessórios tech. Aproveite enquanto durar o estoque.",

    # Selos de confiança
    "selo_1":         "Frete grátis acima de R$ 99",
    "selo_2":         "Pagamento 100% seguro",
    "selo_3":         "7 dias para troca",
    "selo_4":         "Atendimento por WhatsApp",

    # Seções
    "titulo_produtos":   "Mais vendidos",
    "titulo_destaque":   "Em destaque",
    "titulo_avaliacoes": "Quem comprou, aprovou",

    # Rodapé
    "rodape_sobre":   "Curadoria de tecnologia com bom custo-benefício e entrega para todo o Brasil.",
    "rodape_email":   "contato@sualoja.com.br",
    "rodape_cnpj":    "",

    # Cashback / fidelidade
    "cashback_ativo":        True,
    "cashback_por_avaliacao": 5.0,    # R$ que o cliente ganha por avaliar
    "cashback_uso_max_pct":   30,     # % máximo do pedido que pode pagar com cashback

    # SEO
    "seo_titulo":      "TechDrop — Tecnologia com bom custo-benefício",
    "seo_descricao":   "Fones, carregadores, powerbanks e acessórios tech com entrega para todo o Brasil. Pagamento seguro.",
    "seo_palavras":    "fones bluetooth, powerbank, carregador, acessórios tech, eletrônicos",

    # Pixels de rastreamento (cole só o ID)
    "pixel_meta":      "",   # ex: 123456789012345
    "pixel_tiktok":    "",   # ex: C1A2B3...
    "google_analytics":"",   # ex: G-XXXXXXX
    "google_ads":      "",   # ex: AW-XXXXXXX

    # Frete grátis progressivo (barra no carrinho)
    "frete_gratis_ativo":  True,
    "frete_gratis_minimo": 99.0,    # valor mínimo p/ frete grátis

    # Cronômetro de oferta
    "oferta_ativa":          False,
    "oferta_titulo":         "Oferta por tempo limitado",
    "oferta_tipo":           "ficticio",   # "ficticio" (reinicia por visitante) | "real" (data fixa)
    "oferta_fim":            "",           # modo real: "2026-06-10T23:59" (data/hora limite)
    "oferta_duracao_horas":  24,           # modo fictício: quantas horas o relógio mostra

    # Popup de captura de e-mail + cupom de 1ª compra
    "popup_ativo":   False,
    "popup_titulo":  "Ganhe 10% no primeiro pedido",
    "popup_texto":   "Crie sua conta grátis e receba na hora o cupom de boas-vindas.",
    "popup_cupom":   "BEMVINDO10",
    "popup_delay":   4,             # segundos até aparecer

    # Indique e ganhe
    "indicacao_ativa": True,
    "indicacao_valor": 15.0,        # cashback em R$ p/ quem indica E p/ o indicado

    # Parâmetros de custo (alerta de margem + dashboard de lucro)
    "custo_cambio":          5.70,  # cotação USD->BRL usada nas contas
    "custo_trafego_pedido":  10.0,  # gasto de anúncio estimado por pedido (R$)
    "taxa_mp_pct":           4.99,  # taxa do Mercado Pago (% — cartão ~4.99, Pix ~0.99)
}


def obter() -> dict:
    salvo = db.get("_sistema", CHAVE) or {}
    return {**PADRAO, **salvo}


def salvar(novos: dict) -> dict:
    atual = obter()
    # Só aceita chaves conhecidas
    limpo = {k: v for k, v in novos.items() if k in PADRAO}
    atualizado = {**atual, **limpo}
    db.put("_sistema", CHAVE, atualizado)
    return atualizado
