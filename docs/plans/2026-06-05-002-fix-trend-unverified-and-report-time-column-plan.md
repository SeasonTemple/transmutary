# Plan: 修趋势报告误标待核实 + 报告列表加时间列

**Type:** fix
**Depth:** Lightweight
**Date:** 2026-06-05

---

## Context

在 live WebUI 审视真实报告时发现两个质量问题：

1. **所有 trend explain 报告被误标「待核实信号」(Unverified)** —— 真 bug。根因链：`collect/trend.py` 取 `raw_url = row.get("html_url") or row.get("url") or ""`，但 OSS Insight 趋势 payload 不带 `html_url`/`url` → `raw_url=""` → `TrendCandidate.url=""` → `report/explain.py` 构造 `sources=[]`（仅 `if cand.url` 才加来源）→ explain 在**零来源**时标 unverified → **每个趋势报告都被误标**。设计意图（explain.py 注释）：候选自己的 repo URL 算 1 个来源，1 来源=不标、0=标待核实。修法：payload 无显式 url 时从 repo 名**派生** `https://github.com/{repo}`。

2. **dashboard 报告/趋势/告警列表缺时间列** —— UX 缺口。`ReportCard` 已带 `ts: int`（epoch），但 `templates/index.html` 的 recent_reports 表（kind/severity/repo/title）、trend_candidates 列表、supply_chain_alerts 行、`repo.html` 报告列表都没渲染时间。`report/render.py` 已有 `fmt_timestamp(epoch → 'YYYY-MM-DD HH:MM UTC')` 可复用。

预期结果：趋势报告不再被误标（仅真正零来源时才标）；报告列表显示生成时间。

---

## Requirements

- R1：trend payload 无 `html_url`/`url` 时，`TrendCandidate.url` 派生为 `https://github.com/{repo}`；派生 URL 仍过 `_safe_candidate_url`/`assert_candidate_url_allowed` 校验（host 恒 github.com、在 allowlist；repo 走 path 无法篡改 host）。
- R2：修复后正常趋势候选携带 1 个来源 → explain 不再标 unverified（仅真正无 repo/无 url 的退化情形才标）。
- R3：payload 显式带 `html_url`/`url` 时仍用之（不覆盖）；off-allowlist 的显式 url 仍按现状丢弃+警告。
- R4：`ReportCard` 暴露 `ts_display`（`fmt_timestamp(ts)`），并入 `to_dict`（利好 agent-native JSON）。
- R5：index.html 的 recent_reports / trend_candidates / supply_chain_alerts 与 repo.html 报告列表渲染时间；i18n 加 `th.time` 双语键。
- R6：不改 diagnose 路径的 R18 ≥2 独立来源门（那是真诊断的高标准，正确行为）。

---

## Key Technical Decisions

**KTD1：派生 URL 取代空串，而非放宽 explain 门。** explain 的「零来源→待核实」逻辑正确，不动；问题在上游 `cand.url` 空。派生 `https://github.com/{repo}` 让候选携带其自身 repo 作单一来源——这正是 explain 注释描述的设计模型。派生值仍走现有 allowlist 校验：`repo` 是不可信外部内容，但它落在 URL 的 **path** 段，host 恒为 `github.com`（path 无法改 host），故 SSRF/allowlist 面不变。
- 备选（否决）：放宽 explain 在「1 来源也算待核实」或趋势一律不标——会破坏 R18 语义且趋势确有「无任何来源」的退化情形需标记。

**KTD2：`ts_display` 在 view-model 格式化，模板只渲染。** `ReportCard._to_card`（`dashboard/data.py`）用 `report.render.fmt_timestamp(ts)` 算 `ts_display`，存为字段 + 进 `to_dict`。模板渲染 `ts_display`，不在模板做时间运算（与 email/digest 的服务端格式化一致）。

---

## Implementation Units

### U1. 趋势候选派生 repo URL（修误标）
**Goal:** trend 候选无显式 url 时派生 `https://github.com/{repo}`，消除全量误标待核实。
**Requirements:** R1, R2, R3, R6
**Files:** `src/transmutary/collect/trend.py`、`tests/collect/test_trend.py`
**Approach:** KTD1。`raw_url` 回退从 `repo` 派生 `https://github.com/{repo}`（仅当 payload 无 `html_url`/`url`）；派生后仍经 `_safe_candidate_url`。显式 url 路径不变。
**Test scenarios:**
- payload 无 url + 合法 repo（`openai/symphony`）→ `cand.url == "https://github.com/openai/symphony"`。
- payload 带显式 `html_url`（on-allowlist）→ 用显式值，不覆盖。
- payload 带显式 off-allowlist url → 仍丢弃 + 警告（现状回归），且**不**回退派生（显式越界=可疑，不替它补）。
- 端到端：派生 url 的候选经 `explain_trends` → 报告 **不含**「待核实信号」前缀、`sources` 非空、`title` 无 unverified 前缀。
- 退化：repo 为空串 → 现有 `if not repo: continue` 跳过（回归，不构造垃圾 url）。

### U2. 报告列表时间列
**Goal:** dashboard 报告/趋势/告警列表显示生成时间。
**Requirements:** R4, R5
**Dependencies:** 无（与 U1 独立）
**Files:** `src/transmutary/dashboard/data.py`、`src/transmutary/dashboard/templates/index.html`、`src/transmutary/dashboard/templates/repo.html`、`src/transmutary/dashboard/i18n.py`、`tests/test_dashboard.py`
**Approach:** KTD2。`ReportCard` 加 `ts_display`；`_to_card` 用 `fmt_timestamp`。index.html recent_reports 表加 `<th data-i18n="th.time">` 列 + `<td>{{ c.ts_display }}</td>`；trend_candidates 行与 supply_chain_alerts 行加时间元素；repo.html 报告列表同理。i18n en `"th.time": "Time"` / zh `"时间"`。
**Test scenarios:**
- recent_reports 表渲染含 `th.time` 列头 + 某报告的格式化时间（`UTC` 子串）。
- ReportCard.to_dict 含 `ts_display`（JSON 路径）。
- 趋势/告警行含时间。
- i18n en/zh `th.time` 键齐全（沿用现有键对齐测试）。

---

## Scope Boundaries

**In scope:** 趋势候选 URL 派生（修误标）、报告/趋势/告警/repo 列表时间列、`ts_display` view-model + JSON、i18n `th.time`。

**Out of scope:** diagnose 的 R18 ≥2 来源门（正确行为，不动）；report.html 单报告页时间（可顺带但非必需）；趋势来源的真实多源corroboration（趋势本就单源设计）。

---

## Verification

1. `python -m pytest tests/ -q`（移开本地真实 `config/` 避污染）全绿；`ruff check src tests` 通过。
2. 端到端核查：`transmutary-demo` 或对真实趋势候选跑 `explain_trends` → 报告标题/正文**无**「待核实信号」（除非真零来源）。
3. WebUI：dashboard 报告/趋势列表显示时间列（headless 截图核对）。
4. 历史已落盘的误标报告是旧产物，本修复只保证**今后**新报告正确（不追溯重写存量）。
