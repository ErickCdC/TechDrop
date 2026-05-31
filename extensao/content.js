/**
 * Content script — roda na página do AliExpress.
 * Extrai todos os dados do produto e responde quando solicitado.
 */

function extrairProduto() {
  const dados = {
    url:      window.location.href,
    pid:      extrairId(window.location.href),
    titulo:   "",
    imagens:  [],
    video:    null,
    preco_brl: 0,
    preco_original_brl: 0,
    variantes: [],
    avaliacao: "",
    vendas:    "",
  };

  // ── TÍTULO ────────────────────────────────────────────────────────────────
  const tituloEl = document.querySelector([
    "h1[data-pl='product-title']",
    ".product-title-text",
    "h1.title--wrap--",
    "h1",
  ].join(","));
  if (tituloEl) dados.titulo = tituloEl.textContent.trim();

  // ── PREÇOS ────────────────────────────────────────────────────────────────
  const precoSel = document.querySelector([
    ".product-price-value",
    "[class*='price--current']",
    "[class*='uniform-banner-box-price']",
    ".pdp-comp-price-current",
    "[class*='price_current']",
  ].join(","));
  if (precoSel) {
    const txt = precoSel.textContent.replace(/[^\d,.]/g, "").replace(",", ".");
    dados.preco_brl = parseFloat(txt) || 0;
  }

  const precoOrigSel = document.querySelector([
    "[class*='price--del']",
    "[class*='price-del']",
    ".product-price-original",
  ].join(","));
  if (precoOrigSel) {
    const txt = precoOrigSel.textContent.replace(/[^\d,.]/g, "").replace(",", ".");
    dados.preco_original_brl = parseFloat(txt) || 0;
  }

  // ── IMAGENS ───────────────────────────────────────────────────────────────
  const imgsSeen = new Set();

  // 1. Tenta pegar do carrossel de miniaturas
  document.querySelectorAll([
    ".images-view-item img",
    ".slider--item-- img",
    "[class*='thumb'] img",
    "[class*='gallery'] img",
    ".pdp-comp-image-thumb img",
    ".img-magnifier-container img",
  ].join(",")).forEach(img => {
    let src = img.src || img.dataset.src || img.dataset.lazySrc || "";
    src = limparUrlImagem(src);
    if (src && !imgsSeen.has(src)) { imgsSeen.add(src); dados.imagens.push(src); }
  });

  // 2. Imagem principal se não tiver miniaturas
  if (dados.imagens.length === 0) {
    document.querySelectorAll([
      ".magnifier-image",
      ".images-view-item .img",
      "[class*='main-image'] img",
      ".pdp-comp-image-view img",
      "img[class*='product']",
    ].join(",")).forEach(img => {
      let src = img.src || "";
      src = limparUrlImagem(src);
      if (src && !imgsSeen.has(src)) { imgsSeen.add(src); dados.imagens.push(src); }
    });
  }

  // 3. Tenta extrair do window data se disponível
  try {
    const scripts = Array.from(document.scripts);
    for (const s of scripts) {
      const txt = s.textContent;
      if (!txt || txt.length < 100) continue;

      // Procura array de imagens
      const m = txt.match(/"imagePathList"\s*:\s*(\[[^\]]+\])/);
      if (m) {
        const imgs = JSON.parse(m[1]);
        imgs.forEach(url => {
          const clean = limparUrlImagem(url);
          if (clean && !imgsSeen.has(clean)) { imgsSeen.add(clean); dados.imagens.push(clean); }
        });
      }

      // Preço via window data
      if (dados.preco_brl === 0) {
        const pm = txt.match(/"discountPrice"\s*:\s*"?([\d.]+)"?/);
        if (pm) dados.preco_brl = parseFloat(pm[1]);
      }
    }
  } catch(e) {}

  // ── VÍDEO ─────────────────────────────────────────────────────────────────
  const videoEl = document.querySelector("video source, video");
  if (videoEl) dados.video = videoEl.src || videoEl.currentSrc || null;

  // ── VARIANTES ─────────────────────────────────────────────────────────────
  const gruposVariante = document.querySelectorAll([
    "[class*='sku-item']",
    "[class*='product-prop']",
    ".pdp-comp-property",
    "[class*='skuProperties']",
  ].join(","));

  gruposVariante.forEach(grupo => {
    const labelEl = grupo.querySelector([
      "[class*='property-title']",
      "[class*='sku-title']",
      "span.title",
      "dt",
      "label",
    ].join(","));
    const nome = labelEl ? labelEl.textContent.trim().replace(":", "") : "";
    if (!nome) return;

    const opcoes = [];
    grupo.querySelectorAll([
      "[class*='sku-property-item']",
      "[class*='prop-item']",
      "li",
      "dd span",
    ].join(",")).forEach(op => {
      const img = op.querySelector("img");
      const txt = op.textContent.trim();
      if (img) {
        opcoes.push({ label: img.alt || txt, img: img.src });
      } else if (txt && txt.length < 50) {
        opcoes.push({ label: txt });
      }
    });

    if (opcoes.length > 0) {
      dados.variantes.push({ nome, opcoes: opcoes.slice(0, 20) });
    }
  });

  // ── AVALIAÇÃO ─────────────────────────────────────────────────────────────
  const avalEl = document.querySelector([
    "[class*='reviewer-score']",
    "[class*='stars-num']",
    ".overview-rating-average",
  ].join(","));
  if (avalEl) dados.avaliacao = avalEl.textContent.trim();

  const vendasEl = document.querySelector([
    "[class*='trade--trade']",
    "[class*='sold-count']",
    "[class*='order-num']",
  ].join(","));
  if (vendasEl) dados.vendas = vendasEl.textContent.trim();

  // Remove duplicatas e limita
  dados.imagens = [...new Set(dados.imagens)].slice(0, 10);

  return dados;
}

function limparUrlImagem(url) {
  if (!url || url.startsWith("data:") || url.includes("placeholder")) return "";
  if (url.startsWith("//")) url = "https:" + url;
  if (!url.startsWith("http")) return "";
  // Remove parâmetros de tamanho e pega versão grande
  url = url.split("_.webp")[0].split("_.jpg")[0].split("_.png")[0];
  url = url.replace(/_\d+x\d+[^.]*(\.(jpg|png|webp))/i, "_480x480.$1");
  if (!url.match(/\.(jpg|jpeg|png|webp)/i)) url += "_480x480.jpg";
  return url;
}

function extrairId(url) {
  const m = url.match(/\/item\/(\d+)/);
  return m ? m[1] : "";
}

// Escuta mensagens do popup
chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.action === "extrair") {
    const dados = extrairProduto();
    sendResponse({ ok: true, dados });
  }
  return true;
});
