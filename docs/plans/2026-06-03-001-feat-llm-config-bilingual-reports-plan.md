---
title: "feat: LLM provider visual config + bilingual reports"
created: 2026-06-03
status: active
reviewed: 2026-06-03
---

## Summary

两个特性：(1) LLM API key + base URL 可视化配置（dashboard settings + CLI `transmutary config`），凭据写入 `config/llm.yaml`（0600 权限），保留 KTD4 安全不变量（凭据不入 SQLite）；(2) 报告双语输出——LLM 单次调用同时生成中英正文，`Report` schema 扩展 `body_md_zh` 字段，dashboard 报告页按语言 cookie 切换，RSS feed 支持双语条目。

## Problem Frame

**现状问题：**

1. **LLM 凭据配置门槛高** — `TRANSMUTARY_LLM_API_KEY` 和 `TRANSMUTARY_LLM_BASE_URL` 只能通过环境变量设置。用户需要手动查文档、编辑 `.env` 或 `export`，没有引导式交互。对非开发者用户（纯 dashboard 用户）完全不友好。
2. **报告仅英文** — 所有 LLM 生成内容（诊断报告、趋势摘要、供应链告警）均为英文。仪表盘 UI 已支持中英双语切换，但报告内容不受影响，体验割裂。

**用户期望：** 类似 opencode 的交互式 provider 选择 → key 输入 → 保存配置的引导流程。

## Requirements

| ID | 需求 | 来源 |
|---|---|---|
| R1 | Dashboard settings 新增 LLM 配置面板：API key 输入（masked）、base URL 输入 | 用户请求 |
| R2 | CLI `transmutary config` 交互式配置向导：输入 key → 可选 base URL → 确认保存 | 用户请求 |
| R3 | 凭据存储在 `config/llm.yaml`（0600 权限），不存 SQLite，保留 KTD4 不变量 | 架构约束 |
| R4 | 凭据解析优先级：env var > llm.yaml > 报错缺失 | 向后兼容 |
| R5 | 报告正文同时包含中文和英文（单次 LLM 调用输出双语） | 用户请求 |
| R6 | `Report` schema 新增 `body_md_zh: str \| None`，向后兼容（`None` = 旧版单语报告） | 设计 |
| R7 | Dashboard 报告页按 `tmtry-lang` cookie 展示对应语言正文 | 复用现有 i18n |
| R8 | RSS feed 条目包含双语内容 | 投递完整性 |
| R9 | **三条报告管道全覆盖**：diagnose / explain / supply-chain alert 均为双语 | 审查 ADV-01 |
| R10 | **双语安全注解对称**：KTD2 强制命中 + 阻断注解同时追加到 EN 和 ZH 正文 | 审查 ADV-12 |
| R11 | **BILINGUAL:SPLIT 标记消毒**：untrusted data 中出现标记时先清理，防注入 | 审查 ADV-07 |
| R12 | **explain JSON 向后兼容**：保留 `summary` 字段（英文），新增 `summary_zh` | 审查 ADV-02 |

## Key Technical Decisions

### KTD1: 凭据存储方式——YAML 文件而非 SQLite

**决策：** LLM API key 和 base_url 存入 `config/llm.yaml`（项目本地），0600 权限。不存 provider 选择字段（provider 由 LiteLLM model name 决定）。

**理由：**
- 保留 KTD4 安全不变量——凭据永不入 SQLite，不序列化到 `repr`/日志
- YAML 文件与现有 `watchlist.yaml`/`trend_scope.yaml`/`delivery.yaml` 同级，用户心智模型一致
- 0600 权限由加载时强制检查（复用 `permissions.py` 的 `enforce_private_mode`）
- **不存 provider 字段**（审查 ADV-03）：LiteLLM 用 model name 路由 provider，`llm.call()` 没有 provider 参数。provider selector 与实际调用链无关联，留作 model 选择功能的后续增强

**优先级链：** `os.environ` > `config/llm.yaml` > `ConfigError`。环境变量优先确保 CI/Docker 部署不受影响。

**实现要点（审查 ADV-08）：** `TRANSMUTARY_LLM_API_KEY` 需从 `_REQUIRED_ENV` 移除，改为在 `effective_llm_config()` 中单独检查。`Credentials.from_env()` 对 LLM key 缺失不报错（返回 `_Secret("")`），`effective_llm_config()` 合并 env + yaml 后仍为空才报错。GitHub Token 等 4 个 env var 保持 required 不变。

### KTD2: 双语策略——单次 LLM 调用

**决策：** 在 system instruction 中要求 LLM 同时输出英文和中文正文，用 `<!-- BILINGUAL:SPLIT -->` 分隔。管道解析后填入 `body_md`（EN）和 `body_md_zh`（ZH）。

**理由：**
- 成本约 1.5x（输出 token 增长 ~1.5x），远低于双次调用的 2x
- 延迟几乎不增——一次网络往返
- 不增加管道复杂度——不引入翻译步骤、不增加 LLM 调用次数

**备选：** 先英文再翻译（两次调用）。成本 2x、延迟翻倍。留作可选增强。

**标记消毒（审查 ADV-07）：** `llm.py` 的 `_neutralize_fences()` 新增对 `BILINGUAL:SPLIT` 的清理——untrusted data 中出现此标记时替换为 `[redacted]`，防止攻击者注入假分隔符劫持双语拆分点。

### KTD3: 双语 body 解析——后处理策略

**决策：** 双语解析在所有安全后处理之后执行。diagnose 管道的执行顺序：

1. LLM 生成原始双语输出（含 BILINGUAL:SPLIT）
2. `sanitize_security_verdicts()` 对全文运行 KTD2 清洗
3. 追加 forced_hits / blocked notes / 待核实信号 注解到 body_parts
4. 组装完整 body_md（含安全注解）
5. **最后一步**：`parse_bilingual(body_md)` 拆分 EN/ZH
6. 安全注解（forced_hits、blocked notes）对称追加到 ZH 正文

**理由（审查 ADV-12）：** 先完成所有安全后处理，再拆分。安全注解对 EN/ZH 对称追加，确保两种语言的报告信息量一致。

### KTD4: explain JSON 格式向后兼容

**决策（审查 ADV-02）：** explain 的 JSON 格式保留 `summary` 字段（英文），新增 `summary_zh` 字段。`_parse_batch_summaries` 逻辑：`summary = obj.get("summary", "")`, `summary_zh = obj.get("summary_zh", "")`。旧 stub 只返回 `summary` → `summary_zh` 为空 → 向后兼容。

## Scope Boundaries

### In scope

- `config/llm.yaml` 加载/保存/权限（仅 api_key + base_url，不含 provider）
- Dashboard settings LLM 配置面板（api_key + base_url 输入）
- CLI `transmutary config` 交互式子命令
- `effective_config.py` 新增 `effective_llm_config()`
- `_REQUIRED_ENV` 调整：LLM key 改为可选
- Report schema 扩展 `body_md_zh`
- diagnose/explain/**security build_alert** 三条管道的 system instruction 双语改造
- body 双语解析器 + BILINGUAL:SPLIT 标记消毒
- Artifact 双语渲染
- Dashboard 报告页语言切换（从 JSON sidecar 读 body_md_zh）
- RSS feed 双语条目
- Demo `_stub_call` 更新为返回双语格式
- 单元测试覆盖

### Out of scope (non-goals)

- GitHub Token 可视化配置（下轮）
- 邮件正文双语渲染（投递层暂不动）
- LLM provider selector（provider 由 LiteLLM model 决定，本阶段不引入 selector）
- model 选择（当前 LiteLLM alias 已够用）
- critique-refine 双语（refine 先保持单语，双语在初次生成时完成）

### Deferred to follow-up

- `transmutary-demo --serve` 一键 demo → dashboard 联动命令
- provider 连接测试（LLM config 面板上的 "Test connection" 按钮）
- model 选择器 + provider 路由映射
- 运行时凭据热重载（CLI 写 yaml 后 service 自动 reload）

---

## Implementation Units

### U1. LLM config file + loader + credential resolution

**Goal:** 新增 `config/llm.yaml` 加载/保存机制，凭据解析优先级链（env > yaml > error），0600 权限强制。LLM key 从 `_REQUIRED_ENV` 移除，改为 `effective_llm_config()` 合并检查。

**Dependencies:** none

**Files:**
- `src/transmutary/config.py` — 新增 `LLMConfig` dataclass、`load_llm_config()`、`save_llm_config()`；LLM key 从 `_REQUIRED_ENV` 移除；`Credentials` 处理 LLM key 缺失
- `config/llm.example.yaml` — 示例配置文件
- `src/transmutary/effective_config.py` — 新增 `effective_llm_config(settings, config_dir)` 返回 `(api_key, base_url)`
- `src/transmutary/pipeline.py` — `_llm_api_key()` / `_llm_base_url()` 改为调用 `effective_llm_config()`；`build_runtime()` 中 Settings 重建时转发 `llm_config` 字段
- `src/transmutary/service.py` — `build_runtime()` 适配新凭据合并逻辑
- `src/transmutary/demo.py` — `build_runtime()` 调用点适配
- `tests/test_config.py` — 加载/保存/优先级/权限测试
- `tests/test_pipeline.py` — Settings 重建时 llm_config 保留测试
- `tests/test_effective_config.py` — `effective_llm_config` 合并优先级测试

**Approach:**

1. `LLMConfig` dataclass: `api_key: str`, `base_url: str | None`（不含 provider 字段）
2. `load_llm_config(config_dir)` 从 `config/llm.yaml` 读取，文件不存在返回 `None`；存在时检查 0600 权限
3. `save_llm_config(config_dir, config)` 写入 YAML，强制 0600
4. `effective_llm_config()` 返回 `(api_key, base_url)`。合并逻辑：env 值存在 → 用 env；env 缺失 → 用 yaml 值；两者都缺失 → `ConfigError`
5. `TRANSMUTARY_LLM_API_KEY` 从 `_REQUIRED_ENV` 移除。`Credentials.from_env()` 对 LLM key 缺失存 `_Secret("")` 不报错
6. `Settings` 新增 `llm_config: LLMConfig | None = None`（有默认值，向后兼容）。`llm_base_url` 保留在 Settings 上，`effective_llm_config()` 合并后覆盖
7. **pipeline.py build_runtime Settings 重建（审查 ADV-04）**：`Settings(watchlist=..., ..., llm_config=settings.llm_config, ...)` 转发新字段
8. base_url 保持在 `Settings.llm_base_url` 上，不加入 `Credentials`（审查 ADV-06）。`effective_llm_config()` 合并后直接设置到 `Settings.llm_base_url`

**Patterns to follow:** `Delivery` / `TrendScope` 的 YAML 加载模式；`permissions.py` 的 `enforce_private_mode`；`effective_delivery()` 合并模式。

**Test scenarios:**
- yaml 存在且权限正确 → 正常加载
- yaml 不存在 → 返回 None
- yaml 权限过宽 → ConfigError
- env var 设值 → 忽略 yaml 中的同名值
- env var 缺失、yaml 有值 → 使用 yaml 值
- 两者都缺失 + require_credentials=True → ConfigError
- 两者都缺失 + require_credentials=False → 返回空值（dashboard 场景）
- `save_llm_config` 写入后文件权限为 0600
- `build_runtime` Settings 重建后 `llm_config` 不丢失（审查 ADV-04）
- `Report.to_dict()` 不包含 `LLMConfig` 字段（审查 ADV-09：LLMConfig 不挂到 Report 对象图上）

**Verification:** `uv run pytest tests/test_config.py tests/test_effective_config.py tests/test_pipeline.py -q` 全绿。

---

### U2. Dashboard LLM settings UI + CLI config command

**Goal:** Dashboard settings 新增 LLM 配置面板（API key masked 输入 + base URL）；CLI 新增 `transmutary config` 交互式子命令。

**Dependencies:** U1

**Files:**
- `src/transmutary/dashboard/templates/settings.html` — 新增 LLM 配置面板
- `src/transmutary/dashboard/app.py` — 新增 `settings_llm` POST handler
- `src/transmutary/dashboard/data.py` — `AdminSettingsView` 扩展 LLM 配置状态
- `src/transmutary/dashboard/i18n.py` — 新增 LLM 相关 i18n keys
- `src/transmutary/cli.py` — 新增 `config` 子命令
- `tests/dashboard/test_app.py` — settings POST 测试
- `tests/test_cli.py` — CLI config 子命令测试

**Approach:**

**Dashboard:**
1. settings.html 在 Delivery 和 Runtime 之间新增 "LLM Configuration" 面板
2. 表单字段：API key（password input，masked 显示）、base URL（text input）
3. POST `/settings/llm` 接收表单数据，调用 `save_llm_config()` 写入 yaml
4. 页面加载时读取当前配置，key 显示为 `sk-****`（仅展示前4位 + 星号）
5. CSRF 保护沿用现有双重提交模式
6. **HTTP 明文警告（审查 ADV-11）**：POST handler 检测非 localhost + 非 HTTPS 时在响应中附加 warning 注解。不阻断操作，但提醒用户应使用 HTTPS

**CLI:**
1. `transmutary config` 子命令：交互式问答
2. 步骤：输入 API key（`getpass.getpass()`）→ 输入 base URL（可选）→ 确认 → 保存到 `config/llm.yaml`
3. 已有配置时提示 "当前已配置 key=sk-****，是否覆盖？"

**Patterns to follow:** 现有 settings POST handler（`settings_add_repo`, `settings_delivery`）；`cli.py` 的 argparse 子命令模式。

**Test scenarios:**
- POST `/settings/llm` 有效数据 → 303 redirect，yaml 已创建且权限 0600
- POST `/settings/llm` 缺少 api_key → 400
- POST `/settings/llm` 无效 CSRF → 403
- GET `/settings` 展示 LLM 面板，已配置时 key masked 显示
- POST `/settings/llm` over HTTP + 非 localhost → 响应包含 warning
- CLI `transmutary config` 完成交互 → yaml 已写入
- CLI `transmutary config` 取消 → yaml 未修改
- 无 admin token 时 LLM settings 面板不可见

**Verification:** `uv run pytest tests/dashboard/test_app.py tests/test_cli.py -q` 全绿。

---

### U3. Bilingual report schema + LLM instructions + body parser (all 3 pipelines)

**Goal:** Report schema 扩展双语字段；三条管道的 LLM system instruction 改为要求双语输出（diagnose + explain + security build_alert）；新增 body 双语解析器；标记消毒；安全注解对称追加。

**Dependencies:** none

**Files:**
- `src/transmutary/report/schema.py` — `Report` 新增 `body_md_zh: str | None`
- `src/transmutary/report/body_parse.py` — 新模块：双语 body 解析器
- `src/transmutary/report/diagnose.py` — `_DIAGNOSE_SYSTEM` 改为双语；`diagnose()` 后处理中 `parse_bilingual` 放在安全注解之后（R10/KTD3）
- `src/transmutary/report/explain.py` — `_EXPLAIN_SYSTEM` 改为双语；JSON 格式保留 `summary` + 新增 `summary_zh`（R12/KTD4）
- `src/transmutary/collect/security.py` — `_ADVICE_SYSTEM` 改为双语；`build_alert()` 走 `parse_bilingual`（审查 ADV-01）
- `src/transmutary/report/refine.py` — `_CRITIQUE_SYSTEM` / `_REFINE_SYSTEM` 改为双语；refine 明确操作双语全文（审查 ADV-05）
- `src/transmutary/llm.py` — `_neutralize_fences()` 新增 BILINGUAL:SPLIT 标记消毒（审查 ADV-07）
- `src/transmutary/demo.py` — `_stub_call` 更新为返回双语格式（审查 ADV-13）
- `tests/report/test_schema.py` — `body_md_zh` 序列化/反序列化
- `tests/report/test_body_parse.py` — 解析器测试
- `tests/report/test_diagnose.py` — 双语输出 + 安全注解对称测试
- `tests/report/test_explain.py` — 双语输出 + JSON 向后兼容测试
- `tests/collect/test_security.py` — build_alert 双语测试
- `tests/test_llm.py` — 标记消毒测试

**Approach:**

1. **Schema 扩展**: `Report` 新增 `body_md_zh: str | None = None`。`to_dict()` 使用显式字段构建（不用 `asdict`，审查 ADV-09）。`from_dict()` 无 `body_md_zh` 键 → `None`（向后兼容）。

2. **双语 body 解析器**: `parse_bilingual(raw: str) -> tuple[str, str | None]`。拆分 `<!-- BILINGUAL:SPLIT -->`，无标记返回 `(raw, None)`。

3. **标记消毒（审查 ADV-07）**: `llm.py` 的 `_neutralize_fences()` 新增规则：untrusted data 中 `<!-- BILINGUAL:SPLIT -->` 替换为 `[split-marker-redacted]`。

4. **System instruction 改造**: 所有 `_*_SYSTEM` 常量追加：
   - "Produce your response in TWO sections separated by `<!-- BILINGUAL:SPLIT -->`. First section in English, second section in 中文 (Simplified Chinese). Both sections must convey the same information."

5. **diagnose 管道后处理顺序（KTD3/审查 ADV-12）**:
   - LLM 输出 → sanitize_security_verdicts → forced_hits + blocked notes → 组装完整 body_md
   - `parse_bilingual(body_md)` → EN 正文 + ZH 正文
   - 安全注解（forced_hits、blocked notes）同时追加到 ZH 正文，确保信息对称

6. **explain JSON 格式（KTD4/审查 ADV-02）**:
   - `_EXPLAIN_SYSTEM` 要求 JSON: `{"index": N, "summary": "<EN>", "summary_zh": "<ZH>"}`
   - `_parse_batch_summaries` 读 `summary`（EN）和 `summary_zh`（ZH）
   - `_build_report` 用 `summary_zh` 构建 `body_md_zh`

7. **security build_alert（审查 ADV-01）**:
   - `_ADVICE_SYSTEM` 同样追加双语指令
   - `build_alert()` 返回前走 `parse_bilingual`
   - 结果 Report 设 `body_md` 和 `body_md_zh`

8. **refine 双语（审查 ADV-05）**:
   - `_CRITIQUE_SYSTEM` 明确告知模型输入包含双语两部分，要求分别评价
   - `_REFINE_SYSTEM` 明确要求双语输出
   - critique-refine 操作完整的双语文本，两部分同时改进

9. **Demo stub 更新（审查 ADV-13）**:
   - `_stub_call` 的 "sourcing diagnostician" 和 "trend explainer" 分支返回含 `BILINGUAL:SPLIT` 的双语文本

**Patterns to follow:** 现有 `_*_SYSTEM` 指令结构；`_neutralize_fences` 标记消毒模式。

**Test scenarios:**
- `parse_bilingual("EN<!-- BILINGUAL:SPLIT -->ZH")` → `("EN", "ZH")`
- `parse_bilingual("EN only")` → `("EN only", None)`
- `parse_bilingual("")` → `("", None)`
- `parse_bilingual("<!-- BILINGUAL:SPLIT -->")` → `("", "")`
- `Report.to_dict()` 含 `body_md_zh`；不含 `LLMConfig`（审查 ADV-09）
- `Report.from_dict()` 无 `body_md_zh` → `None`
- `Report.from_dict()` 有 `body_md_zh` → 正确加载
- `_neutralize_fences` 清理 BILINGUAL:SPLIT 标记（审查 ADV-07）
- diagnose stub → body_md + body_md_zh 非空
- diagnose stub → KTD2 forced_hits 同时出现在 EN 和 ZH（审查 ADV-12/R10）
- explain stub → reports 每条 body_md_zh 非空
- explain JSON 只有 `summary` 无 `summary_zh` → body_md_zh=None（向后兼容，审查 ADV-02）
- build_alert stub → body_md + body_md_zh 非空（审查 ADV-01）
- refine 双语输入 → 输出仍为双语（审查 ADV-05）

**Verification:** `uv run pytest tests/report/ tests/collect/test_security.py tests/test_llm.py -q` 全绿。

---

### U4. Bilingual artifact rendering + dashboard display + RSS

**Goal:** Artifact store 双语渲染；dashboard 报告页按语言 cookie 切换正文（从 JSON sidecar 读 body_md_zh）；RSS feed 双语条目。

**Dependencies:** U3

**Files:**
- `src/transmutary/store/artifacts.py` — `_render_markdown` 双语渲染；`read_meta` 方法暴露 sidecar JSON
- `src/transmutary/dashboard/data.py` — `ReportView` 扩展 `body_zh`；`build_report_view` 从 JSON sidecar 读 `body_md_zh`
- `src/transmutary/dashboard/templates/report.html` — 报告页语言切换 UI
- `src/transmutary/dashboard/static/dashboard.js` — 报告页语言切换逻辑
- `src/transmutary/dashboard/i18n.py` — 报告页 i18n keys
- `src/transmutary/deliver/rss.py` — feed 双语条目
- `src/transmutary/dashboard/app.py` — `report_page` handler 传递双语 body
- `tests/store/test_artifacts.py` — 双语渲染测试
- `tests/deliver/test_rss.py` — 双语 feed 测试
- `tests/dashboard/test_app.py` — 报告页语言切换测试

**Approach:**

1. **Artifact 双语渲染**: `_render_markdown()` 检查 `body_md_zh`，非 None 时追加 `## 中文` + ZH 正文。JSON sidecar 同样含 `body_md_zh`。

2. **Dashboard 报告页（审查 ADV-10）**:
   - `ReportView` 新增 `body_zh: str | None`
   - `build_report_view` **从 JSON sidecar** 读取 `body_md` 和 `body_md_zh`（不从 .md 文件中解析）
   - .md 文件作为 fallback（无 sidecar 时）
   - 前端 JS：若 `body_zh` 存在，根据 `tmtry-lang` cookie 切换显示；无 `body_zh` 时不展示切换 UI

3. **RSS feed**: `render_entry()` 检查 `body_md_zh`，非 None 时追加中文正文。

4. **i18n keys**: 新增 `report.lang.zh` / `report.lang.en`。

**Patterns to follow:** 现有 `data-i18n` + cookie + JS toggle 模式；`ArtifactStore.read_meta()` sidecar 读取模式。

**Test scenarios:**
- `_render_markdown(report)` 有 body_md_zh → 输出含 `## 中文`
- `_render_markdown(report)` 无 body_md_zh → 输出仅英文（向后兼容）
- JSON sidecar 含 body_md_zh
- `build_report_view` 从 sidecar 读 body_md_zh，不从 .md 解析（审查 ADV-10）
- `build_report_view` 无 sidecar → fallback 到 .md，body_zh=None
- RSS entry 有 body_md_zh → content 含中英两段
- RSS entry 无 body_md_zh → content 仅英文
- GET `/report/...` 返回 HTML 含 body_zh 数据
- GET `/report/...?format=json` 返回 JSON 含 body_zh

**Verification:** `uv run pytest tests/store/test_artifacts.py tests/deliver/test_rss.py tests/dashboard/test_app.py -q` 全绿；手动 dashboard 查看双语报告。

---

## Risks & Mitigations

| 风险 | 概率 | 影响 | 缓解 |
|---|---|---|---|
| LLM 不遵循双语分隔标记 | 中 | 中文解析失败，回退到英文 | `parse_bilingual` 无标记返回 `(raw, None)`；degrade 优雅 |
| `config/llm.yaml` 权限检查在 Windows 不可用 | 低 | 凭据文件可能过宽 | 复用 `IS_WINDOWS` 分支跳过但记录 warning |
| 双语输出 token 增长超预期（中文字符 token 密度更高） | 中 | 日预算消耗加快 | LiteLLM `BudgetManager` 日预算上限兜底 |
| HTTP 明文传输 API key | 低 | LAN 监听风险 | POST handler 检测非 HTTPS 时附 warning；推荐 CLI 配置或 HTTPS |
| BILINGUAL:SPLIT 标记注入 | 低 | 双语拆分点被劫持 | `_neutralize_fences` 清理 untrusted data 中的标记 |
| critique-refine 处理双语文本时质量下降 | 低 | ZH 正文可能被 degrade | critique/refine 指令明确要求双语评价和输出 |

## System-Wide Impact

- **Config**: `config.py` `_REQUIRED_ENV` 缩减；新增 `LLMConfig` + `load_llm_config` + `save_llm_config`
- **Effective config**: `effective_config.py` 新增 `effective_llm_config()`
- **Pipeline**: `_llm_api_key()` / `_llm_base_url()` 改用 `effective_llm_config()`
- **Service**: `build_runtime()` 适配新凭据合并
- **Demo**: `_stub_call` 更新为返回双语格式
- **LLM**: `_neutralize_fences()` 新增标记消毒
- **Delivery**: `rss.py` 渲染逻辑扩展但不改变路由/投递时机
- **Dashboard**: settings 新增面板，report 页新增语言切换
- **All 3 report paths**: diagnose + explain + security build_alert 均为双语

## Open Questions

无阻塞问题。以下为实现时再确认的细节：
- 双语指令的精确措辞（需实测确认 LLM 遵循率）
- 安全注解对称追加的具体实现（append 到 ZH 正文时是否需要翻译标题行）
- Dashboard LLM 面板是否需要 "清除配置" 按钮
