﻿---
description: "Core 转写可读性优化任务清单（固定 7 章 + 空章中性固定文案 + 层级归组 + gap 降级 + 折叠区/参考文献修复 + 来源标注去斜体，改 core/、web/tests/ 与 reader.css 一处样式）"
---

# Tasks：Core 转写可读性优化

**输入**：`specs/005-core-readability/spec.md`

**前置**：spec.md（必需）。Spec 001/002/003 已闭环。

**测试策略**：纯离线。单元测试直接对 `build_plan` / `normalize_content_list` / `build_fold_sections` / pipeline 用构造文档与真实 fixture 断言；
端到端复用 `web/fixtures/fake_run` 与新增 survey fixture（取自任务 `2d26f6b9` 的 parsed 产物），fake LLM 模式跑 `transform_paper`。
最后由用户用真实 DeepSeek 重跑 `2d26f6b9` 做人工验收（任务清单外）。

**硬红线**：
- 只改 `core/`、`web/tests/`，以及 `webui/src/styles/reader.css` 一处纯新增 `.src-note` 样式（不动 `web/app/`、`webui/` 其他文件）。
- 成品**恒为 7 章**，章标题、编号（`## 0 ·` … `## 6 ·`）、slug 顺序完全不变；`PaperResult.sections` 长度恒为 7。
- plan.json 只新增可选字段（`empty` / `empty_slugs` 等），不删除或重命名既有字段；fake 模式必须全程可跑。
- 不引入 LLM 规划/空章文案生成，不新增重量级依赖（标准库 / 已有依赖）。
- 不删既有测试以求通过；fixture 变化时同步更新断言。

**格式**：`[ID] [P?] 描述`。`[P]` = 与同批其他任务文件不重叠、可并行。

---

## Phase 1：测试地基与 survey 回归夹具（先红后绿）

**目的**：把"问题论文"固化成离线夹具，先写出暴露现有 bug 的测试，后续每改一处都有回归保护。

- [x] T001 新建 `web/fixtures/survey_run/`：从 `runs/922d7511-c2dd-4b92-ae3d-917f6dfabe06/2d26f6b9-dd4c-41fe-befd-f31b93914ad8/parsed/`
  拷贝 `*_content_list.json`（重命名为 `content_list.json`）与 `full.md`、`images/` 整目录；
  放一个任意有效小 PDF 作为 `source.pdf`（可复用 `web/fixtures/fake_run/source.pdf` 的副本）。
  夹具只入库必要文件，不得拷贝 `_origin.pdf` 与 `_model.json`/`layout.json`。

- [x] T002 新建 `web/tests/test_core_survey_regression.py`，先用现有代码跑出**红测试**固化 4 个事实（允许断言当前错误行为后在后续任务翻转，或直接 xfail 标注）：
  (a) `build_plan(norm)` 对 survey 的 plan 中 related/wrapup 的 `source_section_ids` 非空但其实质正文块数为 0（即按 FR-001 应判 empty）；
  (b) §2.1（sec-2-1）被分到与 §2（sec-2）不同的 slug；
  (c) `transform_paper(fixture, fake=True)` 产出的 markdown 含 `blk-0` 字样、含"本次分配到的原文块为空"类空章文本（应被固定文案取代）；
  (d) 折叠区 HTML 在 `</t`/`</tt` 处截断（断言不含被截断尾串、应含 `</table>`，此条先红）。
  数据加载方式仿照 `test_core_cancel.py` 对 fixture 的用法。

- [x] T003 [P] 新建 `web/tests/test_planner_grouping.py`：用手工构造的最小 norm dict（sections+blocks）
  覆盖三类文档：①六类小节齐全的标准论文；②survey（无 related_work、无 experiments，顶级方法组多个）；
  ③短篇（仅 abstract+intro+conclusion）。先写对**期望行为**的断言：
  target_order 恒为 7 项且每项含 `empty` bool、plan 顶层含 `empty_slugs`；层级归组正确；
  多章可同时 empty；标准论文七章 `empty` 全为 false。当前实现下应为红。

---

## Phase 2：层级归组 + 空章判定与固定文案（planner 结构修复，US1）

**目的**：消灭章节错位根因，并让 planner 明确告诉 pipeline"哪几章没有实质内容"。本阶段只动 planner/normalize，pipeline 在 Phase 3 接。

- [x] T004 在 `core/normalize.py` 的 `_classify_kind` 增补关键词（向后兼容，仅新增归类）：
  含 `limitation`、`future work`、`discussion` → 归 `conclusion`；`background`/`related`/`survey`/`overview`
  在标题语境下归 `related_work`（注意 `survey on ...` 这种论文总标题不得误判：仅对 section heading 生效，
  显式排除 text_level≤1 的文档 title 块）。更新/新增 `web/tests` 中对 `_classify_kind` 的覆盖（可并入 T003 文件）。

- [x] T005 重写 `core/planner.py` 的未分类节分配逻辑（替换现 L116-140 的"全局顺序瓜分"）：
  - 先按 section 编号层级把 unclassified sections 聚成**层级组**：以编号最靠前的顶级节（如 `2`）为锚，
    其所有后代节（`2.1`/`2.1.3` 等）强制同组（用编号前缀或 sections 顺序+level 嵌套判定）；
  - 整组一次性判 slug：锚点标题或组内块命中 experiment/evaluation/result/数据集对比/指标词 → `experiments`，
    否则 `method`；全文无任何真实评测内容时不得生成 experiments（FR-004），experiments 留空走 Phase 3 降级；
  - appendix/supplementary 节维持不占主线：其块默认 fold，不因此让 wrapup 变成非空。
  `_find_parent_slug` 被层级组逻辑取代后删除。保持 `build_plan` 入参/返回既有顶层字段不变。

- [x] T006 空章判定与固定文案常量：
  - 在 `core/planner.py` 为 `target_order` 每项新增 `"empty": bool`（FR-001）：
    实质内容 = 该 slug 来源块中存在 ≥1 个非 heading、非 parse_gap、非 fold/drop 的正文块（paragraph/equation/figure）；
  - plan 顶层新增 `"empty_slugs": [...]`（有序、仅 empty=true 的 slug）；旧字段不删（FR-024）；
  - 在 Core 中新增固定文案常量（就近放 `core/planner.py` 导出 `EMPTY_FALLBACK: dict[slug, str]`，pipeline 直接 import，
    不新建多余模块）：为全部 7 个 slug 各预写**一句**面向读者的中文中性说明，正常正文口吻、无警告样式。
    示例：related → "本文未设独立的相关工作章节，相关背景与前人方法已随正文介绍。"；
    experiments → "本文以综述/方法阐述为主，未包含独立的实验评测章节。"；其余 5 句同风格实现时给出。
    全部文案严禁包含 `blk`、`content_list`、`parse`、`slug`、`fold`、`重新解析`、`未编造`、`输入`（FR-010）。

- [x] T007 翻转 T002/T003 中的 planner 断言为绿色：survey 下 §2.1 与 §2 同 slug；related（及任何无实质内容章）
  `empty=true` 且出现在 `empty_slugs`；标准论文七章 `empty` 全 false、不丢任何原有主线内容
  （对比启用前后各 slug 的实质块集合，允许的变化仅是"真正为空的章被标 empty"）。
  `pytest web/tests/test_planner_grouping.py web/tests/test_core_survey_regression.py` 全绿。

---

## Phase 3：恒 7 章拼装 + 空章固定文案降级（pipeline，US1）

**目的**：空章零 LLM 成本、体面收场；成品结构恒定。本阶段不改任何章标题/编号渲染。

- [x] T008 修改 `core/pipeline.py` rewrite 循环（现 L148-161）：**恒按 `SLUG_ORDER` 遍历 7 章**；
  slug 在 `plan["empty_slugs"]` 中时**不调用** `chat()`、不构造 section prompt、不产生 API 费用，
  `section_contents[slug]` 直接取 `EMPTY_FALLBACK[slug]`；非空章维持现有 prompt+chat。
  rewrite 阶段进度分母**仍为 7**（章数不变），空章快速划过，进度文案 `i+1/7` 语义不变。

- [x] T009 修改 `core/pipeline.py` assemble 侧（现 L182-193）：**无条件输出全部 7 个 `SECTION_HEADINGS`**
  （保持现固定字符串与 `## 0 ·` … `## 6 ·` 编号，不做任何动态重编号），正文统一取 `section_contents[slug]`
  ——空章即其固定文案，结构上与正常章无差异。增加 FR-009 兜底：非空章 LLM 返回 `strip()` 后为空时，
  退回 `EMPTY_FALLBACK[slug]` 并 print 一条日志，不输出空白正文。

- [x] T010 [P] 在 `web/tests/` 新增/扩展测试，fake 模式跑 survey fixture，断言：
  (a) `chat` 对 empty slug 零调用（monkeypatch `core.rewrite.llm.chat` 或 pipeline 模块内 patch 后计数），
  非 empty slug 调用数等于 `7 - len(empty_slugs)`；
  (b) 成品 markdown **恰好 7 个章标题**，编号严格为 0..6、slug 顺序固定；
  (c) 空章正文逐字等于 `EMPTY_FALLBACK[slug]`，不含"原文块为空/请重新解析/未编造"等成段说明；
  (d) 全文（含固定文案）正则无 `blk-\d`、`content_list`、`fold_group`、`target_slug`；
  (e) 标准论文用例（`fake_run` 或 T003 构造文档）七章全部真实内容、`EMPTY_FALLBACK` 文案零出现。

---

## Phase 4：parse_gap 呈现降级（normalize/planner/prompt，US2）

- [x] T011 在 `core/normalize.py`：空 text 条目生成 gap 时，结合 `bbox` 判定疑似图片位
  （宽×高占页面常规栏位比例大、区域尺寸接近整图时）置 `suspected="figure"`（保守规则，误判率优先低，
  拿不准就不加）；其余 gap 维持现状类型。`_build_gap` 支持新参数且不破坏既有调用。

- [x] T012 在 `core/planner.py` 增加 gap 合并视图（不改原始 blocks）：为每个 slug 计算
  `gap_ranges`：把同小节/同页相邻的多个 parse_gap 聚成一条 `{label, page_start, page_end, suspected}`，
  挂到 plan 上供 prompt 使用；block_actions 中被合并的 gap 仍保留但可标记 `merged_into`。
  注意与 T006 衔接：gap 不算实质块，"整章只剩 gap"的 slug 仍判 empty，且该章 prompt 不构造（走固定文案，
  不再罗列 gap，避免一页两条提示）。

- [x] T013 修改 `core/rewrite/prompt_builder.py`：
  (a) 发给 LLM 的块头移除内部 id 明文——把现 L72 的 `[blk-0021]` 换成读者无关心义的序列定位（如 `[块12]`，
  仅保留 type/section/page），从根上杜绝 `blk-` 泄漏；内部 id 映射仅在代码侧需要回填时保留，不进 prompt 文本；
  (b) 主线块中的 parse_gap 不再逐块输出 placeholder，改为在章节块列表的相应位置插入**一条**
  合并提示（来自 T012 的 gap_ranges，含页码范围与"疑似图片"语义）。

- [x] T014 修改 `core/prompts/system.md` 铁律 2 与 `core/prompts/section.md`：
  缺失提示每章至多一次、措辞中性简短（给死例句，如"（原文第 n 页该位置内容未能解析，此处不作补全）"），
  明令禁止出现 blk/content_list/slug/fold 等内部词、禁止命令读者"请重新解析"；
  疑似图片位用专门措辞（"此处原文可能含一张图，解析未提取"）。整章全缺时不出提示（走空章固定文案）。
  保留"不得编造缺失内容"铁律。

- [x] T015 [P] 新增测试：对 survey fixture 生成的 prompt 与 fake 模式成品断言
  (a) prompt 文本与成品均无 `blk-\d`/`content_list`；(b) 同一章 gap 提示 ≤1 条；
  (c) 13 个空 text 条目的成品可见缺失提示总数 ≤ 原始 gap 数的 1/3（SC-004）；
  (d) empty 章只见固定文案、无额外 gap 提示；
  (e) 疑似图位提示措辞正确（若 T011 在该夹具命中）。

---

## Phase 5：折叠区表格、图片与参考文献（assemble，US3/US4/US5）

- [x] T016 修复 `core/assemble/fold_sections.py`：
  L42 删除 table 的 `html[:3000]`/`text[:3000]` 截断，输出完整 HTML；
  L49 其他类型的 `[:2000]` 同步改为不截断（reference_list 本就未截）；
  L41 移除 `**{blk id}**` 标题写法，改为优先 `block.caption`/原文表题，
  无表题时用"表（第 M 页）"中性文案（FR-014/015）。

- [x] T017 修改 `core/planner.py` 的 figure 折叠判据（替换现 L173-188"experiments 章 figure 一律 fold"）：
  按图片自身 caption/block 特征判定——命中 architecture/framework/overview/pipeline/structure/model 等词视为架构图保留 narrate；
  命中 chart/plot/curve/bar/result/accuracy/% 等数值图表词才 fold；拿不准默认 narrate（宁可多留图）。
  table 一律 fold 的现状保留。

- [x] T018 新建 `core/assemble/references.py`（或在 fold_sections 内新增私有函数，≤80 行）：
  对 reference_list 条目逐条编号（保留原文 `[n]`；缺失则从 1 顺序编号）；
  用标准库正则把 `arXiv:\d{4}\.\d{4,5}`、`10\.\d{4,9}/\S+`(DOI)、`https?://\S+` 转成 Markdown 链接；
  OCR 最小清理仅处理高置信白名单：折叠重复空白、移除结尾形如 `Preprint i \d+` 的残片（不命中不动）。
  `build_fold_sections` 的 references 分组改调用该函数。

- [x] T019 [P] 新增/扩展折叠区测试（新文件 `web/tests/test_core_fold_sections.py`）：
  (a) 4376/3078/3082 字符的构造/真实表格输出含完整 `</table>` 且不含 `</t`/`</tt` 截断尾串；
  (b) 折叠标题不含 `blk-`、有 caption 用 caption、无 caption 用"表（第 M 页）"；
  (c) reference 条目 100% 有序号，构造的 arXiv/DOI/URL 样例被转为链接，普通文本不被加链接；
  (d) 构造损坏条目验证"不命中白名单则原样保留"。

- [x] T020 [P] 图片完整性测试与收尾：fake 模式跑 survey fixture 后断言 `PaperResult.dead_links == []`，
  且所有 narrate/fold 的 figure 文件在输出 `images/` 中存在；
  MinerU 仅 1 张 image 的现状下，对丢失图位断言"疑似丢图弱提示"存在而非伪造图片引用（与 T015 呼应）。
  `copy_images` 逻辑预期无需改动，若测试暴露缺口再最小修改 `image_utils.py`。

---

## Phase 6：来源标注降噪与全量回归（US5 + 关门）

- [x] T021 修改 `core/prompts/section.md` 第 5 节"来源标记"（现 L64-67）：由"每段事实性叙述末尾必须标注"
  改为"每个原文小节至多集中标注一次（小节首句处），或在章首统一给出该章覆盖的原文节号与页码范围；
  禁止逐段机械重复"；标注句式固定为不带 Markdown 强调符号的纯文本「原文 §X，第 Y 页」，
  明令不得用 `*…*` 斜体包裹；💡/📖 补充段规则与术语规则保持不变。system.md 若有对应措辞同步放宽。
  同时在 prompts 中明确：公式 LaTeX 逐字符照用，不得人为加空格/改符号（system.md 铁律 5 已覆盖，section.md 补一句指向）。

- [x] T022 新增 `core/assemble/source_note.py`（≤60 行，标准库正则）：导出
  `render_source_notes(md: str) -> str`，把来源标注统一转为
  `<span class="src-note">原文 §X，第 Y 页</span>`：
  (a) 兼容旧斜体句式 `*（原文 …，第 n 页）*` / `*（原文 …）*`（去星号、去外层全角括号后包 span）；
  (b) 兼容 LLM 按新句式直接输出的裸文本「原文 §X，第 Y 页」（行尾、以"原文"开头到页码结束）；
  (c) 不匹配固定句式的普通强调/其他内容一律不动。
  在 `core/pipeline.py` assemble 循环内、`demote_headings` 同环节对每章 content 调用，
  并存入 `sections_output`（折叠区不处理）。

- [x] T023 [P] 在 `webui/src/styles/reader.css` 纯新增 `.reader-article .src-note` 样式（约 6 行）：
  `font-style: normal;`（关键，覆盖 em 斜体）、字号约 0.85em、颜色取弱化文本变量（如 `var(--reader-text-secondary)`，
  变量名实现时对照文件已有变量）。**只加这一条规则，不改动任何既有样式。**

- [x] T024 [P] 新增来源标注测试（可并入 survey 回归或新文件）：
  (a) 构造含旧句式 `*（原文 §2.2，第 3 页）*` 与新裸句式的章节文本，经 pipeline assemble 后断言
  输出含 `<span class="src-note">原文 §2.2，第 3 页</span>`、不含 `*（原文`；
  (b) 不相关的 `*强调*` 文本不被误转换；(c) section.md 文本契约：含"每小节至多一次"与"不得用 * 斜体包裹"类约束、
  旧措辞"每段…末尾"已移除；(d) survey fake 成品中 `*（原文` 命中数为 0（SC-008）。

- [x] T025 跑全量离线回归并修复：`pytest web/tests/ -q` 全绿；
  重点回归 `test_fake_mode.py`、`test_fake_fixture.py`、`test_core_cancel.py`、artifacts/recovery 相关
  （plan/PaperResult 新增字段后旧夹具若断言严格相等需同步为"包含新字段且旧字段不变"；
  任何断言章节数可变/编号重排的旧测试需改为"恒 7 章、编号 0..6"）。
  输出一份简短对照：同一 survey fixture 改动前后的章节数（均 7）、空章数与降级文案、gap 提示数、
  表格完整率、死链数、来源标注斜体命中数（应 0）。

- [ ] T026 （人工验收，不在离线门内）由用户配置真实 MinerU/DeepSeek 重跑任务 `2d26f6b9`：
  逐章核对标题与内容一致、7 章齐全、空章为得体固定文案、无 blk 泄漏、三表完整、图片/参考文献达标、
  来源标注为非斜体轻样式（数字正体）；打开阅读器确认固定 7 章目录在新结果下正常渲染；对照 SC-001~SC-010 逐项勾选。
