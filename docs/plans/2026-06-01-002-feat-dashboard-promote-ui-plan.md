---
title: "feat: 看板写能力 — promote/demote UI（独立威胁模型）"
type: feat
status: completed
date: 2026-06-01
origin: docs/brainstorms/2026-05-29-repo-observation-system-requirements.md
---

# feat: 看板写能力 — promote/demote UI（独立威胁模型）

**类型:** feat · **深度:** Standard（高安全风险） · **日期:** 2026-06-01
**Origin:** `docs/brainstorms/2026-05-29-repo-observation-system-requirements.md`（F4「一键晋升」延后项「模式 B 摘要里一键加入关注清单」便捷动作）
**前序交接:** `docs/plans/2026-05-31-003-feat-readonly-web-dashboard-plan.md`（只读看板，§Deferred 413-422 明确「写能力下轮、必带独立威胁模型」）；`docs/plans/2026-05-30-002-feat-promotion-f4-plan.md`（F4 后端 promote/demote 表方法 + CLI 已交付）

---

## Summary

只读看板（v0.10.0）与 F4 晋升后端（`promoted_repo` 表 + `promote_repo`/`demote_repo` + reconcile + CLI）均已交付。本计划补**最后一块**：在看板上加 promote/demote 按钮，让用户从 Web 把模式 B 趋势候选仓晋升进关注清单（origin F4「一键晋升按钮」）。

核心不是「再写一遍后端」——后端方法已就位、CLI 已能晋升。核心是**开第一个 POST 写端点**这件事本身：只读看板的安全前提（GET-only 免 CSRF、localhost-bind 防 rebind）一旦开写就部分失效，须建**独立威胁模型**。本轮鉴权档位（已确认）：**反 CSRF（double-submit token + Origin 校验 + SameSite=Strict cookie）+ 确认流 + 公网写硬门**；**不**引身份 token（与只读计划「完整 token 鉴权延后」一致）。

---

## Problem Frame

**现状缺口：** 用户在看板看到模式 B 趋势雷达发现的热门候选仓（`Overview.trend_candidates`），想晋升它进模式 A 关注清单——但当前唯一入口是 CLI `transmutary promote owner/repo`，须离开浏览器、敲命令。origin F4 把这个「一键加入」便捷动作列为延后；只读看板计划再次延后并交接给本轮。

**为什么单独成计划（而非塞进只读 PR）：** 只读看板的整个安全姿态建立在「无任何 mutation 端点」上——所以它不需要 CSRF、可以靠 localhost-bind + Host 头当访问控制。开 POST 后：

- **CSRF 失效面打开**：同浏览器里一个恶意站点能静默 `<form method=post action="http://localhost:8787/promote">` 自动提交；浏览器自带 `Host: localhost:8787`（**通过** Host allowlist——Host 校验防的是 DNS rebind，不防 CSRF）。
- **写操作有副作用**：promote = 让 service 开始持续观测一个仓（消耗 GitHub API 配额、落持久状态）；demote = 停止观测。比只读「泄漏情报」多了「被诱导改状态」的风险。

故安全设计必须独立、不与只读混。

---

## Requirements Traceability

| ID | 需求 | 落点 |
|---|---|---|
| R1 | origin F4「一键晋升按钮」（模式 B → A，UI 入口） | U2, U3, U4 |
| R2 | origin F4 延后项「模式 B 摘要里一键加入关注清单」 | U2, U4（candidate → promote） |
| R3 | 只读计划交接：CSRF token | U1 |
| R4 | 只读计划交接：写端点真鉴权（本轮 = 反 CSRF + 确认 + 公网硬门，已确认） | U1, U2, U5 |
| R5 | 只读计划交接：输入校验 | U2（仓名格式 + demote-only-promoted 守卫） |
| R6 | 只读计划交接：确认流 | U2, U4（GET 确认页 → POST 执行） |
| R7 | CE agent-native parity | 决策 KTD-Write-7（CLI 为 agent 入口，浏览器 POST 不弱化以迁就 agent） |

---

## Key Technical Decisions

**KTD-Write-1 — 反 CSRF 用 double-submit cookie（无 session store 的最优解）。**
看板无会话存储，纯 synchronizer token 需服务端会话态。采用 **double-submit**：`CSRFMiddleware` 在每个响应确保一个 `tmtry-csrf` cookie（`SameSite=Strict; HttpOnly; Path=/`，随机 `token_urlsafe(32)`）；GET 渲染时服务端读 cookie 把同值 token 戳进表单 hidden 字段；POST 时比对 `form.csrf_token == cookie` 用 `secrets.compare_digest`（恒定时间）。跨站攻击者既读不到 cookie（SOP），也设不了匹配的表单字段 → 无法伪造。`SameSite=Strict` 再加一层：跨站 POST 根本不带该 cookie → 必然 mismatch。`HttpOnly` 保留（服务端戳 token，不依赖 JS 读 cookie）。

**KTD-Write-2 — Origin/Referer 校验作纵深防御。**
POST 处理器额外校验 `Origin`（缺则回退 `Referer`）的 host ∈ allowed hosts；两者皆缺 → 拒。这层独立于 token：即便某天 token 实现有洞，跨站 Origin 仍被挡。复用 `_host_only` + allowed_hosts。**`Origin: null` 不被当作可信 Origin**（W3C 合法值，`file://` 本地页 / 沙箱 iframe / 某些重定向降级会发它——攻击者可诱导受害者用同浏览器打开本地 HTML 发 null-Origin 跨站 POST）：`check_origin` 不解析字面 `"null"` 为 host；若同时存在同源 `Referer` 则按 Referer fallback 放行，否则拒绝。实现保持 `Referrer-Policy: same-origin`，让同源表单 POST 在浏览器提供 `Origin: null` 时仍有可校验的同源 Referer。

**KTD-Write-3 — 确认流 = 服务端两步（GET 确认页 → POST 执行），零 JS、CSP 干净。**
点「晋升」走 GET `/promote?repo=owner/repo`（幂等、不写）→ 渲染确认页（含真正的 POST 表单 + csrf hidden + 确认按钮）→ 用户点「确认」提交 POST `/promote`。纯 HTML，无内联脚本，`script-src 'self'` 下零妥协，且服务端可测。demote 同形。**不**靠浏览器 `confirm()` 弹窗（需 JS、不可服务端测）。

**KTD-Write-4 — 写经表、不碰 service 内存；与 CLI 同语义（reconcile 拾取，免重启、非即时）。**
看板与常驻 service 是**不同进程**——够不到 service 内存里的调度器。故 POST 只写 `promoted_repo` 表（复用 F4 `store.promote_repo`/`demote_repo`），运行中 service 的 reconcile job 下一拍同步注册（F4 KTD-B）。UI 晋升语义 = 「下次 reconcile 生效」，与 CLI 完全一致；**不**复制 `Service.promote` 的进程内即时注册（那是同进程便利，看板拿不到）。

**KTD-Write-5 — 写须 RW StateStore 句柄；渲染路径仍只读；写句柄按门控注入。**
当前看板 `StateStore(path, read_only=True)`（防与 service 写竞争）。写端点需 RW。`make_dashboard_app` 新增 `write_store: StateStore | None` 形参：`None` = 写端点全关（公网未显式 opt-in 时）；非 None = 开。渲染仍走只读 `store`，写仅经 `write_store`，两句柄职责分明。

**`busy_timeout` 须落成 `StateStore` 改动、非 app 层「补开关」（可行性审出）。** 现 `state.py.__init__` 无 `busy_timeout` 形参、连接全程未设——sqlite 默认 `busy_timeout=0`，service 一持锁看板即抛 `OperationalError`，「持锁极短没事」失效、503 会频繁误触。修法：U5 给 `StateStore.__init__` 加 `busy_timeout_ms: int | None = None`（或 RW 路径无条件 `PRAGMA busy_timeout=N`，如 3000ms），看板 RW 句柄传该值；`OperationalError`「database is locked」→ 用户可见「忙、请重试」而非 500。

**RW init 须容忍 service 已建好的库（可行性审出）。** RW（`read_only=False`）路径会跑 `_ensure_db_permissions(create=True)` + `PRAGMA journal_mode=WAL` + 二次权限校验；看板与 service 双 RW 进程同库时，WAL 多为 no-op，但权限二次校验在 service 正写时偶发竞争。修法：U5 明确「RW 句柄复用 service 已建好的库（不 create 新库、容忍 WAL 已存在）」，验证双进程下 `_ensure_db_permissions` 不误杀（必要时只读式打开 + 仅对 `promoted_repo` 写，或令 RW 路径在库已存在时跳过 create 语义）。

**KTD-Write-6 — 公网写硬门：`--allow-public` 默认仍禁写。**
只读计划已有「公网须显式 `--allow-public`」硬门。写更危险：公网放行后写端点会暴露给任何够到代理的人。故**写默认随公网关闭**——`--allow-public` 只开读；要开公网写须再加显式 `--allow-public-writes`，且 loud WARN「写端点已暴露在公网，必须前置鉴权代理」。localhost 绑定（默认）→ 写默认开。即：`write_store` 仅在 localhost、或公网+`--allow-public-writes` 时注入。

**KTD-Write-7 — agent parity 由 CLI 满足，不为 agent 弱化浏览器 CSRF。**
CE「用户能做的 agent 也能做」：晋升的 agent 入口是已交付的 CLI `transmutary promote`（F4 KTD-C 钦定的 agent-native 入口）。浏览器 POST 端点带 cookie+Origin 的浏览器语义，非浏览器 agent 本就不满足——**不**为迁就 agent 加一个免 CSRF 的 token 写 API（那正是延后的「完整 token 鉴权」）。看板 JSON GET 面保持只读。此为有意边界，预先回应 agent-native 审查。

---

## High-Level Technical Design

**写请求生命周期（CSRF double-submit + Origin + 确认 + reconcile 桥接）：**

```mermaid
sequenceDiagram
  participant B as 浏览器(用户)
  participant MW as CSRFMiddleware
  participant H as POST 处理器
  participant WS as write_store(RW)
  participant T as (promoted_repo 表)
  participant SVC as service(独立进程) reconcile

  B->>MW: GET /promote?repo=o/r (确认页)
  MW->>MW: 确保 tmtry-csrf cookie(SameSite=Strict,HttpOnly)
  MW-->>B: 确认页(表单含 csrf hidden=cookie 值) + Set-Cookie
  B->>MW: POST /promote (form: repo + csrf_token, Cookie: tmtry-csrf)
  MW->>H: scope[csrf_token]=cookie 值
  H->>H: ① Origin/Referer host ∈ allowed? 否→403
  H->>H: ② compare_digest(form.csrf, cookie)? 否→403
  H->>H: ③ 仓名格式 owner/repo? 否→400
  H->>WS: promote_repo(repo) (busy_timeout; locked→重试提示)
  WS->>T: INSERT OR REPLACE
  H-->>B: 303 → / (新状态自证)
  Note over SVC,T: 下一拍 reconcile 读表→注册该仓 jobs(免重启)
```

**威胁 → 防御 决策矩阵（独立威胁模型核心）：**

| 威胁 | 只读时为何不需 | 开写后风险 | 本轮防御 |
|---|---|---|---|
| CSRF（同浏览器恶意站静默 POST） | 无 mutation 端点 | 被诱导晋升/取消任意仓 | double-submit token + `SameSite=Strict` cookie + Origin 校验 + 确认流（KTD-Write-1/2/3） |
| DNS rebinding | Host allowlist 已防 | 同左（写不增量） | 复用 Host allowlist（rebind 后 Host=攻击域→拒） |
| 公网暴露写 | 读已有 `--allow-public` 硬门 | 任意访客改状态 | 写二级硬门 `--allow-public-writes`（KTD-Write-6） |
| 写竞争/锁 | 看板只读不写 | service 持锁致 500 | RW 句柄 `busy_timeout` + locked 友好提示（KTD-Write-5） |
| 非法/注入仓名 | 不接受输入 | 脏数据入表 | 仓名 owner/repo 格式校验 + demote-only-promoted 守卫（U2） |
| promote 任意外部仓 → 下游资源滥用（service reconcile 持续拉取该仓、耗 GitHub token 配额；LLM 报告管线读其 README/代码 = prompt-injection / SSRF 相邻面） | 不接受输入 | 攻击者批量 promote 恶意/不存在仓触发持续下游动作 | **本轮风险边界 = localhost 单用户**（能写 = 能访问本机，与直接跑 CLI 等价）；`_valid_repo` 只验格式不验存在性/许可范围——**本轮有意接受、无速率限制**。**公网写一旦开（`--allow-public-writes`）此风险升为 blocker**：运维须在前置鉴权代理处加速率限制 / 仓名白名单（文档化于 U6 WARN） |
| 误删 config 仓观测 | — | demote 误碰 config 仓 | config 仓无 demote 入口；服务端拒 demote 非 promoted 仓（U2/U3） |
| 身份冒充（多用户） | localhost 单用户 | （本轮不解） | **延后**：完整 token 身份鉴权（见 Scope） |

---

## Implementation Units

### U1. CSRF double-submit + Origin 校验 primitives + CSRFMiddleware

- **Goal:** 提供 csrf token 签发/校验、Origin 校验的纯函数 + 中间件，使每个响应确保 SameSite=Strict cookie 并把 token 暴露给模板。
- **Requirements:** 只读计划交接「CSRF token」；KTD-Write-1, KTD-Write-2。
- **Dependencies:** 无。
- **Files:** `src/transmutary/dashboard/csrf.py`（新建）、`src/transmutary/dashboard/app.py`（接 `CSRFMiddleware` 进 middleware 栈、CSP 串加 `form-action 'self'`、`_ctx` 加 `csrf_token`）、`tests/dashboard/test_csrf.py`（新建）。
- **Approach:**
  - `csrf.py`：`CSRF_COOKIE = "tmtry-csrf"`；`issue_token() -> str`（`secrets.token_urlsafe(32)`）；`verify_token(form_token, cookie_token) -> bool`（`secrets.compare_digest`，空值→False）；`check_origin(request, allowed_hosts) -> bool`（取 `Origin` host，缺则 `Referer` host，经 `_host_only`/同款解析比 allowlist；两者皆缺→False）。
  - `CSRFMiddleware(BaseHTTPMiddleware)`：dispatch 读 `tmtry-csrf` cookie，无则 `issue_token()` 并标记需 set-cookie；`request.scope["csrf_token"] = token`（同 nonce 走 scope，**不**走 request.state——BaseHTTPMiddleware state 不传下游）；响应若新签发则 `response.set_cookie(CSRF_COOKIE, token, samesite="strict", httponly=True, path="/", secure=<secure>)`，`secure` 由 app 装配传入（localhost=False；公网写=True，见 U5 cookie 定档）——中间件接 `secure: bool = False` 形参，不硬编码。
  - `app.py`：middleware 栈加 `Middleware(CSRFMiddleware)`（在 CSPNonce 之后/之前均可，互不依赖）；`_CSP_NO_NONCE` 与 `_csp_with_nonce` 串尾加 `; form-action 'self'`（限制表单只提交本源，纵深）；`_ctx` 注入 `"csrf_token": request.scope.get("csrf_token", "")`。
- **Patterns to follow:** `CSPNonceMiddleware`（nonce 经 `request.scope`、`setdefault` 头）；`_host_only`（Host/Origin host 解析，含 IPv6）。
- **Test scenarios:**
  - `issue_token` 每次唯一、urlsafe、≥32 字节熵。
  - `verify_token`: 同值→True；不同/空 form/空 cookie→False；用 `compare_digest`（断言不因长度短路——传等长不同值仍 False）。
  - `check_origin`: Origin=允许 host→True；跨站 Origin→False；无 Origin 有合法 Referer→True；两者皆无→False；Origin 带端口经解析→正确；**`Origin: null` + 同源 Referer→True；`Origin: null` 单独出现或搭配跨站 Referer→False（不当 host 解析）**。
  - 中间件：首次 GET 无 cookie → 响应含 `Set-Cookie: tmtry-csrf=...; SameSite=Strict; HttpOnly; Path=/`；带 cookie 的 GET → 不重签（复用，无 Set-Cookie）；`scope["csrf_token"]` 与 cookie 同值。
  - CSP 串含 `form-action 'self'`（no-nonce 与 nonce 两版都含）。
- **Verification:** csrf 测试全过；既有看板测试零回归（中间件不影响 GET 渲染）。

### U2. promote/demote 端点 + 确认页 + RW write_store 接线

- **Goal:** 加 GET 确认页 + POST 执行端点（promote/demote），串起 Origin+CSRF+仓名+demote 守卫校验，写 `promoted_repo` 表，303 重定向；写句柄按 `write_store` 注入、None 则端点关。
- **Requirements:** F4 UI 入口；交接「写端点真鉴权 / 输入校验 / 确认流」；KTD-Write-2/3/4/5。
- **Dependencies:** U1。
- **Files:** `src/transmutary/dashboard/app.py`、`tests/dashboard/test_app_writes.py`（新建）。
- **Approach:**
  - `make_dashboard_app(..., write_store: StateStore | None = None)`。`_writes_enabled = write_store is not None`。
  - 仓名校验 helper `_valid_repo(s) -> bool`：`owner/repo` 形（复用/对齐 CLI 的 `sanitize_repo`/正则，恰一个斜杠、无 scheme、无空白）。
  - GET 确认页处理器 `promote_confirm`/`demote_confirm`：取 `?repo=`，`_valid_repo` 否→400；渲染 `confirm.html`（动作类型 + repo + csrf hidden）。幂等、不写。
  - POST 处理器 `promote_action`/`demote_action`：① `_writes_enabled` 否→404（端点形同不存在）；② `check_origin` 否→403；③ 读 form `repo`+`csrf_token`，`verify_token(csrf, request.cookies.get(CSRF_COOKIE))` 否→403；④ `_valid_repo(repo)` 否→400；⑤ demote 额外守卫：`write_store.is_promoted(repo)` 否→400（拒 demote 非 promoted 仓——挡误删 config 仓观测）；⑥ `try: write_store.promote_repo(repo)/demote_repo(repo) except OperationalError`（locked）→ 503；⑦ 成功 `RedirectResponse("/", status_code=303)`。
  - **失败态用户可见出口（设计审出，须明定非裸状态码）：** 每个错误码渲染一个最小可见结果——统一 `error.html`（继承 `base.html`，显示 `t[error_key]` 文案 + 返回总览链接）：403→`t[error_csrf]`、400→`t[error_invalid_repo]`/`t[error_not_promoted]`、503→`t[write_busy]`。404（写关）走既有 `Not Found` 文本即可（端点形同不存在，不渲染富页）。错误页同走安全头中间件、`debug=False` 不泄漏细节。**注：本单元的处理器渲染 `confirm.html`/`error.html` 与 `t[...]` 键，其模板/键由 U4 创建——U2↔U4 为耦合对、同批落地（U2 定渲染契约：哪个码用哪个模板/键；U4 供模板与键）。**
  - 路由加：`POST /promote`、`POST /demote`、`GET /promote`(confirm)、`GET /demote`(confirm)（**采用同路径双 Route**：`GET /promote`=确认页、`POST /promote`=执行，与 HLD 时序图一致；不另起 `/promote/confirm`，消除实现期分歧，保持 KTD-Write-3 两步语义）。
- **Patterns to follow:** 既有 `index`/`repo_page` 处理器签名与 `_ctx` 用法；F4 `cli.py` 的仓名校验；`store.is_promoted`/`promote_repo`/`demote_repo`（`state.py`）。
- **Test scenarios:**
  - Covers F4. 合法 promote（带匹配 csrf cookie+form + 合法 Origin）→ 表新增行、303 → `/`。
  - demote promoted 仓 → 表移除、303。
  - CSRF 缺 form token → 403、不写表；form token 与 cookie 不匹配 → 403、不写。
  - Origin 跨站（`Origin: http://evil.com`）→ 403、不写；无 Origin 无 Referer 的 POST → 403。
  - 仓名非法（无斜杠 / 含 `javascript:` / 含空白）→ 400、不写。
  - 错误态渲染：403/400/503 各渲染 `error.html` 含对应 `t[error_*]` 文案 + 返回链接（非裸状态码体）；断言页含该文案键文本。
  - demote 非 promoted 仓（如 config 仓 / 不存在）→ 400、不写（误删守卫）。
  - `write_store=None`（公网未 opt-in）→ POST /promote 与 /demote 均 404；GET 确认页亦不暴露写。
  - locked DB（mock `promote_repo` 抛 `OperationalError`）→ 503 友好提示、非 500 泄漏。
  - GET 确认页：渲染含 repo + csrf hidden 的 POST 表单、**不**写表（断言表无变化）。
  - Host allowlist 仍对 POST 生效（坏 Host 的 POST → 400，复用中间件）。
  - 幂等：重复 promote 同仓 → 表不重复行（INSERT OR REPLACE）。
- **Verification:** 写端点测试全过；只读 GET 路径零回归；`write_store=None` 时看板退化为纯只读。

### U3. data.py 视图层按钮门控标志

- **Goal:** view-model 暴露 promotable/demotable，使模板逻辑轻——按钮只在正确的仓上出现。
- **Requirements:** F4 UI；误删守卫（config 仓不可 demote）。
- **Dependencies:** 无（与 U2 并行，U4 依赖本单元）。
- **Files:** `src/transmutary/dashboard/data.py`、`tests/dashboard/test_data.py`。
- **Approach:**
  - `WatchEntry` 加 `demotable: bool`（= `source != "config"`，即仅 promoted 仓可取消）；`to_dict` 显式 allowlist 加该键（R-S2 一致）。
  - `RepoRuntime` 加 `promotable: bool`（= `not in_watchlist`）与 `demotable: bool`（= `in_watchlist and source != "config"`）；`to_dict` 同步加键。
  - `Overview.trend_candidates` 为 `ReportCard`（带 `.repo`）；新增轻量 `promotable_repos: frozenset[str]`（候选中**不在**有效关注清单的仓集），模板据此对候选显示 promote。在 `build_overview` 算：`{c.repo for c in trends} - set(effective_repos)`。
- **Patterns to follow:** 既有 frozen dataclass + 显式 `to_dict` allowlist（`data.py`）；`build_watchlist` 的 config/promoted 来源判定。
- **Test scenarios:**
  - `WatchEntry.demotable`: config 源→False；promoted 源→True。`to_dict` 含 `demotable`。
  - `RepoRuntime`: 未在清单→promotable True/demotable False；promoted→promotable False/demotable True；config→两者皆 False。
  - `Overview.promotable_repos`: 候选含已在清单的仓→不入集；候选未在清单→入集。
  - JSON 面：新键出现且仅这些键（无意外字段泄漏）。
- **Verification:** data 测试过；JSON 契约稳定（显式 allowlist）。

### U4. 模板 promote/demote 按钮 + confirm.html + i18n 文案

- **Goal:** 渲染按钮（据 U3 标志门控）与确认页，加 EN+zh 文案；零内联脚本、零内联 style（CSP 合规）。
- **Requirements:** F4 UI 入口（候选一键晋升）；确认流；i18n（沿用看板现代化计划 `2026-06-01-001` 的 R-I1–R-I4 i18n 基建，本计划无独立源需求）。
- **Dependencies:** U2（端点/路由）、U3（视图标志）。
- **Files:** `src/transmutary/dashboard/templates/index.html`、`repo.html`、新建 `templates/confirm.html`、新建 `templates/error.html`（承接 U2 失败态出口）、`src/transmutary/dashboard/i18n.py`（MESSAGES 加键 EN+zh）、`tests/dashboard/test_i18n.py`（或并入 U2 TestClient 断言）。
- **Approach:**
  - `index.html`：watchlist 区对 `entry.demotable` 仓加「取消晋升」链接 → GET `/demote?repo=`；trend_candidates 区对 `repo in overview.promotable_repos` 的候选加「晋升」链接 → GET `/promote?repo=`。
  - `repo.html`：据 `runtime.promotable`/`runtime.demotable` 显示对应链接。
  - `confirm.html`：继承 `base.html`；显示动作（晋升/取消晋升）+ repo + `<form method=post action="/promote|/demote">` 含 `<input type=hidden name=repo>` + `<input type=hidden name=csrf_token value="{{ csrf_token }}">` + **确认按钮 + 取消按钮**。仓名经 autoescape；csrf 来自 `_ctx`。
  - **取消按钮去向（设计审出，须定，防两入口不一致）：** 取消固定为 `<a href="/">`（回总览）。**不**用 `history.back()`（需 JS、CSP 受限）、不按 Referer 动态回退（两入口[index 候选 / repo 详情]统一回总览，晋升后新状态在总览自证，去向单一可测）。
  - **空态（设计审出）：** 无候选可晋升 / 无 promoted 可取消时，**隐藏对应按钮列**即可（不渲染占位文案——零候选是常态非异常，占位反增噪）；区块本身（trend_candidates / watchlist）由既有只读逻辑照常渲染。仅当**整个 watchlist 为空**时沿用既有空态文案（非本计划新增）。
  - `error.html`：继承 `base.html`；显示 `t[error_key]`（由 U2 处理器按错误码传入）+ 返回总览链接。零内联脚本。
  - `i18n.py`：MESSAGES 加 `promote`/`demote`/`confirm`/`cancel`/`confirm_promote_q`/`confirm_demote_q`/`write_busy`，**加错误态键 `error_csrf`/`error_invalid_repo`/`error_not_promoted`**（对应 U2 的 403/400 出口），EN+zh 对称。文案走 `t[...]`、jinja autoescape，**不**用 innerHTML。
  - 样式复用既有 class，零新内联 style（`style-src 'self'`）。
- **Patterns to follow:** 既有模板 `t[...]` i18n 调用、`base.html` 继承、severity 三通道无内联 style 的写法。
- **Test scenarios:**
  - Covers F4. 候选（未在清单）→ index 渲染晋升链接指向 `/promote?repo=`；已在清单候选→无链接。
  - promoted watchlist 项 → 渲染取消晋升链接；config 项→无（误删守卫 UI 层）。
  - confirm.html → 含正确 action、隐藏 repo、隐藏 csrf_token（值=cookie token）、确认按钮、取消按钮 `href="/"`。
  - 空态：promotable_repos 为空 → index 不渲染任何晋升链接（不报错、无占位）；无 demotable 项 → 不渲染取消链接。
  - error.html → 给定 error_key 渲染对应文案 + 返回链接。
  - i18n：新键（含 `error_csrf`/`error_invalid_repo`/`error_not_promoted`）EN+zh 均存在且非空（断言两语言键集相等）。
  - XSS 回归：repo 名含 `<script>` 经 autoescape 转义（confirm 页 + 链接）。
  - 零内联 script、零内联 style（断言渲染 HTML 无 `<script>` 内联体、无 `style=`）。
- **Verification:** 模板 TestClient 用例过；双语对称；CSP 合规断言过；XSS 转义确证。

### U5. 入口公网写硬门（`--allow-public-writes`）

- **Goal:** `main()` 据绑定决定是否注入 `write_store`：localhost→开；公网→默认关，须 `--allow-public-writes` 显式开 + loud WARN。
- **Requirements:** 交接「写端点真鉴权」；KTD-Write-6。
- **Dependencies:** U2（`write_store` 形参）。
- **Files:** `src/transmutary/dashboard/app.py`（`main()` + 新 helper）、`src/transmutary/store/state.py`（`busy_timeout` 支持 + RW 复用既有库）、`tests/dashboard/test_app.py`（或 `test_writes` 内）、`tests/store/test_state.py`（busy_timeout）。
- **Approach:**
  - **`StateStore` 加 `busy_timeout`（可行性审出，前置）：** `__init__` 加 `busy_timeout_ms: int | None = None`，连接后 `PRAGMA busy_timeout=<ms>`（建议 3000）。看板 RW 句柄传该值，使 service 短暂持锁时看板**等待**而非即抛 `OperationalError`，让 KTD-Write-5 的「locked→503」只在真持久锁时触发。
  - **RW 复用既有库（可行性审出）：** `resolve_write_store` 开的 RW 句柄须容忍 service 已建库——`read_only=False` 路径的 `_ensure_db_permissions(create=True)` + `journal_mode=WAL` 在库已存在时应为幂等 no-op；验证双 RW 进程下权限二次校验不误杀（必要时令 RW 在库存在时跳过 create 语义、仅设 busy_timeout + 写 `promoted_repo`）。
  - `main()` 加 `--allow-public-writes`（action store_true，help：「公网放行写端点——无内置鉴权，必须前置鉴权代理」）。
  - 决策 helper `resolve_write_store(settings, *, is_local, allow_public_writes) -> StateStore | None`：`is_local` → 开 RW `StateStore(path, read_only=False, busy_timeout_ms=3000)`；非 local 且 `allow_public_writes` → 开 RW + `logger.warning(公网写暴露…)`；非 local 且未 opt-in → `None`（写关）。
  - `is_local` 复用 `resolve_bind` 的同款判定（须含 IPv6 loopback `::1`/`[::1]`；`0.0.0.0` 通配视为**非** local）。把 `write_store` 传入 `make_dashboard_app`。
  - **cookie `Secure` 定档（可行性审出，不留实现悬空）：** localhost 用 `secure=False`（http）。**公网写（`allow_public_writes=True`）时 CSRFMiddleware 置 `secure=True`**——前置代理须以 HTTPS 暴露（否则 cookie 在 HTTPS 页被拒、POST 静默 403 像 bug）。U1 的 `set_cookie` 据此参数化 `secure`（由 app 装配按是否公网写传入）。WARN 文案同步提示该 HTTPS 前提。
- **Patterns to follow:** `resolve_bind` 的 localhost 判定 + 公网 WARN；`main()` 现有 store/artifacts 装配；既有 `StateStore` PRAGMA 设置位置。
- **Test scenarios:**
  - `busy_timeout_ms` 传入 → 连接 `PRAGMA busy_timeout` 生效（查 pragma 值）；不传 → 默认行为不变（向后兼容）。
  - RW 句柄对 service 已建库（含已存在 WAL）→ 正常打开、不抛 `StatePermissionError`、可写 `promoted_repo`。
  - localhost 绑定（`127.0.0.1`）→ `resolve_write_store` 返回非 None（RW，read_only False，busy_timeout 已设）。
  - **`::1`（IPv6 loopback）→ is_local=True、返回非 None**（防 IPv6 localhost 被误判禁写）。
  - **`0.0.0.0`（通配）→ is_local=False**（视为公网，无 opt-in → None；防硬门被绕过）。
  - 公网无 `--allow-public-writes` → 返回 None（写关）。
  - 公网 + `--allow-public-writes` → 非 None + 发 WARN（capture log）。
  - 端到端：None 注入 → POST 写端点 404（与 U2 联合断言）。
- **Verification:** 门控测试过；公网默认禁写、显式方开且告警。
- **Execution note:** 先写公网无 opt-in→None 的失败用例（守住默认安全），再实现门控。

### U6. 文档（README EN+zh + CONTEXT）

- **Goal:** README 双语加「看板写能力 / promote UI」段 + 威胁模型摘要 + 公网写门控；路线图把「一键晋升按钮」从延后移除；CONTEXT 补实现指针。
- **Requirements:** F4。
- **Dependencies:** U1-U5。
- **Files:** `README.md`、`README.zh-CN.md`、`CONTEXT.md`。
- **Approach:**
  - README「Dashboard」段加子节：promote/demote 按钮用法、确认流、写默认仅 localhost、公网须 `--allow-public-writes` + 前置鉴权代理警告。
  - **公网写运维须知**（承接威胁矩阵资源滥用行 + KTD-Write-6 Secure 约束）：公网写放开后须在前置代理处加**速率限制 / 仓名白名单**（防批量 promote 恶意仓耗 token 配额 / 注入 LLM 管线），且代理须以 **HTTPS 暴露并配置 cookie `Secure`**（否则 CSRF cookie 丢、写失效）。
  - 威胁模型一句话摘要（反 CSRF + Origin + 确认 + 公网硬门；身份 token、写速率限制仍延后/交运维代理）。
  - 路线图：「一键晋升按钮（UI）」从延后移除（已实现）；**保留**「看板完整 token 身份鉴权」「Web 改 watchlist/trend_scope.yaml」仍延后。
  - CONTEXT「晋升」词条补「看板 UI 入口（写 promoted_repo 表，经 reconcile 生效）」。同步测试数。EN+zh 对称。
- **Test scenarios:** Test expectation: none — 文档。
- **Verification:** 双语段齐全、命令可照抄；路线图反映 promote UI 已实现、token 鉴权与 Web 改配置仍延后；锚点/链接正确。

---

## Scope Boundaries

**In scope：** 看板 promote/demote 按钮 + GET 确认页 + POST 端点；CSRF double-submit + Origin 校验 + SameSite=Strict cookie + `form-action 'self'`；确认流；仓名校验 + demote-only-promoted 守卫；RW `write_store` 接线 + locked 友好处理；公网写硬门 `--allow-public-writes`；视图层按钮门控标志；EN+zh i18n；文档。写经 `promoted_repo` 表、reconcile 拾取（复用 F4 后端）。

### Deferred for later（origin/交接 既定延后，非本轮）
- **看板完整 token 身份鉴权**：本轮访问控制 = localhost-bind + Host 头 + 反 CSRF + 公网写硬门；公网放行后的远程身份鉴权须自行前置鉴权代理。多用户身份冒充威胁不在本轮（与只读计划「完整 token 鉴权延后」一致）。
- **Web 编辑 `watchlist.yaml` / `trend_scope.yaml`**：写配置文件风险最高（写坏可致 service 起不来）；本轮只写 `promoted_repo` 表，不碰配置文件。
- **晋升仓 `dependency_edges` 自动声明**（F4 既有延后，晋升仓为独立观测仓、无自动边）。

### Deferred to Follow-Up Work（本计划发现、本轮不做）
- 浏览器 `confirm()` 渐进增强（外部 dashboard.js 拦截 submit 弹确认）——本轮用服务端确认页即足，JS 增强可选、不阻塞。
- demote 后保留观测历史的「归档视图」增强（当前 demoted 有报告的仓仍可看，足够）。

---

## Risks & Dependencies

| 风险 | 缓解 |
|---|---|
| CSRF token 实现有洞 | 三层纵深（token + SameSite=Strict + Origin），任一独立挡跨站；`compare_digest` 恒定时间 |
| `SameSite=Strict` 致首次跨页导航丢 cookie | 看板单源自导航（非外链进写），Strict 不影响同源使用；确认页与 POST 同源 |
| RW 句柄与 service 写竞争致锁 | `StateStore` 加 `busy_timeout`（U5，非 app 层补；service 短锁时看板等待非即抛）+ 真持久锁 `OperationalError`→503 友好出口；promote 是单条 INSERT、持锁极短 |
| RW init 与 service 双进程同库竞争 | RW 复用 service 已建库（`_ensure_db_permissions`/WAL 库存在时幂等）；U5 验证双进程权限校验不误杀 |
| 公网误配静默暴露写 | 写二级硬门（默认 None）；须 `--allow-public-writes` 显式 + WARN；无 opt-in 端点 404 |
| demote 误删 config 仓观测 | UI 层 config 仓无 demote 入口 + 服务端拒 demote 非 promoted（双层） |
| 改 `make_dashboard_app` 签名破既有测试 | `write_store` 默认 None（向后兼容：不传 = 纯只读，既有测试零回归） |
| `form-action 'self'` 误伤既有表单 | 看板原无表单（只读），新增表单皆同源，无影响 |
| agent 期望免 CSRF 写 API | 明确 CLI 为 agent 入口（KTD-Write-7）；文档化；不弱化浏览器 CSRF |

**依赖：** F4 后端（`promoted_repo` 表 + `promote_repo`/`demote_repo`/`is_promoted`/`list_promoted_meta`，已交付）；只读看板基建（中间件栈、`_ctx`、模板继承、i18n，已交付）；运行中 service 的 reconcile job（已交付，UI 写经其生效）。

---

## Verification

1. `.venv/bin/python -m pytest -q` → 全绿（含新 csrf / 写端点 / data / i18n / 门控测试），只读看板 + F4 + Phase 0-3 零回归（基线 405）。
2. `.venv/bin/ruff check src tests` → clean。
3. 手验（localhost）：起 `transmutary-dashboard`，候选仓点「晋升」→ 确认页 → 确认 → 303 回总览、仓入清单、`promoted_repo` 表有行；promoted 仓点「取消晋升」→ 移除。
4. CSRF 手验：`curl -X POST localhost:8787/promote -d repo=o/r`（无 cookie/Origin）→ 403、不写表。
5. 公网门验：`--allow-public --host 0.0.0.0`（无 `--allow-public-writes`）→ POST /promote → 404；加 `--allow-public-writes` → 起时 WARN、端点开。
6.（可选 live）UI 晋升真实热门仓 → 等 reconcile 拍 → 确认 service 真观测、产诊断（验 KTD-Write-4 跨进程桥接）。

---

## Execution

经 workflow：build（U1→U2、U3→U4、U5、U6）→ 对抗安全审查（CSRF 三层各自有效性 + Origin 边缘[缺头/带端口/Referer 回退] + 恒定时间比对 + 公网门控默认安全 + demote 误删守卫 + locked 友好处理 + 仓名校验绕过 + 向后兼容 write_store=None + i18n 对称 + 零内联 script/style）→ 修复到绿。批准后落盘。版本预计 **v0.11.0**（feat→minor）。
