---
title: "fix: Dashboard LAN access profile + responsive & button-token polish"
type: fix
status: active
date: 2026-06-02
origin: inline (no upstream brainstorm — narrow polish + config scope)
predecessor: docs/plans/2026-06-01-001-feat-dashboard-modern-ui-plan.md
---

# fix: Dashboard LAN access profile + responsive & button-token polish

## Summary

两个独立但同季落地的小修：

1. **LAN / 内网访问** — `docker-compose.yml` 现有 `dashboard` profile 在容器内已开 `--allow-public`，但宿主端口绑死 `127.0.0.1:8787`，本地 `transmutary-dashboard` 也只默认 loopback。新增 `dashboard-lan` opt-in profile，宿主侧绑 `0.0.0.0:8787`，文档同步给局域网/反代部署配方。约 5 行代码改动：`--allow-public` 信号复用，跳过 Host allowlist + `csrf_secure=False`（LAN HTTP 下 cookie 可见）。硬门（`--allow-public` 显式 opt-in、CSRF double-submit、admin token）全部保留。
2. **UI polish** — 已交付的现代 UI（v2 plan 产物）在多断点 + 按钮一致性上有可见 bug：单 `760px` 断点无法覆盖 800–1024 平板宽度；`.row` 默认 `grid-template-areas` 有 `.row-action` 与 `.row-meta` 双占 `meta` 冲突；`.action-link` / `.row-action` / `button.action` 三套 button 视觉散落、且与链接蓝撞色。修法：加 `1024px` / `480px` 两档中间断点、修 grid-areas bug、建立 `.btn` + 4 modifier 的纯 CSS token 系统（**不改 `<a>` vs `<button>` 语义边界**），并迁移全部模板按钮位点。

零依赖、零 build step、零框架引入；CSP 严格契约（`style-src 'self'`、单 nonce-gated inline script）全部不动。

---

## Problem Frame

用户提出两类问题：

**A. 局域网 / 内网访问缺失** — 当前默认 `127.0.0.1:8787` bind 是安全设计（防止意外公网暴露），但缺少显式 opt-in 的 LAN 部署配方。`docker-compose.yml:35` 注释 "公网请放 HTTPS 鉴权反代后" 指向反代方案，但没有对应 profile 直接给 LAN 拉起；README 也只有一句 "For public access, keep it behind HTTPS/auth/rate limiting"。

**B. WebUI 多处样式 / 布局 / 响应式问题，含按钮视觉退化**：

- **响应式** — `dashboard.css:347` 仅 `@media (max-width: 760px)` 单断点。中间宽度（800–1024 平板 / 窄笔记本）下，`.field { min-width: 12rem }`、`.compact-form` `minmax(16rem, 1fr)`、`.delivery-form` `minmax(16rem, 1fr)`、`.login-shell` `minmax(24rem, 28rem)` 均会在 760 断点触发之前先撑爆父容器。小屏（≤480）下 `.stats` 2 列仍嫌挤，`.topbar h1` 字号未缩。
- **布局 bug** — `dashboard.css:120` `.row` 默认 `grid-template-areas: "bar ic pill title repo" "bar ic meta meta meta"`；`.row-action` 声明 `grid-area: meta` 落在第二行，promote 按钮脱离主行（`.row-meta` 有同名 area 但无模板使用，属死 CSS，将在 U2 清除）。`.side { position: sticky; height: 100vh }` 缺 `overflow-y: auto`，长菜单无法滚。
- **按钮** — 用户观察到 "有些按钮甚至只是文字链接"。根因：`.action-link` 颜色用 `var(--accent)`（链接蓝），与真链接撞色；topbar 内 `.action-link` 与 `<h1>` baseline 对齐被大字号压扁；`.row-action` 在 `.row` 第二行 meta 区视觉脱离主行；三个 button class（`.action-link` / `.row-action` / `button.action`）+ `.theme-btn` / `.lang-btn` 各自演化、共享部分样式但不完全一致。

**Out of scope（明确不动）：**

- 现有 `<a class="action-link">` for GET-confirm-entry / `<button type=submit>` for POST-mutating 的语义边界（来自 promote-ui plan 的威胁模型）。
- CSP `default-src 'self'`、`script-src 'self' 'nonce-<x>'`、`style-src 'self'`、`form-action 'self'`。
- 单 FOUC 内联脚本（base.html:12-19，nonce-gated）。
- `request.scope["csp_nonce"]` 存储位置（不要挪到 `request.state`）。
- 默认 `127.0.0.1` bind 默认值（仅新增 LAN profile，默认 profile 行为不变）。
- 框架 / 组件库 / build step / npm 引入（明确拒绝，详见 KTD-1）。
- 公网部署 / HTTPS / 反代配置详细配方（仅引用 `--allow-public` runtime WARN，不重写）。
- `dashboard.js` `textContent`-only 写入约束（`test_js_served_same_origin` 卡死）。
- `to_dict()` 显式 allowlist 约定（不切到 `dataclasses.asdict`）。
- 现有 `i18n` 双语 key 集合（本 plan 不引入新 i18n key，按钮文本沿用 `t['promote']` / `t['demote']` / `t['sign_in']` 等）。

### Deferred to Follow-Up Work

- **非按钮元素的 token 化** — 本 plan 建立 `.btn` + 4 modifier 并在 U3 全部落地（demote / remove 走 `.btn--danger`，sidebar toggle 走 `.btn--ghost`）。后续 plan 扩展 token 系统到 form input、pill、card 等非按钮元素，本 plan 不涉及。
- **axe-core / Lighthouse 自动 a11y / 性能 CI** — 当前测试基线是 `resp.text` substring 断言，无浏览器层。引入 Playwright + axe 是独立工作。
- **`/logout` 路由** — `auth.py` 未提供；与 UI polish 无关，独立 follow-up。
- **`assets/dashboard.png` 截图刷新** — README 已提示需手动从浏览器截图刷新；本 plan 落地后需要一次截图更新，但不进自动测试。
- **`docs/solutions/` 建立** — 研究发现该目录不存在；落地后可考虑把 dashboard CSS/CSP 契约、双语 release notes 约定、compose profile + localhost-bind 模式、false-delivery 防范等沉淀为 solution entries。属 `/ce-compound` 范畴。

---

## Requirements / Constraints

### 硬契约（必须保持，违反即 reject）

- **R1 (CSP)** — 任意新 CSS 必须用 class，不允许 inline `style=`。`test_templates_have_no_inline_style` 必须保持绿（必要时扩展覆盖新路由）。
- **R2 (Nonce 存储)** — `request.scope["csp_nonce"]` 不挪到 `request.state`（BaseHTTPMiddleware 不传播 state）。
- **R3 (FOUC 内联脚本)** — 全站仅 `base.html` 头部那一段 nonce-gated 内联脚本。本 plan 不新增内联脚本。
- **R4 (按钮语义)** — `<a>` 用于 GET-confirm-entry（导航到 `/promote?repo=...` / `/demote?repo=...`），`<button type="submit">` 用于 POST-mutating（confirm 页的 POST form、settings 表单）。**禁止**为视觉统一把 `<a>` 改成 `<button>`。
- **R5 (默认 loopback)** — `dashboard` profile 与 `transmutary-dashboard` 默认仍绑 `127.0.0.1:8787`。LAN 访问通过新 `dashboard-lan` profile opt-in。
- **R6 (硬门保留)** — `--allow-public` / `--allow-public-writes` 标志、Host allowlist middleware、CSRF double-submit、`TRANSMUTARY_ADMIN_TOKEN` 认证全部不动。
- **R7 (双语)** — README 改动 EN + zh-CN 同步；release notes Compatibility / Notes / Verification 块 EN + zh 双语（`tools/release_notes.py` + `test_release_notes.py` 强制）。
- **R8 (i18n key 对齐)** — 本 plan 不加新 i18n key；任何意外新增必须 EN + zh 同时进 `MESSAGES` dict。
- **R9 (textContent-only JS)** — `dashboard.js` 仍禁止 `.innerHTML` / `insertAdjacentHTML`（`test_js_served_same_origin`）。
- **R10 (package-data)** — 不新增 static/templates 子目录；如意外新增，必须更新 `pyproject.toml` `[tool.setuptools.package-data]`。

### 功能性需求

- **F1** — Operator 可通过 `docker compose --profile dashboard-lan up -d dashboard-lan` 在内网启动 dashboard，宿主侧监听 `0.0.0.0:8787`，同一 `.env` 与 `transmutary-state` 卷。
- **F2** — 平板（800–1024）、窄桌面（≈1024）、小屏（≤480）三个区间的关键页面（index / repo / settings / login）无溢出、无 baseline 错位、按钮可点。
- **F3** — 任意 dashboard 按钮视觉属于 `.btn` / `.btn--primary` / `.btn--danger` / `.btn--ghost` 之一；`.action-link` / `.row-action` / `button.action` 在 CSS 内 alias 到对应 token；模板 HTML class 属性迁移到新 token。
- **F4** — `.row` 默认（≥1024）`grid-template-areas` 不再有 area 名冲突；`.row-action` 在主行而非第二行。
- **F5** — `.side` 在内容超过视口时可垂直滚动（`overflow-y: auto`）。
- **F6** — 暗色主题下 4 档按钮对比度全部 ≥ WCAG 2.2 AA（普通文本 4.5:1，大字 3:1）。

---

## Scope Boundaries

### In scope

- `docker-compose.yml` 新增 `dashboard-lan` service 块（与 `dashboard` 平级，profiles `["dashboard-lan"]`）。
- `README.md` + `README.zh-CN.md` 新增"局域网 / 内网访问"小节（双语同步）。
- `src/transmutary/dashboard/static/dashboard.css` 三处改动：
  - 替换单 760 断点为三档系统（1024 / 760 / 480）。
  - 修 `.row` 默认 grid-template-areas 冲突；`.side` 加 overflow。
  - 新增 `.btn` 基底 + 4 modifier；alias 现有 button selectors。
- `src/transmutary/dashboard/templates/*.html` 共 13 处 button class 属性迁移（不改 tag、不改 href/action、不动 CSRF token）。
- `tests/test_dashboard.py` 新增/扩展 substring 断言（grid-template-areas、.btn class 存在、`test_templates_have_no_inline_style` 覆盖扩展）。
- `docs/release-notes/vNEXT.md`（或并入下一次发版的现有草稿）bilingual Compatibility / Notes / Verification 块。
- `_STATIC_VERSION` bump（`app.py:64`），CSS/JS 缓存失效。

### Out of scope

见 Problem Frame → Out of scope 与 Deferred to Follow-Up Work。

---

## Key Technical Decisions

### KTD-1: 拒绝引入前端框架 / 组件库 / build step

**决策** — 不引入 Tailwind / Pico / Bulma / Shoelace / Lit / React / HTMX / Alpine 或任何 npm 包。Token 化停留在纯 CSS 层。

**理由** —

- 项目哲学（README 自述）："pure-pull architecture"、"all-free-data-source"、"deterministic API, LLM only for semantics"、"安全 baseline"。最小依赖、最大确定性。
- 当前 UI 规模（8 模板 / 361 行 CSS / 88 行 vanilla JS）远低于组件库的临界质量。
- 框架化代价巨大且不可逆：build step、CSP nonce-aware 构建、测试改造（HTML → Playwright）、Dockerfile 加 node stage、与 Starlette/Jinja2 SSR 模型的协调成本。
- 真正问题（按钮一致性 + 响应式）用 CSS token + 断点就能解决，无需框架。

**拒绝的替代** — Tailwind（build step + class 膨胀）、Pico/Bulma（全局 selector 冲突 = 全站重写）、Shoelace/Lit（ESM bundle + SSR 边界 + CSP 改造）、shadcn/Radix（抛弃 Jinja SSR = 架构反转）。

**未来路径** — 若 `.btn` token 系统落地后发现 a11y / 交互需求超出 CSS 能力，独立评估 Shoelace web components（保留 SSR 的最优解）。本 plan 不承诺该路径，仅保留可能性。

### KTD-2: LAN 访问形态 = 新增 `dashboard-lan` opt-in profile，默认 profile 不动

**决策** — 不改现有 `dashboard` service 的 `127.0.0.1:8787:8787` 端口绑定，新增 `dashboard-lan` service，profile `["dashboard-lan"]`，宿主侧绑 `0.0.0.0:8787:8787`，复用同一 `.env` / config / state volume。约 5 行 Python 代码改动：复用 `--allow-public` flag 作为 opt-in 信号，在 `_allowed_hosts_for` 跳过 Host 校验 + `csrf_secure=False`。

**理由** —

- 默认 loopback 是安全契约（R5、`--allow-public` 硬门、README 反复声明），改成 `0.0.0.0` 等于静默降级。
- 显式 opt-in profile = 运维心智模型清晰：`--profile dashboard` = localhost-only；`--profile dashboard-lan` = 内网开放；公网 = 反代。
- `--allow-public` 已是显式 opt-in 信号，无需新增 `--lan` flag（避免 CLI 表面积膨胀）。Host allowlist 跳过 + `csrf_secure=False` 均由现有 `--allow-public` 驱动。
- LAN 场景无证书，接受明文 HTTP 风险。`csrf_secure=False` 确保 admin cookie + CSRF cookie 在 HTTP 下可用。
- 现有 `dashboard` profile 同样使用 `--allow-public`，也受此行为变更影响。但因 Docker 端口映射 `127.0.0.1:8787` 仅本地可达，Host 校验跳过无安全影响；`csrf_secure=False` 在 localhost 上无嗅探风险。

**拒绝的替代** — 新增 `--lan` flag（与 `--allow-public` 语义重复，增加 CLI 表面积）；新增 `TRANSMUTARY_DASHBOARD_HOST` 环境变量（增加配置面）；保持"零代码改动"（LAN profile 不可用，AE1 必败）。

### KTD-3: 断点策略 = 替换为 1024 / 760 / 480 三档

**决策** — 不沿用 v2 plan 的单 760 契约。改为三档：

- `@media (max-width: 1024px)` — 平板 / 窄桌面：sidebar 收窄、`.stats` 2 列、`.compact-form` / `.area-form` / `.delivery-form` 折叠为单列。
- `@media (max-width: 760px)` — 现有 mobile 断点保留（sidebar 改顶部横向、`.login-shell` 单列）。
- `@media (max-width: 480px)` — 小屏：`.stats` 1 列（可选 2）、`.topbar h1` 字号缩、`.main` padding 收紧、按钮全宽。

**理由** — 单 760 是 bug 来源（`.field min-width 12rem` 在 800px 已爆）。v2 plan 当时只考虑 "sidebar demotes to horizontal" 一档需求，没覆盖平板宽度。继续固守单 760 = 不修 bug。

**对 v2 plan KTD-V2 的解读修订** — v2 plan KTD-V2 强调"1px borders + 2-3% luminance delta, no shadows/gradients/glass"。本 plan 完全遵守（按钮 token 用 border + bg-elevated 颜色变体，不引入阴影）。但 KTD-V2 没有显式锁定单断点——研究把它们绑在一起是过度解读。本 plan 的三档系统是 KTD-V2 视觉契约的延伸而非违反。

**拒绝的替代** — Container queries（浏览器支持 OK 但增加复杂度，且现有 layout 是 page-level grid，不是 component-level）；CSS Grid `auto-fit / minmax` 全自适应（部分 selector 如 `.login-shell` 的双列布局无法仅靠 minmax 解决，硬解 = 视觉退化）。

### KTD-4: 按钮语义边界不动；token 仅作用于 CSS class

**决策** — `.btn` / `.btn--primary` / `.btn--danger` / `.btn--ghost` 是视觉 class，挂在任意 tag 上。模板迁移只改 class 属性，不改 tag：

- `<a class="action-link" href="/promote?repo=...">` → `<a class="btn btn--primary" href="/promote?repo=...">`（GET-confirm-entry 保留）
- `<a class="row-action" href="/demote?repo=...">` → `<a class="btn btn--danger" href="/demote?repo=...">`
- `<button class="action" type="submit">` → `<button class="btn btn--ghost" type="submit">`（如 settings remove）
- `<button class="action primary" type="submit">` → `<button class="btn btn--primary" type="submit">`（form submit）
- `.theme-btn` / `.lang-btn` → `.btn btn--ghost` + 保留 `.theme-btn` / `.lang-btn` 作为 hook class（sidebar 内布局仍需）

**理由** — `<a>` vs `<button>` 是 promote-ui plan 的威胁模型决策：`<a>` 导航到 GET 确认页（无副作用、可分享、可中键新窗），`<button>` 提交 POST mutating（需 CSRF、需 confirm flow）。视觉统一不能吞掉语义差异。

**拒绝的替代** — 全部统一为 `<button>` + JS 导航（破坏 SSR + 无 JS 退化）；全部统一为 `<a>` + 砍 confirm flow（破坏 CSRF 防护）。

### KTD-5: `.row-action` 默认位置 = 主行而非第二行

**决策** — 修 `dashboard.css:120` `.row` 默认 `grid-template-areas`：

- 当前：`"bar ic pill title repo" "bar ic meta meta meta"` — 主行 5 列，第二行 meta 占满。`.row-action` 声明 `grid-area: meta` 落在第二行（`.row-meta` 有同名 area 但无模板使用，属死 CSS，将在 U2 清除）。
- 新：`"bar ic pill title repo action"` — 单行 6 列，`.row-action` 占 `action` 区。当窄屏（≤1024）折叠为两行时，action 区移到第二行右对齐。注意：`grid-template-columns: auto auto auto 1fr auto auto` 中 `repo` 列需 `max-width` + `text-overflow: ellipsis` + `overflow: hidden` 防止长 repo 名撑爆。

**理由** — promote 按钮在主行内可见 = 用户能直接看到操作入口；当前默认布局按钮被丢到第二行 meta 区 = 视觉上像 "挂角小链"。同时消解 grid-area 冲突 bug。

### KTD-6: 不引入新 i18n key，按钮文本沿用既有

**决策** — 按钮文本 `t['promote']` / `t['demote']` / `t['sign_in']` / `t['add']` / `t['save']` / `t['remove']` / `t['confirm']` / `t['cancel']` 全部沿用。本 plan 零新 i18n key。

**理由** — 减少 i18n dict parity 风险（`test_i18n_dict_key_parity` 卡死）；视觉 polish 不需要新文案。

---

## High-Level Technical Design

### Component layout: 断点系统设计

```text
width ≥ 1025px  [desktop]   sidebar 220 | main (4-stat, 3-col forms, login 2-col)
≤1024           [tablet]    sidebar 200 | main (2-stat, 1-col forms, login 1-col)
≤760            [mobile]    sidebar→topbar | main (2-stat, full-stack forms)
≤480            [small]     .topbar h1 downsized | .main padding 1rem | .btn full-width in forms
```

每档调整的属性集合互不覆盖（mobile 不撤回 tablet 的规则，只是叠加更严约束）。`.btn` token 在 4 档下保持视觉一致（仅 padding 在 ≤480 略增大以贴合触控目标 ≥44px）。

### Button token 视觉矩阵

```text
token           | light bg          | light fg  | dark bg           | dark fg  | use case
.btn            | --bg-base         | --accent  | --bg-base         | --accent | default / cancel
.btn--primary   | --accent          | #ffffff   | --accent          | #ffffff  | form submit, promote (GET entry)
.btn--danger    | --sev-critical-bg | --sev-critical | --sev-critical-bg | --sev-critical | demote entry, remove submit
.btn--ghost     | transparent       | --text-secondary | transparent  | --text-secondary | sidebar toggles, secondary actions
```

所有 4 档共享 `--btn-radius`, `--btn-padding-y/x`, `--btn-min-height`, `--btn-focus-ring` 等 token。`:focus-visible` 统一为 2px outline + offset（继承现有 `.snav a:focus-visible` 风格）。

### `.row` 新版 grid-template-areas

```text
default (≥1025):
  "bar ic pill title repo action"
  (single row, 6 columns)

≤1024 (tablet):
  "bar ic pill title repo"
  "bar ic action action action"
  (action collapses to second row, right-aligned)

≤760 (mobile - existing, kept):
  "bar ic pill title"
  "bar ic repo repo"
  "bar ic action action"

≤480 (small):
  "bar ic pill title"
  "bar ic repo repo"
  "action action action action"
  (action full width, no bar/ic prefix — pairs with full-width .btn in forms)
```

### Sequencing: LAN vs UI 解耦

U1 (LAN) 与 U2/U3 (UI) **完全独立**，可并行实现、独立合并。U4 (release notes) 等 U1–U3 全部 merge 后再写。建议顺序：U1 先（最简单、风险最低、解锁用户实际部署），然后 U2（响应式 bug 修），然后 U3（按钮 token，触面最广放最后）。

---

## System-Wide Impact

- **运维** — 多一个 opt-in profile（`dashboard-lan`）；现役 `--profile dashboard` 用户行为零变化。
- **文档** — README 双语各加 ~30 行 LAN 部署小节；release notes 双语块 ~20 行。
- **测试** — `test_dashboard.py` 新增 ~5 条 substring 断言；`test_templates_have_no_inline_style` 覆盖路由可能扩展（取决于是否启用新路由，本 plan 不启用）。
- **资产** — `_STATIC_VERSION` bump 触发 CSS 缓存失效；现有用户下次访问自动加载新 CSS。
- **依赖** — 零新依赖。零 build step 改动。零 Dockerfile 改动。
- **安全 posture** — 默认 loopback 不变；LAN profile 是显式 opt-in 且仍受 `--allow-public` runtime WARN、Host allowlist、CSRF、admin token 全套保护。

---

## Implementation Units

### U1. LAN access: `dashboard-lan` compose profile + bilingual README

**Goal** — Operator 可通过 `docker compose --profile dashboard-lan up -d dashboard-lan` 拉起一个宿主侧监听 `0.0.0.0:8787` 的 dashboard，复用同一 `.env` / config / state volume。`--allow-public` flag 作为 opt-in 信号：跳过 Host allowlist 校验（LAN 客户端 IP 不在默认白名单）、`csrf_secure=False`（LAN HTTP 下 Secure cookie 不发送）。安全硬门（显式 opt-in、CSRF double-submit、admin token）保留。

**Requirements** — F1, R5, R6, R7.

**Dependencies** — 无（可与 U2/U3 并行）。

**Files** —

- `docker-compose.yml` — 新增 `dashboard-lan` service 块（与 `dashboard` 平级）。
- `src/transmutary/dashboard/app.py` — `_allowed_hosts_for`：当 `--allow-public` 激活时跳过 Host 校验（~2 行）。`csrf_secure` 计算：当 `--allow-public` 激活时设 `False`（~1 行）。总计约 5 行改动。
- `README.md` — 新增 "LAN / intranet access" 小节（在现有 dashboard 段落之后）。
- `README.zh-CN.md` — 对应 "局域网 / 内网访问" 小节。
- `tests/test_dashboard.py` — 无新测试（compose 改动不走单元测试；README substring 不测）。

**Approach** —

- `docker-compose.yml` 新 service 块：复制现有 `dashboard` service，改名 `dashboard-lan`，`profiles: ["dashboard-lan"]`，ports 改 `"0.0.0.0:8787:8787"` 或 `"8787:8787"`（前者更明确），注释说明用途。其余（env_file、volumes、healthcheck、command、image）完全复用。**维护注意**：后续对 `dashboard` service 的任何变更（healthcheck、command、env 等）必须同步到 `dashboard-lan` 块。
- README EN：在 `## Dashboard` 段落末尾（`README.md:200-219`）追加 `### LAN / intranet access` 小节，给出 `docker compose --profile dashboard-lan up -d dashboard-lan` 命令、防火墙提示、强引用 `--allow-public` runtime WARN、链接回现有 security posture 段。
- README zh-CN：对应位置同样追加 `### 局域网 / 内网访问`，文案与 EN 语义对齐。

**Patterns to follow** — `docker-compose.yml` 现有 `dashboard` service 块结构；README 现有双语 dashboard 段（行号 200–219）；release-notes v0.12.0 / v0.12.1 双语结构（用于 U4）。

**Test scenarios** —

- *Happy path*: `docker compose --profile dashboard-lan config` 渲染出 `dashboard-lan` service，端口 `0.0.0.0:8787:8787`，profile 包含 `dashboard-lan`。（手动或集成测试，不进 pytest）
- *Regression gate*: 现有 `dashboard` service 的 `ports[0]` 仍是 `127.0.0.1:8787:8787`（grep 检查，可在 `tests/test_compose.py` 新建一条结构断言；若不存在该文件，本 plan 不强制新建，留 U1 实现者判断）。
- *No default change*: `docker compose config` （无 profile）渲染出的服务集合不含 `dashboard-lan`（profile 隔离生效）。

**Verification** —

- `docker compose --profile dashboard-lan up -d dashboard-lan` 启动后，从同网段另一台机器 `curl http://<host>:8787/healthz` 返回 200。
- `docker compose --profile dashboard up -d dashboard` 启动后，`docker port dashboard` 显示 `127.0.0.1:8787`（无变化）。
- README 双语渲染正常（`tools/release_notes.py prepare` 不报错，因为 release notes 在 U4 才动）。

---

### U2. Responsive breakpoints: 1024 / 760 / 480 三档 + grid bug 修复

**Goal** — 平板（800–1024）、窄桌面用户访问 dashboard 不再出现 form 溢出、stats 挤压、topbar baseline 错位；小屏（≤480）按钮可达触控目标 ≥44px；`.row` 默认布局不再有 grid-area 冲突。

**Requirements** — F2, F4, F5, R1, R9.

**Dependencies** — 无（与 U1 并行；与 U3 可并行，但建议 U2 先于 U3 因为 U3 的 button 视觉受新断点影响）。

**Files** —

- `src/transmutary/dashboard/static/dashboard.css` — 主战场。新增 1024 / 480 媒体查询；修复 `.row` 默认 grid-template-areas；`.side` 加 `overflow-y: auto`；`.topbar h1` 在 ≤480 缩字号；`.main` padding 在 ≤480 收紧。
- `src/transmutary/dashboard/app.py:64` — `_STATIC_VERSION` bump（如 `"20260602-ui-polish"`）。
- `tests/test_dashboard.py` — 扩展或新增 substring 断言：
  - 现有 `test_index_links_stylesheet` 验证 `?v=` 存在；新增同测试验证新 `_STATIC_VERSION` 字符串。
  - 现有 `test_templates_have_no_inline_style` 保持绿；无需扩展（无新路由）。

**Approach** —

- **新断点结构**（在现有 `@media (max-width: 760px)` 之前插入 `1024px`，之后追加 `480px`）：
  ```css
  @media (max-width: 1024px) {
    body { grid-template-columns: 200px 1fr; }
    .stats { grid-template-columns: repeat(2, 1fr); }
    .compact-form, #repos .compact-form, .area-form, .delivery-form { grid-template-columns: 1fr; }
    .login-shell { grid-template-columns: 1fr; }
    .runtime-grid { grid-template-columns: 1fr; }
    .status-strip { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  }
  @media (max-width: 760px) {
    /* existing rules, keep */
  }
  @media (max-width: 480px) {
    .topbar h1 { font-size: 1.15rem; }
    .main { padding: 1rem 1rem 3rem; }
    .stats { grid-template-columns: 1fr; }
    .compact-form .btn, .area-form .btn, .delivery-form .btn, .login-form .btn { width: 100%; min-height: 2.5rem; }
    .topbar { flex-direction: column; align-items: flex-start; }
  }
  ```
- **`.row` grid-template-areas 修复**（dashboard.css:120）：
  ```css
  .row {
    grid-template-columns: auto auto auto 1fr auto auto;
    grid-template-areas: "bar ic pill title repo action";
  }
  .row-action { grid-area: action; justify-self: end; }
  .row-meta { grid-area: meta; display: none; /* 或并入 action 区，视实现 */ }
  ```
  （注：当前 `index.html` 的 trend row 没用 `.row-meta`，只用 `.row-action`，所以移除 `meta` 区不破坏现有渲染。）
- **`.side` overflow**：`.side { overflow-y: auto; }` 加在现有规则块内。
- **`.field min-width`** 在 1024 断点内放宽：`.field { min-width: 0; }`（让 flex/grid 自适应）。

**Patterns to follow** — 现有 dashboard.css `:root` 变量定义；现有 `@media (max-width: 760px)` 风格（class list + grid 重定义）；不引入新 color token（KTD-V2）。

**Test scenarios** —

- *Happy path*: 渲染 `/`、`/repo/<repo>`、`/settings`、`/login` 路由（用 Starlette TestClient），`resp.text` 包含 `dashboard.css?v=20260602-ui-polish`（验证 `_STATIC_VERSION` bump 生效）。
- *No inline style*: 扩展 `test_templates_have_no_inline_style` 覆盖路由集合不变（保持 `/`、`/repo/acme/cli`），`"style="` 不出现在 resp.text。
- *Grid-areas fix*: 新增 `test_row_action_in_main_row` —— 渲染 `/`，断言 `resp.text` 包含 trend row HTML；CSS 解析在测试中过重，改为：断言 `dashboard.css` 文件内容（read 文件后 substring 检查）包含 `"bar ic pill title repo action"`，不包含旧的 `"bar ic meta meta meta"` 双 meta 形式。这是文件级断言，与现有 `test_js_served_same_origin` 同形（read static file, substring check）。
- *Breakpoint presence*: read `dashboard.css`，断言 `@media (max-width: 1024px)` 与 `@media (max-width: 480px)` 均存在。
- *Sidebar overflow*: read `dashboard.css`，断言 `.side` 规则块包含 `overflow-y: auto`（substring 检查）。
- *JS untouched*: `test_js_served_same_origin` 保持绿（不应被本单元破坏）。

**Verification** —

- 手动浏览：Chrome DevTools 切设备到 iPad（820×1180）和 iPhone SE（375×667），检查 `/`、`/settings`、`/login` 无溢出。
- `pytest -q tests/test_dashboard.py` 全绿。
- `ruff check src tests tools` 全绿。

---

### U3. Button CSS token taxonomy: `.btn` + 4 modifiers + 模板迁移

**Goal** — Dashboard 任意按钮视觉属于 `.btn` / `.btn--primary` / `.btn--danger` / `.btn--ghost` 之一；`.action-link` / `.row-action` / `button.action` / `.theme-btn` / `.lang-btn` 在 CSS 内 alias 到对应 token；模板 HTML class 属性迁移；`<a>` vs `<button>` 语义边界 100% 保留。

**Requirements** — F3, F6, R1, R4, R6.

**Dependencies** — U2 推荐先落（`.row-action` 的 `.btn` 视觉受 `.row` grid 变化影响）；若并行，U3 实现者需在 `.row` 修复假设下写 CSS。

**Files** —

- `src/transmutary/dashboard/static/dashboard.css` —
  - 新增 `--btn-*` custom properties 块。
  - 新增 `.btn` 基底 + 4 modifier 规则。
  - alias `.action-link, .row-action, button.action` 到 `.btn`-equivalent（保留旧 class 作为向后兼容 hook，但视觉规则统一走 `.btn`）。
  - 暗色模式变体（4 modifier × 2 主题 = 8 套规则）。
- `src/transmutary/dashboard/templates/index.html` — 行 39（demote `.action-link` → `.btn .btn--danger`）、行 55（promote `.row-action` → `.btn .btn--primary`）。
- `src/transmutary/dashboard/templates/repo.html` — 行 11（promote `<a class="action-link">` → `.btn .btn--primary`）、行 12（demote `<a class="action-link">` → `.btn .btn--danger`）。注意：此处两个按钮均为 `.action-link`（非 `.row-action`），与 index.html 的 promote 使用 `.row-action` 不同。
- `src/transmutary/dashboard/templates/confirm.html` — 行 13（`button.action` → `button.btn` + Jinja 条件 `{% if action == '/demote' %}btn--danger{% else %}btn--primary{% endif %}`，复用已有 `action` 上下文变量）。行 14（`a.back` cancel → `a.btn.btn--ghost`）。行 5（`a.back` 页面导航保留 `.back` 不动）。
- `src/transmutary/dashboard/templates/login.html` — 行 47（`button.action.primary` → `.btn .btn--primary`）。
- `src/transmutary/dashboard/templates/settings.html` —
  - 行 59（add repo → `.btn .btn--primary`）
  - 行 73（remove repo → `.btn .btn--danger`）
  - 行 95（add edge → `.btn .btn--primary`）
  - 行 108（remove edge → `.btn .btn--danger`）
  - 行 129（save trends → `.btn .btn--primary`）
  - 行 145（save delivery → `.btn .btn--primary`）
- `src/transmutary/dashboard/templates/base.html` — 行 32–33（`.theme-btn` / `.lang-btn` 加 `.btn .btn--ghost` 共存，保留原 hook class 用于 sidebar 布局）。
- `src/transmutary/dashboard/app.py:64` — `_STATIC_VERSION` bump（与 U2 同一 bump，如 U2 已合则不再重复）。
- `tests/test_dashboard.py` — 新增按钮 class 断言：
  - `test_button_token_classes_present`：渲染 `/`、`/repo/<repo>`、`/settings`（需 admin login）、`/login`、`/promote?repo=<promotable>`，断言 `resp.text` 包含 `class="btn btn--primary"`、`class="btn btn--danger"`、`class="btn btn--ghost"` 中至少一个（按页面预期组合）。
  - `test_legacy_action_link_anchored`：迁移完成后，`class="action-link"` 与 `class="row-action"` 应不再出现在 `resp.text`（U3 Approach 已确定全迁移，旧 class 在模板中清理干净）；`.theme-btn` / `.lang-btn` 保留作为 sidebar layout hook，对应断言为 `class="theme-btn btn btn--ghost"` 与 `class="lang-btn btn btn--ghost"` 在 `resp.text` 中出现。

**Approach** —

- **CSS 新增（dashboard.css 顶部 `:root` 块内）**：
  ```css
  --btn-radius: var(--r);
  --btn-padding-y: .4rem;
  --btn-padding-x: .8rem;
  --btn-min-height: 2rem;
  --btn-focus-ring: 2px solid var(--accent);
  --btn-bg: var(--bg-base);
  --btn-fg: var(--accent);
  --btn-border: var(--border);
  --btn-bg-hover: var(--bg-elevated);
  /* dark variants picked up automatically via [data-theme="dark"] overrides of --bg-base etc. */
  ```
- **`.btn` 基底**：
  ```css
  .btn {
    display: inline-flex; align-items: center; justify-content: center; gap: .4rem;
    min-height: var(--btn-min-height); padding: var(--btn-padding-y) var(--btn-padding-x);
    border: 1px solid var(--btn-border); border-radius: var(--btn-radius);
    background: var(--btn-bg); color: var(--btn-fg);
    font: 600 .82rem var(--sans); cursor: pointer;
    text-decoration: none;
  }
  .btn:hover { background: var(--btn-bg-hover); text-decoration: none; }
  .btn:focus-visible { outline: var(--btn-focus-ring); outline-offset: 2px; }
  .btn--primary { background: var(--accent); border-color: var(--accent); color: #fff; }
  .btn--primary:hover { background: var(--accent); filter: brightness(.96); }
  .btn--danger { background: var(--sev-critical-bg); border-color: var(--sev-critical); color: var(--sev-critical); }
  .btn--danger:hover { background: var(--sev-critical); color: #fff; }
  .btn--ghost { background: transparent; border-color: transparent; color: var(--text-secondary); }
  .btn--ghost:hover { background: var(--bg-elevated); color: var(--text-primary); }
  .btn--primary:focus-visible { outline: 2px solid var(--text-primary); }
  .btn--danger:focus-visible { outline: 2px solid var(--text-primary); }
  .btn--ghost:focus-visible { outline: 2px solid var(--text-primary); }
  ```
- **旧 selector 清理** — 本 plan 全迁移，模板中不再使用旧 class。直接移除 `.action-link, .row-action, button.action` 的旧视觉规则（dashboard.css:182-194）和 `.back` 规则（dashboard.css:180），不保留 CSS alias。`.back` 导航链接（confirm:5, repo:5, error:5, report:5）保留 `.back` class 作为裸链接（全局 `a { color: var(--accent) }` 已覆盖），仅 confirm:14 的 cancel 升级为 `.btn--ghost`。`test_legacy_action_link_anchored` 断言旧 class 不出现在 `resp.text`。`.theme-btn` / `.lang-btn` 保留在 HTML 中作为 sidebar layout hook（加 `.btn .btn--ghost` 共存）。
- **模板迁移** — 按 Files 列表逐 site 改 class 属性。每处迁移前 grep 确认 HTML 结构、CSRF token、href/action 不动。
- **`.theme-btn` / `.lang-btn`** — 加 `.btn .btn--ghost` 共存：
  ```html
  <button class="theme-btn btn btn--ghost" type="button" aria-label="toggle theme">...
  ```
  保留 `.theme-btn` / `.lang-btn` 因为 `.side-foot` 的 flex 布局依赖这两个 hook。

**Patterns to follow** — 现有 `.action-link, .row-action, button.action` 视觉规则（dashboard.css:182-194）；现有 `:focus-visible` 风格；CSS 变量层级（不引入新 color，只用 `--accent` / `--sev-critical` / `--bg-*` / `--text-*`）。

**Test scenarios** —

- *Happy path - login*: 渲染 `/login`，断言 `'<button class="btn btn--primary" type="submit"'` in `resp.text`，断言 `'class="action primary"'` not in `resp.text`。
- *Happy path - settings*: admin login 后渲染 `/settings`，断言至少 4 个 `class="btn btn--primary"` 出现（add repo / add edge / save trends / save delivery）+ 至少 2 个 `class="btn btn--danger"` （remove buttons）。
- *Happy path - promote entry*: 渲染 `/`（含 promotable trend），断言至少 1 个 `class="btn btn--primary" href="/promote?repo=` 出现（GET entry 仍是 `<a>`）。
- *Semantic boundary preserved*: grep 渲染结果，`<a class="btn` 出现且 `href` 非 `javascript:`；`<button class="btn` 出现且均在 `<form method="post">` 内。可用 substring + 解析 form 块的方式断言。
- *No style= regression*: `test_templates_have_no_inline_style` 保持绿。
- *Sidebar toggle buttons*: `base.html` 渲染后 `.theme-btn.btn.btn--ghost` 与 `.lang-btn.btn.btn--ghost` 共存，断言 `class="theme-btn btn btn--ghost"` 出现。
- *Dark mode parity*: read `dashboard.css`，断言 `[data-theme="dark"]` 块内存在 `.btn--primary` 颜色调整规则（或确认 4 modifier 全部依赖 `[data-theme="dark"]` 已覆盖的 `--accent` / `--sev-critical` / `--bg-base` / `--text-*` 变量）。这是文件级 substring 断言。
- *JS untouched*: `test_js_served_same_origin` 保持绿。

**Verification** —

- 手动浏览 `/`、`/repo/<repo>`、`/settings`、`/login`、`/promote?repo=<promotable>`、`/demote?repo=<demotable>` 全部页面，按钮视觉一致（4 档区分清晰、暗色模式正常）。
- `pytest -q tests/test_dashboard.py` 全绿（含新增断言）。
- `ruff check src tests tools` 全绿。

---

### U4. Bilingual release notes Compatibility / Notes / Verification 块

**Goal** — 下一次发版（v0.13.0 或 v0.12.2，由版本策略决定）的 release notes 包含本 plan 的改动；双语 EN + zh；Compatibility / Notes / Verification 三个子段齐全。

**Requirements** — R7.

**Dependencies** — U1 + U2 + U3 全部 merge。

**Files** —

- `docs/release-notes/v0.13.0.md`（或并入下一次 patch 版本的现有草稿；具体版本号由 semantic-release 流程决定，本 plan 不锁定）。

**Approach** —

- 参照 `docs/release-notes/v0.12.1.md` 双语结构（Highlights / Compatibility / Verification / Notes）。
- **Highlights** —— 列两点：(1) dashboard 新增 `dashboard-lan` opt-in profile 支持局域网访问；(2) UI 响应式 + 按钮 token 系统修复。
- **Compatibility** —— 明示：默认 `dashboard` profile 行为微变（`--allow-public` 已激活时 Host allowlist 跳过、`csrf_secure=False`；但因 Docker 端口映射 `127.0.0.1:8787` 仅本地可达，无实际安全影响）；`_STATIC_VERSION` bump 触发 CSS 缓存失效；无新 CLI flag；无 API 改动；无配置文件格式改动。
- **Verification** —— `ruff check src tests tools`、`pytest -q`、手动浏览器验证步骤（DevTools 切 iPad / iPhone SE）。
- **Notes** —— 重申 `--allow-public` 是显式 opt-in 信号；LAN profile 在可信内网段运行，接受明文 HTTP 风险；公网部署仍需反代 + HTTPS + auth + rate limit。

**Test scenarios** —

- *Bilingual parity*: `tools/release_notes.py check docs/release-notes/v<NEW>.md` 退出码 0。
- *Existing test green*: `pytest -q tests/test_release_notes.py` 全绿。

**Verification** —

- `tools/release_notes.py check` 通过。
- 文档 review：双语段落语义对齐，无未翻译残留。

---

## Risks & Dependencies

| Risk | Severity | Mitigation |
|---|---|---|
| U3 模板迁移漏改某个 button site，导致旧 `.action-link` CSS alias 残留死代码 | Low | grep + 新增 `test_legacy_action_link_anchored` 断言全部迁移 |
| `.row-action` 移到主行后，promote 按钮在窄行（repo 名 + title 长）下挤压 | Medium | U2 grid-template-areas 在 ≤1024 折叠到第二行右对齐；手动测试 long repo name 场景 |
| U2 新断点引入意外 CSS specificity 冲突 | Medium | 测试矩阵覆盖 4 个断点 × 4 个核心页面；保持 mobile-first 思维（1024 是 narrower-than-default，不是 wider-than-mobile） |
| 暗色模式 4 档按钮对比度不足 WCAG AA | Medium | U3 测试场景显式断言 dark mode parity；手动用 DevTools 检查对比度 |
| `_STATIC_VERSION` bump 导致 CDN 缓存策略误判（如有 CDN） | Low | 项目当前无 CDN；bump 是常规操作 |
| LAN profile 误用为公网部署 | Medium | README 双语明确 "公网仍需反代 + HTTPS + auth + rate limit"；强引用 `--allow-public` runtime WARN |
| 误把 `<a class="btn--primary">` 改成 `<button>` 破坏 GET-confirm 流 | High | U3 测试场景 *Semantic boundary preserved* 显式卡死；review 必查 |
| False-delivery（dashboard-readonly memory 教训） | Medium | 每个 unit 落地后用 clean `Read` 验证文件改完；不并行 echo 批；最终 `pytest -q` 全绿 |

**Dependencies** — 无外部 blocker。所有工作可在 main 分支独立开展。`ce-learnings-researcher` 发现 `docs/solutions/` 不存在，但不阻塞本 plan。

---

## Acceptance Examples / Test Scenarios

详细测试场景已嵌入每个 Implementation Unit 的 Test scenarios 字段。汇总 acceptance：

- **AE1** — Operator 在 Linux/macOS host 上 `docker compose --profile dashboard-lan up -d dashboard-lan`，从同网段另一台机器 `curl http://<host>:8787/healthz` 返回 200。
- **AE2** — Operator 仍可 `docker compose --profile dashboard up -d dashboard`，`docker port dashboard` 显示 `127.0.0.1:8787`，无回归。
- **AE3** — 用户在 iPad（820×1180）浏览 `/settings`，5 个表单全部单列布局，无横向滚动条；按钮可点。
- **AE4** — 用户在 iPhone SE（375×667）浏览 `/`，stats 单列、topbar h1 缩小、按钮全宽 ≥44px 触控目标。
- **AE5** — 用户在桌面（≥1025）浏览 `/`，trend row promote 按钮在主行右侧（不在第二行）。
- **AE6** — 用户在任意页面看到任意按钮，其视觉属于 4 档（primary / danger / ghost / default）之一；不再出现 "按钮像文字链" 的情况。
- **AE7** — `pytest -q tests/test_dashboard.py` 全绿，含新增 `test_button_token_classes_present` / `test_row_action_in_main_row` / `test_breakpoint_media_queries_present`（具体名由实现者定）。
- **AE8** — `tools/release_notes.py check` 通过下版本 release notes 双语校验。
- **AE9** — `ruff check src tests tools` 全绿。

---

## Documentation / Operational Notes

- README EN + zh 双语新增 LAN 小节（U1）。
- Release notes vNEXT 双语块（U4）。
- 不引入新文档文件（`docs/dashboard.md` / `docs/deployment.md`）；LAN 配方放 README，足够。
- `_STATIC_VERSION` bump 通知：用户下次访问 dashboard 自动加载新 CSS，无需手动清缓存。
- 无运维 runbook 改动；dashboard 行为对现有部署透明（除非显式 opt-in LAN profile）。

---

## Open Questions

### Resolved during 2026-06-02 review + grill

- **[P0 → RESOLVED] LAN Host allowlist + CSRF + Secure cookie** — 决策：复用 `--allow-public` 信号。`_allowed_hosts_for` 在 `--allow-public` 下跳过 Host 校验；`csrf_secure=False`。~5 行代码改动。见 KTD-2 修订。
- **[P1 → RESOLVED] LAN 明文 HTTP** — 决策：接受可信 LAN 段风险，不强制 HTTPS。
- **[P1 → RESOLVED] Secure cookie** — 决策：`csrf_secure=False` 在 `--allow-public` 下，与 Host allowlist 一同解决。
- **[P2 → RESOLVED] `.btn` 默认 fg `--accent`** — 决策：保持 `--accent`。border + `font-weight: 600` 已足够与普通链接区分。若后续 design token plan 重新评估，可再调。

### From 2026-06-02 review — still open

- **[P1] `.btn` disabled 状态未定义** — 4 个 modifier 均无 disabled/readonly 视觉规范。login submit、settings 按钮在提交中需 disabled 反馈。需决策：定义 `.btn:disabled` 规则，或维持当前 Jinja `{% if %}` 隐藏模式。 — *design-lens*
- **[P1] 平板折叠行 promote 可发现性** — ≤1024 折叠后 action 区在第二行，无视觉分隔符。用户可能不知道下方有操作按钮。需 UX 判断：加 padding-top / 分隔线 / 保持现状。 — *design-lens*
- **[P2] Alert row 空 action 列** — KTD-5 新 6 列 grid 中 alert row 不填 action 区，导致 trend row 与 alert row 列不对齐。需决策：(a) 接受空列（`auto` 宽度零宽度）；(b) alert row 加 chevron 指示器。 — *design-lens*

### Deferred to follow-up plan

- **Design token 基础层** — spacing scale、type scale（当前 21 个字号 → 8 级）、radius 补充、统一 card 基底。当前 362 行 CSS / 8 模板无设计系统，每次 plan 迭代各加各的组件。独立 `refactor: design token foundation` plan 处理。

---

## Sources & Research

- **ce-repo-research-analyst** 报告：详列 dashboard 代码结构、CSP/nonce 契约、test_dashboard.py 断言风格、docker-compose.yml:26-43 现状、README 200–219 段落、commit/release-notes 双语约束。
- **ce-learnings-researcher** 报告：6 条 institutional learning，包括 KTD-V2 视觉契约、KTD-C1 CSP nonce 存储、CSRF/confirm flow 按钮语义、Docker profile + localhost-bind 模式、双语 release notes 强制要求、false-delivery 防范。
- **既有 plan 文档**：
  - `docs/plans/2026-05-31-003-feat-readonly-web-dashboard-plan.md`（read-only 契约）
  - `docs/plans/2026-06-01-001-feat-dashboard-modern-ui-plan.md`（v2 UI 视觉契约）
  - `docs/plans/2026-06-01-002-feat-dashboard-promote-ui-plan.md`（CSRF + GET-vs-POST 按钮语义）
  - `docs/plans/2026-06-01-003-feat-dashboard-admin-config-plan.md`（settings control plane）
- **既有代码**：`src/transmutary/dashboard/{app.py,csrf.py,auth.py,i18n.py,data.py,llms.py}` + `templates/*.html` + `static/dashboard.{css,js}` + `docker-compose.yml` + `README.md` + `README.zh-CN.md` + `tests/test_dashboard.py`。
- **memory 文件**：`dashboard-readonly.md`（read vs write 威胁模型分离）、`tool-batching-discipline.md`（false-delivery 防范）、`no-claude-coauthor.md` + `git-author-identity.md`（提交规范）。
