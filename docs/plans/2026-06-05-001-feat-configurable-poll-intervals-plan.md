# Plan: WebUI 可配置 per-repo 轮询间隔（预设档位 + 自定义 + 热更新）

**Type:** feat
**Depth:** Standard
**Date:** 2026-06-05

---

## Context

per-repo 抓取节奏当前是 `service.py` 的模块常量 —— `SECURITY_INTERVAL_SECONDS=300`、`RELEASE_ISSUE_INTERVAL_SECONDS=600`、`RECONCILE_INTERVAL_SECONDS=600`。注释标注 **KTD-B：分层节奏是代码常量，NOT 配置 schema**。WebUI 投递设置只暴露 `digest_hour`。用户要从 WebUI 调每仓抓取频率。

这**反转 KTD-B 的「间隔不可配」决定**。正当化：KTD-B 初衷是「避免把运维节奏暴露成易错的配置」，但现实是用户需要按自己的 GitHub 配额 / LLM 成本 / 关注强度自调节奏；且本系统已有成熟的三级配置先例（`email_lang`、`digest_hour`：admin-pref SQLite > yaml > 默认）和免重启热更新机制（promote 经 reconcile 拾取）。把间隔纳入同一套配置+热更新轨道，风险可控。计划须更新 `service.py` 中 KTD-B 的注释表述，从「常量非配置」改为「常量是默认，可经 effective_delivery 覆盖」。

**目标**：WebUI 投递设置增加「抓取频率」——预设档位（实时/均衡/宽松）下拉 + 「高级」展开填精确分钟（security / release-issue 各一），带边界（60s–24h）+ 过激档位警告。改动经 reconcile 热更新生效，无需重启 serve。

**用户已定决策**：UX = 预设档位 + 高级自定义分钟；生效 = 热更新（reconcile reschedule）。

---

## Requirements

- R1：`security_interval` / `release_issue_interval` 可经 WebUI 投递设置配置；沿用 `digest_hour` 三级解析（admin-pref > yaml > 默认常量）。
- R2：UX = 预设档位下拉（实时/均衡/宽松，映射到具体秒值）+ 「高级」展开按 security/release-issue 各填精确分钟。不暴露裸秒。
- R3：边界校验 —— **硬下限 120s（2min）**，上限 86400s（24h）；clamp 到 [120, 86400]。不允许 <2min 配置（既然不推荐就不放行，下限即护栏，无软警告）。校验双层：dashboard POST + `effective_config` normalize（参考 `email_lang` 的 `normalize_*` 模式）。UI 文案说明 2min 下限的理由（GitHub 限流 + LLM 成本）。
- R4：改间隔**热更新**生效 —— 扩展 reconcile job：每次重对账时按当前 effective 间隔 reschedule 现有 security/release-issue job（apscheduler `replace_existing=True` 重注册）。无需重启。
- R5：`reconcile_interval` 不单独暴露 UI（保持默认常量；档位只驱动 security/release-issue）。
- R6：默认（未配置）行为不变 —— security 300s / release-issue 600s（现有常量作为 fallback）。
- R7：双语 i18n（档位名、字段标签、警告文案）；不引入新依赖；不加 env 覆盖（与 `digest_hour` parity —— 它也只 admin+yaml）。

---

## Key Technical Decisions

**KTD1：数据模型 = 两个 interval 秒值，预设是 UI 糖。** `Delivery` 加 `security_interval_seconds` / `release_issue_interval_seconds`（int）。预设档位**不入库**——它只是 UI 下拉，选档填充两个字段；「高级」让用户改具体分钟。服务端只认两个秒值。预设→秒映射放核心常量表（如 `i18n` 或 service 旁），供 UI 与「当前生效值反推档位名」共用。预设值须全 ≥ 下限 120s：**实时=120s/120s（=下限）、均衡=300s/600s（默认）、宽松=1800s/1800s**。
- 依据：避免存「档位枚举 + 覆盖值」的双态歧义；两个秒值是唯一真相，preset 纯展示层。

**KTD2：三级解析 + 双层 normalize，复刻 `email_lang`。** `config.normalize_interval(value, default)`：非数字/越界 → clamp 到 **[120, 86400]**，缺失 → 默认。下限常量 `MIN_POLL_INTERVAL_SECONDS=120` / 上限 `MAX_POLL_INTERVAL_SECONDS=86400` 单一来源。`parse_delivery` 用它。`effective_delivery` 解析 `admin-pref > yaml > 默认常量`（同 `digest_hour`/`email_lang` 现有结构）。`store.AdminDeliveryPreferences` 加两个 int 字段 + set/get（同 `digest_hour` 键值行）。dashboard POST 也 normalize（拒越界、回 clamp 值 + 警告）。

**KTD3：热更新经 reconcile **diff-based** reschedule（防饿死）。** `register_pipeline_jobs` / `register_repo_jobs` 的 `security_interval` / `release_issue_interval` 形参改为从 `effective_delivery(settings, store)` 取（非模块常量默认）。`reconcile_repo_jobs` 扩展：除现有「注册新 repo / 删旧 repo」，对 **desired∩registered（已存在的 repo）逐个比对**当前 job 的 `trigger.interval.total_seconds()` 与 effective 间隔，**仅在不等时** `scheduler.reschedule_job(job_id, trigger="interval", seconds=N)`。
- **关键（grill 发现 + 已验证）**：**禁止**无条件 `add_job(replace_existing=True)` 重注册全部——apscheduler 3.11.2 重注册会重算 `next_run_time`，reconcile(600s) 每轮重置长间隔 job 倒计时 → 宽松档(1800s) **永不触发**（饿死）。diff-based reschedule 稳态 no-op（间隔没变就不动），只在用户真改了间隔时 reschedule 一次。`reschedule_job` API 已确认存在（3.11.2）。
- 新 repo 注册路径（reconcile 内 `register_repo_jobs`）也须传 effective 间隔，否则新 repo 用默认而已存 repo 用配置值，不一致。
- 改间隔后最迟下个 reconcile 周期（默认 10min）生效；reconcile 自身间隔不变。UI 文案说明非即时。

**KTD4：service.py KTD-B 注释更新。** 把「Real tiered cadences (KTD-B — module constants, NOT config schema)」改为「module constants are the DEFAULTS; overridable via effective_delivery (admin-pref > yaml)」。常量保留为 fallback。

---

## Implementation Units

### U1. config 层：Delivery 间隔字段 + normalize
**Goal:** Delivery 承载两个可配置间隔，带边界 normalize 与默认 fallback。
**Requirements:** R1, R3, R6
**Files:** `src/transmutary/config.py`、`tests/test_config.py`
**Approach:** KTD1/KTD2。`Delivery` 加 `security_interval_seconds: int = 300` / `release_issue_interval_seconds: int = 600`（默认 = 现有常量值，或从 service 常量 import 避免漂移）。新增 `MIN_POLL_INTERVAL_SECONDS=120` / `MAX_POLL_INTERVAL_SECONDS=86400` + `normalize_interval(value, *, default) -> int`：非 int/越界 clamp [120, 86400]，None/空 → default。`parse_delivery` 用之解析 yaml 键 `security_interval_seconds` / `release_issue_interval_seconds`。
**Test scenarios:**
- yaml 给合法秒值 → 解析原值；给 90（<120）→ clamp **120**；给 30 → clamp 120；给 100000（>86400）→ clamp 86400；非数字 → default；缺失 → default。
- `parse_delivery` 默认（无键）→ 300/600。

### U2. state 持久化间隔 admin pref
**Goal:** dashboard 写入的间隔持久化到 SQLite admin pref。
**Requirements:** R1
**Dependencies:** U1
**Files:** `src/transmutary/store/state.py`、`tests/store/test_state.py`
**Approach:** `AdminDeliveryPreferences` 加 `security_interval_seconds: int | None` / `release_issue_interval_seconds: int | None`。`set_admin_delivery_preferences` 加两参（写键值行，加入 DELETE-then-INSERT 键集，同 `email_lang` 模式）。`get_admin_delivery_preferences` 读出。
**Test scenarios:**
- set→get round-trip 两间隔；partial set 行为与现有 recipients/digest_hour/email_lang 一致（特征化）。
- 未设 → None。

### U3. effective_delivery 解析间隔
**Goal:** 运行态间隔 = admin-pref > yaml > 默认，clamp 安全。
**Requirements:** R1, R3
**Dependencies:** U1, U2
**Files:** `src/transmutary/effective_config.py`、`tests/test_effective_config.py`
**Approach:** `effective_delivery` 并入两间隔：`normalize_interval(prefs.X if not None else base.X, default=base.X)`。返回的 `Delivery` 带 effective 间隔。
**Test scenarios:**
- admin 覆盖 yaml；都无 → 默认；admin 越界值（如 60）→ clamp 120；store=None 早返回路径携带 yaml 间隔。

### U4. 调度器读 effective 间隔 + reconcile 热更新
**Goal:** 注册用 effective 间隔；改间隔经 reconcile reschedule 生效，无需重启。
**Requirements:** R4, R5, R6
**Dependencies:** U3
**Files:** `src/transmutary/service.py`、`tests/test_service.py`
**Approach:** KTD3/KTD4。`register_pipeline_jobs` 从 `effective_delivery` 取 security/release-issue 间隔（替默认形参）。`reconcile_repo_jobs`：新 repo 用 effective 间隔注册（`register_repo_jobs` 传 effective）；**已存在 repo 逐个 diff** `job.trigger.interval.total_seconds()` vs effective，**仅不等时** `reschedule_job(job_id, trigger="interval", seconds=N)`。reconcile_interval 仍用常量。更新 KTD-B 注释。
**Execution note:** 先写一个会饿死的 characterization 测试（间隔 > reconcile 间隔时，多轮 reconcile 后 job 仍能保持其 next_run 不被无限推后），确保 diff-based 实现通过它——锁死「禁止无条件重注册」这条不变量。
**Test scenarios:**
- `register_pipeline_jobs` 用 effective 间隔注册（注入带非默认间隔的 settings/store → 验 job trigger seconds = effective）。
- reconcile 检测间隔**变化** → 该 repo 的 security/release job `reschedule_job` 被调用且新 seconds 正确（mock scheduler）。
- 间隔**未变** → **不调** reschedule_job（diff no-op；防饿死的核心断言）。
- 长间隔（1800s）+ 多轮 reconcile（间隔未变）→ job 不被反复 reschedule（断言 reschedule_job 调用次数为 0）。
- 新 repo + 已存 repo 间隔变化同周期 → 新 repo 注册用 effective、已存 repo reschedule，各自正确。
- reschedule 目标 job 不存在（竞态：同周期被 demote）→ 容错不抛。

### U5. dashboard 抓取频率 UI（预设 + 高级 + 校验）
**Goal:** WebUI 投递设置可选档位 / 填精确分钟，带警告，写入 admin pref。
**Requirements:** R2, R3, R7
**Dependencies:** U2, U3
**Files:** `src/transmutary/dashboard/app.py`（`settings_delivery` POST）、`templates/settings.html`、`static/dashboard.js`（preset→字段 + 警告）、`static/dashboard.css`、`dashboard/i18n.py`、`tests/test_dashboard.py`
**Approach:** delivery 表单加「抓取频率」预设 `<select>`（实时/均衡/宽松）+ `<details>` 高级（security/release 各一分钟输入，`min=2` HTML 属性 + 预填当前 effective 值）。POST 解析：优先读高级分钟字段（×60→秒）；否则用预设映射秒值；`normalize_interval` clamp 到 [120, 86400]，越界回 clamp 值。JS：选档填充两高级字段；高级输入 `min="2"` 客户端阻止 <2min。i18n：档位名、`抓取频率`、字段标签、**2min 下限说明文案**（双语，常驻提示非软警告）。预设→秒映射 + 「当前秒值反推档位」共用核心表。
**Test scenarios:**
- POST 预设「宽松」→ 持久化对应秒值；GET 表单预选「宽松」。
- POST 高级 security=3min/release=5min → 持久化 180/300。
- POST 越界（security=1min/60s）→ **clamp 120 持久化、303 不 400**（服务端硬下限，不信任客户端 min 属性）。
- POST 预设「实时」→ 持久化 120/120（= 下限）。
- 当前生效值不匹配任何预设 → 表单预选「自定义/高级展开」。
- 渲染含双语 2min 下限说明 key。

### U6. 文档 + KTD-B 表述更新
**Goal:** README/配置示例反映可调间隔；KTD-B 注释正当化反转。
**Requirements:** R7
**Dependencies:** U1, U4
**Files:** `README.md`、`README.zh-CN.md`、`config/delivery.example.yaml`、（KTD-B 注释已在 U4 service.py 内改）
**Approach:** delivery.example.yaml 加注释化 `security_interval_seconds` / `release_issue_interval_seconds` 示例。README 投递/配置段补「抓取频率可调（WebUI 或 yaml），改动经 reconcile 最多 ~10min 热更新生效」。
**Test scenarios:** `Test expectation: none -- 文档 + 示例 yaml；delivery.example.yaml 仍能被 parse_delivery 加载（U1 测试覆盖示例 yaml 回归）。`

---

## Scope Boundaries

**In scope:** security/release-issue 间隔 WebUI 可配（预设+高级）、三级解析+clamp、reconcile 热更新、双语 UI、文档 + KTD-B 注释更新。

**Out of scope:**
- `digest_hour` 改造（已可调）。
- env 覆盖间隔（与 `digest_hour` parity —— 它无 env；保持 admin+yaml）。
- `reconcile_interval` 暴露 UI（保持常量；改它影响热更新延迟语义，单列）。
- per-repo 不同间隔（本轮全局两间隔，非每仓独立）。

**Deferred to Follow-Up Work:**
- 若需更快生效：把 reconcile_interval 也调小 / 或 dashboard 写入后主动触发一次 reconcile（免等周期）。

---

## System-Wide Impact

- **运维**：用户可自调节奏；过激档位 = 真实 GitHub 限流 + LLM 成本风险 → **120s(2min) 硬下限 clamp** 是护栏（既然不推荐 <2min 就不放行，服务端强制）。
- **L3 预算**：更快 release-issue tick → 更多 diagnose → 更高 LLM 花费；与现有 `L3_DAILY_BUDGET` 日上限正交（预算门仍兜底）。
- **热更新延迟**：改间隔非即时，最多等一个 reconcile 周期（~10min）；UI 文案须说明，避免「改了没反应」误解。

---

## Verification

1. `python -m pytest tests/ -q`（移开本地真实 `config/` 避污染）全绿；`ruff check src tests tools` 通过。
2. 单测覆盖：config normalize/clamp、state round-trip、effective 三级、service reconcile reschedule、dashboard 预设/高级/越界/警告。
3. 手测热更新：起 serve（默认间隔）→ dashboard 改「实时」→ 等一个 reconcile 周期 → 日志显示 security/release job 以新 seconds 重注册（无需重启）。
4. 手测边界：填 1min（<2min）→ 服务端 clamp 120s（即便客户端 min 被绕过）；填 30min → 接受；预设「实时」→ 120s。
5. 掩码核查：无凭据泄漏（间隔是非密配置）。
