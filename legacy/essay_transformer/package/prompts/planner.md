# 任务：教程结构与块处置规划（schema_kind = planner）

## 输入

- `{{NORMALIZED_JSON}}`：规范化论文全文（sections 章节树 + blocks 扁平块，
  含 id/type/page_idx/caption/mineru_type/章节初始 kind）

## 目标

做一次全局决策，输出整个转写的唯一决策源 `plan.json`：把原文章节重排为
固定七段教程结构，并为每个块决定处置动作与关键公式定位。**你不写正文**，
只做规划。

## 1. target_order：李沐式七段（动机优先顺序，硬性）

七段必须恰好各出现一次，顺序固定为：

1. `tldr`：30 秒摘要，素材取自摘要/abstract，点到即止（成稿总字数 ≤ 正文 5%）
2. `intro`：这篇论文要解决什么问题——展开动机与背景
3. `conclusion`：结论前置——先讲它最后声称了什么
4. `related`：背景与相关工作——**压缩**，只讲理解本文必需的前作
5. `method`：核心方法——结构图导读 + 公式卡 + 手算示例，教程式充分展开
6. `experiments`：实验说明了什么——**结论级表达为主**（结论性陈述占比
   ≥ 80%），完整数据退折叠区
7. `wrapup`：总评——贡献、局限、适用边界的收束

规则：

- `target_order` 每项是对象，字段**只能**是：`target_slug`（七段标识
  tldr/intro/conclusion/related/method/experiments/wrapup 之一）、
  `source_section_ids`（取材的真实节 id 字符串数组，至少 1 个）、
  `treatment`；**禁止**自造 section_id/section/goal/target_title 等任何
  schema 之外的字段（七段中文标题由组装层固定生成，你不要输出标题）。
- 每个原文 section 必须在七段中出现**且仅出现一次**；无独立结论章时，
  从结尾讨论/结论性段落抽取素材组织 `conclusion`。
- 真实论文章节数常常不足七段。允许使用**合成抽取 id** 从同一真实节取材
  补足（FR-014）：`<真实节 id>-tail`（取该节结尾素材）与
  `<真实节 id>-extract`（抽取该节部分素材）。合成 id 不占用真实节的
  "恰好一次"覆盖名额，但其基底必须是真实存在的非 appendix 节；七段中
  每个真实节直接出现仍须恰好一次。
- appendix 章节不得进入主线：只能把其内容映射为 `fold`（derivation）或
  不映射；`source_section_ids` 至少 1 个。
- `treatment` 按该段处理方式取：brief / expand / front_load_extract /
  compress / tutorial / conclusion_only / summary。

## 2. block_actions：逐块处置（每个非噪声块必须给出 action）

**结构硬性纪律**（违反会被 JSON Schema 拒绝）：

- `block_actions` 是**对象（object）**，以块 id 为键（如 `"blk-0009"`），
  **不是数组**；每个非噪声块恰好一个键。
- 每个决策对象只允许 schema 声明的字段（`action`/`fold_group`/
  `one_liner`/`rewrite_caption`/`rationale`），**禁止自造字段**
  （不要写 section_id、goal、reason、comment 等任何其他键名）。
- `rationale` **只有图片块**（type=figure，含 chart）需要，且必须非空、
  **不超过 40 个汉字**；其他块不要输出 rationale。
- `fold_group` 仅 `action=fold` 时出现，取值只能是
  `derivation`/`repro_data`/`references`。

动作枚举（`action` 字段，逐字取值）：

- `narrate`：转写讲解（主线的普通论述块）
- `condense`：压成一句话结论；可给 `one_liner` 示范口径
- `fold`：进折叠区，**必须**带 `fold_group`：
  - `derivation`：完整推导、证明、附录数学
  - `repro_data`：完整超参数表、消融全表、训练曲线图、硬件/耗时、复现用
    原始数据（正文允许留 `condense` 一句话结论，并与折叠数据共享来源）
  - `references`：参考文献列表
- `drop`：页眉页码、页脚、旁注等噪声
- `keep_figure`：保留图片并重写中文图注（`rewrite_caption=true`）
- `keep_table_explained`：保留表格并配套中文讲解（概念性/机制对比表）

### 解析降级块（FR-026，确定性校验，违反即重试）

`type=parse_gap` 是规范化阶段标记的"该位置原文解析失败"占位块
（公式/表格/段落个别条目损坏，带 gap_kind 与 placeholder）：

- **只能**给 `narrate` 或 `condense`，让它留在对应主线段；组装链路会在
  成稿对应位置自动渲染固定缺失说明，你**不要**也**无法**在 section_rewrite
  中引用它（source_block_ids 含 parse_gap 会被拒绝）；
- 禁止 `fold`/`drop`/`keep_figure`/`keep_table_explained`，杜绝静默丢失；
- 禁止编造该位置原本应有的公式/表格/段落内容。

### 主线必须退出的内容（FR-019/020）

完整超参数表、消融全表、训练曲线图、硬件配置与训练耗时：主线计数必须为 0，
一律 `fold` 进 `repro_data`（或确无价值时 `drop`）。

### 折叠归类的硬性规则（确定性校验，违反即重试）

- `type=reference_list` 的参考文献块：**只能** `action=fold` +
  `fold_group=references`，不得 narrate/condense/drop；成稿折叠区以解析出的
  原文条目为准，必须完整、无缺失（FR-022/023）。
- `mineru_type=chart` 的训练曲线/工程图：**只能** `action=fold` +
  `fold_group=repro_data`，不得进主线（FR-020）。
- appendix 节内的公式块若 `fold`，**必须** `fold_group=derivation`
  （完整证明进推导折叠区，不得混入 repro_data/references，FR-021/022）。
- 其余 `derivation`（完整推导/证明/附录数学）与 `repro_data`（完整超参表、
  消融全表、复现原始数据）的归类按上文判据执行；折叠块必须保留完整原始内容，
  表格 HTML 转成 Markdown 表格原样收入 `content_markdown`，正文只留一句话
  结论并与折叠块共享来源。

### 图片二次判定（research R2，不信任 mineru_type=image）

- `mineru_type=chart`（数据折线/柱状图、工程曲线、训练曲线图）：确定性
  硬规则，一律 `fold` 到 `repro_data`，不得进主线（FR-020）；
- `mineru_type=image` 仅为提示，**必须依据 caption 与上下文重新判定**：
  模型结构图、机制示意图、概念可视化图给 `keep_figure`，重写图注并供导读；
  若 caption 表明它其实是数据图/训练曲线，则按 chart 处理 `fold` 到
  `repro_data`；
- 解析缺失的图片块（`image_missing=true`，`img_path=null`）：仍给
  `keep_figure`，由转写/组装链路在成稿对应位置渲染固定缺失占位
  （FR-026），不得 `drop` 成静默丢失，也不得编造图中内容；
- 每个图片块（含 chart）的决策**必须**给非空 `rationale` 字符串
  （依据 caption/上下文的保留、折叠或丢弃理由，随 plan.json 落盘审计；
  缺失或为空会被确定性校验拒绝，FR-027）。

## 3. key_formulas：关键公式定位

- 至少 1 条；每条的字段只能是 `block_id`、`latex`（与原文一致）、
  `worked_example_required`（布尔，**每条都必填**）、可选 `label`。
- `worked_example_required=true` **恰好 1 条**：选全文最关键、最值得手算
  的公式（如 attention 的核心计算式），并给 `example_spec` 对象：
  - `tokens` 是一个 **3 到 5 之间的整数**（玩具词的**个数**，如 `3`），
    **不是字符串数组**；玩具词由后续 worked_example 阶段生成，你不要列词；
  - `steps` 是字符串数组，列出计算步骤名（如 `["Q/K/V 投影", "打分", "softmax"]`）。
- 不要复制公式周围的长段原文，保持 plan 紧凑。

## 事实纪律

规划依据只能来自输入块；无法判定归属的内容在 `rationale` 中说明并保守处理
（优先 narrate/condense，不得凭空创造章节或结论）。

## 输出形态

只输出符合 schema_kind=planner 的 JSON 对象（`target_order` 恰 7 项、
`block_actions`、`key_formulas`），无其他文本。
