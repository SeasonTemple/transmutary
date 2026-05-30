# feat: L2 embedding rerank — 漏斗中层（去重聚类）

**类型:** feat · **深度:** Standard · **日期:** 2026-05-30
**Origin:** brainstorm「L2 语义层」R8/R9 · CONTEXT「漏斗」· KTD6（MVP 延后，现补）
**前序:** MVP Phase 0-3 + F4 + 部署（v0.4.0），两模式 live 验证

---

## Context

CONTEXT 漏斗定义为 `L1 规则 → L2 轻量 embedding rerank → L3 LLM-as-judge`，但 MVP（KTD6）砍了 L2，当前 `filter.py` L1 后直接 L3、`explain.py` artifact-diff 后直接 batch LLM。L2 缺位导致：高 issue 浪涌时大量近似重复 issue 各自进 L3 judge（成本浪费）；模式 B 候选不按相关性收窄。本批补 L2 中层。

**安全约束（决定形态）**：origin「零漏报优先」（安全/故障类）——L2 **不得**把真故障在 L3 前丢掉。故 L2 取**去重聚类**语义（非 top-K 收窄），且对权威信号不施加。

## 决策（已确认）

**KTD-A — L2 = 语义分组（semantic grouping，zero-miss 安全）。** 模式 A：L1 命中的 issue 按 embedding cosine 聚成组，**整组文本一次性喂 L3 judge**（非只喂代表）——减 L3 调用次数但**judge 看得到组内全部信号**，根除「代表掩盖簇内真故障」。模式 B：候选按 cosine 分组去近似重复后再 explain。**非 top-K 收窄**（那会丢低相似真信号）。术语用「语义分组/grouping」区别于 dedup.py 既有的 fingerprint「簇/cluster」。
- **聚类比较基准（P0，零漏报前提）：representative-linkage —— 新文本只与各组「代表」比 cosine，≥阈才入该组，否则新建组。禁用 single-linkage（与组内任一成员比），因其传递性会把 cos(A,C)<阈 的不同信号经 B 误并。**
- **代表掩盖根除（P0）：L3 judge 收「整组拼接文本」（同 explain.py 既有 batch 单调用同构），judge 看全组、只算一次调用 → 组内任何真故障都不会被噪声代表掩盖。**

**KTD-B — embed 失败降级透传。** embedding 不可达/报错 → L2 跳过，全部候选进 L3/explain（即当前行为）。L2 是优化非门控，基础设施故障绝不致漏报。

**KTD-C — 范围仅 issue-surge + trend。** 供应链公告（security tick）+ release 是**权威一手信号**，**绕过 L2**（零漏报，不被 embedding 环节延误/误降）。

**KTD-D — llm.embed() 单一入口。** `llm.py` 加 `embed(texts, *, api_key, base_url, model, model_tier=EMBED)` 经 litellm.embedding，复用 creds/base_url，返回向量。**无注入栅栏**（embedding 是向量化非指令执行，但仍视 data 为 data）。budget：embedding 廉价，不占 L3 judge 预算（独立或不计）。新 `ModelTier.EMBED` + 默认模型别名。

**KTD-E — 阈值走模块常量 + 方向性原则（P1）。** cosine 分组阈值 `L2_GROUP_THRESHOLD` 模块常量可参数覆盖，不动 config。**阈值未经模型校准前从严（0.90+），方向性原则「宁少合并勿误并」**——零漏报系统宁可多花 L3 也不误并真信号。U2 含一个校准子步骤（用已知应分开/应合并的故障文本对测该模型 cosine 分布，据此定阈并在常量注释写依据）。边界用严格 `>` 比较（边界抖动偏向不合并）。

**KTD-G — L2 与 L1 dedup 职责隔离（P2，防双重折叠）。** dedup.py 已有 fingerprint 簇 + evidence_count/escalation。L2 **不参与 fingerprint/evidence_count 计算、不回写 dedup 状态**，只在 L3 judge 前临时收窄送审组数；L2 输入是 L1 hash 去重后的存活 issue；L2 绝不改变 escalation 语义（防止 L2 误并压低 evidence_count 致漏过 escalation 阈值）。

**KTD-H — embedding 成本上限（P1，与 L3 硬帽姿态一致）。** 加模块常量 `L2_MAX_EMBED_ITEMS`：单次 tick 待 embed 条数超上限 → **不做 L2、全量透传进 L3**（KTD-B 降级语义，零漏报安全方向）。把浪涌 embedding 成本钳在常数内。`embed()` 完全不碰 L3 BudgetManager（保持 L3 budget 纯净）。

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
- **Requirements:** R9, KTD-D, KTD-F, KTD-H。
- **Files:** `src/transmutary/llm.py`、`tests/test_llm.py`。
- **Approach:** `ModelTier.EMBED` + `DEFAULT_TIER_MODELS` 加别名（如 `text-embedding-3-small`）。`embed(texts, *, api_key=None, base_url=None, model=None, model_tier=ModelTier.EMBED) -> list[list[float]]` 经 `litellm.embedding`。**`embed()` 是独立函数，不复用 `call()` 骨架**（embedding 无 system/data slot、无 fence）；**完全不碰 BudgetManager**（不调 `_enforce_budget`/`_record_cost`，保持 L3 budget 纯净，KTD-H）。api_key/base_url 透传，异常归一 `LLMError`。**部分失败语义（P2）：batch 中任一条失败即整体抛 `LLMError`**（caller 全量降级，不做部分聚类——部分聚类引入不确定漏并）。**超长文本（P2）：按模型上限保守截断后再 embed**（或注明截断=follow-up、MVP 靠 KTD-B 降级兜底）。
- **Patterns to follow:** `call()` 的 kwargs 透传 + 异常归一（`llm.py`）；但 budget/fence 不复用。
- **Test scenarios:**
  - mock litellm.embedding → 返回向量列表，形状正确。
  - api_key/base_url/model override 透传（断言传参）。
  - embedding 报错 → 抛 LLMError；batch 部分失败 → 整体抛 LLMError（不返回部分）。
  - 空输入 → 空列表（不调 provider）。
  - 不碰 L3 budget：embed 不影响 BudgetManager 状态（断言 L3 预算计数不变）。
- **Verification:** llm 测试过。

### U2. 语义分组工具（确定性 cosine，representative-linkage）
- **Goal:** 给定文本 + embed_fn，按语义分组、返回**组成员映射**（非仅代表）。
- **Requirements:** R8, KTD-A, KTD-E, KTD-G。
- **Files:** `src/transmutary/rerank.py`（新建）、`tests/test_rerank.py`（新建）。
- **Approach:** `group_semantic(texts, *, embed_fn, threshold=L2_GROUP_THRESHOLD) -> list[Group]`，`Group{representative_index: int, member_indices: list[int]}`（确定性按输入序）。**输出契约含组成员映射（P0），下游据此回填判定**。算法：embed 全部 → **representative-linkage**（新文本只与各组代表比 cosine，> 阈入组否则新组，KTD-A）。手算 cosine 免新依赖。**零/空向量、零范数（P2）：不参与分组、各自独立成组（绝不合并，安全方向）**。边界用严格 `>`（KTD-E 抖动偏不合并）。`embed_fn` 抛错 → 传播由 caller 降级。常量 `L2_GROUP_THRESHOLD`（校准前 0.90，KTD-E）。**校准子步骤**：附一组中英混合故障文本对（outage/crash/security 三桶，已知应分/应合）测模型 cosine 分布定阈、注释写依据。
- **Test scenarios:**
  - **链式反例（P0）**：A~B(0.94)、B~C(0.94)、A≁C(0.77) → 必产 ≥2 组、C 不被 A 误并（representative-linkage 验证）。
  - 阈值边界两侧：cos=0.851 入组 vs 0.849 不入（严格 `>` 0.85 测试或按定阈）。
  - 3 近似 + 1 不同 → 2 组，**断言组成员映射正确**（哪些 index 属哪组）。
  - 全不同 → 每条独立成组。
  - 零向量/空文本 → 各自独立成组，不被误并。
  - 确定性：同输入同输出、代表按序。
  - 单文本/空输入 → 原样。
  - embed_fn 抛错 → 传播（caller 测降级）。
  - **mock 向量指引（P2，U2/U3/U4 共用 fixture）**：二维单位向量按夹角构造 cosine=cos(θ)。固定集：`v0=[1,0]`、`v1=[cos5°,sin5°]`、`v2=[cos8°,sin8°]`（彼此≈0.99 同组）、`vx=[0,1]`（与 v0 cos=0 必不同组）；链式：0°/20°/40° → cos(A,B)=cos(B,C)=0.94、cos(A,C)=0.77。`fixed_embed_fn(text)->vec` 字典式 mock。
- **Verification:** rerank 测试过。

### U3. 模式 A 接 L2（issue-surge）
- **Goal:** L1 命中 + rate gate 后、L3 judge 前插去重聚类，L3 只判代表。
- **Requirements:** R9, F1, KTD-A, KTD-B, KTD-C。
- **Files:** `src/transmutary/filter.py`、`tests/test_filter.py`。
- **Approach:** `filter_issue_surge` 加 `embed_fn=None`（None → 跳过 L2，当前行为）。rate gate 通过后，若 embed_fn 给且条数 ≤ `L2_MAX_EMBED_ITEMS`（KTD-H）：`group_semantic([o.text...], embed_fn=...)` → **每组整组拼接文本喂一次 `_judge`**（KTD-A 整组喂，根除代表掩盖），judge 数 = 组数 < issue 数。**回填（P0）：某组 judge=fault → 整组成员都标记为该故障证据**，matched_count/reason 反映全组覆盖。embed 抛错 / 超 MAX → 捕获、全量进 L3（KTD-B/H 降级，记 note）。**L2 不回写 dedup evidence_count/escalation**（KTD-G）。**security/release 不经此**（KTD-C）。
- **Test scenarios:**
  - Covers R9. 10 条含 6 近似 → L2 分 N 组、`_judge` 调用数 = N < 10。
  - 触发判定不变：组 judge 确认故障 → triggered（AE1 保持）。
  - **代表掩盖反例（P0）**：组=[噪声,噪声,真故障]整组喂 judge → 不得因代表是噪声而漏判故障。
  - **回填（P0）**：组 judge=fault → 整组成员计入证据/matched_count（断言覆盖全组非仅代表）。
  - **零漏报**：embed_fn 抛错 → 全量进 L3、不丢 issue（降级 note）。
  - **成本上限（KTD-H）**：条数 > L2_MAX_EMBED_ITEMS → 跳 L2 全量进 L3（降级 note）。
  - 不同故障(低 cosine) 不被分到一组 → 各自仍被 judge。
  - embed_fn=None → 行为同现状（向后兼容）。
- **Verification:** 场景过；既有 filter 测试零回归。

### U4. 模式 B 接 L2（trend）
- **Goal:** artifact-diff 后、batch explain 前插去重聚类。
- **Requirements:** R8, F2, KTD-A, KTD-B。
- **Files:** `src/transmutary/report/explain.py`、`tests/report/test_explain.py`。
- **Approach:** `explain_trends` 加 `embed_fn=None`。`_diff_candidates` 后若 embed_fn 给且条数 ≤ `L2_MAX_EMBED_ITEMS`：对 fresh 候选 `group_semantic([cand desc/repo...])` → 每组代表进 clean+explain；embed 抛错 / 超 MAX → 全量（KTD-B/H）。**回填可见性（P0）：报告注明该代表折叠了哪些近似 repo**（保留证据计数，不静默丢）。skipped/reaccelerated 语义不变。
- **Test scenarios:**
  - 近似候选分组 → explain 批量含代表、**报告注明折叠的近似 repo**（不静默丢，P0）。
  - embed_fn 抛错 → 全量 explain（降级）。
  - 成本上限：超 L2_MAX_EMBED_ITEMS → 全量（降级）。
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
| 贪心聚类传递性误并不同故障（P0 漏报） | representative-linkage（只与代表比，禁 single-linkage，KTD-A）；链式反例测试 |
| 噪声代表掩盖组内真故障（P0 漏报） | 整组文本喂 L3 judge（KTD-A）；代表掩盖反例测试 |
| 输出无组成员映射致下游漏回填（P0 语义错） | group_semantic 返回组成员映射；U3/U4 回填断言 |
| 阈值未校准误并真信号（P1） | 校准子步骤 + 方向性「宁少合并勿误并」+ 严格 `>`（KTD-E） |
| embedding 浪涌成本无上限（P1） | L2_MAX_EMBED_ITEMS 上限 + 超限降级透传（KTD-H）；embed 不碰 L3 budget |
| L2 与 L1 dedup 双重折叠压低 escalation | L2 不回写 dedup 状态、输入是 hash 去重后存活（KTD-G） |
| 零/空向量除零 NaN 误并 | 零范数各自独立成组（U2，安全方向） |
| 权威信号被 L2 延误/误降 | 供应链/release 绕过 L2(KTD-C) |
| MiniMax 无 embedding API | live 降级透传(KTD-F)；单测 mock；文档标 provider 门控 |
| 新依赖(numpy) | 手算 cosine 免依赖 |
| 改 filter/explain 破既有测试 | embed_fn=None 默认=现状行为；既有测试零回归 |

## Verification
1. `.venv/bin/python -m pytest -q` 全绿（含新 rerank/embed 测试），Phase 0-3+F4 零回归。
2. `.venv/bin/ruff check src tests` clean。
3. embed_fn=None 路径行为与现状一致（向后兼容断言）。
4.（可选 live）配 embedding provider → 跑 issue-surge 确认 L3 调用数 < 候选数。

## Execution
经 workflow：build(U1-U5) → 对抗审查（聚类零漏报正确性/不同信号不被并、降级透传、权威信号绕过、embed 入口、向后兼容 embed_fn=None、文档）→ 修复到绿。
