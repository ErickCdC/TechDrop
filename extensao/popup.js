const USD_BRL = 5.70, TRAFEGO = 15, GATEWAY_PCT = 3.5, MARGEM_ALVO = 35;
let _dadosCapturados = null;
let _imgSelecionada  = "";

// ── CARREGA CONFIG SALVA ───────────────────────────────────────────────────────
chrome.storage.local.get(["admin_url", "admin_token"], (cfg) => {
  if (cfg.admin_url)   document.getElementById("cfg-url").value   = cfg.admin_url;
  if (cfg.admin_token) document.getElementById("cfg-token").value = cfg.admin_token;
});

// ── CAPTURAR ──────────────────────────────────────────────────────────────────
async function capturar() {
  mostrar("loading");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab.url.includes("aliexpress.com/item/")) {
      mostrarErro("Abra a página de um produto AliExpress primeiro.");
      return;
    }

    // Injeta o content script se ainda não estiver ativo
    await chrome.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });

    const resp = await chrome.tabs.sendMessage(tab.id, { action: "extrair" });
    if (!resp?.ok || !resp.dados) {
      mostrarErro("Não foi possível capturar. Recarregue a página e tente novamente.");
      return;
    }

    _dadosCapturados = resp.dados;
    renderPreview(resp.dados);
    mostrar("preview");
  } catch(e) {
    mostrarErro("Erro: " + e.message);
  }
}

// ── RENDER PREVIEW ────────────────────────────────────────────────────────────
function renderPreview(d) {
  document.getElementById("prev-titulo").textContent = d.titulo || "Produto AliExpress";

  // Preços
  const precoBrl = d.preco_brl || 0;
  document.getElementById("prev-preco").textContent = precoBrl ? `R$ ${precoBrl.toFixed(2).replace(".",",")}` : "—";
  if (d.preco_original_brl && d.preco_original_brl > precoBrl) {
    document.getElementById("prev-preco-de").textContent = `R$ ${d.preco_original_brl.toFixed(2).replace(".",",")}`;
  }

  // Galeria
  const galeria = document.getElementById("prev-galeria");
  galeria.innerHTML = "";
  if (d.imagens && d.imagens.length) {
    _imgSelecionada = d.imagens[0];
    document.getElementById("prev-img").src = d.imagens[0];
    d.imagens.forEach((img, i) => {
      const el = document.createElement("img");
      el.src = img;
      el.title = `Foto ${i+1}`;
      if (i === 0) el.classList.add("sel");
      el.onclick = () => {
        _imgSelecionada = img;
        document.getElementById("prev-img").src = img;
        document.querySelectorAll(".galeria img").forEach(x => x.classList.remove("sel"));
        el.classList.add("sel");
      };
      galeria.appendChild(el);
    });
  } else {
    galeria.innerHTML = '<span style="color:#64748b;font-size:11px;">Nenhuma foto capturada</span>';
  }

  // Variantes
  const varEl = document.getElementById("prev-variantes");
  varEl.innerHTML = "";
  if (d.variantes && d.variantes.length) {
    d.variantes.forEach(v => {
      const div = document.createElement("div");
      div.className = "variantes";
      div.innerHTML = `<div class="var-nome">${v.nome}</div>
        <div class="var-opcoes">${v.opcoes.map(op =>
          `<span class="var-op">${op.img ? `<img src="${op.img}"/>` : ""}${op.label}</span>`
        ).join("")}</div>`;
      varEl.appendChild(div);
    });
  }

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
  document.getElementById("p-trafego").textContent = `R$ ${TRAFEGO.toFixed(2).replace(".",",")}`;
  document.getElementById("p-gateway").textContent = `R$ ${taxaGw.toFixed(2).replace(".",",")}`;
  document.getElementById("p-venda").textContent   = `R$ ${precoVenda}`;
  document.getElementById("p-margem").textContent  = `Margem líquida: ${margem}% • Lucro: R$ ${lucro.toFixed(2).replace(".",",")} por venda`;

  _dadosCapturados._preco_venda = precoVenda;
  _dadosCapturados._preco_de    = Math.round(precoVenda * 1.65 / 10) * 10;
  _dadosCapturados._imagem_sel  = _imgSelecionada;
}

// ── ENVIAR PARA O PAINEL ──────────────────────────────────────────────────────
async function enviarParaPainel() {
  const adminUrl   = document.getElementById("cfg-url").value.trim().replace(/\/$/, "");
  const adminToken = document.getElementById("cfg-token").value.trim();

  if (!adminUrl)   { alert("Informe a URL do painel."); return; }
  if (!adminToken) { alert("Informe o token admin."); return; }

  // Salva config
  chrome.storage.local.set({ admin_url: adminUrl, admin_token: adminToken });

  const d = _dadosCapturados;
  const produto = {
    titulo:          d.titulo || "Produto AliExpress",
    imagem:          d._imagem_sel || (d.imagens?.[0] || ""),
    imagens_extra:   d.imagens || [],
    preco_venda:     d._preco_venda,
    preco_de:        d._preco_de,
    link_aliexpress: d.url,
    variantes:       d.variantes || [],
    avaliacao:       parseFloat(d.avaliacao) || 4.8,
    vendas:          parseInt(d.vendas) || 0,
    ativo:           true,
    badge:           "Novo",
    categoria:       "acessorios",
    descricao:       "",
  };

  try {
    const resp = await fetch(`${adminUrl}/api/admin/produtos`, {
      method:  "POST",
      headers: { "Content-Type": "application/json", "Authorization": `Bearer ${adminToken}` },
      body:    JSON.stringify(produto),
    });
    const data = await resp.json();
    if (data.ok) {
      document.getElementById("link-painel").href = `${adminUrl}/admin-panel/`;
      mostrar("sucesso");
    } else {
      alert("Erro: " + (data.erro || "resposta inválida"));
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

// Começa no estado inicial
mostrar("inicial");
