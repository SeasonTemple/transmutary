# Plan: Report Presentation — Bilingual Fix, MD Rendering, HTML Output, Dashboard Scroll

**Type:** fix/feat
**Depth:** Standard
**Date:** 2026-06-04
**Sequence:** 001

---

## Context

监测功能端到端跑通后，报告**呈现层**暴露 4 个缺陷（dashboard 实测发现）：

1. **WebUI 双语 bug** — 报告页英文 tab 显示了中英全文。根因：`build_report_view`（`src/transmutary/dashboard/data.py:412`）的 body 读 `.md` 文件（`artifacts.py:92-94` 故意拼成 `body_md` + `## 中文` + `body_md_zh` 双语全文），而非 sidecar JSON 的纯英文 `body_md`。中文 tab 用 sidecar `body_md_zh`（纯中文）正确。
2. **无 MD 渲染** — `report.html` 用 `<pre>{{ view.body }}</pre>` 显示裸 markdown 文字，标题/列表/链接/强调均不渲染，人类阅读不便。
3. **无 HTML 产物** — 报告最终产物应是带设计布局的 HTML（非纯文字）。需求矩阵：WebUI 单报告=渲染 MD；汇总(digest)=HTML+MD；邮件=HTML 正文+MD fallback（multipart/alternative）；RSS=纯文字（当前 OK）。
4. **看板观感** — 趋势候选 `.stream` 和最近报告 `.tbl` 无高度限制，列表长时页面无限拉长。需加滚动条 + 观感优化。

---

## Requirements

- R1: WebUI 报告英文区只显示英文，中文区只显示中文（双语正确切分）
- R2: WebUI 报告 body 渲染为格式化 HTML（标题/列表/链接/强调/代码块）
- R3: 渲染的 HTML 遵守现有 CSP（`style-src 'self'` 禁 inline style，类驱动 CSS；无 inline script）
- R4: untrusted markdown 渲染必须防 XSS（禁 raw HTML 注入、`javascript:` 链接、`<script>`）
- R5: 邮件投递 multipart/alternative — HTML 正文 + 纯文字 fallback
- R6: 趋势候选 + 最近报告区加 `max-height` + 滚动条，限制页面长度
- R7: RSS 保持纯文字（不改）
- R8: HTML 产物 CJK 友好 — 中文字体栈（`-apple-system`/`PingFang SC`/`Microsoft YaHei`/`Noto Sans CJK SC` fallback）、`<html lang>` 按语言段标注、`word-break`/`line-height` 适配中英混排
- R9: HTML 产物 a11y — 语义标签（`<main>`/`<article>`/`<h1-h3>` 层级）、severity 不只靠颜色（加文字/图标）、WCAG AA 对比度、链接可辨识、`lang` 属性辅助读屏
- R10: 日度聚合 digest — 每天 `digest_hour` 聚合过去 24h 全量报告（diagnose+explain+security）→ 一份 HTML 汇总（设计布局，CJK+a11y）+ MD 版 + 邮件投递。immediate 高危仍即时单发（聚合是补充，不替代即时告警）。

---

## Key Technical Decisions

**KTD1: markdown-it-py 做渲染。** 已安装（v3.0.0，transitive dep）。零新依赖。配置 `MarkdownIt("commonmark", {"html": False})`：转义所有 raw HTML（`<script>`/`<img onerror>`/`<div onclick>` → 文本）。

**KTD2: XSS 防护 = `html:False` + markdown-it 默认 `validateLink`（已验证）。** 实测 v3.0.0 默认 validateLink 挡掉 `javascript:`/`vbscript:`/`data:text/html` 及大小写/tab/前导空格变体（全部不渲染成链接）。**无需自定义 link 规则、无需 bleach**。jinja2 `| safe` 输出 markdown-it 结果安全，因为 raw HTML 已转义、危险链接已拒。

**KTD3: 双语修复 = WebUI body 改读 sidecar `body_md`。** `build_report_view` 当前读 `.md`（双语全文）。改为：英文 body 读 sidecar JSON 的 `body_md`（纯英文），中文读 `body_md_zh`。`.md` 文件保持双语全文（磁盘人读用，不变）。

**KTD4: HTML digest 产物 = jinja2 模板渲染。** 复用 dashboard 的 markdown 渲染器，套一个带 inline CSS 的邮件 HTML 模板（邮件客户端不支持外链 CSS，必须 inline style —— 这是邮件 HTML 例外，与 dashboard CSP 无关，因为邮件不经 dashboard 渲染）。digest MD 版同时保留。

**KTD5: 滚动条 = CSS `max-height` + `overflow-y: auto`。** 纯样式，加 `.stream` 和报告 `.tbl` 容器的高度上限 + 滚动。类驱动，符合 CSP。

**KTD6: 日度聚合 digest 按时间窗口，非 delivered 标记。** grill 确认数据层已齐：`render_feed(reports: list[Report])` 多报告聚合、`list_repos`+`list_reports`（`ReportRef.ts` int 时间戳）列举、`digest_hour` cron 都在。聚合编排 = 每天 `digest_hour` 遍历所有 repo 的 `list_reports`，过滤 `ts >= now-86400`，读回 Report，`render_feed` 出 RSS + 新 HTML 模板出汇总 + 邮件。**按固定 24h 窗口，幂等**，不需新增 delivered 持久标记（避免状态复杂度）。挂在现有 trend tick 的同一 `digest_hour` cron 旁（或并入）。

---

## Implementation Units

### U1. Fix bilingual split in WebUI report view

**Goal:** 报告英文区显示纯英文，中文区纯中文。

**Files:**
- `src/transmutary/dashboard/data.py` — `build_report_view`
- `tests/test_dashboard.py`

**Approach:**
- `build_report_view` 不再用 `artifacts.read_report`（读 `.md` 双语全文）做 body。改读 sidecar JSON 的 `body_md` 字段（纯英文）。`body_zh` 继续读 sidecar `body_md_zh`。
- `artifacts.read_meta` 已返回 sidecar dict，含 `body_md`。直接 `meta.get("body_md")`。
- **安全注意**：sidecar 的**结构/元数据**（card/severity/sources）可信，但 `body_md`/`body_md_zh` **内容**仍是 untrusted LLM 文本（data.py:114 已标）。U1 只改"从哪读 body"，渲染安全由 U2 负责。不可因"读 sidecar"就当可信直出。
- `.md` 文件、`read_report` 其他用途不动。

**Test scenarios:**
- report 有 `body_md`(EN) + `body_md_zh`(ZH) → view.body 纯英文（无 `## 中文`），view.body_zh 纯中文
- report 仅 `body_md`（旧单语，无 zh）→ view.body=英文，view.body_zh=None
- sidecar 缺 `body_md` 键（极旧报告）→ fallback 到 `.md` 或空，不崩

**Verification:** dashboard 打开 coreutils 报告，英文 tab 无中文，中文 tab 无英文。

---

### U2. Server-side markdown rendering with XSS-safe sanitization

**Goal:** report body 渲染为格式化 HTML，安全防注入。

**Files:**
- `src/transmutary/dashboard/render.py` (新建) — markdown→HTML 渲染器
- `src/transmutary/dashboard/data.py` — view 加 `body_html` / `body_zh_html` 字段
- `src/transmutary/dashboard/templates/report.html` — `<pre>` 改渲染 HTML
- `tests/dashboard/test_render.py` (新建)

**Approach:**
- `render.py`：`render_markdown(md: str) -> str`。`MarkdownIt("commonmark", {"html": False, "linkify": False})`。自定义 link `validateLink` 拒 `javascript:`/`data:`（保留 http/https/mailto）。
- `data.py`：`ReportView` 加 `body_html`、`body_zh_html`（渲染后），保留 raw `body`/`body_zh` 供 JSON consumer。
- **`to_dict` 不变**：JSON 端点（`/report/.../json`）继续只返回 raw `body`/`body_zh` + `_content_trust:external`（契约：consumer 自行转义）。`body_html`/`body_zh_html` 仅 WebUI 模板用，**不进 to_dict** —— 避免 API 返回预渲染 HTML 诱导 consumer 直出。
- `report.html`：`<pre>{{ view.body }}</pre>` → `<div class="report-body md-rendered" data-lang-body="en">{{ view.body_html | safe }}</div>`。双语切换的 `<div data-lang-body="zh">` 用 `body_zh_html`。**保留 `data-lang-body` 属性** —— dashboard.js:103 用 `[data-lang-body]` 属性选择器切换，不依赖 `<pre>` 标签名（grill 确认），换 `<div>` 不破坏切换。
- CSS：`.md-rendered h1/h2/ul/code/a` 等样式（类驱动，无 inline）。

**Test scenarios:**
- `# Title\n- item` → `<h1>Title</h1><ul><li>item</li></ul>`
- `[x](javascript:alert(1))` → 链接 href 被拒/清空，不输出 `javascript:`
- `<script>alert(1)</script>` 输入 → 转义/不执行（html:False → 当文本）
- `**bold** [ok](https://y)` → `<strong>` + `<a href="https://y">`
- 空 body → 空输出不崩
- 中文 markdown 正常渲染

**Verification:** 报告页标题/列表/链接渲染，view-source 无 `<script>`/`javascript:`。

---

### U3. HTML email body (multipart/alternative) + per-report HTML artifact

**Goal:** immediate 分支邮件 HTML 正文 + 纯文字 fallback；每报告旁挂 HTML artifact。

**Files:**
- `src/transmutary/deliver/email.py` — multipart/alternative
- `src/transmutary/dashboard/templates/email_report.html` (新建) — 邮件 HTML 模板（inline CSS）
- `src/transmutary/deliver/render_email.py` (新建，或并入 render.py) — 报告→邮件 HTML
- `src/transmutary/store/artifacts.py` — 写报告时旁挂 `.html`
- `tests/deliver/test_email.py`, `tests/store/test_artifacts.py`

**Approach:**
- **架构事实**（grill 确认）：`deliver()` 处理**单报告**；email **只在 IMMEDIATE 分支发**（`stub.py:135`），digest 分支只写 RSS feed、不发邮件。没有"多报告聚合 digest"层。所以本单元聚焦**单报告**邮件 HTML + 单报告 HTML artifact，不做聚合 digest（移 deferred）。
- `email.py`：`_build_message` 现 `set_content(body_md)`（纯文字）。改 `msg.set_content(text_md)` + `msg.add_alternative(html, subtype="html")` → multipart/alternative。text fallback = `body_md`（纯英文）+ 拼 `body_md_zh`（与 RSS 同款双语纯文字）。
- 邮件 HTML：复用 U2 `render_markdown` 渲染 body（EN + ZH 两段），套 `email_report.html`（标题/severity/sources/双语正文，**inline style** —— 邮件客户端无外链 CSS，邮件例外，不经 dashboard CSP）。
- HTML artifact：`artifacts.py` 写 `.md` 时同时写 `.html`（设计布局版，供未来预览/分享）。`.md`/`.json` sidecar 不变。
- **CJK (R8)**：HTML 模板 `<html lang="...">`，英文段 `lang="en"`、中文段 `lang="zh-CN"`；font-family 栈含 `-apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif`；`line-height: 1.7`、`word-break: break-word` 适配中英混排长行。
- **a11y (R9)**：语义标签 `<main><article>` + `<h1>` 标题 + `<h2>` 章节；severity 用「文字 + 图标 + 颜色」三重编码（不单靠色）；正文/背景对比度 ≥ WCAG AA 4.5:1；链接带下划线（不单靠色区分）；`alt`/`aria-label` 补图标语义。

**Patterns to follow:** dashboard 现有 `report.html` 的 severity pill 结构（`sev-{severity}` + `aria-label`）—— 邮件模板复用同样的「文字+aria」模式。

**Test scenarios:**
- send_report → 消息是 multipart/alternative，含 text/plain + text/html part
- HTML part 含渲染后的 `<h1>`/`<a>`，text part 是裸 MD
- HTML 渲染走 U2 sanitizer（无 script 注入）
- 邮件失败仍降级（RSS 不受影响，现有行为）
- CJK：中文段 `lang="zh-CN"`、字体栈含 CJK fallback；中英混排长行不溢出
- a11y：severity 有文字+aria（非纯色）、`<main>/<article>` 语义结构、链接有下划线

**Verification:** 实测发邮件，Gmail 显示格式化 HTML（非裸 MD），中文不乱码/不挤压，读屏可辨 severity。

---

### U4. Dashboard scroll + visual polish for trends & recent reports

**Goal:** 趋势候选 + 最近报告区限高加滚动条，控制页面长度。

**Files:**
- `src/transmutary/dashboard/static/dashboard.css`
- `tests/test_dashboard.py`（CSS 类存在性，可选）

**Approach:**
- **复用现有 `.table-shell`**（grill 确认：dashboard.css:371 已有 `overflow:auto` + 373 `sticky thead`，是现成滚动容器 pattern）。最近报告表外层包 `.table-shell` + 加 `max-height`（如 `28rem`），表头 sticky 已自带。
- `.stream`（趋势候选，css:120）加 `max-height`（如 `28rem`）+ `overflow-y: auto`。
- 注意 `.card { overflow: hidden }`（css:154）—— 滚动容器放 `.card` 内层或用 `.table-shell` 替代，避免被裁切。
- 滚动条样式（webkit `::-webkit-scrollbar` + firefox `scrollbar-width: thin`），用现有 `--border`/`--bg-*` 变量适配明暗主题。
- 观感：行间距、hover（`.tbl tbody tr:hover` 已有）、滚动区 fade 边缘（可选）。

**Test scenarios:** Test expectation: none — 纯 CSS，无行为变化。手动验证滚动 + 主题协调。

**Verification:** 趋势/报告列表超高时出现滚动条，页面不无限拉长，明暗主题滚动条样式正常。

---

### U5. Daily aggregate digest — HTML summary of last 24h reports

**Goal:** 每天 `digest_hour` 聚合过去 24h 全量报告 → 一份 HTML 汇总 + MD + 邮件。

**Requirements:** R10, R8 (CJK), R9 (a11y)

**Dependencies:** U2 (markdown 渲染器), U3 (邮件 HTML 模板/multipart 复用)

**Files:**
- `src/transmutary/deliver/digest.py` (新建) — 聚合编排：窗口过滤 + 组装
- `src/transmutary/dashboard/templates/digest_report.html` (新建) — 汇总 HTML 模板（CJK+a11y，inline CSS for email）
- `src/transmutary/service.py` — digest_hour cron 挂聚合 job
- `src/transmutary/deliver/stub.py` 或 pipeline — 聚合投递接线
- `tests/deliver/test_digest.py` (新建)

**Approach:**
- `digest.py`：`collect_digest_reports(artifacts, now_ts) -> list[Report]` —— 遍历 `list_repos()`，每 repo `list_reports()`，过滤 `ts >= now_ts - 86400`，`read_meta` 重建 Report（body_md/body_md_zh/severity/sources）。按 severity+ts 排序。
- 汇总组装：分组（critical/high → digest 顶部；info/trend → 下方），每条用 U2 `render_markdown` 渲染。套 `digest_report.html`（标题"Daily Digest YYYY-MM-DD"+ 计数概览 + 分组报告卡片，CJK 字体栈 + a11y 语义/对比度/severity 三重编码 + email inline CSS）。
- RSS：复用现有 `render_feed(reports, feed_name="digest")` 写 `digest.atom.xml`（已支持 batch）。
- 邮件：复用 U3 multipart（text=MD 拼接，html=digest_report.html 渲染）。空窗口（24h 无报告）→ 不发（或发"无事件"——倾向不发，避免噪音）。
- cron：service.py 在 `digest_hour` 加 `daily-digest` job（与 trend tick 同 cron，独立 isolated job）。
- **HTML artifact**：汇总 HTML 写 `<artifact_root>/_digest/YYYY-MM-DD.html`。

**Test scenarios:**
- 窗口内 3 报告（1 high + 2 info）→ digest 含全 3，high 排前，计数概览正确
- 窗口边界：`ts == now-86400` 含/不含（明确 `>=`），`ts < now-86400` 排除
- 空窗口（24h 无报告）→ 不发邮件、不写空 HTML（或写"无事件"占位，按决策）
- HTML 走 U2 sanitizer（无 script），CJK 段 `lang="zh-CN"`，severity 文字+aria
- 多 repo 跨仓聚合：repoA + repoB 报告都进同一 digest
- RSS digest feed 含 batch 条目
- 邮件 multipart：text part 含全报告 MD，html part 含渲染汇总
- read_meta 缺字段（旧报告）→ 跳过或降级，不崩

**Verification:** 手动触发聚合（注入测试 ts），生成 HTML 汇总含窗口内全报告、分组正确、CJK 正常、邮件 multipart 收到格式化汇总。

---

## Scope Boundaries

### In scope
- WebUI 报告双语切分修复 + MD 渲染
- 单报告邮件 HTML（multipart）+ 单报告 HTML artifact
- 日度聚合 digest（HTML 汇总 + MD + 邮件，24h 全量）
- 看板趋势/报告滚动条 + 观感

### Out of scope
- RSS 格式（纯文字，保持不变 — R7）
- `.md` 磁盘文件双语布局（人读用，不变）
- 报告内容生成逻辑（diagnose/explain 不动，只动呈现）

### Deferred to Follow-Up Work
- 报告 PDF 导出
- HTML artifact 的独立预览端点（如 `/report/.../html` / `/digest/YYYY-MM-DD`）
- markdown 渲染缓存（性能优化，当前每请求渲染足够）
- delivered 持久标记（当前 digest 按固定 24h 窗口幂等，不需；若未来要精确"未投递才聚合"再加）

---

## System-Wide Impact

- **安全**：U2/U3 渲染 untrusted LLM markdown。CSP（dashboard）+ markdown-it `html:False` + link scheme 校验 三层防护。邮件 HTML inline style 是邮件客户端要求，不破坏 dashboard CSP（不同渲染路径）。
- **依赖**：markdown-it-py 3.0.0 已装（grill 确认：经 litellm→rich transitive）。需提升为**显式 core 依赖**（`pyproject.toml` `dependencies`，非 dashboard extra）—— 因为 email 渲染（U3）是 core 投递链路，且避免 rich 消失时 transitive 断裂。
- **向后兼容**：旧报告（无 `body_md_zh` / 无 sidecar `body_md`）需 fallback，U1 test 覆盖。
