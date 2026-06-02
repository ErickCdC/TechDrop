const USD_BRL = 5.70, TRAFEGO = 10, GATEWAY_PCT = 3.5, MARGEM_ALVO = 25, FRETE_PCT = 5;
let _dadosCapturados = null;
let _imgSelecionada  = "";

// ── CARREGA CONFIG SALVA ───────────────────────────────────────────────────────
// URL padrão é só conveniência; o token NUNCA fica no código (segurança).
const DEFAULT_URL = "https://web-production-ccdcc.up.railway.app";

chrome.storage.local.get(["admin_url", "admin_token"], (cfg) => {
  document.getElementById("cfg-url").value   = cfg.admin_url   || DEFAULT_URL;
  document.getElementById("cfg-token").value = cfg.admin_token || "";
});

// ── CAPTURAR ──────────────────────────────────────────────────────────────────
async function capturar() {
  mostrar("loading");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });

    if (!tab.url || !tab.url.includes("aliexpress.com/item/")) {
      mostrarErro("Abra a página de um produto AliExpress primeiro.\n\nURL deve conter /item/");
      return;
    }

    // Injeta e executa o extrator direto via executeScript (mais confiável)
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: extrairDadosPagina,
    });

    const dados = results?.[0]?.result;
    if (!dados) {
      mostrarErro("Não foi possível capturar os dados.\nRecarregue a página do AliExpress e tente novamente.");
      return;
    }

    _dadosCapturados = dados;
    renderPreview(dados);
    mostrar("preview");

  } catch(e) {
    mostrarErro("Erro: " + e.message + "\n\nCertifique-se de estar na página do produto AliExpress.");
  }
}

// ── FUNÇÃO INJETADA NA PÁGINA (async p/ buscar reviews) ──────────────────────
async function extrairDadosPagina() {
  const resultado = {
    url:      window.location.href,
    pid:      (window.location.href.match(/\/item\/(\d+)/) || [])[1] || "",
    titulo:   "",
    imagens:  [],
    video:    null,
    reviews:  [],
    especificacoes: [],
    preco_brl: 0,
    preco_original_brl: 0,
    variantes: [],
    skus: [],
    avaliacao: "",
    vendas: "",
  };

  // TÍTULO
  const tituloSeletores = [
    "h1[data-pl='product-title']",
    ".product-title-text",
    "[class*='title--wrap'] h1",
    "[class*='product-title'] h1",
    "h1",
  ];
  for (const sel of tituloSeletores) {
    const el = document.querySelector(sel);
    if (el && el.textContent.trim().length > 5) {
      resultado.titulo = el.textContent.trim();
      break;
    }
  }

  // ── PREÇO VIA URL (mais confiável) ─────────────────────────────────────────
  // A URL do AliExpress traz o preço no parâmetro pdp_npi: ...!BRL!original!venda!...
  try {
    const npi = decodeURIComponent(window.location.href.match(/pdp_npi=([^&]+)/)?.[1] || "");
    if (npi) {
      const partes = npi.split("!");          // ex: 6@dis BRL 294.72 136.52 ...
      const iMoeda = partes.findIndex(x => /^[A-Z]{3}$/.test(x));
      if (iMoeda >= 0) {
        const p1 = parseFloat(partes[iMoeda + 1]);  // original
        const p2 = parseFloat(partes[iMoeda + 2]);  // venda (com desconto)
        if (!isNaN(p2) && p2 > 0) resultado.preco_brl = p2;
        if (!isNaN(p1) && p1 > 0) resultado.preco_original_brl = p1;
      }
    }
  } catch (e) {}

  // Extrai um objeto JSON BALANCEADO a partir de uma posição (evita cortar pela metade)
  function jsonBalanceado(s, start) {
    let depth = 0, inStr = false, esc = false;
    for (let i = start; i < s.length; i++) {
      const ch = s[i];
      if (inStr) { if (esc) esc = false; else if (ch === "\\") esc = true; else if (ch === '"') inStr = false; }
      else { if (ch === '"') inStr = true; else if (ch === "{") depth++; else if (ch === "}") { depth--; if (depth === 0) return s.slice(start, i + 1); } }
    }
    return null;
  }

  // ── FONTE PRINCIPAL: JSON embutido (runParams.data) ────────────────────────
  let DATA = null;
  try {
    if (window.runParams && window.runParams.data) DATA = window.runParams.data;
  } catch (e) {}
  if (!DATA) {
    for (const s of document.scripts) {
      const t = s.textContent || "";
      if (!t.includes("runParams") || !t.includes("priceModule")) continue;
      // Procura "data:" e extrai o objeto JSON COMPLETO (balanceado)
      let idx = t.indexOf("data:");
      while (idx !== -1 && !DATA) {
        const bs = t.indexOf("{", idx);
        if (bs === -1) break;
        const j = jsonBalanceado(t, bs);
        if (j) { try { const o = JSON.parse(j); if (o.priceModule || o.skuModule || o.titleModule) DATA = o; } catch (e) {} }
        idx = t.indexOf("data:", idx + 5);
      }
      if (!DATA) {
        const wi = t.indexOf("runParams");
        const bs = t.indexOf("{", wi);
        if (bs !== -1) { const j = jsonBalanceado(t, bs); if (j) { try { const o = JSON.parse(j); DATA = o.data || o; } catch (e) {} } }
      }
      if (DATA) break;
    }
  }

  function num(x) {
    if (x == null) return 0;
    if (typeof x === "number") return x;
    const v = parseFloat(String(x).replace(/[^\d.,]/g, "").replace(/\.(?=\d{3})/g, "").replace(",", "."));
    return isNaN(v) ? 0 : v;
  }

  if (DATA) {
    // Título
    const tm = DATA.titleModule || DATA.productInfoComponent || {};
    if (tm.subject && tm.subject.length > 5) resultado.titulo = tm.subject;

    // Preço (priceModule) — só se a URL não trouxe
    const pm = DATA.priceModule || {};
    const cur = pm.minActivityAmount || pm.minAmount || pm.maxActivityAmount || pm.maxAmount || {};
    if (!resultado.preco_brl)
      resultado.preco_brl = num(cur.value || cur.formatedAmount || pm.formatedActivityPrice || pm.formatedPrice);
    const orig = pm.maxAmount || pm.minAmount || {};
    if (!resultado.preco_original_brl)
      resultado.preco_original_brl = num(orig.value || pm.formatedPrice) || 0;

    // Imagens (imageModule)
    const im = DATA.imageModule || {};
    (im.imagePathList || []).forEach(u => addImg(u));

    // Especificações (specsModule) — Marca, Modelo, Material, etc.
    const spm = DATA.specsModule || DATA.productPropComponent || {};
    (spm.props || spm.productProperty || []).forEach(p => {
      const nome = (p.attrName || p.attrNameOrigin || "").trim();
      const valor = (p.attrValue || p.attrValueOrigin || "").trim();
      if (nome && valor) resultado.especificacoes.push({ nome, valor });
    });

    // Variantes (skuModule) — Cor, Tamanho, Capacidade COM os IDs do AliExpress
    const sm = DATA.skuModule || {};
    (sm.productSKUPropertyList || []).forEach(prop => {
      const nome = (prop.skuPropertyName || "").trim();
      const propId = prop.skuPropertyId;
      const opcoes = (prop.skuPropertyValues || []).map(v => {
        let img = v.skuPropertyImagePath || "";
        if (img && img.startsWith("//")) img = "https:" + img;
        return {
          label:   (v.propertyValueDisplayName || v.propertyValueName || "").trim(),
          img,
          valueId: v.propertyValueId,                 // ID exigido pela API de pedido
          valueName: v.skuPropertyValue || v.propertyValueName || "",
        };
      }).filter(o => o.label);
      if (nome && opcoes.length) resultado.variantes.push({ nome, propId, opcoes: opcoes.slice(0, 50) });
    });

    // Lista de SKUs: cada combinação tem skuAttr (string que a API precisa) + preço
    resultado.skus = (sm.skuPriceList || []).map(s => {
      const v = s.skuVal || {};
      return {
        sku_id:    s.skuId,
        sku_attr:  s.skuAttr || "",      // ex "14:771#Red;5:100014064#XL"
        prop_ids:  s.skuPropIds || "",   // ex "771,100014064"
        preco:     parseFloat(v.skuActivityAmount?.value || v.skuAmount?.value || 0) || 0,
        estoque:   v.availQuantity || v.inventory || 0,
      };
    });

    // URL da descrição completa (HTML separado do AliExpress)
    const dm = DATA.descriptionModule || {};
    resultado._desc_url = dm.descriptionUrl || dm.detailDesc || "";

    // Vídeo
    const vm = DATA.videoModule || {};
    if (vm.mp4Url) resultado.video = vm.mp4Url.startsWith("//") ? "https:" + vm.mp4Url : vm.mp4Url;

    // Avaliação média e total
    const fm = DATA.titleModule || {};
    if (fm.feedbackRating) {
      resultado.avaliacao = fm.feedbackRating.averageStar || "";
      resultado.vendas = (fm.feedbackRating.totalValidNum || "") + " avaliações";
    }
  }

  // PREÇO via DOM (fallback se JSON não trouxe)
  if (!resultado.preco_brl) {
    for (const sel of [".product-price-value","[class*='price--current']","[class*='price_current']",
                       ".pdp-comp-price-current","[class*='uniform-banner-box-price']",
                       "[class*='product-price-current']","[class*='es--wrap']"]) {
      const el = document.querySelector(sel);
      if (el) { const v = num(el.textContent); if (v > 0) { resultado.preco_brl = v; break; } }
    }
  }
  if (!resultado.preco_original_brl) {
    const o = document.querySelector("[class*='price--del'], [class*='price-del'], [class*='price--lineThrough']");
    if (o) resultado.preco_original_brl = num(o.textContent);
  }

  // IMAGENS — múltiplas estratégias
  const imgsSeen = new Set();
  function addImg(src) {
    if (!src || src.startsWith("data:") || src.length < 20) return;
    if (src.startsWith("//")) src = "https:" + src;
    if (!src.startsWith("http")) return;
    // Remove sufixos de tamanho e força versão grande
    src = src.replace(/_\d+x\d+\.[a-z]+/i, "").replace(/\.(jpg|png|webp).*/i, ".$1");
    if (!src.match(/\.(jpg|jpeg|png|webp)/i)) src += ".jpg";
    if (!imgsSeen.has(src)) { imgsSeen.add(src); resultado.imagens.push(src); }
  }

  // 1. Miniaturas do carrossel
  document.querySelectorAll([
    ".images-view-item img",
    "[class*='thumb--'] img",
    "[class*='thumbnails'] img",
    ".pdp-comp-image-thumb img",
    "[class*='gallery-image'] img",
    "[class*='slider--item'] img",
  ].join(",")).forEach(img => addImg(img.src || img.dataset.src || ""));

  // 2. Imagem principal
  document.querySelectorAll([
    ".magnifier-image",
    ".pdp-comp-image-view img",
    "[class*='main-image'] img",
    ".images-view-item .img",
  ].join(",")).forEach(img => addImg(img.src || ""));

  // 3. Extrai do código da página (window.__pageData ou scripts)
  try {
    // Tenta window.runParams
    const rp = window.runParams;
    if (rp) {
      const str = JSON.stringify(rp);
      const matches = str.matchAll(/"(https:\/\/ae\d+\.alicdn\.com\/kf\/[^"]+\.(?:jpg|png|webp)[^"]*)"/g);
      for (const m of matches) addImg(m[1]);
    }
  } catch(e) {}

  try {
    // Procura em todos os scripts
    for (const script of document.scripts) {
      const txt = script.textContent;
      if (!txt || txt.length < 50 || txt.length > 500000) continue;

      // imagePathList
      const m1 = txt.match(/"imagePathList"\s*:\s*(\[[^\]]+\])/);
      if (m1) {
        try { JSON.parse(m1[1]).forEach(addImg); } catch(e) {}
      }

      // ae-alicdn urls
      const urlMatches = txt.matchAll(/https:\/\/ae\d+\.alicdn\.com\/kf\/[a-zA-Z0-9_]+\.(jpg|png|webp)/g);
      for (const m of urlMatches) addImg(m[0]);
    }
  } catch(e) {}

  resultado.imagens = resultado.imagens.slice(0, 10);

  // VARIANTES via DOM — SEMPRE roda e MESCLA (garante TODAS as opções visíveis)
  try {
    const gruposVariante = document.querySelectorAll([
      "[class*='sku-item--property']",
      "[class*='product-prop']",
      ".pdp-comp-property",
      "[class*='sku-property-list']",
    ].join(","));

    gruposVariante.forEach(grupo => {
      const labelEl = grupo.querySelector(
        "[class*='property-title'], [class*='sku-title'], dt, label, .title"
      );
      let nome = (labelEl?.textContent || "").trim().replace(/[:：].*/,"").trim();
      if (!nome || nome.length > 40) return;

      const opcoes = [];
      const vistos = new Set();
      grupo.querySelectorAll("[class*='sku-property-item'], li, [data-sku-col], button, dd span, [role='option']").forEach(op => {
        const img = op.querySelector("img");
        const txt = (op.getAttribute("title") || op.textContent || "").trim();
        if (!txt || txt.length > 60) return;
        if (vistos.has(txt)) return; vistos.add(txt);
        if (img?.src) opcoes.push({ label: img.alt || txt, img: img.src.startsWith("//") ? "https:"+img.src : img.src });
        else opcoes.push({ label: txt });
      });
      if (!opcoes.length) return;

      // Mescla: se já existe essa propriedade (do JSON), usa a que tiver MAIS opções
      const existente = resultado.variantes.find(v => v.nome.toLowerCase() === nome.toLowerCase());
      if (existente) {
        if (opcoes.length > existente.opcoes.length) {
          // mantém os valueIds do JSON quando os labels baterem
          existente.opcoes = opcoes.map(o => {
            const m = existente.opcoes.find(x => x.label.toLowerCase() === o.label.toLowerCase());
            return m ? { ...o, valueId: m.valueId } : o;
          });
        }
      } else {
        resultado.variantes.push({ nome, opcoes: opcoes.slice(0, 60) });
      }
    });
  } catch(e) {}

  // AVALIAÇÃO E VENDAS
  const avalEl = document.querySelector("[class*='reviewer-score'], [class*='stars-num'], .overview-rating-average");
  if (avalEl) resultado.avaliacao = avalEl.textContent.trim();

  const vendasEl = document.querySelector("[class*='trade--trade'], [class*='sold-count'], [class*='order-num']");
  if (vendasEl) resultado.vendas = vendasEl.textContent.trim();

  // ── REVIEWS REAIS (API de feedback do AliExpress) ──────────────────────────
  // Busca TODAS as avaliações: várias páginas, filtro "all" primeiro.
  if (resultado.pid) {
    const vistos = new Set();
    function addReview(ev) {
      const texto = (ev.buyerTranslationFeedback || ev.buyerFeedback || ev.feedback || "").trim();
      const fotos = (ev.images || ev.thumbnails || []).map(i => {
        const u = typeof i === "string" ? i : (i.url || i.image || "");
        return u.startsWith("//") ? "https:" + u : u;
      }).filter(Boolean);
      const chave = (ev.buyerName || ev.name || "") + texto.slice(0, 30);
      if (vistos.has(chave)) return;
      if (!texto && fotos.length === 0) return;
      vistos.add(chave);
      const evalNum = ev.buyerEval || ev.rating || ev.star || 100;
      resultado.reviews.push({
        nome:  ev.buyerName || ev.name || "Cliente AliExpress",
        pais:  ev.buyerCountry || ev.country || "",
        nota:  evalNum > 5 ? Math.round(evalNum / 20) : Math.round(evalNum),
        texto, fotos,
        data:  ev.evalDate || ev.evaDate || ev.date || "",
      });
    }

    // fetch com timeout — nunca trava a captura
    async function fetchT(url, ms) {
      const ctrl = new AbortController();
      const t = setTimeout(() => ctrl.abort(), ms);
      try { return await fetch(url, { credentials: "include", signal: ctrl.signal }); }
      finally { clearTimeout(t); }
    }
    // Busca avaliações (máx 3 páginas, com timeout)
    for (let page = 1; page <= 3; page++) {
      let achou = false;
      try {
        const url = `https://feedback.aliexpress.com/pc/searchEvaluation.do?productId=${resultado.pid}&lang=pt_BR&country=BR&page=${page}&pageSize=20&filter=all&sort=complex_default`;
        const resp = await fetchT(url, 4000);
        const json = await resp.json();
        const lista = (json.data && json.data.evaViewList) || json.evaViewList ||
                      (json.data && json.data.evaluationList) || [];
        if (lista.length) { achou = true; lista.forEach(addReview); }
      } catch (e) {}
      if (!achou) break;
      if (resultado.reviews.length >= 30) break;
    }
  }

  // ── DESCRIÇÃO COMPLETA (HTML separado do AliExpress) ───────────────────────
  if (resultado._desc_url) {
    try {
      let url = resultado._desc_url;
      if (url.startsWith("//")) url = "https:" + url;
      const ctrl = new AbortController();
      const tid = setTimeout(() => ctrl.abort(), 4000);
      const html = await (await fetch(url, { credentials: "include", signal: ctrl.signal })).text();
      clearTimeout(tid);
      // Imagens da descrição (fotos detalhadas, tabelas em imagem)
      const imgs = [];
      for (const m of html.matchAll(/<img[^>]+src=["']([^"']+)["']/gi)) {
        let u = m[1]; if (u.startsWith("//")) u = "https:" + u;
        if (u.startsWith("http") && !imgs.includes(u)) imgs.push(u);
      }
      resultado.descricao_imagens = imgs.slice(0, 20);
      // Texto da descrição (limpo de scripts/estilos/tags)
      const texto = html
        .replace(/<script[\s\S]*?<\/script>/gi, "")
        .replace(/<style[\s\S]*?<\/style>/gi, "")
        .replace(/<[^>]+>/g, " ")
        .replace(/&nbsp;/g, " ").replace(/\s+/g, " ").trim();
      resultado.descricao_texto = texto.slice(0, 3000);
    } catch (e) {}
  }

  return resultado;
}

// ── RENDER PREVIEW ────────────────────────────────────────────────────────────
function renderPreview(d) {
  document.getElementById("prev-titulo").textContent = d.titulo || "Produto AliExpress";
  // Mostra contagem de reviews capturadas
  const nRev = (d.reviews || []).length;
  const nFoto = (d.reviews || []).filter(r => r.fotos && r.fotos.length).length;
  const elRev = document.getElementById("prev-reviews");
  const nVar = (d.variantes || []).reduce((s, v) => s + (v.opcoes ? v.opcoes.length : 0), 0);
  const nSpec = (d.especificacoes || []).length;
  const nDescImg = (d.descricao_imagens || []).length;
  if (elRev) elRev.innerHTML =
    `⭐ ${nRev} avaliações (${nFoto} c/ foto)<br>` +
    `🎨 ${(d.variantes||[]).length} variações (${nVar} opções) · 📋 ${nSpec} specs · 🖼️ ${nDescImg} imgs descrição`;

  const precoBrl = d.preco_brl || 0;
  document.getElementById("prev-preco").textContent =
    precoBrl ? `R$ ${precoBrl.toFixed(2).replace(".",",")}` : "Preço não detectado";
  if (d.preco_original_brl > precoBrl) {
    document.getElementById("prev-preco-de").textContent =
      `R$ ${d.preco_original_brl.toFixed(2).replace(".",",")}`;
  }

  // Galeria
  const galeria = document.getElementById("prev-galeria");
  galeria.innerHTML = "";
  if (d.imagens?.length) {
    _imgSelecionada = d.imagens[0];
    document.getElementById("prev-img").src = d.imagens[0];
    d.imagens.forEach((img, i) => {
      const el = document.createElement("img");
      el.src = img;
      el.title = `Foto ${i+1}`;
      if (i === 0) el.classList.add("sel");
      el.onerror = () => el.style.display = "none";
      el.onclick = () => {
        _imgSelecionada = img;
        document.getElementById("prev-img").src = img;
        galeria.querySelectorAll("img").forEach(x => x.classList.remove("sel"));
        el.classList.add("sel");
      };
      galeria.appendChild(el);
    });
  } else {
    galeria.innerHTML = '<p style="color:#64748b;font-size:11px;padding:4px 0;">Nenhuma foto capturada — o AliExpress pode ter bloqueado. Tente recarregar a página.</p>';
  }

  // Variantes
  const varEl = document.getElementById("prev-variantes");
  varEl.innerHTML = "";
  d.variantes?.forEach(v => {
    const div = document.createElement("div");
    div.className = "variantes";
    div.innerHTML = `<div class="var-nome">${v.nome}</div>
      <div class="var-opcoes">${v.opcoes.map(op =>
        `<span class="var-op">${op.img ? `<img src="${op.img}" onerror="this.style.display='none'"/>` : ""}${op.label}</span>`
      ).join("")}</div>`;
    varEl.appendChild(div);
  });

  // Precificação
  const custoUsd   = precoBrl > 0 ? precoBrl / USD_BRL : 15;
  const custoBrl   = custoUsd * USD_BRL * (1 + FRETE_PCT/100);
  const divisor    = 1 - (GATEWAY_PCT + MARGEM_ALVO) / 100;
  let   precoVenda = (custoBrl + TRAFEGO) / divisor;
  precoVenda       = Math.round(precoVenda / 10) * 10 - 1;
  const taxaGw     = precoVenda * GATEWAY_PCT / 100;
  const lucro      = precoVenda - custoBrl - TRAFEGO - taxaGw;
  const margem     = ((lucro / precoVenda) * 100).toFixed(1);

  document.getElementById("p-custo").textContent   = `R$ ${custoBrl.toFixed(2).replace(".",",")}`;
  document.getElementById("p-trafego").textContent = `R$ ${TRAFEGO},00`;
  document.getElementById("p-gateway").textContent = `R$ ${taxaGw.toFixed(2).replace(".",",")}`;
  document.getElementById("p-venda").textContent   = `R$ ${precoVenda}`;
  document.getElementById("p-margem").textContent  =
    `Margem: ${margem}% • Lucro por venda: R$ ${lucro.toFixed(2).replace(".",",")}`;

  _dadosCapturados._preco_venda = precoVenda;
  _dadosCapturados._preco_de    = Math.round(precoVenda * 1.65 / 10) * 10;
  _dadosCapturados._imagem_sel  = _imgSelecionada;
}

// Monta uma descrição a partir do título + especificações capturadas
function _montarDescricao(d) {
  const partes = [];
  if (d.especificacoes && d.especificacoes.length) {
    partes.push(d.especificacoes.slice(0, 15).map(e => `${e.nome}: ${e.valor}`).join(". "));
  }
  if (d.descricao_texto) partes.push(d.descricao_texto);
  return partes.join("\n\n") || d.titulo || "";
}

// ── ENVIAR PARA O PAINEL ──────────────────────────────────────────────────────
async function enviarParaPainel() {
  const adminUrl   = document.getElementById("cfg-url").value.trim().replace(/\/$/, "");
  const adminToken = document.getElementById("cfg-token").value.trim();

  if (!adminUrl)   { alert("Informe a URL do painel admin."); return; }
  if (!adminToken) { alert("Informe o token admin."); return; }

  chrome.storage.local.set({ admin_url: adminUrl, admin_token: adminToken });

  const d = _dadosCapturados;
  const produto = {
    titulo:          d.titulo || "Produto AliExpress",
    imagem:          _imgSelecionada || d.imagens?.[0] || "",
    imagens_extra:   d.imagens || [],
    preco_venda:     d._preco_venda || 99,
    preco_de:        d._preco_de || 199,
    link_aliexpress: d.url,
    variantes:       d.variantes || [],
    avaliacao:       parseFloat(d.avaliacao) || 4.8,
    vendas:          parseInt((d.vendas || "0").replace(/\D/g, "")) || 0,
    ativo:           true,
    badge:           "Novo",
    categoria:       "acessorios",
    especificacoes:  d.especificacoes || [],
    skus:            d.skus || [],
    descricao_imagens: d.descricao_imagens || [],
    descricao:       _montarDescricao(d),
  };

  try {
    const resp = await fetch(`${adminUrl}/api/admin/produtos`, {
      method:  "POST",
      headers: {
        "Content-Type":  "application/json",
        "Authorization": `Bearer ${adminToken}`,
      },
      body: JSON.stringify(produto),
    });
    if (resp.status === 401) {
      alert("Token admin inválido ou expirado.\n\nGere um novo token: faça login no painel, abra o Console (F12) e digite localStorage.getItem('admin_token')");
      return;
    }
    const data = await resp.json();
    if (data.ok) {
      // Importa as avaliações reais para o produto recém-criado
      const reviews = _dadosCapturados.reviews || [];
      const novoId  = data.produto && data.produto.id;
      if (novoId && reviews.length) {
        try {
          const rev = await fetch(`${adminUrl}/api/admin/avaliacoes/importar`, {
            method:  "POST",
            headers: {"Content-Type":"application/json", "Authorization":`Bearer ${adminToken}`},
            body: JSON.stringify({ produto_id: novoId, reviews }),
          });
          const rd = await rev.json();
          if (rd.ok) {
            const msg = document.querySelector("#estado-sucesso p");
            if (msg) msg.textContent = `Produto + ${rd.importadas} avaliações reais importadas! Revise e publique.`;
          }
        } catch(e) {}
      }
      document.getElementById("link-painel").href = `${adminUrl}/admin-panel/`;
      mostrar("sucesso");
    } else {
      alert("Erro do painel: " + (data.erro || JSON.stringify(data)));
    }
  } catch(e) {
    alert("Erro de conexão: " + e.message);
  }
}

// ── UTILS ─────────────────────────────────────────────────────────────────────
function mostrar(estado) {
  ["inicial","loading","preview","erro","sucesso"].forEach(e => {
    document.getElementById("estado-" + e).style.display = e === estado ? "block" : "none";
  });
}
function mostrarErro(msg) {
  document.getElementById("msg-erro").textContent = msg;
  mostrar("erro");
}
function resetar() {
  _dadosCapturados = null;
  _imgSelecionada  = "";
  mostrar("inicial");
}

// ── EVENT LISTENERS (sem onclick inline) ──────────────────────────────────────
document.getElementById("btn-capturar").addEventListener("click", capturar);
document.getElementById("btn-enviar").addEventListener("click", enviarParaPainel);
document.getElementById("btn-voltar").addEventListener("click", resetar);
document.getElementById("btn-tentar-novamente").addEventListener("click", resetar);
document.getElementById("btn-outro").addEventListener("click", resetar);

mostrar("inicial");
