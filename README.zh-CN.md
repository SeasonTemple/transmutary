<div align="center">

<!-- hero banner: assets/hero-banner.* (TODO) -->

# 嬗变 · Transmutary

**主动的开源生态情报 —— 持续观测仓库与依赖，把变化转成诊断报告，在出事前把要紧的推送给订阅者。**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![CI](https://github.com/SeasonTemple/transmutary/actions/workflows/ci.yml/badge.svg)](https://github.com/SeasonTemple/transmutary/actions/workflows/ci.yml)
[![Tests: 239 passing](https://img.shields.io/badge/tests-239_passing-brightgreen.svg)](#测试)

[English](README.md) · [简体中文](README.zh-CN.md) · [为何](#为何做嬗变) · [快速开始](#快速开始) · [工作原理](#工作原理) · [发布](#发布与版本)

</div>

---

**嬗变（Transmutary）** 是面向「外部开源生态情报」的仓库观测系统。持续观测一批仓库及其依赖，把变化转成可读的诊断/说明报告，主动推送给订阅者——把「出事后被动核查」变成「主动感知」。

## 为何做嬗变

订阅者对所依赖的外部生态，反应是结构性滞后的：

- **依赖中断只能事后核查** —— 上游 CLI 工具一变，私有内部网关开始 504，只能手工让 LLM 去查两个仓。
- **无 AI 热门情报渠道** —— 快速崛起的工具出现在社交媒体，不在你拥有的任何 feed 里。
- **供应链投毒反应慢** —— 恶意 npm 包发布后才有人察觉。

嬗变用一条纯拉取、数据源全免费拼合的管线补上这些缺口，无需 webhook、无需付费 API。

## 两个观测模式

系统由**两条采集管线 + 一个共享投递层**组成（两条管线、一个投递层，非统一引擎）：

- **模式 A · 事件驱动（关注清单）** —— 盯订阅者重点维护/依赖的具体仓库，一有变化（release、issue 激增、供应链安全公告）即时检测、溯源诊断、分级推送。
- **模式 B · 定时跑批（趋势雷达）** —— 定期扫描指定范围（MVP 锁 AI 方向），发现 star 快速递增的新热门仓库并出说明摘要。

两模式只在采集阶段分叉，之后共用 `LLM 报告 → channel 投递（私有 RSS / 邮件）`。模式 B 发现的热门仓可晋升进模式 A 关注清单。

## 工作原理

```
收集 → 清洗 → 去重 → 筛选 → 报告 → 投递
```

- **纯拉取架构** —— 不依赖 webhook（无法对第三方仓库建 webhook），靠 Atom feed + REST 增量轮询。
- **清洗先于 LLM** —— 先做结构化检查（URL/内容指纹、staleness、可达性），过关内容才进 LLM 做 chunk 级相关性过滤。
- **确定性 API、语义才 LLM** —— 外部 API 走确定性代码，LLM 只做诊断/相关性/摘要；安全裁决与确定性 OSV/GHSA 命中交叉校验。
- **分级调度** —— 单常驻服务 + 内部分级周期：供应链分钟级、release/issue ~10 分钟、趋势日级。
- **安全基线** —— 不可信外部内容与指令结构隔离（防 prompt injection）、凭据只走 env 不入库、SSRF allowlist 禁重定向、产物私有访问受控。

## 快速开始

### 安装

```bash
python -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

### 配置

复制示例配置并填写。凭据从环境变量（`TRANSMUTARY_*`）读取，不入库。

```bash
cp config/watchlist.example.yaml   config/watchlist.yaml
cp config/trend_scope.example.yaml config/trend_scope.yaml
cp config/delivery.example.yaml    config/delivery.yaml
export TRANSMUTARY_GITHUB_TOKEN=...      # 只读
export TRANSMUTARY_LLM_API_KEY=...       # 任意 LiteLLM 支持的 provider
export TRANSMUTARY_LLM_BASE_URL=...      # 可选：OpenAI/Anthropic-compatible 端点
```

### 验证

```bash
.venv/bin/python -m pytest -q
.venv/bin/ruff check src tests
```

## 配置文件

| 文件 | 用途 |
|---|---|
| `config/watchlist.yaml` | 模式 A 仓库 + 手工依赖边 |
| `config/trend_scope.yaml` | 模式 B 范围过滤器（topics + keywords） |
| `config/delivery.yaml` | DB/产物路径、摘要发送时辰、可选 RSS feed 目录 + SMTP 收件人 |

## 产物与存储

两个根，均在 `delivery.yaml` 配置。所有报告私有（文件 `0600`、目录 `0700`），gitignored 不入库。

```
<artifact_root>/
├── <owner>__<repo>/                       # 按仓库归档的分析产物（权威，R24）
│   └── <ts>-<kind>.md                     #   每份报告的溯源载体
├── _delivered/<route>/                    # 投递渲染的报告
│   └── <owner>__<repo>-<kind>.md          #   route = immediate(高危) | digest(摘要)
└── _feed/<route>.atom.xml                 # 私有 RSS feed，按路由各一

<state_db_path>  state.sqlite3  (SQLite, WAL)
  event_fingerprint   事件去重（release / advisory / issue 聚类）
  seen_set            7 天滚动已见集（产物差分）
  issue_baseline      每仓 issue 速率基线
  collect_cursor      每仓 since 增量游标（跨重启）
  star_snapshot       模式B star 快照（增速）
  subscriber_token    订阅者 RSS token（撤销 / 有效期）
```

一拍流程：

```
collect (atom + REST 增量)
  → dedup (event_fingerprint / seen_set)
  → release 直诊   |   issue 走筛选漏斗 (L1 规则 → L3 judge) → 诊断
  → diagnose (LLM + R18 质量门控 + OSV/GHSA 交叉校验)
  → 归档 per-repo 分析产物  +  投递 (路由 → _delivered/<route>/ + RSS；immediate 加邮件)
  → 持久化 state (推进游标 / 更新基线 / 记指纹)
```

路由按 severity：高危（malware/critical）→ `immediate` + 邮件；其余（或 R18 降级）→ `digest`。

## 架构与文档

- 领域术语表：[`CONTEXT.md`](CONTEXT.md)
- 调研规划（brainstorm）：[`docs/brainstorms/`](docs/brainstorms/)
- 实现计划：[`docs/plans/`](docs/plans/)

## 发布与版本

发布由 [python-semantic-release](https://python-semantic-release.readthedocs.io/) 自动化。版本号、changelog、tag、GitHub Release 均由 `main` 上的 [Conventional Commits](https://www.conventionalcommits.org/) 推导：

- `feat:` → minor · `fix:` / `perf:` → patch · `BREAKING CHANGE:` → major。

clone 后启用一次本地提交校验钩子：

```bash
git config core.hooksPath .githooks
git config commit.template .gitmessage
```

发布历史见 [`CHANGELOG.md`](CHANGELOG.md)。

## 项目

### 状态

| 阶段 | 状态 |
|---|---|
| 需求 + 计划 | ✅ 完成（多轮评审） |
| Phase 0 — 共享骨架（U1-U5, U14） | ✅ 完成 |
| Phase 1 — 模式 A（采集/诊断/投递/供应链） | ✅ 完成 · F1 真实仓里程碑已验收 |
| Phase 2 — 模式 B（趋势雷达） | ✅ 完成 |
| Phase 3 — 调度接线（pipeline + service） | ✅ 完成 |
| 测试 | ✅ 239 passing · ruff clean |

### 路线图

按设计延后：L2 embedding rerank、critique→refine 报告增强、channel 接口抽象、一键晋升、订阅配置、Web 仪表盘、真实常驻跑。

### 测试

```bash
.venv/bin/python -m pytest -q      # 239 passing
.venv/bin/ruff check src tests     # clean
```

### 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)：开发环境、Conventional Commits 规范（由 `.githooks/commit-msg` 强制）、自动发布流程。PR 提向 `main`。

### 许可

[Apache-2.0](LICENSE) © SeasonTemple
