# Plan: Release 自动化 — 精选 release-note 缺文件时优雅降级

**Type:** fix
**Depth:** Lightweight
**Date:** 2026-06-04

---

## Context

Release workflow 自 **v0.14.0 起每次全红**，且 **GHCR Docker 镜像（含 `latest`）从未推送**，卡在 v0.13.0。

根因（已查清）：`docs/release-notes/<tag>.md` 是**手写**双语精选 notes，CI 步骤「Apply curated bilingual release notes」先跑 `tools/release_notes.py check <tag>`（缺文件即 `exit 1`）再 `gh release edit`。本会话快速连发 ~10 个 PR，semantic-release 自动升版到 v0.29.0，但**没补对应手写文件** → `check` 失败 → 该步骤失败。该步骤位于 **Docker 构建/推送步骤之前**，job 中止 → Docker 5 步全被跳过（run trace 实证：`Apply curated…` ✗、Docker 步骤 `-`）。

GitHub Release 本身仍由 semantic-release 先建（带默认自动 notes，非模版），故 release「存在但没遵循双语模版」。

设计哲学冲突：`release_notes.py` 注释「每个发布 tag 都需双语 note」是**硬门**，与快速自动发版节奏不兼容——硬门挡住后续（含 Docker）。

**目标**：缺 note 文件 → 保留 semantic-release 自动 notes + 警告，**不 fail、不挡 Docker**；文件存在但双语格式坏/含占位符 → **仍 fail**（保质量门）；Docker 推送不再被 notes 步骤阻断。

---

## Requirements

- R1：CI release 在 `docs/release-notes/<tag>.md` **缺失**时不失败，保留 semantic-release 自动 notes（不调 `gh release edit`），打印警告。
- R2：文件**存在但无效**（双语校验失败 / 残留占位符）时 **仍以非零退出失败**，保留质量门。
- R3：文件**存在且有效**时照常 `gh release edit` 应用精选 notes。
- R4：Docker 构建/推送步骤不再被 notes 步骤的缺文件失败阻断——release 推 v0.14.0+ 应能产出 GHCR 镜像。
- R5：保留 `check` / `prepare` 现有本地用法语义（不破坏开发者手动校验/生成）。

---

## Key Technical Decisions

**KTD1：`check` 加 `--allow-missing` 标志区分「缺失」与「无效」。** 当前 `check()` 对缺失和无效都返回 failures → `_cmd_check` 一律 `exit 1`，无法区分。加 `--allow-missing`：缺文件 → 打印警告到 stderr、**exit 0**；文件存在但校验失败/占位符 → 仍 **exit 1**；有效 → exit 0。默认（无标志）保持现状严格语义（R5）。
- 依据：单一标志即可表达 CI 需要的「缺失可容忍、无效不可容忍」三态，无需新子命令或把 `gh` 塞进 Python。

**KTD2：workflow notes 步骤守 `gh release edit` 于文件存在。** 改为先 `check --allow-missing`（缺→0 不挡、坏→1 挡），再**仅当文件存在**时 `gh release edit --notes-file`。缺失时该步骤整体 exit 0 → 后续 Docker 步骤照常运行（R1/R4）。
- Docker 无需 reorder：notes 步骤不再在缺失时失败，Docker 自然解封。（reorder 为备选，已否决——它能让 Docker 跑但 workflow 仍会因缺文件变红，丢失「红=真问题」信号。）

**KTD3：缺失走警告非静默。** 缺文件时 stderr 打印明确警告（含 tag + 期望路径 + 「保留自动 notes」），让红→绿后仍有可见线索提示「这个 release 没精选 notes」（no-silent-cap 原则）。

---

## Implementation Units

### U1. `check --allow-missing` 三态退出
**Goal:** `check` 能区分缺失（可容忍）与无效（不可容忍），供 CI 优雅降级。
**Requirements:** R1, R2, R3, R5
**Files:** `tools/release_notes.py`、`tests/test_release_notes.py`
**Approach:** KTD1/KTD3。`_cmd_check` 加 `--allow-missing`；缺失分支（`check()` 返回的 "missing release-note file" 信号）在该标志下打印警告、exit 0；其余失败（双语/占位符）仍 exit 1。区分「缺失」与「无效」可让 `check()` 返回结构带一个 missing 标记，或 `_cmd_check` 复用 `note_path_for(tag).exists()` 判定。默认无标志行为不变。
**Test scenarios:**
- 缺文件 + `--allow-missing` → exit 0，stderr 含警告（含 tag/路径）。
- 缺文件 + 无标志（默认）→ exit 1（回归：现有严格语义不变）。
- 文件存在但缺一种语言/校验失败 + `--allow-missing` → 仍 exit 1（质量门）。
- 文件存在含占位符 + `--allow-missing` → 仍 exit 1。
- 文件存在且有效（+/− 标志）→ exit 0，打印 OK。

### U2. release.yml notes 步骤优雅降级 + 解封 Docker
**Goal:** CI 缺 note 不再 fail、不再挡 Docker。
**Requirements:** R1, R4
**Files:** `.github/workflows/release.yml`
**Approach:** KTD2。「Apply curated bilingual release notes」步骤改为 `release_notes.py check "$RELEASE_TAG" --allow-missing`，随后仅当 `docs/release-notes/$RELEASE_TAG.md` 存在时执行 `gh release edit`。Docker 步骤位置不变（now 自然解封）。
**Test scenarios:** `Test expectation: none -- CI workflow YAML；无单测框架，靠下一次真实 release 运行验证（见 Verification）。` 评审点：缺文件时步骤 exit 0、文件存在有效时 `gh release edit` 仍执行、坏文件时步骤失败。

---

## Scope Boundaries

**In scope:** `check --allow-missing` 三态 + release.yml notes 步骤降级 + Docker 解封；对应 `tools/release_notes.py` 单测。

**Out of scope:** 全量 backfill v0.14.0–v0.29.0 共 ~15 个手写双语 note（用户明确排除；自动 notes 已够读）。

**Deferred to Follow-Up Work:**
- 一次性手动构建并推送当前版本（v0.29.0）的 GHCR `latest` 镜像，让 README 的 `docker pull …:latest` 恢复（手工运维操作，非本 PR 代码）。
- 若日后想要每个 release 都有精选 notes：在合并 PR 时把 `prepare` 生成的 note 一并提交（流程约定，非本修复）。

---

## Verification

1. `python -m pytest tests/test_release_notes.py -q` 全绿；`ruff check src tests tools` 通过（release.yml 的 verify job 同款门）。
2. 手验三态：
   - `python tools/release_notes.py check v9.9.9 --allow-missing` → exit 0 + 警告（无此文件）。
   - `python tools/release_notes.py check v9.9.9` → exit 1（默认严格）。
   - 造一个缺中文段的临时 note → `check <tag> --allow-missing` → exit 1。
3. 真实验证（合并后下一次 release）：release workflow **转绿**，且即便无对应 `docs/release-notes/<tag>.md`，**Docker 步骤运行**、GHCR 推出新版本镜像。
4. 既有 release（v0.14–v0.29）的红/缺 Docker 是历史遗留，本修复只保证**今后**绿 + Docker 推送；历史不追溯（backfill 出范围）。
