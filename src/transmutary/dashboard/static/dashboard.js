/* transmutary dashboard — vanilla JS (U4). No framework, served same-origin
   (script-src 'self'). Theme three-state toggle + language switch + collapse.
   The FOUC-avoidance head script (sets data-theme/lang before first paint) lives
   inline in base.html with a per-request CSP nonce; this file is the post-load
   interaction layer. */
(function () {
  "use strict";

  var LANG_COOKIE = "tmtry-lang";
  var THEME_KEY = "tmtry-theme";

  function loadI18n() {
    var node = document.getElementById("i18n-dict");
    if (!node) return {en: {}, zh: {}};
    try { return JSON.parse(node.textContent || "{}"); } catch (e) { return {en: {}, zh: {}}; }
  }
  var I18N = loadI18n();

  function getCookie(name) {
    var m = document.cookie.match(new RegExp("(?:^|; )" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[1]) : null;
  }
  function setCookie(name, value) {
    // SameSite=Strict: pure same-origin UI preference. No HttpOnly (JS reads it).
    document.cookie = name + "=" + encodeURIComponent(value) + "; Path=/; SameSite=Strict; Max-Age=31536000";
  }
  function currentLang() {
    var c = getCookie(LANG_COOKIE);
    return c === "zh" || c === "en" ? c : (document.documentElement.lang.indexOf("zh") === 0 ? "zh" : "en");
  }
  function htmlLang(l) { return l === "zh" ? "zh-CN" : "en"; }

  function applyLang(lang) {
    var dict = I18N[lang] || I18N.en;
    document.documentElement.lang = htmlLang(lang);
    var nodes = document.querySelectorAll("[data-i18n]");
    for (var i = 0; i < nodes.length; i++) {
      var key = nodes[i].getAttribute("data-i18n");
      if (dict[key] != null) nodes[i].textContent = dict[key]; // textContent, never innerHTML (P0)
    }
    var lb = document.querySelector(".lang-btn .lang-label");
    if (lb) lb.textContent = dict["lang.toggle"];
    var tl = document.querySelector(".theme-btn .theme-label");
    if (tl) tl.textContent = dict["theme." + currentTheme()];
    var titleMeta = document.querySelector('meta[name="i18n-title-key"]');
    var titleKey = titleMeta ? titleMeta.getAttribute("content") : "";
    if (titleKey && dict[titleKey]) document.title = "transmutary — " + dict[titleKey];
  }

  function currentTheme() {
    try { return localStorage.getItem(THEME_KEY) || "system"; } catch (e) { return "system"; }
  }
  function applyTheme(t) {
    if (t === "system") document.documentElement.removeAttribute("data-theme");
    else document.documentElement.setAttribute("data-theme", t);
  }

  function init() {
    var langBtn = document.querySelector(".lang-btn");
    if (langBtn) {
      langBtn.addEventListener("click", function () {
        var next = currentLang() === "zh" ? "en" : "zh";
        setCookie(LANG_COOKIE, next);
        applyLang(next);
      });
    }
    var themeBtn = document.querySelector(".theme-btn");
    if (themeBtn) {
      themeBtn.addEventListener("click", function () {
        var cur = currentTheme();
        var next = cur === "light" ? "dark" : cur === "dark" ? "system" : "light";
        try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
        applyTheme(next);
        var dict = I18N[currentLang()] || I18N.en;
        var tl = themeBtn.querySelector(".theme-label");
        if (tl) tl.textContent = dict["theme." + next];
      });
    }
    // sync labels to server-rendered language on load
    applyLang(currentLang());

    // bilingual report body toggle
    document.querySelectorAll(".lang-toggle .lang-btn").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var lang = btn.getAttribute("data-lang");
        btn.parentElement.querySelectorAll(".lang-btn").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
        document.querySelectorAll("[data-lang-body]").forEach(function (el) {
          el.classList.toggle("hidden", el.getAttribute("data-lang-body") !== lang);
        });
      });
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }

  /* Auto-dismiss save-confirmation messages after 4 s. */
  var msg = document.querySelector(".saved");
  if (msg) {
    setTimeout(function () { msg.remove(); }, 4000);
  }

  /* LLM provider presets */
  var presets = {
    openai:    { url: "https://api.openai.com/v1", strong: "gpt-4o", cheap: "gpt-4o-mini", embed: "text-embedding-3-small" },
    anthropic: { url: "https://api.anthropic.com", strong: "claude-sonnet-4-6", cheap: "claude-haiku-4-5", embed: "" },
    minimax:   { url: "https://api.minimaxi.com/anthropic", strong: "", cheap: "", embed: "" },
    azure:     { url: "", strong: "", cheap: "", embed: "" },
  };
  window.applyProviderPreset = function (sel) {
    var p = presets[sel.value];
    if (!p) return;
    var urlEl = document.getElementById("llm-url");
    var sEl = document.getElementById("llm-strong");
    var cEl = document.getElementById("llm-cheap");
    var eEl = document.getElementById("llm-embed");
    if (p.url && !urlEl.value) urlEl.value = p.url;
    if (p.strong && !sEl.value) sEl.value = p.strong;
    if (p.cheap && !cEl.value) cEl.value = p.cheap;
    if (p.embed && !eEl.value) eEl.value = p.embed;
  };

  /* LLM test connection */
  window.testLLM = function (btn) {
    var result = document.getElementById("llm-test-result");
    result.textContent = "Testing...";
    result.style.color = "";
    btn.disabled = true;
    fetch("/settings/llm/test", { method: "POST", headers: { "Content-Type": "application/x-www-form-urlencoded" } })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        result.textContent = d.ok ? "✓ OK (" + d.model + ")" : "✗ " + d.error;
        result.style.color = d.ok ? "#15803d" : "#dc2626";
      })
      .catch(function () { result.textContent = "✗ Network error"; result.style.color = "#dc2626"; })
      .finally(function () { btn.disabled = false; });
  };
})();
