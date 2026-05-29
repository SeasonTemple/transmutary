---
date: 2026-05-29
topic: repo-observation-system
---

# Transmutary 仓库观测系统 — 调研规划

## Summary

构建一套面向「外部开源生态情报」的仓库观测系统，由**两个观测模式**组成、共享同一条「报告 → 投递」后段：

- **模式 A — 事件驱动（关注清单）**：盯着团队重点维护/依赖的具体仓库（上游 CLI 工具、内部网关 等）及其依赖，一有变化（release、issue 激增、安全公告）即时检测、诊断、分级推送。对应原能力 #1。
- **模式 B — 定时跑批（趋势雷达）**：定期扫描指定范围（MVP 锁 AI 方向），发现 star 快速递增的新热门仓库，总结其能力/技术特点，进摘要。对应原能力 #2。

两模式**共用投递层（channel）+ 报告数据 schema**；其余（触发、去重、报告生成）按模式分叉——更准确说是「两条管线、一个投递层」，而非统一引擎（避免为「表面统一」堆条件分支）。架构为纯拉取（无 webhook）、数据源全免费拼合。目标主场是 Web 仪表盘 + 分级告警，MVP 先用 RSS/邮件两个 channel 验证「主动感知 > 被动核查」这一核心假设。

本文档是**调研规划**，用于驱动后续 `ce-plan`，不含落地代码。

## Problem Frame

团队当前对外部依赖与生态的感知是**被动且滞后**的，已发生三类真实代价：

1. **依赖中断只能事后核查。** 本地 上游 CLI 工具 指向团队私有 内部网关，出现 504 后，团队**手工让 LLM 去 上游 CLI 工具 与 内部网关 两个源仓库调查**——结果两个仓库当时**都有可观测的问题**，最终通过升级 内部网关 解决了部分问题。这说明上游 OSS 侧的信号确实存在且可定位；但整个「发现问题 → 指定仓库 → 让 LLM 查 → 拼溯源报告 → 决定升级」的链条全靠人工事后发起。
2. **技术情报缺渠道。** 没有稳定途径获知近期 AI 相关最热门的开源仓库/技术，团队认知与信息滞后于生态。
3. **供应链安全反应慢。** npm 依赖投毒等事件需要「快速感知 + 快速处置」，目前没有任何主动预警。

第 1、3 类痛点共性是「对**已知重点仓库**的变化反应太慢」（→ 模式 A）；第 2 类是「对**未知新热门**没有发现渠道」（→ 模式 B）。市面工具（Dependabot/Renovate/Snyk/Socket/newreleases.io/OSS Insight/deps.dev）各自只覆盖其中一块，且几乎都从「保护自己的代码库」出发，而非「情报监控外部生态」。

## Key Decisions

- **两条管线、一个投递层（不是统一引擎）。** 系统是两个观测模式：模式 A（事件驱动 / 关注清单 / 诊断）与模式 B（定时跑批 / 趋势雷达 / 说明）。**真正共享的只有投递层（channel）+ 报告数据 schema**；触发模型、去重策略（A 事件指纹 vs B 产物差分）、报告生成（A 单事件诊断 vs B 批量摘要）都按模式分叉。如实命名共享边界，避免把两套工作流硬塞一条代码路径、为「表面统一」累积条件复杂度。两模式不分主次，且有交叉：模式 B 发现的热门仓可**晋升**进模式 A 的关注清单。

- **去重策略按模式不同。** 模式 A 用**事件指纹**（见 R8）防止同一变化跨多次轮询重复推送；模式 B 用**产物差分**（本轮分析产物 vs 上轮同仓产物）只上报新增/变化。

- **纯拉取架构，不依赖 webhook。** GitHub 不允许对非自己拥有的仓库创建 webhook，监控第三方仓库只能靠 `releases.atom`/`tags.atom`/`commits.atom` feed（无限额、分钟级延迟）+ REST `?since=` 增量轮询兜底。这是系统最硬的架构约束，决定了「轮询调度 + 增量去重」是核心工程，而非数据获取。

- **依赖关系分两类，分别处理。** (a) **包依赖**（代码 manifest 里声明的）→ 系统自动解析（MVP 锁 npm `package.json`，逐步扩 PyPI/Go）。(b) **运行时服务链**（如 上游 CLI 工具 经 `base_url` 指向 内部网关，不在任何 manifest 里）→ 由用户在观测清单中**手工声明依赖边**，系统不猜；跨仓关联（「上游 CLI 工具 和 内部网关 同期都有 5xx」）交给报告阶段 LLM 完成。

- **存储双层。** 报告产物（markdown）按**仓库名建目录**存放，人可读、可 git 版本化；运行状态（事件指纹、star 快照时序、issue 基线、去重记录）存**轻量 SQLite**，便于查询与差分。

- **数据源全部走免费 API 拼合。** **数据获取**成本接近零（注意：这不含 LLM 推理花费——L3 judge 用强模型有真实成本，见 Success Criteria 的成本项）。自建价值在「跨源聚合 + LLM 诊断 + 情报整理」这层，而非任何单一数据获取能力。详见 Sources / Research。

- **送达层是 channel 抽象。** 报告/告警不绑定具体投递方式，统一经 channel 接口分发。MVP 只实现 RSS 源（必做主 channel）+ 邮件两个 channel；邮件走现成邮箱 SMTP 经客户端库（nodemailer/smtplib）发送（免费、零运维），第三方发信 API 为可选升级，自建 MTA 不做。RSS feed 含团队私有仓库的诊断与内部依赖信息，**必须是带 token 的私有 feed**（URL 含 secret 或 basic auth），不进公开索引。Slack/企微/Webhook 等为后续插件式扩展。

- **分级触达。** 高危事件（投毒、安全公告、依赖中断）走紧急推送；趋势/一般问题走每日或每周摘要。仪表盘为目标主场，但延后到 MVP 之后。

- **LLM 负责「事件 → 报告」。** 复刻团队当前「让 LLM 查 + 出溯源报告」的人工动作并自动化：对一条检测到的事件，自动拉取上下文（issue 正文、release notes、advisory 详情、关联依赖）喂给 LLM，产出结构化诊断/说明报告。

- **采集流水线骨架 = `收集 → 清洗 → 去重 → 筛选 → 报告`（借鉴开源 deep-research skill 生态的事实标准）。** 两模式都跑这条骨架，差别在触发与采集源。关键工程纪律来自社区高热 skill 的反复验证：(1) **清洗先于 LLM**——先做结构化检查（URL/内容指纹、staleness、可达性），过关的内容才进 LLM 做 chunk 级相关性过滤，绝不把全文直接塞 LLM（控成本、防低质输入污染判断）；(2) **筛选走漏斗**，越靠后越贵、候选越少，中间层不用 LLM；(3) **确定性 API、语义才 LLM**——GitHub/OSV/deps.dev 等 API 调用走确定性代码路径，LLM 只做语义判断（诊断/相关性/摘要），规避「推理模型放大 tool hallucination」；(4) 规避反模式：平铺 ingestion 不分桶、单源即确认、无阈值 LLM 评分、丢失溯源链。详见 Sources / Research 的方法论参考。

- **单常驻服务 + 内部分级定时。** 部署形态选常驻进程内嵌调度器（高危源分钟级、release feed 十几分钟、趋势日级），而非碎 cron 或冷启动+状态难的 serverless。

## Requirements

### 观测模式与对象

- R1. 系统提供**两个观测模式**：模式 A（事件驱动 / 关注清单）盯已知重点仓库的变化；模式 B（定时跑批 / 趋势雷达）发现指定范围内的新热门仓库。两模式共享报告与投递后段。
- R2. 关注清单（模式 A 对象）来自**手工配置**，清单项是**用户指定的仓库**，不区分归属（自有维护 / 第三方开源一视同仁）；初始至少含 上游 CLI 工具、内部网关 相关上游仓库。
- R3. 清单项可**手工声明运行时依赖边**（指向同清单另一仓，如 `上游 CLI 工具.depends_on = [内部网关]`），用于事件发生时连带拉取关联仓上下文。系统对清单仓**自动解析包依赖**（MVP 锁 npm `package.json`）并纳入观测范围；运行时服务链不自动发现。
- R4. 趋势雷达（模式 B）按**指定范围**（MVP 锁「AI 方向」）持续产出热门候选；范围由一个**过滤器**定义，MVP 采用 `topic 标签（llm/agent/rag…）+ 描述/README 关键词` 的 OR 兜底策略（topic 抓主流、关键词补新仓），过滤器定义存配置、后续可调。候选可**晋升**进关注清单（MVP 手工编辑配置，一键晋升延后，见 F4）。

### 信号采集

- R5. **依赖健康信号（模式 A）**：检测被观测仓库的新 release/tag、破坏性变更线索、热点 issue（新增、高互动、含 breaking/outage/regression 等关键词）。
- R6. **技术趋势信号（模式 B）**：检测指定范围内 star 快速递增的仓库，计算增速（绝对增量 + 时间窗），识别新晋热门；同时总结其能力/技术特点。
- R7. **供应链安全信号（模式 A）**：对关注清单仓库及其依赖，检测已知漏洞（CVE/GHSA）与投毒/恶意包公告，覆盖主流生态（至少 npm，逐步扩展 PyPI/Go 等）。归属模式 A，因其针对的是团队**已依赖**的对象、要求快。
- R8. **采集增量去重，分层 + 引用链归并**：模式 A 以**事件指纹**去重——release/advisory 指纹 = tag / GHSA-id（一次性事件）；issue 类指纹 = `(仓库, 关键词桶, 滚动时间窗)`，同一窗内新增只更新证据、不重推，跨升级阈值才推「升级」。去重分两层：**MVP 只做 L1**——内容/URL hash seen-set（滚动 7d 窗，O(1) 精确去重）；**L2 语义相似度（embedding 余弦 ≥ 阈值，捕获「同事件不同表述/转载」）列为 MVP 后第一项增强**（避免 MVP 即引入 embedding 依赖）。**引用链归并**：博客/转载与其引用的原始 PR/issue 共享同一 canonical ID，避免同一事件被计为多条、虚高信号强度（L1 阶段可先按 URL 规范化做粗归并，语义级归并随 L2 上线）。模式 B 以**产物差分**去重（本轮 vs 上轮同仓产物）。
- R9. **筛选走漏斗 + 每仓基线**：**MVP = L1 → L3 两层**。L1 关键词/规则过滤（仓库黑白名单、事件类型白名单，无 LLM 成本）；L3 **LLM-as-judge 二次确认**——用强模型（非最低档，弱模型误报压制不稳，见 Sources），判定是否真为故障/中断（吸收多语言如「挂了/超时」、过滤无关语境误命中），L3 日总量设硬上限控成本。**L2 轻量 embedding rerank 列为 MVP 后增强**（与 R8 L2 同批）。issue 激增阈值按**每仓基线**：触发 = 当前窗速率 > 基线 × 倍数（如 3×）且绝对量 ≥ 下限（防 0→2 误报）；冷启动无基线时用保守绝对阈值。不靠纯关键词匹配触发推送。judge 的具体提示结构（如 JudgeRank「先抽核心信号再判相关」三步）属实现细节，留 planning，方法论出处见 Sources。

### 诊断与报告

- R10. 对一条模式 A 事件（依赖健康 / 供应链安全），系统自动聚合上下文（issue/release/advisory 正文 + 受影响依赖 + 手工声明的关联仓上下文），调用 LLM 产出**溯源诊断报告**：问题是什么、源头在哪、影响范围、建议处置。
- R11. 对模式 B 趋势发现，系统产出**说明报告**：仓库做什么、为何在涨、关键数据（star/增速/语言/活跃度）、是否值得团队关注。**批量 + 阈值触发深挖**：日常对通过筛选漏斗的候选做批量轻摘要（每条 2-3 句、廉价模型）；重要性过阈值（如 judge 分高 / star 增量大）才触发**单事件深挖**。**MVP 深挖 = 单次综合**；`综合 → 批判(critique) → 修订(refine)` 三段式（对抗式自查提质）**列为 MVP 后增强，待单次综合质量实测不足再启用**。诊断类（模式 A）默认单事件单次调用。
- R12. 报告以结构化形式存储，可被多个 channel 渲染（RSS 条目、邮件正文）。

### 存储

- R13. **双层存储**：报告产物按**仓库名建目录**存放（markdown，人读、可 git 版本化）；运行状态（事件指纹、star 快照时序、issue 基线、去重记录）存 **SQLite**，支撑去重与产物差分。

### 送达

- R14. MVP 实现 **RSS 源**（必做主 channel）与**邮件**两种投递输出，**共用同一报告数据结构**；正式的 channel 接口抽象**待加入第三个 channel 时再引入**（当前两输出 push/pull、文件/SMTP 差异大，过早抽象易把错误假设泄进双方）。RSS 为**私有 feed**（含私有仓库信息，不公开索引），鉴权见 R20；邮件走现成邮箱 SMTP 经客户端库（免费、零运维），第三方发信 API 为可选升级，自建 MTA 不做。
- R15. 触达**分级**：高危事件（R7 投毒/安全公告、R5 依赖中断）即时推送；趋势与一般问题进入定期（每日/每周）摘要。
- R16. **MVP 用固定路由**：模式 A 高危事件 → 即时 RSS + 邮件；模式 B 与低优先 → 每日/每周摘要 RSS。可配置订阅（按信号类别选 channel/节奏）**待观察到真实路由偏好后再做**。

### 采集与质量方法论

- R17. **清洗分层（结构化先于 LLM）**：采集入口先做结构化检查——内容/URL 指纹去重、staleness 判定（按 source type 差异化：新闻类数月降级、release 超期视为已知）、可达性检查（抓取失败/付费墙标记并剔除）；只有过关条目才进 LLM 做 **chunk 级相关性过滤**（逐段判与观测目标的相关性、丢弱相关段，类 ChunkRAG），不整文透传给 LLM。
- R18. **质量门控（按来源类型分级，不一刀切）**：(a) **≥2 独立来源**规则**只作用于派生/转载类结论**（博客、二手分析、跨源推断）——这类才需多源背书 + R8 引用链归并防虚高。**权威一手单源信号豁免**：GHSA/OSV 公告、上游仓库 issue/release 这类一手权威源，单源即可直接出结论并即时推送——否则会把最时效的安全/中断信号降级，直接违反「零漏报优先」（见 Success Criteria）。模式 A 单仓诊断本质就是一手单源（一条 issue 线程 + release），适用豁免。(b) 每条进报告的结论附 `source_id + 抓取时间戳`（MVP 必做，溯源载体）；citations 落盘（防 context 压缩丢溯源链）。引用可达性的**自动验证脚本延后**（MVP 由读者人工判断；清单小、读者即团队）。无法满足其适用门控的结论降级为「待核实信号」。
- R19. **确定性 API、语义才 LLM**：所有外部 API 调用（GitHub / OSV / deps.dev / OSS Insight）走确定性代码路径，不交由 LLM 发起；LLM 仅承担语义任务（诊断、相关性判断、摘要），规避推理模型放大 tool hallucination。

### 安全与凭据

- R20. **私有 RSS feed 鉴权**：优先用 HTTP `Authorization` 头（Basic/Bearer）承载密钥，**不把 token 放进 URL path/query**（URL 中的 token 会经 Referer 头、代理/CDN 日志、浏览器历史泄露，而 feed 含私有供应链情报）。支持**按订阅者独立 token + 失效/轮换机制**（撤销单个订阅者不影响其他人）、token 最大有效期、feed 服务端日志脱敏 token 值。
- R21. **凭据集中管理**：所有密钥（SMTP 口令、GitHub App 私钥/PAT、RSS token）从注入式 secret store 读取（环境变量来自密钥管理器或加密配置），**绝不入源码/明文配置**；密钥值**不写入 SQLite 状态库、不写入报告 markdown**。凭据**按环境隔离**（dev/prod 至少分离），配置上杜绝开发态误连生产投递通道。
- R22. **GitHub App 最小权限**：凭据只读，权限收敛到 Issues(read)/Contents(read)/Metadata(read)，**无写、无 admin**；安装范围**按仓库 allowlist**，非组织级（缩小凭据泄露爆炸半径）。
- R23. **不可信外部内容边界（prompt injection / SSRF）**：第三方 issue 正文/README/advisory 是不可信输入，喂 LLM 前**数据与指令必须结构隔离**（「待分析数据」与「给 LLM 的指令」边界在提示架构层强制）；**安全裁决（R7 供应链）不得仅凭 LLM 对注入内容的总结**——必须与确定性 API 路径（OSV/GHSA ID 命中）交叉校验（呼应 R19）。URL 抓取限**可信 API 域 allowlist**（github.com/osv.dev/deps.dev/ossinsight.io）、**禁跟随重定向**；MVP **绝不下载/执行/解包**可疑包内容（tarball/安装脚本）。
- R24. **敏感产物存储隔离**：报告产物（含私有仓诊断、内部依赖拓扑、漏洞详情）与 SQLite 状态库置于**私有、访问受控**存储，**绝不与公开仓库同库**；历史漏洞报告设保留期/TTL。

## Key Flows

- F1. **模式 A — 依赖中断诊断（复刻 504 场景）。**
  - **Trigger:** 关注清单某仓出现热点 issue 激增（按 R9 基线触发）或异常 release。
  - 引擎检测信号 → LLM 二次确认是否真故障 → 聚合该 issue/release 上下文 + 经手工依赖边连带拉取的关联仓上下文（如 上游 CLI 工具 事件连带 内部网关）→ LLM 产出溯源诊断报告 → 经紧急 channel 推送。复刻团队当时「让 LLM 查两仓 → 定位 → 升级」的人工动作，但在问题暴露到自己环境之前/同时就给出诊断。
  - **覆盖边界（诚实声明）**：F1 只能覆盖**上游 OSS 侧有可观测信号**的中断（504 那次属此类——两仓都有 issue）。纯私有部署/网络/配额类故障上游无 issue 激增，F1 不会触发；这类只能靠团队自身可观测性兜底，**不在本系统职责内**。F1 的实际命中率取决于「中断中有上游信号者」的占比（见 Dependencies 的 base-rate 假设）。

- F2. **模式 B — 趋势雷达。**
  - **Trigger:** 每日定时任务。
  - 拉取指定范围 trending（按语言取榜）+ star 快照差值 → 对候选补查 topic/描述并按 R4 过滤器筛到 AI 范围 → 识别新晋/加速仓库 → 产物差分去重 → LLM 批量产出说明报告 → 进每日/每周摘要 channel。候选可晋升进关注清单。

- F3. **模式 A — 供应链预警。**
  - **Trigger:** 安全源（GHSA malware feed / OSV 查询）出现命中关注清单某依赖（含传递依赖）的新公告。
  - 引擎检测命中 → 聚合受影响包与依赖路径 → LLM 产出处置建议报告 → 即时推送高危告警。

- F4. **热门晋升（MVP = 手工编辑配置；一键晋升延后）。**
  - **MVP:** 用户直接编辑关注清单配置文件加入该仓 → 解析其包依赖 → 从此被模式 A 盯着。足以验证两模式各自闭环。
  - **延后:** 在模式 B 摘要里「一键加入关注清单」的便捷动作（跨模式状态写入），待两模式各自稳定后再做。

## Acceptance Examples

- AE1. **Covers R5, R9, R10, F1.** 当关注清单某仓 issue 速率超过其基线 × 倍数且绝对量达下限、且 LLM 二次确认为真故障时，系统在下一轮询周期内产出含「疑似源头 + 受影响依赖 + 关联仓 + 建议」的诊断报告并紧急推送，无需人工发起查询。冷启动无基线时退化为保守绝对阈值。
- AE2. **Covers R6, R8, F2.** 当某 AI 方向仓库单日 star 增量进入范围 top 区间且产物差分判定为新增/显著变化时，系统在当日趋势摘要纳入该仓；同一仓不在后续摘要重复出现，除非再次显著加速。
- AE3. **Covers R7, F3.** 当 GHSA/OSV 发布一条命中关注清单某依赖的 malware/critical 公告时，系统即时推送高危告警，而非等待下一次定期摘要。**传递依赖匹配的 MVP 边界**：仅对 deps.dev 可解析的对象（已发包仓库）覆盖传递依赖；未发包仓库（如私有 内部网关）只按直接 manifest 依赖匹配。
- AE4. **Covers R8.** 模式 A 同一条 release/issue 聚类/advisory 事件，跨多次轮询周期只产出并推送一次报告（issue 聚类仅在跨升级阈值时追加一次「升级」通知）；模式 B 同一仓在内容未变时不重复进摘要。

## Scope Boundaries

### MVP 范围内
- 模式 A 事件驱动（关注清单 + 手工依赖边 + 自动 npm 包依赖）+ 诊断报告。
- 模式 B 定时跑批（AI 单一范围趋势发现）+ 说明报告。
- 三类信号采集（健康 / 趋势 / 供应链安全）；去重 **L1 hash only**；筛选 **L1 规则 → L3 强模型 judge**。
- 双层存储（目录产物 + SQLite 状态），私有访问受控（R24）。
- channel：RSS（私有 feed，Authorization 头鉴权）+ 邮件（现成邮箱 SMTP）；固定路由 + 分级触达。
- **安全基线 R20-R24**（feed 鉴权 / 凭据管理 / GitHub App 最小权限 / 不可信内容边界 / 产物隔离）。
- 报告含 `source_id + 时间戳`；权威一手单源可直接出结论（R18）。
- 单常驻服务 + 内部分级定时。

### Deferred for later（要做，但不在 MVP）
- **L2 语义层**：embedding 去重（R8）+ embedding rerank（R9）——MVP 后第一项增强。
- **报告深挖三段式** `综合→批判→修订`（R11）；**引用可达性自动验证脚本**（R18）。
- **channel 接口抽象**（加入第三个 channel 时引入，R14）；**一键晋升**（F4）；**按信号订阅配置**（R16）。
- 完整 **Web 仪表盘**（目标主场形态）。
- 更多 channel：Slack / 企业微信 / Webhook；第三方发信 API。
- 包依赖解析扩展到 PyPI/Go 等更多生态。
- **下游依赖反向图**（谁依赖了某仓库）——GitHub 无 dependents API，需经 deps.dev 按包名映射，复杂度高。
- 趋势范围扩展到 AI 之外的多个领域；可配置的范围定义。
- Socket.dev 行为级投毒检测（付费/CI 集成）作为差异化安全信号层。
- 投毒/中断事件的自动处置动作（如自动开 issue、通知 owner）。

### Outside this product's identity（明确不做）
- **自动升级/修复 PR**：Dependabot / Renovate 已覆盖，本系统定位是「情报观测」不是「依赖管理执行器」。
- **SCA 门禁 / 自动修复 PR**：Snyk / Dependabot 已覆盖「在 CI 卡住构建、自动提升级 PR」这类**执行动作**。本系统对清单仓（含自有仓）依赖确实会查 CVE/GHSA，但只做**漏洞情报呈现**（诊断报告 + 告警），不做门禁拦截、不开修复 PR——边界是「呈现 vs 执行」，不是数据范围。
- 通用 RSS 阅读器 / 通用监控告警平台。

## Dependencies / Assumptions

- **假设（待确认）**：MVP 趋势范围先锁「AI 方向」；关注清单先用一份手工维护的仓库 + 手工依赖边。两者均可后续配置化。
- **假设**：团队可接受分钟级（轮询/Atom）延迟，无需秒级实时。
- **假设**：运行时服务链（如 上游 CLI 工具→内部网关）由用户手工声明；系统不自动发现非包依赖的拓扑。
- **base-rate 假设（F1 命中率的命门，待验证）**：F1 价值取决于「依赖中断事件中、上游 OSS 仓库有可观测 issue/release 信号」的占比。504 那次落在此类，但这是**单一轶事**——若多数真实中断是私有部署/网络/配额类（上游无信号），F1 会大量空转或极少触发。planning/试运行需估这个 base-rate 并据此设 F1 的预期命中率，不能用一次轶事自证。
- **外部依赖稳定性风险**：OSS Insight（PingCAP/TiDB Cloud 运营，beta）与 ecosyste.ms 为较新服务，长期可用性需兜底（star 快照差值作为 trending 的降级方案）。
- **GitHub 速率**：未认证 60 req/h 且 2025-05 起进一步收紧；MVP 应注册一个 GitHub App / PAT 获得 5,000 req/h，并以 Atom feed 承担大部分近实时订阅以省配额。
- **issue 是配额瓶颈（模式 A 招牌场景的硬约束）**：release/tag/commit 有免费无限 Atom，但 **issue 无通用 Atom feed，只能 REST `/issues?since=` 轮询**，吃 5,000/h 配额——而 F1（504 诊断）恰恰靠 issue 激增。后果：(a) 关注清单规模与 issue 轮询频率受配额上限约束，清单越大轮询越稀；(b) R9 的每仓基线需要先攒一段历史 issue 速率，存在冷启动期。MVP 应据此设**关注清单规模上限**并把 issue 轮询频率作为可调参（高优先仓更勤）。
- **邮箱 SMTP 限额**：现成邮箱（Gmail ~500 封/天等）有日发送上限，MVP 量级足够；超量再上第三方 API。
- **LLM 能力**：报告质量依赖 LLM 对聚合上下文的诊断能力，需在 MVP 验证报告可用性（见 Success Criteria）。L3 judge 的误报压制效果**依赖强模型档位**（实测弱模型提升不稳，见 Sources），不可为省钱降档。
- **新增依赖（embedding）**：R8 L2 语义去重与 R9 L2 rerank 需要一个 embedding 能力（自托管或 API）；MVP 若想最省，可先只上 L1 hash 去重 + L1/L3 筛选，把 L2 embedding 列为第一项增强。

## Success Criteria

- **核心假设验证（前瞻，非复现轶事）**：在试运行窗口内，系统至少**抢在团队手工发现之前**推送一次**此前不知道的**依赖中断/风险事件的可用诊断（而非仅复现 504 这个种子轶事——复现已知历史事件只证明能回放，不证明能抓未来未知）。复现 504 可作为冒烟测试，但不是核心假设的达标线。
- **报告可用性**：诊断/说明报告达到「不必再人工补查即可决策」的水平（团队主观评定，抽样合格率作为指标）。
- **零漏报优先于零误报（安全类）**：R7 供应链高危公告命中观测依赖时不漏推；可容忍一定误报。R18 的多源门控**不得**拦截一手权威单源安全信号（见 R18 豁免）。
- **误报可控（健康类）**：R9 基线 + LLM 二次确认应把 issue 激增误报压到团队可接受水平（不被噪声淹没）。
- **成本可控（含 LLM）**：MVP 在免费数据源 API 配额内稳定运行，无人工干预可连续运行 ≥1 周。**「成本接近零」仅指数据源 API**；L3 judge 用强模型有真实推理花费，须单列 LLM 预算（预期调用量 × 单价）并纳入此项——日上限是限流手段，本质是在「零漏报」与「成本」间取舍，需明确取向。

## Sources / Research

外部数据源调研结论（驱动上述架构决策）。所有源在 MVP 组合中成本接近零。

### 信号采集源

| 信号 | 主源 | 辅源 | 关键限制 |
|---|---|---|---|
| 依赖健康（release/issue） | `github.com/{o}/{r}/releases.atom`（免费无限额，分钟级） | GitHub REST `/releases`、`/issues?since=`（GitHub App 5,000 req/h） | **不能对非自有仓库建 webhook**；纯拉取；pre-release 不进 atom，需 REST `?prerelease=true` |
| star 趋势 | OSS Insight `/v1/trends/repos/`（免费，600 req/h，含历史时序） | 每日快照 GitHub `stargazers_count` 差值 | GitHub 本身不给 star 时序；Search API 结果上限 1,000；OSS Insight 为 beta，稳定性需兜底；trending 按语言切榜，topic 需对候选补查 |
| 供应链安全 | OSV.dev `POST /v1/querybatch`（免费、无限额、多生态，批量≤1,000） | GHSA `/advisories?type=malware` + `advisories.atom` 快速感知 | OSV 2025-08 曾误隐藏部分有效漏洞（已修） |
| 依赖图（上游） | deps.dev API（免费，Google，7 大生态，含传递依赖+advisory+license） | 直接 parse 仓库 manifest（MVP npm `package.json`）；GitHub SBOM API `/dependency-graph/sbom`（仅默认分支静态 manifest） | libraries.io 限额极低（60/min）不推荐规模化；未发包仓库需走 manifest parse 而非 deps.dev |
| 依赖图（下游） | deps.dev `DependentVersions`（按包名，需 GitHub 仓库↔包名映射） | ecosyste.ms/packages | **GitHub 无 dependents API**；故下游反向图列为 deferred |
| 官方通告 | `releases.atom` / `tags.atom` / `commits/{branch}.atom`（监控 CHANGELOG） | 官方 blog RSS、GitHub Discussions（需认证轮询）、`github.blog/changelog/feed/` | blog feed URL 需人工发现 |

### 同类产品覆盖与差异化

| 产品 | 覆盖 | 缺口 |
|---|---|---|
| Dependabot / Renovate | 自有仓库 CVE 告警 + 升级 PR | 监控外部仓库、star 趋势、投毒检测 |
| Snyk | SCA + 漏洞 + SBOM | 趋势情报、外部生态监控 |
| Socket.dev | 行为级投毒检测（npm/PyPI 最深） | 仓库活跃度/趋势；API 付费（$25+/seat） |
| newreleases.io | 多平台 release 通知 | 安全分析、趋势、依赖图 |
| OSS Insight | star 趋势 / trending | 安全告警、依赖图、issue 监控 |
| deps.dev | 依赖图、advisory、license | star 趋势、issue 监控、投毒检测 |

**差异化结论**：无任何单一现有产品同时覆盖「任意第三方仓库的 issue/release 健康监控 + star 增速情报 + 跨生态供应链安全聚合」三者。现有方案均以「保护自己代码库」为出发点；transmutary 的独特定位是「情报监控外部生态」，且把「重点仓库事件驱动诊断」与「广域趋势发现」合于一套。这正是自建价值所在。

### MVP 最省力数据源组合
- 模式 A 健康：`releases.atom`（主）+ GitHub App 轮询 REST（辅），纯拉取无 webhook。
- 模式 A 安全：OSV.dev querybatch（主）+ GHSA malware atom（快速感知）。
- 模式 A 上游依赖：manifest parse（npm，主）+ deps.dev（已发包仓库辅）。
- 模式 B 趋势：OSS Insight trending（主）+ 每日 star 快照差值（兜底）。
- 工程量集中在**调度层**（轮询间隔、增量去重、多源聚合）与**送达层**，而非数据获取。

### 流水线方法论参考（开源 deep-research skill 生态 + RAG 文献）

调研 skills.sh / GitHub 高热 deep-research skill 与相关文献，提炼出一条收敛的事实标准骨架，已并入 Key Decisions 与 R8/R9/R11/R17-R19：

| 阶段 | 借鉴模式 | 来源 |
|---|---|---|
| 收集 | 按 source type 分桶、不同 cadence（事件流/周期拉取/深爬），并行 fan-out | deep-research-skill（199-biotechnologies / ravmike / standardhuman）、ZenML 新闻管道、Elastic Labs |
| 清洗 | 结构化检查先于 LLM；chunk 级相关性过滤而非整文透传 | ChunkRAG (arXiv 2410.19572)、daymade/claude-code-skills V6 |
| 去重 | L1 hash seen-set + L2 embedding 相似度 + 引用链归并到 primary source | 「5 篇引用 1 篇 = 1 个独立源」(standardhuman)、ZenML、大规模文档去重实践 |
| 筛选 | 多级漏斗（bi-encoder → cross-encoder → LLM judge），LLM judge 需强模型 + JudgeRank 三步 | SkillFlow (arXiv 2504.06188)、JudgeRank (2411.00142)、Meta-Judge (2504.17087)、误报过滤实测 (2601.22952) |
| 报告 | 批量轻摘要 + 阈值触发深挖；`综合 → critique → refine` 三段；引用即时落盘 | deep-research-skill critique 阶段、validate/verify_citations 脚本 |
| 反模式 | 平铺 ingestion、单源确认、无阈值 LLM、丢溯源链、API 走推理模型（放大 tool hallucination） | SkillFlow、The Reasoning Trap (arXiv 2510.22977) |

**关键取舍**：误报过滤实测（arXiv 2601.22952）显示 agentic judge 把误报率从 >92% 压到 6.3%，但**仅对强模型有效**——故 R9 的 L3 judge 明确要求强模型档位，省钱靠「漏斗前两层无 LLM + L3 日总量上限」而非降模型档。

## Outstanding Questions

### Resolved（2026-05-29，已并入 Key Decisions / Requirements）
- ~~趋势范围定义~~ → MVP 锁 AI 单一范围，过滤器用 `topic + 关键词 OR 兜底`，trending 按语言取榜后补查 topic（见 R4、F2）。
- ~~观测清单初始内容与结构~~ → 清单 = 用户指定仓库不分归属 + 手工依赖边 + 自动 npm 包依赖；产物按仓库名建目录（见 R2/R3/R13）。
- ~~邮件发送方式~~ → 现成邮箱 SMTP 经客户端库（免费），第三方 API 可选升级，RSS 为必做私有主 channel（见 R14）。
- ~~两模式 vs 统一引擎~~ → 定为两模式（A 事件驱动 / B 定时跑批）共享报告/投递（见 R1、Key Decisions）。
- ~~依赖解析源头（运行时服务链 vs 包依赖）~~ → 包依赖自动解析、服务链手工声明边、跨仓关联交 LLM（见 R3、Key Decisions）。
- ~~去重粒度~~ → 模式 A 事件指纹、模式 B 产物差分（见 R8）。
- ~~存储载体~~ → 目录产物 + SQLite 状态双层（见 R13）。
- ~~RSS 隐私~~ → 私有带 token feed，不公开索引（见 R14）。
- ~~部署形态~~ → 单常驻服务 + 内部分级定时（见 Key Decisions）。

### Resolve Before Planning（teams 评审新增）
- **F1 base-rate**：依赖中断中「上游有可观测信号」的占比需估（决定 F1 是招牌还是边角，见 Dependencies）。试运行前定一个可接受命中率门槛。
- **模式 B 证据缺口**：痛点#2（技术情报缺渠道）无具体代价锚点（不像 504 / 投毒有真实事件）。是否补一个具体损失（错过的工具/滞后的决策），还是接受其为弱证据需求与 A 同期建？（已决同期，此处仅记录缺口待补证。）
- **L2/三段式/抽象/晋升/订阅配置 的启用判据**：各延后项以什么实测信号触发上线（如 L1+L3 实测误报率超 X% 两周 → 上 L2）。

### Deferred to Planning
- 轮询调度的具体周期数值与分级（各源频率）。
- 安全落地：secret store 选型、RSS token 轮换/撤销机制、GitHub App 仓库 allowlist 范围、报告仓与 SQLite 的访问控制与加密方案（R20-R24 的实现）。
- 各源 URL 抓取 allowlist 与重定向策略（R23）。
- 事件指纹 / star 快照 / 基线表的具体 SQLite schema。
- issue 激增的基线倍数、绝对下限、滚动窗时长的初始取值（MVP 可调参）。
- LLM 调用的具体模型选择与提示工程（可在 MVP 迭代）。
- 关注清单 / 依赖边 / 订阅配置的文件格式。
- 关注清单规模上限的具体数值（受 issue 轮询配额约束，见 Dependencies）。
- 趋势报告「批量摘要 + top 仓单独深挖」的分层策略（避免批量稀释单仓深度）。
- 事件指纹 / star 快照表的保留期（TTL）与归档策略（防表无限增长）。
- L2 去重/rerank 的 embedding 模型选择与相似度阈值；L3 judge 的强模型档位与日调用上限数值。
- 漏斗各层（L1 规则集、L2 阈值、L3 prompt）的初始配置。
