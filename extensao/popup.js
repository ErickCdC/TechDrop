const USD_BRL = 5.70, TRAFEGO = 15, GATEWAY_PCT = 3.5, MARGEM_ALVO = 35;
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
    preco_brl: 0,
    preco_original_brl: 0,
    variantes: [],
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

  // PREÇO ATUAL
  const precoSeletores = [
    ".product-price-value",
    "[class*='price--current']",
    "[class*='price_current']",
    ".pdp-comp-price-current",
    "[class*='uniform-banner-box-price']",
    "[class*='product-price-current']",
  ];
  for (const sel of precoSeletores) {
    const el = document.querySelector(sel);
    if (el) {
      const txt = el.textContent.replace(/[^\d,]/g, "").replace(",", ".");
      const val = parseFloat(txt);
      if (val > 0) { resultado.preco_brl = val; break; }
    }
  }

  // PREÇO ORIGINAL (riscado)
  const precoOrigSel = document.querySelector(
    "[class*='price--del'], [class*='price-del'], .product-price-original, [class*='price--lineThrough']"
  );
  if (precoOrigSel) {
    const txt = precoOrigSel.textContent.replace(/[^\d,]/g, "").replace(",", ".");
    resultado.preco_original_brl = parseFloat(txt) || 0;
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

  // VARIANTES
  try {
    const gruposVariante = document.querySelectorAll([
      "[class*='sku-item--property']",
      "[class*='product-prop']",
      ".pdp-comp-property",
      "[class*='sku-property-list'] > li",
    ].join(","));

    gruposVariante.forEach(grupo => {
      const labelEl = grupo.querySelector(
        "[class*='property-title'], [class*='sku-title'], dt, label, .title"
      );
      const nome = (labelEl?.textContent || "").trim().replace(":", "");
      if (!nome || nome.length > 30) return;

      const opcoes = [];
      grupo.querySelectorAll("li, [class*='sku-property-item'], dd span").forEach(op => {
        const img = op.querySelector("img");
        const txt = op.textContent.trim();
        if (img?.src) opcoes.push({ label: img.alt || txt, img: img.src });
        else if (txt && txt.length < 40) opcoes.push({ label: txt });
      });

      if (opcoes.length > 0) resultado.variantes.push({ nome, opcoes: opcoes.slice(0, 15) });
    });
  } catch(e) {}

  // AVALIAÇÃO E VENDAS
  const avalEl = document.querySelector("[class*='reviewer-score'], [class*='stars-num'], .overview-rating-average");
  if (avalEl) resultado.avaliacao = avalEl.textContent.trim();

  const vendasEl = document.querySelector("[class*='trade--trade'], [class*='sold-count'], [class*='order-num']");
  if (vendasEl) resultado.vendas = vendasEl.textContent.trim();

  // ── REVIEWS REAIS (API de feedback do AliExpress) ──────────────────────────
  if (resultado.pid) {
    try {
      // Busca 2 páginas: primeiro as com foto, depois gerais
      const consultas = [
        `https://feedback.aliexpress.com/pc/searchEvaluation.do?productId=${resultado.pid}&lang=pt_BR&country=BR&page=1&pageSize=20&filter=image&sort=complex_default`,
        `https://feedback.aliexpress.com/pc/searchEvaluation.do?productId=${resultado.pid}&lang=pt_BR&country=BR&page=1&pageSize=20&filter=all&sort=complex_default`,
      ];
      const vistos = new Set();
      for (const url of consultas) {
        try {
          const resp = await fetch(url, { credentials: "include" });
          const json = await resp.json();
          const lista = (json.data && json.data.evaViewList) || json.evaViewList || [];
          for (const ev of lista) {
            const texto = (ev.buyerTranslationFeedback || ev.buyerFeedback || "").trim();
            const chave = (ev.buyerName || "") + texto.slice(0, 30);
            if (vistos.has(chave)) continue;
            vistos.add(chave);
            const fotos = (ev.images || []).map(i => (i.startsWith("//") ? "https:" + i : i));
            if (!texto && fotos.length === 0) continue;
            resultado.reviews.push({
              nome:  ev.buyerName || "Cliente AliExpress",
              pais:  ev.buyerCountry || "",
              nota:  Math.round((ev.buyerEval || 100) / 20),  // 100 -> 5 estrelas
              texto: texto,
              fotos: fotos,
              data:  ev.evalDate || ev.evaDate || "",
            });
          }
        } catch (e) {}
        if (resultado.reviews.length >= 25) break;
      }
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
  if (elRev) elRev.textContent = nRev ? `⭐ ${nRev} avaliações capturadas (${nFoto} com foto)` : "Sem avaliações encontradas";

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
  const custoBrl   = custoUsd * USD_BRL * 1.12;
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
    descricao:       "",
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
