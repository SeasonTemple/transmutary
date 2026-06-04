# Plan: Per-Tier LLM Configuration

**Type:** feat
**Depth:** Standard
**Date:** 2026-06-04
**Sequence:** 002

---

## Context

三个 LLM tier 各有真实用途（本 session 核实）：
- **STRONG** — diagnose（诊断）+ filter L3 judge（issue 真假告警判定）+ refine（critique/refine）
- **CHEAP** — security 告警建议 + trend explain 摘要
- **EMBED** — `rerank.py` L2 语义分组（issue 激增聚类 + trend 去重，把多条 L3 调用折叠成每组一次）

**问题**：当前 `LLMConfig` 是**一组共享 `api_key`/`base_url`/`transport` + 三个 model 名**。但 tier 可能在**不同 provider**。实证：用户 chat（strong/cheap）走 MiniMax（`minimax/` transport），但 **LiteLLM 的 minimax provider 不支持 embedding**——embed 需要不同 transport（OpenAI 兼容）+ 大概率不同 key。当前 embed 被迫共用 chat 的 key/url/transport → 运行时失败 → pipeline 降级全 L3（能跑，但费钱、慢）。

**目标**：每 tier 可选独立 `api_key`/`base_url`/`transport`/`model`，未覆盖回落共享默认。单 provider 常见场景不变；embed 跨 provider 可行。向后兼容现有扁平 yaml/env。

---

## Requirements

- R1: 每 tier（strong/cheap/embed）可独立配置 `api_key`/`base_url`/`transport`/`model`，逐字段回落到共享默认
- R2: 单 provider 场景零改动 —— 现有扁平 `config/llm.yaml`（顶层 api_key/base_url/transport/model_*）继续工作
- R3: 现有 env vars（`TRANSMUTARY_LLM_API_KEY`/`_BASE_URL`/`_TRANSPORT`/`_MODEL_{STRONG,CHEAP,EMBED}`）继续工作；新增 per-tier env 覆盖
- R4: Dashboard 表单默认单 provider（现状），per-tier 覆盖藏在折叠「高级」区
- R5: embed 明确可选 —— 不配 embed 凭据时，pipeline 降级全 L3（现有 KTD-F 行为），文档化
- R6: per-tier key 都是 secret，同 0600/env 优先/掩码 规则
- R7: CLI 向导支持 per-tier 覆盖（不强制，默认只问共享组）
- R8: 中英 README 文档化 per-tier 模型

---

## Key Technical Decisions

**KTD1: 数据模型 = 共享默认 + per-tier 可选覆盖（完整 4 字段）。** `LLMConfig` 保留顶层 `api_key`/`base_url`/`transport`/`model_strong`/`model_cheap`/`model_embed`（向后兼容），新增 `tier_overrides: dict[str, TierOverride]`。`TierOverride` 是**嵌套 frozen dataclass**（`api_key`/`base_url`/`transport`/`model` 各 `str | None`，`api_key` 标 `repr=False`）—— 用显式 dataclass 而非裸 dict，关键是 per-tier api_key 也要排除出 repr（grill 核实：裸 dict 装 key 会进 repr 泄漏，KTD4 破口）。每 tier 解析：`tier 覆盖 > 共享顶层 > 默认`，**逐字段独立回落**（如 embed 只覆盖 base_url+transport，key 仍回落共享）。选完整 per-tier（A）而非「仅 embed 块」（B）——代价几乎相同，但不锁死未来 strong/cheap 也拆 provider。

**KTD2: `effective_llm_config` 返回 per-tier 解析结果。** 当前返回 `(api_key, base_url, models_dict)` 单 key/url。改为返回每 tier 的完整 `(api_key, base_url, transport_prefixed_model)` 三元组——即 `{strong: (key,url,model), cheap: (...), embed: (...)}`。pipeline 按 tier 取对应三元组，不再共用单 key/url。

**KTD3: yaml schema —— 嵌套 `tiers` 块，顶层保持扁平。** 顶层字段 = 共享默认（向后兼容）。新增可选 `tiers: {embed: {api_key, base_url, transport, model}, ...}`。只填 `tiers.embed.base_url` + `tiers.embed.transport` 就够（其余回落）。

**KTD4: env var per-tier 覆盖 —— 统一 `TRANSMUTARY_LLM_<TIER>_<FIELD>` 命名。** 现有 `TRANSMUTARY_LLM_*`（无 tier）= 共享。新增 per-tier 全走统一格式：`TRANSMUTARY_LLM_EMBED_API_KEY` / `_EMBED_BASE_URL` / `_EMBED_TRANSPORT` / `_EMBED_MODEL`（strong/cheap 同理）。**命名一致性**（grill 核实）：现有 model env 是 `TRANSMUTARY_LLM_MODEL_EMBED`（MODEL 在前），新格式是 `TRANSMUTARY_LLM_EMBED_MODEL`（TIER 在前）—— 不一致会乱。决策：per-tier 一律 `<TIER>_<FIELD>`；旧 `TRANSMUTARY_LLM_MODEL_{STRONG,CHEAP,EMBED}` 保留为 back-compat 别名（继续识别，文档标 deprecated）。env per-tier > env 共享 > yaml per-tier > yaml 共享 > 默认。

**KTD5: Dashboard 折叠式 per-tier 覆盖。** 默认表单 = 现状单 provider 组。底部 `<details>` 折叠区「Per-tier overrides (advanced)」，展开露每 tier 的 key/url/transport/model（占位提示「留空 = 用上面共享配置」）。CSP 合规（无 inline style/script，`<details>` 原生）。

**KTD6: embed 可选 + 降级文档化。** embed 凭据全空 → `effective_llm_config` 的 embed 三元组 key 为空 → `_embed_fn` 调用 `llm.embed` 抛 LLMError → 现有 KTD-F 降级全 L3。**这是现成行为**，本 plan 只确保配置层能独立指向 + 文档说明「不配 embed = 全 L3，正确但更贵」。

---

## High-Level Technical Design

解析优先级（每 tier 每字段独立求值）：

```
tier 字段最终值 =
    env  per-tier  (TRANSMUTARY_LLM_<TIER>_<FIELD>)
 → env  shared    (TRANSMUTARY_LLM_<FIELD>)
 → yaml per-tier  (tiers.<tier>.<field>)
 → yaml shared    (顶层 <field>)
 → 内置默认       (仅 model：DEFAULT_TIER_MODELS)
```

数据流（改动点）：

```
config/llm.yaml (扁平共享 + 可选 tiers 块)
  + env vars (共享 + 可选 per-tier)
        ↓ effective_llm_config()  ← 改：返回 per-tier 三元组 dict
  {strong: (key,url,model), cheap: (key,url,model), embed: (key,url,model)}
        ↓ pipeline 按 tier 取三元组
  diagnose/filter → strong 三元组
  security/explain → cheap 三元组
  _embed_fn → embed 三元组  ← 改：取 embed 的 key/url，非共享
        ↓
  llm.call / llm.embed (已收 api_key/base_url/model)
```

---

## Implementation Units

### U1. Extend LLMConfig schema with optional per-tier overrides

**Goal:** schema 支持 per-tier 覆盖，向后兼容扁平。

**Requirements:** R1, R2, R6

**Files:**
- `src/transmutary/config.py` — `LLMConfig` + `load_llm_config`/`save_llm_config`
- `tests/test_config.py`

**Approach:**
- `LLMConfig` 加可选 per-tier 字段。倾向 `tier_overrides: dict[str, dict] | None`（键 strong/cheap/embed，值含 api_key/base_url/transport/model 可选子集），或显式 dataclass `TierConfig`。保持顶层字段不变。
- `load_llm_config`：解析 `tiers:` 嵌套块（缺失 → None，向后兼容）。per-tier api_key 同样受 0600 保护（整文件 0600）。
- `save_llm_config`：序列化 tiers 块（仅非空）。
- `api_key` repr 排除规则扩展到 per-tier key（KTD4 secrecy）。

**Test scenarios:**
- 扁平 yaml（无 tiers）→ 加载等价现状，tier_overrides=None
- yaml 含 `tiers.embed.{base_url,transport}` → 加载出 embed 覆盖，strong/cheap 无覆盖
- per-tier api_key 不入 repr
- save→load roundtrip 保留 tiers 块
- 旧 `model`/`vendor`/`provider` 向后兼容键仍映射（现有 back-compat 不破）

---

### U2. Per-tier resolution in effective_llm_config

**Goal:** 解析输出每 tier 完整 (key, base_url, model) 三元组，逐字段回落。

**Requirements:** R1, R3, R5

**Dependencies:** U1

**Files:**
- `src/transmutary/effective_config.py`
- `src/transmutary/dashboard/app.py` — `settings_llm_test` 适配新返回（取 strong 三元组）
- `tests/test_effective_config.py`
- `tests/test_dashboard.py` — test endpoint 适配

**Approach:**
- 重构 `effective_llm_config` 返回类型：`{tier: (api_key, base_url, model)}`（model 已应用 transport 前缀）。**破坏性签名变更** —— grill 核实调用方共 **2 处生产代码 + 9 处 test**：pipeline 的 `_llm_api_key`/`_llm_base_url`/`_llm_model`（U3 改）、dashboard `settings_llm_test`（app.py:601，本单元一并改：取 strong 三元组测连接，否则测的共享 key 与实际 strong key 不一致）、`tests/test_effective_config.py` ×9（本单元改）。无外部 consumer。
- 每 tier 每字段按 HTD 五级优先级求值。transport 前缀逻辑（现有 bare-name → prefix）按 tier 的 transport 应用。
- `require` 语义调整：require=True 时只要求**至少 strong 可用**（embed 可空 → 降级）；embed 空不报错。

**Test scenarios:**
- 全扁平共享 → 三 tier 同 key/url，各自 model
- embed 覆盖 base_url+transport，key 回落共享 → embed 三元组 = (共享 key, embed url, embed-transport/model)
- env per-tier 覆盖 yaml per-tier 覆盖 env 共享 覆盖 yaml 共享（五级优先级逐一验证）
- embed 全空 → embed 三元组 key="" （触发降级，不报错）
- require=True + strong 有 key + embed 空 → 不报错（strong 够）
- require=True + strong 也空 → ConfigError
- dashboard `settings_llm_test` 取 strong 三元组（test endpoint 与运行用同一 strong key/url）

---

### U3. Thread per-tier credentials through pipeline

**Goal:** pipeline 每 tier 用各自 key/url/model，embed 用 embed 三元组。

**Requirements:** R1, R5

**Dependencies:** U2

**Files:**
- `src/transmutary/pipeline.py`
- `tests/test_pipeline.py`

**Approach:**
- 替换 `_llm_api_key`/`_llm_base_url`/`_llm_model`（返回共享单值）为按 tier 取三元组的 helper（如 `_tier_creds(rt, "strong") -> (key,url,model)`）。
- diagnose/filter 调用用 strong 三元组；security/explain 用 cheap；`_embed_fn` 用 embed 三元组（**关键修复点**：当前 line ~230 取共享，改取 embed）。
- `_embed_fn` embed key 为空时 → 让 `llm.embed` 自然抛 LLMError（现有降级路径，不新增逻辑）。

**Test scenarios:**
- strong/cheap 同 provider、embed 不同 provider → 各 call 收到正确 key/url（mock call_fn/embed_fn 断言收到的 kwargs）
- embed 全空 → filter/explain 降级全 L3（embed_fn 抛错被吞，tick 不崩，结果仍出）
- 全共享配置 → 行为等价现状（回归）
- 现有 `test_ticks_thread_llm_api_key_from_creds` 适配新 helper（注意本地 env 干扰，CI 干净）

---

### U4. Dashboard per-tier override UX (collapsible)

**Goal:** 默认单 provider 表单不变，per-tier 覆盖藏折叠区。

**Requirements:** R4, R6

**Dependencies:** U1

**Files:**
- `src/transmutary/dashboard/templates/settings.html`
- `src/transmutary/dashboard/app.py` — `settings_llm` POST 读 per-tier 字段
- `src/transmutary/dashboard/i18n.py` — 折叠区标签双语
- `src/transmutary/dashboard/static/dashboard.css` — 折叠区样式（类驱动）
- `tests/test_dashboard.py`

**Approach:**
- settings.html LLM 面板底部加 `<details class="llm-advanced"><summary>Per-tier overrides (advanced)</summary>` 区，内含 strong/cheap/embed 各一组 key(password)/base_url/transport/model 输入，name 如 `embed_api_key`/`embed_base_url`/...。占位文案「留空 = 用上面共享配置」。
- `settings_llm` POST：读 per-tier 字段，非空才组 tiers 块传 `save_llm_config`。掩码回显（per-tier key 同 last-4 规则）。
- **`_secret_env` 运行态状态表不扩展**（grill 核实）：共享 LLM key 状态仍是主指标，per-tier override key 不逐个列入 secret 状态表（避免噪音；高级用户的折叠区自己看掩码回显）。降范围——secret_env 逻辑不动。
- `<details>` 原生折叠，无 JS（CSP 合规）。
- i18n：`settings_llm_advanced`、`settings_llm_tier_strong/cheap/embed`、`settings_llm_override_hint` 等。

**Test scenarios:**
- POST 仅共享字段（per-tier 空）→ 存扁平 yaml，无 tiers 块
- POST embed override（embed_base_url + embed_transport 填）→ yaml 含 tiers.embed
- per-tier key 掩码回显（不明文回吐）
- 渲染含 `<details>` 折叠区 + 双语标签
- CSP：无 inline style/script

---

### U5. CLI wizard per-tier + env vars + docs

**Goal:** CLI 可选 per-tier，env per-tier 覆盖，中英文档。

**Requirements:** R3, R7, R8

**Dependencies:** U1, U2

**Files:**
- `src/transmutary/cli.py` — config 向导
- `.env.example` — per-tier env 注释
- `README.md`、`README.zh-CN.md` — per-tier 模型说明 + embed 可选降级
- `tests/test_cli.py`

**Approach:**
- CLI：共享组问完后，问「配置 per-tier 覆盖？[y/N]」，y 才逐 tier 问（默认 N，保持轻量 R7）。
- `.env.example`：加 `TRANSMUTARY_LLM_EMBED_API_KEY` 等注释（说明 per-tier > 共享）。
- README（EN+zh）：Credential security model 段附近加「Per-tier models」小节——说明三 tier 用途、跨 provider 配法、embed 可选（不配 → 全 L3 降级，正确但更贵）。
- effective_config 已在 U2 处理 env 解析；本单元只补 `.env.example` 文档 + CLI。

**Test scenarios:**
- CLI 共享组 + 拒绝 per-tier（N）→ 存扁平
- CLI 接受 per-tier → 问 embed 覆盖，存 tiers 块
- env `TRANSMUTARY_LLM_EMBED_BASE_URL` 覆盖共享（U2 已测解析，此处验 CLI 显示既有 per-tier 配置）
- README 渲染无断链（手验）

---

## Scope Boundaries

### In scope
- per-tier 完整覆盖（key/url/transport/model）+ 逐字段回落
- 扁平/env 向后兼容
- dashboard 折叠 UX、CLI 可选向导、env per-tier、中英文档
- embed 可选 + 降级文档化

### Out of scope
- 改 tier→用途映射（strong/cheap/embed 各自调用点不动）
- 新增第 4 个 tier
- embed 降级逻辑本身（KTD-F 已存在，不改）
- OS keychain / vault（上轮已结论：headless 不适用）

### Deferred to Follow-Up Work
- per-tier 独立 L3 预算上限（当前单一 `TRANSMUTARY_L3_DAILY_BUDGET_USD`）
- dashboard「Test Connection」扩展到逐 tier 测（当前只测 strong）

---

## System-Wide Impact

- **破坏性内部签名**：`effective_llm_config` 返回类型变（单 key/url → per-tier dict）。调用方仅 pipeline 内部（U3 同步改）+ dashboard test endpoint（settings_llm_test 取 strong tier）。无外部 consumer。
- **安全**：per-tier key 数量增加 → 掩码/0600/env 规则需覆盖全部 per-tier key（U1/U4 test 覆盖）。
- **向后兼容**：现网扁平 yaml + 现有 env 必须零改动工作（R2/R3，U2 回归 test 守护）。
- **配置复杂度**：默认路径不变（折叠区不展开 = 现状）；复杂度只对主动 opt-in per-tier 的用户可见。
