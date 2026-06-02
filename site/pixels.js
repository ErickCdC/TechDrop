/**
 * Pixels de rastreamento — injeta Meta, TikTok e Google a partir da config do admin.
 * Expõe window.track(evento, dados) para disparar conversões.
 */
(async function () {
  let c = {};
  try {
    const d = await (await fetch("/api/config")).json();
    if (d.ok) c = d.config || {};
  } catch (e) {}
  window._pixelCfg = c;

  // ── META (Facebook / Instagram) PIXEL ──────────────────────────────────────
  if (c.pixel_meta) {
    !function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?
    n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;
    n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;
    t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,
    document,'script','https://connect.facebook.net/en_US/fbevents.js');
    fbq('init', c.pixel_meta);
    fbq('track', 'PageView');
  }

  // ── TIKTOK PIXEL ────────────────────────────────────────────────────────────
  if (c.pixel_tiktok) {
    !function(w,d,t){w.TiktokAnalyticsObject=t;var ttq=w[t]=w[t]||[];
    ttq.methods=["page","track","identify","instances","debug","on","off","once","ready","alias","group","enableCookie","disableCookie"];
    ttq.setAndDefer=function(t,e){t[e]=function(){t.push([e].concat(Array.prototype.slice.call(arguments,0)))}};
    for(var i=0;i<ttq.methods.length;i++)ttq.setAndDefer(ttq,ttq.methods[i]);
    ttq.load=function(e){var i="https://analytics.tiktok.com/i18n/pixel/events.js";var o=d.createElement("script");
    o.type="text/javascript";o.async=!0;o.src=i+"?sdkid="+e;var a=d.getElementsByTagName("script")[0];a.parentNode.insertBefore(o,a)};
    ttq.load(c.pixel_tiktok);ttq.page();}(window,document,'ttq');
  }

  // ── GOOGLE ANALYTICS / ADS (gtag) ───────────────────────────────────────────
  if (c.google_analytics || c.google_ads) {
    const gid = c.google_analytics || c.google_ads;
    const s = document.createElement("script");
    s.async = true; s.src = "https://www.googletagmanager.com/gtag/js?id=" + gid;
    document.head.appendChild(s);
    window.dataLayer = window.dataLayer || [];
    window.gtag = function () { dataLayer.push(arguments); };
    gtag("js", new Date());
    if (c.google_analytics) gtag("config", c.google_analytics);
    if (c.google_ads)       gtag("config", c.google_ads);
  }
})();

// Dispara um evento de conversão em todos os pixels configurados
window.track = function (evento, dados) {
  dados = dados || {};
  try { if (window.fbq)  fbq("track", evento, dados); } catch (e) {}
  try { if (window.ttq)  ttq.track(evento, dados);    } catch (e) {}
  try { if (window.gtag) gtag("event", evento, dados); } catch (e) {}
};
