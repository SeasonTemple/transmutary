# feat: critique→refine 报告增强（对抗式自查提质）

**类型:** feat · **深度:** Standard · **日期:** 2026-05-31
**Origin:** brainstorm R11（`综合→批判→修订` 三段式，列为 MVP 后增强，待单次综合质量实测不足再启）
**前序:** MVP Phase 0-3 + F4 + 部署 + L2（v0.5.1），全套测试绿且快

---

## Context

origin R11 把诊断/说明报告的 `综合→批判(critique)→修订(refine)` 三段式列为 MVP 后增强：先 LLM 出初稿（综合），再让 LLM **批判自己的草稿**（找未据证断言/遗漏/逻辑洞），据批判**修订**一版。现状 `report/diagnose.py`、`report/explain.py` 都是单次 `call_fn` 出报告。本批加**可选** critique-refine pass，默认关（合 R11「待实测不足再启」姿态），由调用方显式开。

**核心安全设计点**：`diagnose` 当前流程是 `初稿 → cross_validate(OSV/GHSA 交叉校验) → sanitize_security_verdicts → R18 gate`。critique-refine **必须插在初稿之后、交叉校验之前**——否则 refine 引入的新断言会绕过安全裁决。三段产出的「修订稿」与初稿一样要过完整的 cross_validate + sanitize + R18，绝不豁免。

---

## 决策（已确认）

**KTD-A — 显式开关，默认关。** `diagnose`/`explain_trends` 加 `refine: bool = False`。调用方（pipeline tick）显式传 `refine=True` 才启。零意外成本、可控、好测，合 R11。pipeline tick 暴露 `refine_reports` 参数透传（默认 False）。

**KTD-B — critique-refine 经 llm.py 单入口，数据/指令分槽不破（KTD3）。** critique 与 refine 都是新 `call_fn` 调用：critique 的 system=「批判指令」、data=「初稿 + 原始证据」；refine 的 system=「修订指令」、data=「初稿 + 批判 + 原始证据」。**初稿/批判文本进 data 槽**（它们含 LLM 生成内容，但仍按 data 处理，注入隔离一致）。复用既有 `call_fn` 缝，测试可 mock。

**KTD-C — 修订稿不豁免安全管控（zero-trust 关键）。** refine 产出的 `diagnosis_text` **替换初稿后，照样过** `cross_validate_security_full` + `sanitize_security_verdicts` + `evaluate_source_gate`。安全裁决/R18 门控对修订稿与初稿一视同仁——refine 只在「初稿生成」这一步之前注入，不改变其后的安全管线。

**KTD-D — refine 失败降级到初稿（不阻断）。** critique 或 refine 的 LLM 调用抛 `LLMError` → 捕获、回退用初稿继续（记 note），不让报告生成失败。三段式是质量增强非门控，基础设施故障不致报告产不出。

**KTD-E — 模式 B 同构但走廉价档。** `explain_trends` 的 critique-refine 用 `ModelTier.CHEAP`（与其初稿同档，R11 模式 B 是廉价批量）；`diagnose` 用 `ModelTier.STRONG`。

---

## High-Level Technical Design

```mermaid
flowchart TD
  D[data_block 证据] --> S1[综合: call_fn 出初稿 draft]
  S1 -->|refine=False| OUT[draft 作终稿]
  S1 -->|refine=True| C[批判: call_fn(draft+证据) → critique]
  C --> R[修订: call_fn(draft+critique+证据) → revised]
  R --> OUT
  C -.LLMError.-> OUT
  R -.LLMError.-> OUT
  OUT --> XV[cross_validate OSV/GHSA + sanitize + R18 gate]
  XV --> REP[Report]
```

终稿（draft 或 revised）**统一**进安全管线 XV——refine 不绕过任何裁决。

---

## Implementation Units

### U1. critique-refine 核心 helper
- **Goal:** 一个可复用函数：给定初稿 + 证据 + call_fn + tier，跑 critique→refine 返回修订稿；失败降级初稿。
- **Requirements:** R11, KTD-B, KTD-D。
- **Files:** `src/transmutary/report/refine.py`（新建）、`tests/report/test_refine.py`（新建）。
- **Approach:** `critique_refine(draft, evidence, *, call_fn, tier, api_key, base_url) -> tuple[str, list[str]]` 返回 `(final_text, notes)`。两次 call_fn：critique（system=批判指令，data=`draft + evidence`）、refine（system=修订指令，data=`draft + critique + evidence`）。任一抛 LLMError → 返回 `(draft, [note])` 降级。**system 是可信指令、data 是不可信内容**（含 draft/critique，按 data 处理，KTD3）。critique 指令明确：找未据证断言、遗漏、逻辑漏洞，不要新增证据外的事实。
- **Patterns to follow:** `diagnose` 的 call_fn 用法（`report/diagnose.py:358`）；`llm.call` 的 system/data/tier 签名。
- **Test scenarios:**
  - happy: mock call_fn 两次（critique 返批判、refine 返修订）→ 返回修订稿。
  - 降级: critique call_fn 抛 LLMError → 返回初稿 + note。
  - 降级: refine call_fn 抛 LLMError → 返回初稿 + note。
  - KTD3: 注入串在 draft/evidence 里 → 只进 data 槽、不进 system（断言 mock 收到的 system 不含注入）。
  - 全 mock call_fn，无真实网络。
- **Verification:** refine 测试过。

### U2. diagnose 接 critique-refine
- **Goal:** `diagnose` 加 `refine=False`；为真时初稿后跑 critique_refine，修订稿照过安全管线。
- **Requirements:** R11, F1, KTD-A, KTD-C, KTD-D。
- **Files:** `src/transmutary/report/diagnose.py`、`tests/report/test_diagnose.py`。
- **Approach:** 签名加 `refine: bool = False`。`diagnosis_text = call_fn(...)` 出初稿后，若 `refine`：`diagnosis_text, notes = critique_refine(diagnosis_text, data_block, call_fn=call_fn, tier=STRONG, ...)`。**关键：此替换在 `cross_validate_security_full` / `sanitize_security_verdicts` / `evaluate_source_gate` 之前**——修订稿照过全部安全裁决（KTD-C）。notes 进 DiagnoseOutcome（审计）。
- **Test scenarios:**
  - refine=False → 行为同现状（向后兼容，既有 diagnose 测试零回归）。
  - refine=True → critique_refine 被调、修订稿进报告。
  - **KTD-C 关键**：refine=True 且修订稿含未据证安全断言（无 OSV/GHSA backing）→ 仍被 sanitize_security_verdicts 删除（断言修订稿不绕过裁决）。
  - **KTD-C**：refine=True 且修订稿派生源 <2 → 仍被 R18 降「待核实信号」。
  - 降级: refine 阶段 LLMError → 用初稿出报告、不崩。
  - 全 mock call_fn。
- **Verification:** 场景过；既有 diagnose 测试零回归。

### U3. explain_trends 接 critique-refine
- **Goal:** `explain_trends` 加 `refine=False`；为真时批量摘要后跑 critique_refine（廉价档）。
- **Requirements:** R11, F2, KTD-A, KTD-D, KTD-E。
- **Files:** `src/transmutary/report/explain.py`、`tests/report/test_explain.py`。
- **Approach:** 签名加 `refine: bool = False`。批量摘要初稿后若 refine：对摘要文本跑 critique_refine（tier=CHEAP）。R18 门控（既有）不变。降级同 KTD-D。
- **Test scenarios:**
  - refine=False → 现状行为（既有 explain 测试零回归）。
  - refine=True → critique_refine 被调（CHEAP 档）、修订摘要进报告。
  - 降级: refine LLMError → 用初稿摘要。
  - 全 mock call_fn。
- **Verification:** 场景过；既有 explain 测试零回归。

### U4. pipeline 透传 refine 开关 + 文档
- **Goal:** tick 暴露 `refine_reports=False` 透传给 diagnose/explain；README/CONTEXT 记三段式。
- **Requirements:** R11, KTD-A。
- **Files:** `src/transmutary/pipeline.py`、`README.md`、`README.zh-CN.md`、`CONTEXT.md`、`tests/test_pipeline.py`。
- **Approach:** `run_release_issue_tick`/`run_trend_tick` 加 `refine_reports=False`，透传给 diagnose/explain 的 `refine`。README「工作原理」补三段式（可选、默认关）；CONTEXT 加「批判-修订」词条（或更新报告词条）。
- **Test scenarios:**
  - tick refine_reports=True → diagnose/explain 收到 refine=True（mock 断言）。
  - 默认 False → 不触发 critique-refine（向后兼容）。
  - 全 mock。
- **Verification:** pipeline 测试过；零回归。

---

## Scope Boundaries
**In scope:** critique-refine helper、diagnose/explain 接入（默认关）、pipeline 透传、文档。修订稿照过安全管线。

### Deferred to Follow-Up Work
- 多轮 critique-refine（>1 轮迭代）——本批单轮。
- 按 severity 自动触发——本批显式开关，自动策略延后。
- critique 质量度量/A-B（实测三段式提质多少）。
- refine 的独立成本预算（当前共用 L3 budget）。

---

## Risks & Dependencies
| 风险 | 缓解 |
|---|---|
| refine 引入新断言绕过安全裁决 | 修订稿照过 cross_validate+sanitize+R18(KTD-C)；专门断言测试 |
| critique/refine 把注入串当指令 | system/data 分槽，draft/critique 进 data 槽(KTD3)；注入测试 |
| refine 失败致报告产不出 | LLMError 降级初稿(KTD-D)；降级测试 |
| 成本倍增 | 默认关(KTD-A)；显式开才付费 |
| 改 diagnose/explain 破既有测试 | refine=False 默认=现状；既有测试零回归 |
| 测试触真实网络(刚踩的坑) | 全 mock call_fn；conftest 已 stub embed；新测试不触网 |

## Verification
1. `.venv/bin/python -m pytest -q` 全绿（含新 refine 测试），Phase 0-3+F4+L2 零回归；**总耗时 <60s**（不触网）。
2. `.venv/bin/ruff check src tests` clean。
3. refine=False 路径与现状一致（向后兼容断言）。
4. KTD-C 安全断言：refine=True 下修订稿仍过 OSV/GHSA 交叉校验 + R18 门控。

## Execution
经 workflow：build(U1-U4) → 对抗审查（修订稿不绕安全裁决、注入分槽、降级、向后兼容 refine=False、测试不触网）→ 修复到绿。批准后落盘 `docs/plans/2026-05-31-001-feat-critique-refine-plan.md`。
