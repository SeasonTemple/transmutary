# Transmutary

面向「外部开源生态情报」的仓库观测系统。持续观测一批仓库及其依赖，把变化转成可读的诊断/说明报告，主动推送给团队——把「出事后被动核查」变成「主动感知」。

## 两个观测模式

系统由**两条采集管线 + 一个共享投递层**组成（两条管线、一个投递层，非统一引擎）：

- **模式 A · 事件驱动（关注清单）** — 盯团队重点维护/依赖的具体仓库，一有变化（release、issue 激增、供应链安全公告）即时检测、溯源诊断、分级推送。
- **模式 B · 定时跑批（趋势雷达）** — 定期扫描指定范围（MVP 锁 AI 方向），发现 star 快速递增的新热门仓库并出说明摘要。

两模式只在采集阶段分叉，之后共用 `LLM 报告 → channel 投递（私有 RSS / 邮件）`。模式 B 发现的热门仓可晋升进模式 A 关注清单。

## 设计要点

- **纯拉取架构**：不依赖 webhook（无法对第三方仓库建 webhook），靠 Atom feed + REST 增量轮询。
- **采集流水线**：`收集 → 清洗 → 去重 → 筛选 → 报告`；清洗先于 LLM，筛选走漏斗（规则 → 强模型 judge）。
- **确定性 API、语义才 LLM**：外部 API 走确定性代码，LLM 只做诊断/相关性/摘要；安全裁决与确定性 OSV/GHSA 命中交叉校验。
- **安全基线**：不可信外部内容结构隔离（防 prompt injection）、凭据只走 env 不入库、SSRF allowlist、产物私有访问受控。
- **数据源全免费拼合**：GitHub releases.atom + REST、OSV.dev、GHSA、deps.dev、OSS Insight。

## 技术栈

Python · httpx · feedparser/feedgen · sqlite3 · APScheduler · LiteLLM（LLM provider 传输，可经 OpenAI-compatible base_url 接入）· starlette + uvicorn（私有 feed 服务）· smtplib

## 当前状态

| 阶段 | 状态 |
|---|---|
| 调研规划（brainstorm） | ✅ 完成 |
| 实现计划（plan） | ✅ 完成（经多轮评审） |
| Phase 0 共享骨架（U1-U5, U14） | ✅ 完成 · 46 tests / ruff clean |
| Phase 1 模式 A（采集/诊断/投递/供应链） | ⏳ 待开发（spec 就绪） |
| Phase 2 模式 B（趋势雷达） | ⏳ gate 在 Phase 1 F1 试运行 |

## 文档

- 调研规划：[`docs/brainstorms/2026-05-29-repo-observation-system-requirements.md`](docs/brainstorms/2026-05-29-repo-observation-system-requirements.md)
- 实现计划：[`docs/plans/2026-05-29-001-feat-transmutary-observation-system-plan.md`](docs/plans/2026-05-29-001-feat-transmutary-observation-system-plan.md)
- Phase 1 goal spec：[`docs/plans/goals/2026-05-29-phase1-mode-a.goals.md`](docs/plans/goals/2026-05-29-phase1-mode-a.goals.md)
- 领域术语表：[`CONTEXT.md`](CONTEXT.md)

## 开发

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

配置见 `config/*.example.yaml`；凭据从环境变量读取（`TRANSMUTARY_*`），不入库。
