# Plan: Dashboard Settings — LLM Status Accuracy + Save Feedback

**Type:** fix
**Depth:** Lightweight
**Date:** 2026-06-03
**Sequence:** 002

---

## Context

Dashboard settings page has two UX defects:

1. **LLM key 显示缺失** — 运行态 secrets 表只查 `os.environ.get("TRANSMUTARY_LLM_API_KEY")`，不查本地 `config/llm.yaml`。用户通过表单保存了 LLM key 后，secrets 表仍显示"缺失"，但 pipeline 实际能通过 `effective_llm_config()` 正常使用（env > yaml 优先级）。
2. **保存无反馈** — 全部 8 个 POST handler（LLM、repo add/remove、edge add/remove、trends、delivery、login）成功后只做 303 重定向，无任何视觉反馈。settings 模板仅有 `error_key` 基础设施，无成功消息区域。

---

## Requirements

- R1: LLM API key 状态应反映 env var **或** local yaml 任一来源已配置即为"已配置"
- R2: 所有 settings POST 操作成功后显示短暂成功提示
- R3: 成功提示需双语（en/zh-CN），与现有 i18n 体系一致
- R4: 不引入 session store 或第三方依赖（app 架构约束：csrf.py 注明 "no session store"）

---

## Key Technical Decisions

**KTD1: Flash messages via query parameter.** 303 重定向时在 URL 上带 `?saved=llm` / `?saved=repo` 等参数，settings 页读取后渲染成功消息。理由：无 session store，query param 是最简单的有状态方案，与现有 `error_key` 模式对称。

**KTD2: LLM status 查 yaml.** `_secret_env()` 中 LLM API key 的检测改为：env var 有值 **或** `load_llm_config(config_dir)` 返回非 None 的 `LLMConfig`（`api_key` 非空）。复用已有 `load_llm_config()` 函数（`src/transmutary/config.py:288`）。

**KTD3: 成功提示样式复用 `empty` class.** 现有 error 消息使用 `<p class="empty">` 样式。成功消息使用 `<p class="saved">` 并在 CSS 中添加 `.saved` 样式（绿色调），保持视觉一致。

---

## Implementation Units

### U1. Fix LLM secret status to check yaml fallback

**Goal:** Settings 运行态 secrets 表正确反映 LLM key 来自 env var 或 yaml。

**Files:**
- `src/transmutary/dashboard/app.py` — `_secret_env()` closure
- `tests/test_dashboard.py`

**Approach:**
- `_secret_env()` 需访问 `config_dir`（已在 `make_dashboard_app` 作用域内）。调用 `load_llm_config(config_dir)` 获取 yaml 配置，合并判断：`bool(os.environ.get("TRANSMUTARY_LLM_API_KEY")) or bool(yaml_cfg and yaml_cfg.api_key)`。
- 同理处理 `base_url`：对 TRANSMUTARY_LLM_BASE_URL 也检查 yaml fallback（仅 status 显示，不影响功能）。

**Test scenarios:**
- LLM key only in env var → `configured=True`
- LLM key only in yaml → `configured=True`
- LLM key in both → `configured=True`
- LLM key in neither → `configured=False`

**Verification:** 启动 dashboard，通过 settings 表单保存 LLM key，刷新后运行态显示"已配置"。

---

### U2. Add flash-based save feedback to all settings POST handlers

**Goal:** 用户执行 settings 操作后看到短暂成功提示。

**Files:**
- `src/transmutary/dashboard/app.py` — 所有 POST handler 的 redirect URL
- `src/transmutary/dashboard/templates/settings.html` — 成功消息渲染
- `src/transmutary/dashboard/static/dashboard.css` — `.saved` 样式
- `src/transmutary/dashboard/i18n.py` — 双语成功消息 key
- `tests/test_dashboard.py`

**Approach:**

1. **i18n keys** — 在 `i18n.py` 的 en/zh-CN 字典中添加：
   - `"saved_llm"`, `"saved_repo"`, `"saved_repo_removed"`, `"saved_edge"`, `"saved_edge_removed"`, `"saved_trends"`, `"saved_delivery"`

2. **Redirect with query param** — 每个 POST handler 成功路径改为 `_settings_redirect_with("saved", "llm")` 等，返回 `RedirectResponse("/settings?saved=llm", status_code=303)`。

3. **Template rendering** — `settings_page` GET handler 从 `request.query_params` 读取 `saved`，映射到 i18n key（`"saved_" + value`），传入模板 context 为 `success_key`。

4. **Template** — 在现有 `{% if error_key %}` 之后加 `{% if success_key %}<p class="saved">{{ t[success_key] }}</p>{% endif %}`。

5. **CSS** — `.saved { color: #15803d; }` （Tailwind green-700 色调，与现有设计协调）。

6. **JS auto-dismiss（可选）** — 在 `dashboard.js` 中添加简单逻辑：3 秒后自动隐藏 `.saved` 消息（`setTimeout(() => el.remove(), 3000)`）。

**Test scenarios:**
- POST `/settings/llm` 成功 → 303 redirect → GET `/settings?saved=llm` → 页面含成功消息
- POST `/settings/llm` 失败（空 key）→ 400 → 页面含错误消息，无成功消息
- GET `/settings` 无 query param → 无成功消息也无错误消息
- 各 handler 的 saved param 值正确映射到对应 i18n key

**Verification:** 启动 dashboard，登录后逐一操作 settings 表单，确认每次保存后出现绿色成功提示并在数秒后消失。

---

## Scope Boundaries

### In scope
- `_secret_env()` LLM yaml fallback
- 所有 8 个 POST handler 的成功反馈
- 双语 i18n 支持

### Out of scope
- Session store 或 server-side flash 基础设施
- 第三方 toast/notify 库
- Login 成功的反馈（login 后本身就是进入 settings 页，`saved` param 语义不符；login 成功可单独处理或跳过）
- 其他 secret（SMTP、RSS 等）的 yaml fallback（它们不走 yaml，只走 env）

### Deferred
- 登录成功提示 — 可在 U2 中顺带加一个 `?saved=login` 但语义上 login 不是 "save"，需考虑 UX 措辞
