# Plan: 投递层本地化收口 — RSS 单语言 + chrome i18n + 标题本地化

**Type:** feat
**Depth:** Standard
**Date:** 2026-06-04

---

## Context

PR #13 把**邮件**正文改成单语言（`email_lang` 配置驱动），但只覆盖了一半投递面，且留下残余英文 chrome。截图 + 代码核查暴露的完整缺口：

1. **RSS 仍双语堆叠** —— `src/transmutary/deliver/rss.py` 的 `_render_entry` 仍 `body_md + "\n\n## 中文\n\n" + body_md_zh`，与邮件改前同款墙；`Sources:` 英文 + 裸浮点 `fetched {s.fetched_at}`；`fg.language("en")` 硬编码。
2. **邮件 chrome 残英** —— 主题 `[transmutary/high] {title}` / `[transmutary] Daily Digest {date}`（收件箱首见）、`Sources` 标题、`Daily Digest`/概览/`No reports`、严重度徽章 `HIGH/CRITICAL`、`<html lang="en">` 硬编码（zh 邮件文档语言错标，a11y bug）。
3. **标题未本地化** —— 标题只存一个串（无 `title_zh`）。来源三分：security/explain 是**我方模板**（`Supply-chain {kind}`、`Trend:`，可本地化）；diagnose 是**上游外部标题**（不该机翻）；`[待核实信号]` 前缀**硬编码中文**（英文部署也显示中文，潜在 bug）。

**决策（用户确认：一起做、跟看板语言走、复用 `email_lang`）**：投递语言已有三级解析（admin 偏好 > yaml > 默认）+ dashboard 选择器（PR #13）。本轮让 **RSS + 全 chrome + 标题** 一致跟随它。标题加 `title_zh`（像 `body_md_zh`，确定性、无 LLM prompt 改动）：模板出两语、外部标题两边相同、unverified 前缀按语言。

预期结果：email + RSS 两投递面在配置语言下完全本地化、无残英、无双语墙、文档 lang 正确。

---

## Requirements

- R1：RSS entry 正文单语言（跟 `email_lang`），删双语堆叠；`Sources`/`fetched_at` 本地化+格式化；`fg.language()` 跟随。
- R2：邮件 + digest chrome 本地化：主题的人类部分（标题/`Daily Digest` 标签）、`Sources`、概览、`No reports`。**严重度词保持英文**（`HIGH/CRITICAL` 通用标签；主题前缀 `[transmutary/{severity.value}]` 保留机器值供运维过滤，不本地化）。
- R3：`<html lang>`（email/digest）+ RSS `fg.language()` 跟随渲染语言（en→`en`，zh→`zh-CN`）。
- R4：`Report.title_zh` schema 字段；三生成器产出；渲染按语言选标题（`localized_title`，zh 缺失回退）。
- R5：`[待核实信号]` unverified 前缀按语言（en 变体），不再硬编码中文。
- R6：语言常量 + delivery chrome 串集中到**中性核心模块**，dashboard/deliver/config 共用，消除分层倒置与重复 `DEFAULT_LANG`。
- R7：默认（en，未配置）行为不变；外部 diagnose 标题原样保留两语；不引入新依赖。

---

## Key Technical Decisions

**KTD1：新建核心 i18n 模块 `src/transmutary/i18n.py`（最低层，无内部依赖）。** 持有 `SUPPORTED_LANGS`/`DEFAULT_LANG`/`HTML_LANG` + **delivery chrome 串表** `DELIVERY_STRINGS[lang]`（`sources`/`daily_digest`/`digest_overview`/`digest_high_risk`/`no_reports`/`unverified_prefix`/`trend_title`/feed 标题）+ `delivery_strings(lang)` 访问器。**不含严重度标签**（Finding A：保持英文通用值）。`supply_chain_title` 模板**不放串表**——security.py 已有内联 `kind_label_zh`，直接复用（Finding C）。
- `dashboard/i18n.py`：从核心 import 常量（删重复定义），保留其 UI `MESSAGES`/`LANG_COOKIE`/`resolve_lang`/`messages_for`。
- `config.py`：`SUPPORTED_EMAIL_LANGS`/`DEFAULT_EMAIL_LANG` 改为引用核心常量（删重复）。
- `deliver/render_email.py`/`digest.py`/`rss.py`：删 `_DEFAULT_LANG` 字面，引核心。
- 依据：核心 i18n 无 starlette/无内部依赖 → deliver/config/report/dashboard 都可向下引，彻底解决之前刻意规避的 deliver→dashboard 倒置。

**KTD2：`title_zh` 镜像 `body_md_zh`。** `Report` 加 `title_zh: str | None = None`；`to_dict`/`from_dict` 带上（向后兼容：`d.get("title_zh")`）。`report/render.py` 加 `localized_title(report, lang) -> str`（zh→`title_zh` 回退 `title`；其它→`title`），与现有 `localized_body` 对称。

**KTD3：生成器双产标题（确定性，无 LLM 改动）。**
- `collect/security.py`：`title` = en 模板，`title_zh` = `f"供应链{kind_label_zh}：{pkg} ({ids})"`，**复用已有内联 `kind_label_zh`**（恶意软件/漏洞），无需新映射。
- `report/explain.py`：`title="Trend: {repo}"` / `title_zh="趋势：{repo}"`；unverified 时两边各加本地化前缀。
- `report/diagnose.py`：`title=ctx.title`（外部上游标题，不翻）。**`title_zh` 仅 gated 时设**（=zh 前缀 + ctx.title）；非 gated → `title_zh=None`，`localized_title` 回退 `title`（Finding B，避免冗余）。`title` 的 gated 前缀用 en 变体 `unverified_prefix["en"]`。
- 严重度徽章/chrome 在**渲染时**按 lang 取串（非生成时），故只有标题需要 `title_zh`；严重度词不本地化（Finding A）。

**KTD4：渲染器按 lang 取 chrome + 标题 + lang 属性。**
- `render_email.py`：`localized_title`；`Sources`→串表；`<html lang="{html_lang}">`；**严重度徽章保持英文**（不改）。
- `digest.py`：`Daily Digest`/概览/`No reports`→串表；`localized_title`；`<html lang>`；严重度不改。
- `deliver/email.py:_build_message`：主题 = `[transmutary/{severity.value}] {localized_title}` —— **保留机器化 severity 前缀**，仅本地化标题部分（已有 `lang` 参数）。
- `pipeline.py:run_daily_digest`：digest 主题 = `[transmutary] {daily_digest_label[lang]} {date}`（已有 `lang`）。
- `<html lang>` 修复自动惠及 `.html` 产物（PR #13 已让 `ArtifactStore` 跟随 `render_lang`）。

**KTD5：RSS 跟随（rss.py + 线程）。** `render_feed`/`render_single` 加 `lang` 参数；`_render_entry(fg, report, lang)` 用 `localized_body`（单语言）+ `localized_title` + `Sources`/`fmt_timestamp(fetched_at)` 本地化 + `fg.language(HTML_LANG[lang])` + feed 标题串表。调用方传 `outbound.email_lang`：`stub._deliver_outbound`（immediate/per-report digest）、`pipeline.run_daily_digest`（digest 批）。

---

## Implementation Units

### U1. 核心 i18n 模块 + 常量去重
**Goal:** 中性 i18n 核心模块承载语言常量 + delivery chrome 串，消除分层倒置与重复定义。
**Requirements:** R6
**Dependencies:** 无
**Files:** `src/transmutary/i18n.py`(new)、`src/transmutary/dashboard/i18n.py`、`src/transmutary/config.py`、`tests/test_i18n.py`(new)
**Approach:** KTD1。核心模块持常量 + `DELIVERY_STRINGS`/`delivery_strings(lang)`。dashboard/config 改引核心，删重复常量（保持现有公开名可用，必要时 re-export）。
**Test scenarios:**
- `delivery_strings("zh")["sources"]` == 中文；`("en")` == "Sources"。
- 非法 lang → 回退 DEFAULT_LANG 串集。
- `SUPPORTED_LANGS`/`DEFAULT_LANG` 单一来源（dashboard.i18n 与 config 引用同对象/值）。
- 回归：`dashboard.i18n.resolve_lang`/`messages_for` 行为不变。

### U2. `title_zh` schema + `localized_title`
**Goal:** 报告承载双标题，渲染层可按语言选取。
**Requirements:** R4
**Dependencies:** 无
**Files:** `src/transmutary/report/schema.py`、`src/transmutary/report/render.py`、`tests/report/test_render.py`、`tests/store/test_artifacts.py`
**Approach:** KTD2。
**Test scenarios:**
- `Report(title_zh=...)` round-trip `to_dict`/`from_dict`；旧 dict 无 `title_zh` → `None`（向后兼容）。
- `localized_title(r,"zh")` 用 title_zh；缺失回退 title；`"en"` 用 title。
- json sidecar 含 `title_zh`。

### U3. 生成器双产标题
**Goal:** 三生成器在生成时填 `title` + `title_zh`，修硬编码中文前缀。
**Requirements:** R4, R5, R7
**Dependencies:** U1, U2
**Files:** `src/transmutary/collect/security.py`、`src/transmutary/report/explain.py`、`src/transmutary/report/diagnose.py`、对应测试
**Approach:** KTD3。模板两语、外部标题两边同、unverified 前缀本地化。
**Test scenarios:**
- security：`title` 英文模板、`title_zh` 中文模板，kind 标签正确。
- explain：`Trend:`/`趋势：`；reaccelerated/unverified 时前缀两语。
- diagnose 非 gated：`title`==ctx.title，`title_zh`==None（`localized_title` 回退）。
- diagnose gated：`title` 带 en unverified 前缀，`title_zh` 带 zh 前缀，二者尾部均为 ctx.title（外部不翻）。
- 回归：现有 body 双语解析不受影响。

### U4. 邮件渲染 chrome + 标题 + lang 属性
**Goal:** immediate 邮件在配置语言下全本地化。
**Requirements:** R2, R3, R4
**Dependencies:** U1, U2
**Files:** `src/transmutary/deliver/render_email.py`、`src/transmutary/deliver/email.py`、`tests/deliver/test_email_html.py`
**Approach:** KTD4。`Sources`/严重度/`<html lang>`/标题按 lang；主题本地化。
**Test scenarios:**
- `lang="zh"`：HTML `<html lang="zh-CN">`、`Sources` 中文、标题用 title_zh；**严重度徽章仍 `HIGH`（英文，未本地化）**。
- `lang="en"`：`<html lang="en">`、英文 chrome。
- 主题：`_build_message(lang="zh")` 主题 = `[transmutary/{sev}] {中文标题}`，severity 前缀仍机器值；英文默认不变。
- XSS 回归：标题 `<script>` 仍转义。

### U5. digest 渲染 chrome + 标题 + lang
**Goal:** digest 总结邮件全本地化。
**Requirements:** R2, R3, R4
**Dependencies:** U1, U2
**Files:** `src/transmutary/deliver/digest.py`、`src/transmutary/pipeline.py`、`tests/deliver/test_digest.py`、`tests/test_daily_digest.py`
**Approach:** KTD4。`Daily Digest`/概览/`No reports`/严重度/标题/`<html lang>` 按 lang；digest 邮件主题本地化。
**Test scenarios:**
- `lang="zh"`：h1/概览/标题中文，`<html lang="zh-CN">`；严重度徽章仍英文。
- 空窗 `No reports` 中文。
- e2e：`run_daily_digest` 配 `email_lang="zh"` → 主题 `[transmutary] {中文 Daily Digest 标签} {date}` + HTML 中文。

### U6. RSS 单语言 + chrome + lang + fetched_at
**Goal:** RSS 两 feed 在配置语言下单语言全本地化。
**Requirements:** R1, R3, R4
**Dependencies:** U1, U2
**Files:** `src/transmutary/deliver/rss.py`、`src/transmutary/deliver/stub.py`、`src/transmutary/pipeline.py`、`tests/deliver/`（rss + outbound）
**Approach:** KTD5。
**Test scenarios:**
- `render_single(report, lang="zh")`：entry 仅中文正文（无英文 body、无 `## 中文` 双堆）、标题 title_zh、`Sources` 中文、`fetched_at` 格式化非裸浮点、feed `xml:lang`/`fg.language` = zh-CN。
- `lang="en"` 默认：英文、单语言（不再双堆）。
- 线程：`OutboundDelivery.email_lang="zh"` → `_deliver_outbound` 写的 feed 中文；`run_daily_digest` digest feed 跟随。
- feed 标题本地化。

---

## Scope Boundaries

**In scope:** 核心 i18n 模块 + 去重、`title_zh` schema + 生成器、email/digest/RSS chrome+标题+lang 本地化、RSS 单语言+fetched_at、unverified 前缀本地化。

**Out of scope:** 外部 diagnose 标题机翻（外部内容，原样）；per-recipient 不同语言；digest feed「单条覆写 flicker」（独立问题）；邮件富排版重设计；dashboard UI `MESSAGES` 整体迁移（只下沉常量）。

**Deferred to Follow-Up Work:** secrets 表来源列 provenance；promote 写能力 UI。

---

## Verification

1. `python -m pytest tests/ -q`（移开本地真实 `config/` 避污染）全绿；`ruff check .` 通过。
2. headless 渲染截图对照：`email_lang=zh` 的 immediate + digest + **RSS** → 全中文、无双语墙、无残英 chrome、文档 lang 正确；`en` 对照。
3. 手测：dashboard 投递设置选 zh → demo/serve 发信 + 生成 RSS → 三面一致中文。
4. 掩码核查：渲染/日志不泄露凭据（遵守 never-print-secrets）。
