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
