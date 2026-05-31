/* transmutary dashboard — vanilla JS (U4). No framework, served same-origin
   (script-src 'self'). Theme three-state toggle + language switch + collapse.
   The FOUC-avoidance head script (sets data-theme/lang before first paint) lives
   inline in base.html with a per-request CSP nonce; this file is the post-load
   interaction layer. */
(function () {
  "use strict";

  var LANG_COOKIE = "tmtry-lang";
  var THEME_KEY = "tmtry-theme";

  // i18n dictionary — MUST mirror i18n.py MESSAGES key set (a test asserts parity).
  var I18N = {
    en: {
      "nav.overview": "Overview", "nav.alerts": "Alerts", "nav.trends": "Trends",
      "nav.reports": "Reports", "page.overview": "Overview", "ro": "read-only",
      "stat.repos": "Watched repos", "stat.alerts": "Supply-chain alerts",
      "stat.trends": "Trend candidates", "stat.reports": "Recent reports",
      "stat.attn": "needs attention", "sec.alerts": "Supply-chain alerts",
      "sec.watchlist": "Watchlist", "sec.trends": "Trend candidates",
      "sec.reports": "Recent reports", "sec.feeds": "Feeds",
      "sec.runtime": "Runtime state", "sec.sources": "Sources", "sec.report": "Report",
      "note.watchlist": "config ∪ promoted",
      "note.feeds": "token via Authorization header, not shown",
      "th.repo": "Repo", "th.source": "Source", "th.stars": "Stars",
      "th.growth": "Growth", "th.kind": "Kind", "th.severity": "Severity",
      "th.title": "Title", "empty.alerts": "No supply-chain alerts",
      "empty.trends": "No trend candidates", "empty.reports": "No reports yet",
      "empty.watchlist": "No repos observed",
      "empty.repo_reports": "No reports for this repo",
      "field.baseline": "Issue baseline (rate)", "field.stars": "Latest stars",
      "field.growth": "Star growth", "field.cursor": "Collect cursor",
      "tag.observed": "observed", "tag.archived": "archived only",
      "theme": "Theme", "theme.light": "Light", "theme.dark": "Dark",
      "theme.system": "System", "lang.toggle": "中文", "back.overview": "Overview"
    },
    zh: {
      "nav.overview": "总览", "nav.alerts": "告警", "nav.trends": "趋势",
      "nav.reports": "报告", "page.overview": "总览", "ro": "只读",
      "stat.repos": "关注仓库", "stat.alerts": "供应链告警",
      "stat.trends": "趋势候选", "stat.reports": "最近报告",
      "stat.attn": "需关注", "sec.alerts": "供应链告警",
      "sec.watchlist": "关注清单", "sec.trends": "趋势候选",
      "sec.reports": "最近报告", "sec.feeds": "订阅源",
      "sec.runtime": "运行态", "sec.sources": "来源", "sec.report": "报告",
      "note.watchlist": "config ∪ 晋升",
      "note.feeds": "token 经 Authorization 头传递，不展示",
      "th.repo": "仓库", "th.source": "来源", "th.stars": "星标",
      "th.growth": "增速", "th.kind": "类型", "th.severity": "严重度",
      "th.title": "标题", "empty.alerts": "无供应链告警",
      "empty.trends": "无趋势候选", "empty.reports": "暂无报告",
      "empty.watchlist": "无观测仓库",
      "empty.repo_reports": "该仓库暂无报告",
      "field.baseline": "Issue 基线（速率）", "field.stars": "最新星标",
      "field.growth": "星标增长", "field.cursor": "采集游标",
      "tag.observed": "观测中", "tag.archived": "仅归档",
      "theme": "主题", "theme.light": "亮色", "theme.dark": "暗色",
      "theme.system": "跟随系统", "lang.toggle": "EN", "back.overview": "总览"
    }
  };

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
