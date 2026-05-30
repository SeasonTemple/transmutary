# CHANGELOG

<!-- v0.1.0 为首发，条目手工撰写；后续版本由 python-semantic-release 从 -->
<!-- main 分支的 Conventional Commits 自动生成，请勿手工编辑后续条目。 -->

## v0.1.0 (2026-05-30)

首个 MVP —— 嬗变（Transmutary）模式 A/B 双管线观测系统全功能就绪。
First MVP — the full Mode A / Mode B observation system is in place.

### Features

- **模式 A（事件驱动 / 关注清单）** — `采集 → 去重 → 筛选 → 诊断 → 投递`：release / issue 激增检测、供应链公告（OSV/GHSA），溯源诊断含 R18 质量门控与 OSV/GHSA 交叉校验。
- **模式 B（趋势雷达）** — OSS Insight + star 快照采集、产物差分去重、LLM 批量说明。
- **共享投递** — 私有 RSS（按订阅者独立 token）+ 邮件，分级路由。
- **调度** — 单常驻服务，分级周期（供应链分钟级 / release ~10min / 趋势日级）。
- **安全基线** — 经 `llm.py` 单入口的 prompt-injection 隔离、SSRF allowlist + 禁重定向、凭据仅 env、确定性 API 与 LLM 语义分离。

### Milestone

- **F1 真实仓 end-to-end 验收** —— 真实 GitHub 拉取 + 真实 LLM 诊断跑通核心假设。

### Implementation units

- Phase 0 — 共享骨架（U1-U5, U14）
- Phase 1 模式 A — 采集 / 依赖 / 去重 / 筛选（U6-U9）
- Phase 1 模式 A — 诊断 / 投递 / 供应链（U10, U15, U11）
- Phase 2 模式 B — 趋势 / 说明（U12, U13）
- Phase 3 — 调度接线：pipeline + service

### Quality

239 tests passing · ruff clean · Apache-2.0
