# 任务：单章节耦合转写（schema_kind = section_rewrite）

## 输入

- `{{TARGET_SLUG}}`：本段目标段（tldr/intro/conclusion/related/method/
  experiments/wrapup 之一）
- `{{SECTION_BLOCKS}}`：映射到本段的**英文原文块**（含 id/type/page_idx/
  正文/公式 latex/表格 html/图片 caption）
- `{{BLOCK_ACTIONS}}`：planner 对每个块的处置标注（action/fold_group/
  one_liner 等）
- `{{PLAN_SUMMARY}}`：全局规划摘要（七段顺序与本段角色）
- `{{GLOSSARY_TERMS}}`：**确认术语表全量逐条枚举**（每条含 en/zh/mode/
  aliases/note）

## 目标

把分配给本段的英文原块，在**同一次生成中**完成跨语言转换与通俗化讲解，
直接输出中文教程块。这是"换说法讲给人懂"，不是翻译任务。

## 1. 耦合一步转写（硬性，research R5）

- 输出即最终中文教程内容；**严禁产出独立译文、逐字直译、中英双语对照**。
  sections 中间产物只保存中文结果，任何英文原句照抄式"先译后讲"都视为失败。
- 按 block_actions 执行：narrate 讲解、condense 压成一句中文结论、fold 只
  输出 `fold_ref`（折叠区完整内容由原文转写承载，不进主线）、drop 不产出、
  keep_figure/keep_table_explained 按对应块形态产出。
- intro 要展开动机、补前置背景；experiments 以**结论级表达**为主（这段
  说明了什么、对比意味着什么），不复述实验流水账；related 只讲理解本文必需
  的前作。

## 2. 术语规则（逐条枚举注入，硬性 FR-007）

下面是确认词表的**全部**条目，每一次出现都必须遵守，0 条例外：

`{{GLOSSARY_TERMS}}`

- `mode=bilingual`：术语首次及之后每次出现，一律写
  **`中文说明（English 原词）`**，使用半角括号（示例：
  `自注意力机制（self-attention）`）；`zh` 与 `en` 文本必须与词表逐字一致。
- `mode=zh_only`：只写中文，不括注英文。
- 匹配范围含 `aliases`，大小写不敏感、词边界匹配；多个术语同时命中时
  **最长词优先**（`attention` 不得误匹配进 `attentional` 这类词）。
- 同一术语全文译名与格式必须完全一致，不得换说法。
- **数学符号豁免（FR-003）**：equation 类型块、行内 `` `$...$` `` 片段内部，
  以及作为算子名/矩阵名出现的 `Q`/`K`/`V`/`softmax` 等，不套用术语改写
  规则，保持原符号。
- 遇到疑似术语但不在词表中的词：按上下文临时处理（可音译+英文或保留
  英文），并逐条进 `out_of_glossary`（term/context/page_idx/
  temporary_handling），**严禁静默自造统一译名**。

## 3. 事实只能来自输入块（硬性，宪法原则 III）

- 除 supplement 外，每个块的 `source_block_ids` **必须非空**，只引用
  `{{SECTION_BLOCKS}}` 中真实存在的 id；事实（方法、数字、结论、前人工作）
  不得超出输入块。
- 叙事段落可在末尾用简短中文标记点出原文章节（如"（原文 §3.2）"），供
  组装层与 `source_block_ids`/`source_pages` 一起形成溯源。
- 输入块缺失或不可读、导致该处无法讲解时：不要编造。图片块按第 6 节
  用 `expected_img_path` 输出 figure 块（组装层自动渲染固定缺失占位）。
- `type=parse_gap` 的解析降级占位块（个别公式/表格/段落原文解析失败）：
  **不要**在任何输出块的 `source_block_ids` 中引用它，也不要转述或猜测
  其内容；组装层会按 planner 决策在本段对应位置自动插入固定缺失说明
  （FR-026），你只需正常讲解其余块。

## 4. supplement 补充块（硬性，宪法原则 III / research R15）

- 凡你为帮助理解而补充、**原文中不存在**的背景知识、前置概念、生活化类比，
  必须**单独**输出为 `kind=supplement` 的块：
  - `supplement_kind=background`：补充背景/前置知识；
  - `supplement_kind=analogy`：生活化类比。
- supplement 块的 `source_block_ids` 必须是**空数组**，且**不得**携带
  "（原文 §…）"溯源标记；`markdown` 为纯中文讲解。
- **严禁**把无出处的补充性断言写进 `paragraph` 普通块；普通块只承载可
  溯源的论文内容。
- supplement 中不得出现伪造成原文事实的具体数字。

## 5. formula_card 公式卡（四件套，FR-017）

关键/核心公式用 `kind=formula_card` 输出，四件套缺一不可：

1. `latex`：与原文逐字符一致的 LaTeX（保留 `\tag{}` 编号，不含 `$$`
   包裹）；
2. `symbol_breakdown`：逐项 `{symbol, meaning}` 列出每个符号是什么意思；
3. `intuition`：一段中文直觉讲解（它在算什么、为什么这样算）；
4. `worked_example_ref`：planner 指定需要手算的关键公式，引用手算示例 id；
   非关键公式可无手算引用，但前两件套（符号拆解 + 直觉）必备。

## 6. 其他块形态

- `tldr` 块只允许出现在 tldr 段，30 秒可读完，点到即止。
- `figure`：给 `img_path`（沿用原相对路径）与 `rewritten_caption` 中文
  图注；数据/工程图不产出到主线。
  - 原图块 `image_missing=true`（`img_path=null`）时，`img_path` 仍须
    沿用该块的 `expected_img_path`，照常给中文 `rewritten_caption` 与
    `source_block_ids`/`source_pages`；组装层检测到图片文件不存在时会在
    成稿该位置自动渲染固定缺失占位（FR-026），你不需要、也不允许编造
    图中内容。
- `table_explained`：`markdown_table` + `explanation` 中文讲解。
- `fold_ref`：`fold_group` + `title`，折叠完整内容放 `content_markdown`，
  与附近结论句共享同一组 source_block_ids，便于结论↔原始数据对应。
  - `derivation`/`repro_data`：`content_markdown` 必须保留完整原始内容
    （表格 HTML 转 Markdown 全表、图片沿用原相对路径），不得只给概述；
    fold_ref 要与同段一句话结论共享至少一个叙述块来源。
  - `references`：`source_block_ids` 必须包含本段的 `reference_list`
    原文块；`content_markdown` 给原文条目逐字转写（组装层以解析原文为
    权威来源渲染，中文转述/节选都会被判定无效），禁止翻译或删减。

## 输出形态

只输出符合 schema_kind=section_rewrite 的 JSON 对象：
`target_slug`、`blocks`（每种块按 schema 填对应字段）、`out_of_glossary`。
无 JSON 以外文本。
