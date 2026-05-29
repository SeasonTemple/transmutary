# Phase 1（模式 A）后台 goal spec — 备用

权威规格：`docs/plans/2026-05-29-001-feat-transmutary-observation-system-plan.md`。术语：`CONTEXT.md`。
适用：Codex CLI 0.128+ `/goal`，或 Claude Code 后台 `Agent`（run_in_background）prompt。
前置：Phase 0（#1，U1-U5+U14）已完成且 `pytest` 绿。

Phase 1 是重相（7 单元），按依赖缝拆 **#2a → #2b** 两个 goal，各自审计可靠。

---

## #2a — 模式 A 采集 + 去重 + 筛选（U6, U7, U8, U9）

```
/goal 实现 Transmutary 模式 A 的采集/依赖/去重/筛选层(U6 U7 U8 U9),按 docs/plans/2026-05-29-001-feat-transmutary-observation-system-plan.md。产出「触发事件」,不含诊断报告/投递/趋势。

First action: 先读计划 + 跑 `.venv/bin/python -m pytest -q` 确认 Phase 0(U1-U5,U14)存在且绿;报 Phase 0 状态 + 本批 4 单元(U6 github, U7 deps, U8 dedup, U9 filter)及 Files,等确认再写码。Phase 0 不绿则停下报告,不重建。

Scope:
  - 创建 src/transmutary/collect/{github.py, deps.py}、src/transmutary/{dedup.py, filter.py} + 对应 tests/
  - 不碰 collect/{security,trend}.py、report/*、deliver/{server,rss,email}.py — 那是后续批

Constraints:
  - 所有 LLM 调用经 llm.py(U14),禁裸 SDK;U9 的 L3 judge 必须经 llm.py 数据/指令分槽(KTD3)
  - 所有外部 API 走确定性代码(KTD2);SSRF: URL 按可信域 allowlist 构造校验(github.com/deps.dev)、httpx follow_redirects=False(R23)
  - GitHub token 只从 env、只发只读请求(R22);凭据不入库/日志(KTD4)
  - U8 引用归并必须确定性(抽显式引用 URL 归并),不依赖 L2 embedding;R18 源独立性按「不同域 + 不共引同一上游 URL」计(KTD6)
  - U9 冷启动必须发一个具体默认绝对阈值(N 条/窗 W),非纯 config-defer;L3 日上限走 llm.py 的 litellm budget
  - 测试全 mock(httpx/litellm/内存 sqlite),禁真实网络

Done when:
  1. `.venv/bin/python -m pytest -q` 退出码 0,覆盖 U6/U7/U8/U9 全部 Test scenarios;`.venv/bin/ruff check src tests` 退出码 0
  2. U6: mock atom+REST → 解析 release/tag/issue 规范事件;pre-release 经 REST 兜底;since= 增量游标推进;429 退避;非 github.com 主机名 watchlist 项被拒、不跟随重定向;只发只读请求
  3. U7: package.json → 直接依赖集;未发包仓仅手工边不报错;手工边关联仓并入观测上下文;已发包仓经 deps.dev 取传递依赖、未发包仓仅直接依赖(AE3);deps.dev 不可达 → 退化记录降级
  4. U8: 同 release 跨周期只产一次、issue 聚类新增只增 evidence_count、跨升级阈值发一次升级(AE4);URL 规范化归并;3 篇不同域博客同引一上游 issue → 按引用归并计 1 个独立源(R18 不被绕)
  5. U9: 速率超基线×倍数且达下限 + judge 确认 → 触发(AE1);冷启动无基线用确定默认绝对阈值(默认值可断言);速率高但 judge 判非故障 → 不触发;中文「挂了/超时」被识别为故障;judge 的 issue 文本含注入串 → 判定不被改写;L3 日上限触发抛可捕获异常;judge 调用失败 → 保守处理
  9. 不创建任何后续批文件(security/trend/report/deliver)

Stop if:
  - Phase 0(U1-U5,U14)缺失或 pytest 不绿 — 停下报告,不重建 Phase 0
  - 测试出现真实网络调用(httpx/litellm 未 mock)
  - 任何凭据值出现在 repr/日志/SQLite
  - LLM 调用绕过 llm.py
  - 触碰 collect/security.py、collect/trend.py、report/*、deliver/server|rss|email
  - 既有测试(Phase 0)开始失败 — regression,不要改测试/加 skip 解决
  - 同一 Done when 连试 2 次仍不过 — 停下报告卡点

Use a token budget of 120000 tokens for this goal.
```

---

## #2b — 模式 A 诊断 + 完整投递 + 供应链 + F1 里程碑（U10, U15, U11）

```
/goal 实现 Transmutary 模式 A 的诊断报告(U10)、完整投递(U15)、供应链预警(U11),并完成 F1 真实仓 end-to-end 里程碑,按 docs/plans/2026-05-29-001-feat-transmutary-observation-system-plan.md。

First action: 先读计划 + 跑 `.venv/bin/python -m pytest -q` 确认 #2a(U6-U9)与 Phase 0 全绿;报状态 + 本批 3 单元(U10 diagnose, U15 delivery, U11 supply-chain)及 Files,等确认再写码。前置不绿则停下报告。

Scope:
  - 创建 src/transmutary/clean.py、src/transmutary/report/diagnose.py、src/transmutary/deliver/{server,rss,email}.py、src/transmutary/collect/security.py + 对应 tests/
  - 用真实现替换 deliver/stub.py 体、保持 deliver() 签名
  - 不碰 collect/trend.py、report/explain.py — 那是 Phase 2

Constraints:
  - 所有 LLM 调用经 llm.py(U14);system 指令/数据分槽(KTD3)
  - 安全裁决(供应链)不得仅凭 LLM,必须与确定性 OSV/GHSA ID 命中交叉校验(KTD2/R23)
  - R18 门控:一手权威单源(GHSA/OSV/上游 issue)直接出结论;派生类按 U8 归并后独立源计数,<2 降「待核实信号」
  - clean.py 结构化检查先于 LLM;相关性裁剪 MVP 用段落规则(chunk 级语义随 L2 延后),显式接受 token 偏高
  - U15: RSS 鉴权走 HTTP Authorization 头、token 不入 URL(R20);按订阅者独立 token + 撤销/有效期(用 U2 subscriber_token 表);日志脱敏 token;SMTP 凭据从 env(R21);路由两分支内联(不建 channel.py,KTD1)
  - U11: SSRF allowlist(osv.dev/github.com)、禁重定向、MVP 不下载/解包可疑包(R23);F3 不经完整 diagnose、只加简短处置建议 + 确定性 ID 命中事实
  - 单元测试全 mock;真实网络仅在最后的 F1 里程碑步骤、明确隔离

Done when:
  1. `.venv/bin/python -m pytest -q` 退出码 0,覆盖 U10/U15/U11 全部 Test scenarios;`.venv/bin/ruff check src tests` 退出码 0
  2. U10(代码门,KTD8): 触发事件 → 诊断报告含「疑似源头+受影响依赖+关联仓+建议」;上游 CLI 工具 事件经依赖边连带拉 内部网关 上下文(F1);issue 正文注入 → 数据分槽诊断不被改写;LLM 称安全但 OSV/GHSA 无证据 → 交叉校验不放行;单一 GHSA 源直接出、多博客共引单一上游 → 按 U8 归并降「待核实」;staleness/不可达内容进 LLM 前剔除;上游无信号的中断 → F1 不触发
  3. U15: 高危 Report → 即时 RSS 项 + 邮件、模式 B 占位 → 摘要 feed;高危供应链立即进即时 feed(F3);feed URL/项不含 token、鉴权走 Authorization 头、无/无效 token 被拒;撤销单订阅者 token 不影响他人、过期 token 被拒;SMTP 凭据来自 env;SMTP 失败降级不丢 RSS
  4. U11: 命中清单依赖 malware/critical → 即时高危告警(AE3/F3);已发包仓覆盖传递依赖、未发包仓仅直接;advisory 内嵌 URL 受 allowlist 限不跟随重定向;不下载/解包 tarball;GHSA advisory 文本含注入 → 经 llm.py 隔离;OSV 不可达 → GHSA 兜底;依赖 >1000 分批
  5. **F1 真实仓里程碑(KTD8,核心假设验收)**: 对 ≥1 真实关注清单仓库做一次 end-to-end 实跑(真 atom/REST/OSV 拉取 + 经配置 provider 真 LLM 调用),产出一份真实诊断报告并落盘;在收尾报告里粘贴该报告摘要 + 用到的真实仓/事件。**凭据不可用则标记里程碑 pending 并停下报告,不得伪造**

Stop if:
  - #2a(U6-U9)或 Phase 0 缺失/不绿 — 停下报告,不重建
  - 测试(非 F1 里程碑)出现真实网络;F1 里程碑外的任何真实 LLM/HTTP 调用
  - 安全裁决仅凭 LLM、无 OSV/GHSA 交叉校验
  - 任何凭据值出现在 repr/日志/SQLite/报告
  - LLM 调用绕过 llm.py
  - 引入 channel.py 或 channel 接口抽象(KTD1)
  - 触碰 collect/trend.py、report/explain.py(Phase 2)
  - 既有测试(Phase 0/#2a)开始失败 — regression,不改测试解决
  - 下载/执行/解包任何可疑包内容
  - 同一 Done when 连试 2 次仍不过 — 停下报告卡点

Use a token budget of 140000 tokens for this goal.
```

---

## 设计选择简述

- **Phase 1 拆 2a/2b**：7 单元一个 goal 预算逼近 200K、审计不可靠。按依赖缝切——2a 产「触发事件」(全 mock)、2b 产「报告+投递+预警」(依赖 2a)。各 ≤140K。
- **F1 验收只在 #2b 末以「真实仓里程碑」出现**(KTD8)：mock 测试只证代码正确;核心假设要真 atom/REST/OSV + 真 LLM 跑一遍真实仓。凭据缺 → pending 不伪造（写进 Stop if）。
- **真实网络的边界**：单元测试一律 mock；唯一允许真实网络 = #2b 的 F1 里程碑步骤，明确隔离，Stop-if 双向锁死。
- **安全贯穿**：每批都重申 llm.py 单一入口 + 注入隔离 + SSRF allowlist + 交叉校验 + 凭据不落库；Stop-if 把「绕过 llm.py」「裁决仅凭 LLM」「解包可疑包」列为单向门。
- **前置守卫**：每个 goal 的 First action 先验前置批 pytest 绿，不绿即停不重建——防后台 agent 在残缺地基上硬建。

## 审计友好度：优 · #2a 5 项 / #2b 5 项验收 · 0 风险标记
