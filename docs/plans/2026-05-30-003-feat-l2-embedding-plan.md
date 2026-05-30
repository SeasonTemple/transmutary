# feat: L2 embedding rerank — 漏斗中层（去重聚类）

**类型:** feat · **深度:** Standard · **日期:** 2026-05-30
**Origin:** brainstorm「L2 语义层」R8/R9 · CONTEXT「漏斗」· KTD6（MVP 延后，现补）
**前序:** MVP Phase 0-3 + F4 + 部署（v0.4.0），两模式 live 验证

---

## Context

CONTEXT 漏斗定义为 `L1 规则 → L2 轻量 embedding rerank → L3 LLM-as-judge`，但 MVP（KTD6）砍了 L2，当前 `filter.py` L1 后直接 L3、`explain.py` artifact-diff 后直接 batch LLM。L2 缺位导致：高 issue 浪涌时大量近似重复 issue 各自进 L3 judge（成本浪费）；模式 B 候选不按相关性收窄。本批补 L2 中层。

**安全约束（决定形态）**：origin「零漏报优先」（安全/故障类）——L2 **不得**把真故障在 L3 前丢掉。故 L2 取**去重聚类**语义（非 top-K 收窄），且对权威信号不施加。

## 决策（已确认）

**KTD-A — L2 = 去重聚类（zero-miss 安全）。** 模式 A：L1 命中的 issue 按 embedding cosine ≥ 阈值聚成簇，L3 只 judge 每簇代表（非每条）——减 L3 调用但**不丢「不同」信号**（对齐 R8 去重）。模式 B：候选按 cosine 聚类去近似重复后再 explain。**非 top-K 收窄**（那会丢低相似真信号）。

**KTD-B — embed 失败降级透传。** embedding 不可达/报错 → L2 跳过，全部候选进 L3/explain（即当前行为）。L2 是优化非门控，基础设施故障绝不致漏报。

**KTD-C — 范围仅 issue-surge + trend。** 供应链公告（security tick）+ release 是**权威一手信号**，**绕过 L2**（零漏报，不被 embedding 环节延误/误降）。

**KTD-D — llm.embed() 单一入口。** `llm.py` 加 `embed(texts, *, api_key, base_url, model, model_tier=EMBED)` 经 litellm.embedding，复用 creds/base_url，返回向量。**无注入栅栏**（embedding 是向量化非指令执行，但仍视 data 为 data）。budget：embedding 廉价，不占 L3 judge 预算（独立或不计）。新 `ModelTier.EMBED` + 默认模型别名。

**KTD-E — 阈值走模块常量。** cosine 聚类阈值 `L2_CLUSTER_THRESHOLD`（如 0.85）模块常量可参数覆盖，不动 config（沿用既往姿态）。

**KTD-F — live provider 门控。** MiniMax `/anthropic` 端点可能无 embedding API；L2 live 需 embedding-capable provider（复用 LLM creds 或独立 env）。**单测全 mock embed_fn**；live 缺 embedding provider → 降级透传（KTD-B），不阻断。

---

## High-Level Technical Design

```mermaid
flowchart LR
  subgraph 模式A 漏斗
    L1A[l1_matches 规则] --> RG[rate gate]
    RG --> L2A[L2 去重聚类<br/>cosine≥阈]
    L2A -->|每簇代表| L3[_judge L3]
    L2A -.embed失败.->|全量透传| L3
  end
  subgraph 模式B 漏斗
    SCOPE[filter_scope topic/kw] --> DIFF[artifact diff]
    DIFF --> L2B[L2 去重聚类] --> EXP[explain_trends batch]
    L2B -.embed失败.->|全量透传| EXP
  end
  L2A & L2B -.->|向量| EMB[llm.embed litellm.embedding]
  SUP[供应链/release] -.绕过 L2.-> DIRECT[直 L3/直投]
```

## Implementation Units

### U1. llm.embed() 嵌入入口
- **Goal:** 全项目唯一 embedding 入口，复用 creds/base_url。
- **Requirements:** R9, KTD-D, KTD-F。
- **Files:** `src/transmutary/llm.py`、`tests/test_llm.py`。
- **Approach:** `ModelTier.EMBED` + `DEFAULT_TIER_MODELS` 加别名（如 `text-embedding-3-small`）。`embed(texts: list[str], *, api_key=None, base_url=None, model=None, model_tier=ModelTier.EMBED) -> list[list[float]]` 经 `litellm.embedding`，api_key/base_url 透传，异常归一为 `LLMError`（caller 据此降级）。不走 L3 budget。
- **Patterns to follow:** `call()` 的 kwargs 透传 + 异常归一（`llm.py`）。
- **Test scenarios:**
  - mock litellm.embedding → 返回向量列表，形状正确。
  - api_key/base_url/model override 透传（断言传参）。
  - embedding 报错 → 抛 LLMError。
  - 空输入 → 空列表（不调 provider）。
- **Verification:** llm 测试过。

### U2. 聚类工具（确定性 cosine 去重）
- **Goal:** 给定文本 + embed_fn，聚近似重复、返回每簇代表索引。
- **Requirements:** R8, KTD-A, KTD-E。
- **Files:** `src/transmutary/rerank.py`（新建）、`tests/test_rerank.py`（新建）。
- **Approach:** `cluster_representatives(texts, *, embed_fn, threshold=L2_CLUSTER_THRESHOLD) -> list[int]`：embed 全部 → 贪心聚类（cosine ≥ 阈归入已有簇，否则新簇），返回每簇首个（确定性：按输入序）。`embed_fn` 抛错 → 传播由 caller 降级。纯 numpy-free（手算 cosine，避免新依赖）或评估加 numpy。常量 `L2_CLUSTER_THRESHOLD=0.85`。
- **Test scenarios:**
  - 3 近似文本(高 cosine) + 1 不同 → 2 簇代表。
  - 全不同 → 全保留。
  - 确定性：同输入同输出、代表按序。
  - 单文本/空 → 原样。
  - embed_fn 抛错 → 传播（caller 测降级）。
- **Verification:** rerank 测试过。

### U3. 模式 A 接 L2（issue-surge）
- **Goal:** L1 命中 + rate gate 后、L3 judge 前插去重聚类，L3 只判代表。
- **Requirements:** R9, F1, KTD-A, KTD-B, KTD-C。
- **Files:** `src/transmutary/filter.py`、`tests/test_filter.py`。
- **Approach:** `filter_issue_surge` 加 `embed_fn=None` 参数（None → 跳过 L2，当前行为）。rate gate 通过后，若 embed_fn 给：`cluster_representatives([o.text...], embed_fn=...)` 取代表子集喂 `_judge`；embed 抛错 → 捕获、全量进 L3（KTD-B，记 note）。matched_count/reason 反映 L2 收窄。**security/release 路径不经此**（KTD-C，本就不在 filter_issue_surge）。
- **Test scenarios:**
  - Covers R9. 10 条含 6 近似 → L2 聚成 N 簇、_judge 收到代表数 < 10。
  - 触发判定不变：簇代表 judge 确认故障 → triggered（AE1 保持）。
  - **零漏报**：embed_fn 抛错 → 全量进 L3、不丢 issue（降级 note）。
  - 不同故障(低 cosine) 不被聚并 → 各自仍进 L3。
  - embed_fn=None → 行为同现状（向后兼容）。
- **Verification:** 场景过；既有 filter 测试零回归。

### U4. 模式 B 接 L2（trend）
- **Goal:** artifact-diff 后、batch explain 前插去重聚类。
- **Requirements:** R8, F2, KTD-A, KTD-B。
- **Files:** `src/transmutary/report/explain.py`、`tests/report/test_explain.py`。
- **Approach:** `explain_trends` 加 `embed_fn=None`。`_diff_candidates` 后若 embed_fn 给：对 fresh 候选 `cluster_representatives([cand desc/repo...])` 取代表再 clean+explain；embed 抛错 → 全量（KTD-B）。skipped/reaccelerated 语义不变。
- **Test scenarios:**
  - 近似候选聚并 → explain 批量含代表、近似重复不重复出。
  - embed_fn 抛错 → 全量 explain（降级）。
  - embed_fn=None → 现状行为。
  - AE2 不破：新/重加速仍进摘要。
- **Verification:** 场景过；既有 explain 测试零回归。

### U5. 接线 + 文档
- **Goal:** pipeline ticks 注入 embed_fn；README/CONTEXT 体现 L2 启用。
- **Requirements:** KTD-C, KTD-F。
- **Files:** `src/transmutary/pipeline.py`、`README.md`、`README.zh-CN.md`、`CONTEXT.md`、`tests/test_pipeline.py`。
- **Approach:** `run_release_issue_tick`/`run_trend_tick` 把 `llm.embed`（或注入的 embed_fn）透传给 filter/explain；embed 不可用时降级（KTD-B/F）。README「工作原理」漏斗补 L2、「产物与存储」无变化；CONTEXT 漏斗词条标 L2 已实现（去重聚类，issue-surge+trend，权威信号绕过）。
- **Test scenarios:**
  - tick 传 embed_fn → filter/explain 收到（mock 断言）。
  - embed 不可用 → tick 不崩、降级透传。
- **Verification:** pipeline 测试过；零回归。

---

## Scope Boundaries
**In scope:** llm.embed、聚类工具、模式A/B 接 L2（去重聚类）、降级透传、文档。

### Deferred to Follow-Up Work
- L2 相关性 top-K 收窄（zero-miss 风险，暂只去重聚类）。
- chunk 级语义清洗（CONTEXT 另列）。
- embedding 缓存/向量持久化（重复 embed 成本优化）。
- 独立 embedding provider 配置（当前复用 LLM creds，live 缺则降级）。

---

## Risks & Dependencies
| 风险 | 缓解 |
|---|---|
| L2 聚类丢真故障（违零漏报） | 只去重「近似重复」、阈值保守(0.85)、不同信号不聚并；embed 失败全量透传(KTD-B)；测试断言不同故障不被并 |
| 权威信号被 L2 延误/误降 | 供应链/release 绕过 L2(KTD-C) |
| MiniMax 无 embedding API | live 降级透传(KTD-F)；单测 mock；文档标 provider 门控 |
| 新依赖(numpy) | 优先手算 cosine 免依赖；必要才加 |
| 改 filter/explain 破既有测试 | embed_fn=None 默认=现状行为；既有测试零回归 |

## Verification
1. `.venv/bin/python -m pytest -q` 全绿（含新 rerank/embed 测试），Phase 0-3+F4 零回归。
2. `.venv/bin/ruff check src tests` clean。
3. embed_fn=None 路径行为与现状一致（向后兼容断言）。
4.（可选 live）配 embedding provider → 跑 issue-surge 确认 L3 调用数 < 候选数。

## Execution
经 workflow：build(U1-U5) → 对抗审查（聚类零漏报正确性/不同信号不被并、降级透传、权威信号绕过、embed 入口、向后兼容 embed_fn=None、文档）→ 修复到绿。
