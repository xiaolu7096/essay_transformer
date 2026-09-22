# 核心数据模型说明

本文档定义流经 core 层各模块的所有数据结构。这些结构是模块之间的契约，改动需要谨慎。

## 0. 数据流转总览

```
SourceInfo          ← ingest 输出
    ↓
content_list (raw)  ← Parser 输出（MinerU 原生格式）
    ↓
NormalizedDoc       ← normalize 输出（标准格式，最核心的中间模型）
    ↓
Plan                ← planner 输出
    ↓
SectionContents     ← rewrite 输出（7 章 Markdown 字符串）
    ↓
PaperResult         ← assemble 输出（最终结果）
```

**所有结构均可 JSON 序列化**（除了 Path 对象会转成字符串）。

---

## 1. SourceInfo（摄取结果）

**产生位置**：`ingest.ingest()`
**下游消费者**：pipeline

```python
@dataclass
class SourceInfo:
    kind: str           # "arxiv" | "local"
    ref: str            # arXiv id（如 "1706.03762v7"）或本地文件名
    pdf_path: Path      # 本地 PDF 路径
    paper_id: str       # 稳定标识：arxiv-xxx 或 sha256 前 10 位
```

**字段说明**：

| 字段 | 类型 | 说明 |
|---|---|---|
| kind | str | 来源类型：`arxiv` 或 `local` |
| ref | str | 原始引用标识。arXiv 就是 id，本地就是文件名 |
| pdf_path | Path | 本地磁盘上的 PDF 文件路径 |
| paper_id | str | 稳定的论文标识。arXiv 来源用 `arxiv-<id>`，本地上传用 SHA-256 前 10 位 |

**paper_id 稳定性保证**：
- 同一篇 arXiv 论文，不管用 abs 链接还是 pdf 链接，paper_id 相同
- 同一个本地 PDF 文件，不管从哪上传，paper_id 相同（基于内容 hash）

---

## 2. NormalizedDoc（归一化文档）——最核心

**产生位置**：`normalize.normalize_content_list()`
**下游消费者**：planner, rewrite, assemble

这是整个系统中最重要的数据结构——所有下游模块都依赖它。

```python
{
    "paper_id": "arxiv-1706.03762",
    "source": {
        "kind": "arxiv",
        "ref": "1706.03762",
    },
    "title": "Attention Is All You Need",
    "authors": [],
    "sections": [
        {
            "id": "sec-1",
            "heading": "Introduction",
            "level": 2,           # 章节层级（2=一级子节，3=二级子节...）
            "kind": "introduction",  # 初始分类，可能不准
            "page_start": 0,     # 起始页（从 0 开始）
            "blocks": ["blk-0004", "blk-0005", ...],  # 本节包含的块 id 列表
        },
        ...
    ],
    "blocks": [
        {
            "id": "blk-0001",
            "type": "paragraph",
            "page_idx": 0,
            "text_level": None,
            "raw_type": "text",    # MinerU 原始类型
            "text": "The dominant sequence transduction models...",
        },
        {
            "id": "blk-0042",
            "type": "equation",
            "page_idx": 3,
            "raw_type": "equation",
            "latex": "Attention(Q,K,V) = softmax(QK^T/\\sqrt{d_k})V",
            "text": "Attention(Q,K,V) = softmax(QK^T/\\sqrt{d_k})V",  # 冗余一份，方便直接用
            "number": "1",  # 公式编号，可选
        },
        ...
    ],
    "references": [
        "[1] ...",
        "[2] ...",
        ...
    ],
    "appendix_section_ids": ["sec-5", "sec-5-1", ...],
}
```

### 2.1 块类型全集

| type | 说明 | 关键字段 |
|---|---|---|
| `paragraph` | 普通段落 | `text` |
| `heading` | 标题 | `text`, `level` |
| `equation` | 公式 | `latex`, `text`, `number?` |
| `table` | 表格 | `html`, `caption`, `footnote`, `text`（text 是摘要） |
| `figure` | 图片/图表 | `img_path`, `caption`, `mineru_type` |
| `reference_list` | 参考文献段 | `items[]`, `text` |
| `parse_gap` | 解析缺失占位 | `gap_kind`, `placeholder`, `reason` |
| `noise` | 噪声（页码、页脚等） | `noise_kind`, `text` |

### 2.2 块 id 规则

- 格式：`blk-<4位数字序号>`，如 `blk-0001`, `blk-0042`
- 序号对应 content_list 中的下标
- **稳定**：同一份解析产物，块 id 永远不变
- **唯一**：同一篇论文内不重复

### 2.3 kind 类型（章节初始分类）

`unclassified` 表示没匹配到，留给 planner 处理。

| kind | 说明 |
|---|---|
| `abstract` | 摘要 |
| `introduction` | 引言 |
| `related_work` | 相关工作 |
| `conclusion` | 结论 |
| `experiments` | 实验 |
| `appendix` | 附录 |
| `unclassified` | 未分类 |

### 2.4 sections 数组说明

- 扁平数组，不是树结构（简单、好遍历）
- 用 `level` 字段表示层级：1=文档标题，2=一级节，3=二级节……
- `blocks` 数组是本节包含的块 id，按阅读顺序排列

---

## 3. Plan（规划结果）

**产生位置**：`planner.build_plan()`
**下游消费者**：rewrite, assemble

```python
{
    "schema_kind": "rule_based_v1",
    "target_order": [
        {
            "target_slug": "tldr",
            "source_section_ids": ["sec-abstract"],
            "treatment": "brief",
        },
        {
            "target_slug": "method",
            "source_section_ids": ["sec-3", "sec-3-1", "sec-3-2", ...],
            "treatment": "tutorial",
        },
        ...  // 共 7 章，顺序固定
    ],
    "block_actions": {
        "blk-0042": {"action": "narrate", "rationale": "核心公式"},
        "blk-0093": {"action": "fold", "rationale": "消融表格", "fold_group": "repro_data"},
        "blk-0001": {"action": "drop", "rationale": "噪声块"},
        ...
    },
    "key_formulas": ["blk-0042", "blk-0055", ...],
}
```

### 3.1 SLUG_ORDER（7 章固定顺序）

```
tldr → intro → conclusion → related → method → experiments → wrapup
```

**每个 slug 固定对应一个 treatment**（一一对应，不是自由组合）：

| slug | 固定 treatment | 章标题 |
|---|---|---|
| `tldr` | `brief` | 0 · 30 秒摘要 |
| `intro` | `expand` | 1 · 引言精讲 |
| `conclusion` | `front_load_extract` | 2 · 结论前置 |
| `related` | `compress` | 3 · 背景与相关工作 |
| `method` | `tutorial` | 4 · 核心方法 |
| `experiments` | `summary` | 5 · 实验说明了什么 |
| `wrapup` | `conclusion_only` | 6 · 总评 |

treatment 决定了 prompt 模板的语气和展开程度，由 planner 写入 plan，rewrite 阶段读取。

### 3.2 schema_kind 说明

`schema_kind` 字段标记 plan 的生成方式，当前有：
- `rule_based_v1`：规则生成（当前 planner 的默认输出）
- `llm_v1`：LLM 生成（未实现，留作扩展）

这个字段是**可选的**，用于调试和追溯，下游模块不依赖它做逻辑判断。

### 3.3 treatment 取值含义

| treatment | 含义 | 对应章节 |
|---|---|---|
| `brief` | 极简摘要 | tldr |
| `expand` | 充分展开 | intro |
| `front_load_extract` | 提取前置 | conclusion |
| `compress` | 压缩 | related |
| `tutorial` | 教程式展开 | method |
| `summary` | 结论级总结 | experiments |
| `conclusion_only` | 总评收束 | wrapup |

### 3.4 block_actions 动作

| action | 含义 | 去向 |
|---|---|---|
| `narrate` | 主线讲解 | 进章节正文 |
| `fold` | 折叠区 | 进文末 `<details>` |
| `drop` | 丢弃 | 不出现 |

fold 动作可选的 `fold_group`：
- `repro_data`：实验数据与表格（默认）
- `derivation`：推导与证明细节
- `references`：参考文献

---

## 4. SectionContents（转写结果）

**产生位置**：rewrite 阶段，逐章生成
**下游消费者**：assemble

就是一个简单的 dict：

```python
{
    "tldr": "### 一句话总结\n\nTransformer ...",
    "intro": "### 背景\n\n在深度学习中...",
    "conclusion": "### 核心贡献\n\n...",
    "related": "### 前人工作\n\n...",
    "method": "### 整体架构\n\n...",
    "experiments": "### 主要结果\n\n...",
    "wrapup": "### 总评\n\n...",
}
```

**注意**：
- 每章内容都是**完整的 Markdown**，从 `###` 级标题开始
- 不包含章大标题（`## N · xxx`）——章大标题由拼装层统一添加
- 图片引用用相对路径 `images/xxx.jpg`

---

## 5. PaperResult（最终结果）

**产生位置**：`pipeline.transform_paper()`
**下游消费者**：表现层（CLI / Web / App）

```python
@dataclass
class PaperResult:
    paper_id: str
    title: str
    sections: list[dict]       # [{slug, heading, content}]
    full_markdown: str         # 拼装好的完整 Markdown
    images_dir: Path | None    # 图片目录
    image_files: list[str]     # 图片文件名列表
    total_chars: int           # 总字符数
    dead_links: list[str]      # 图片死链列表（空表示全部正常）
```

### 5.1 sections 数组

按 SLUG_ORDER 排列，每一项：

```python
{
    "slug": "method",
    "heading": "4 · 核心方法",   # 不带 ## 前缀
    "content": "### 整体架构\n\n...",  # 本章 Markdown 内容
}
```

方便前端做「章节目录 + 逐章显示」。

### 5.2 full_markdown

完整的 Markdown 文档，包含：
- 一级标题（论文标题）
- 7 个二级标题（章标题）
- 各章内容（三级及以下标题）
- 文末折叠区

可以直接保存为 .md 文件或渲染。

---

## 6. 设计原则

### 6.1 向后兼容

- **只增不改**：新增字段不破坏旧字段
- 不删除字段，不用的字段可以标记为 deprecated
- 新增的可选字段要有默认值

### 6.2 稳定 id

- 块 id 在 normalize 阶段生成后，所有下游阶段都用同一个 id 引用
- 不要在 planner / rewrite / assemble 阶段生成新的块 id

### 6.3 可序列化

所有数据结构都满足：
- 可以用 `json.dumps()` 直接序列化
- 可以用 `json.loads()` 还原
- Path 对象在需要序列化时转成字符串

### 6.4 冗余但方便

某些字段在多个地方有冗余（比如 equation 块同时有 `latex` 和 `text`），这是有意为之的——下游模块不用判断该读哪个字段，直接用 `text` 就行。

---

## 7. 版本演进（记录）

| 版本 | 变化 | 说明 |
|---|---|---|
| v1 (MVP) | 初始版本 | 7 章结构 + 标准块 + plan |

---

## 8. 相关文档

- [项目说明与快速开始](README.md) —— 怎么跑、目录结构
- [架构与边界](architecture.md) —— 6 模块架构、设计原则
- [外部服务接口说明](services.md) —— MinerU / DeepSeek 调用方式
