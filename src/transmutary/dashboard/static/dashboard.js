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
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
