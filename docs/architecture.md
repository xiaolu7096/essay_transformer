# 核心层架构与边界说明

## 1. 核心定位

`core/` 是论文转写的**纯业务逻辑层**，它：

- ✅ 包含所有与「论文怎么转写」相关的逻辑
- ✅ 可被任何表现层（CLI / Web / App / API）调用
- ✅ 可独立测试（fake 模式不花 API 费用）
- ❌ 不包含任何表现层代码（没有路由、没有组件、没有页面）
- ❌ 不硬编码配置（API Key、URL 等全部由调用方传入）
- ❌ 不做持久化（文件读写仅限输入输出，不维护内部状态）

## 2. 6 模块架构

### 2.1 总体结构

core 层由 6 个业务模块 + 1 个编排模块组成，按数据流向排列：

```
  输入 ──► ingest ──► mineru_client ──► normalize ──► planner ──► rewrite ──► assemble ──► 输出
            (摄取)       (解析)           (归一化)       (规划)       (转写)       (拼装)
```

**pipeline.py** 是编排器，按顺序调用以上 6 个模块，串成全流程并提供进度回调。

### 2.2 模块一览

| 模块 | 文件 | 核心职责 | 副作用 | 纯函数占比 |
|---|---|---|---|---|
| ingest | `ingest.py` | 输入识别、下载、校验、生成 paper_id | 有（下载文件） | ~50% |
| mineru_client | `mineru_client.py` | 调用 MinerU API 解析 PDF | 有（网络 + 写文件） | 0% |
| normalize | `normalize.py` | MinerU 原生格式 → 标准块格式 | 无 | 100% |
| planner | `planner.py` | 原始章节 → 7 章结构 + 块动作 | 无 | 100% |
| rewrite | `rewrite/` 目录 | LLM 转写 + prompt 构建 + 公式回填 | 有（网络） | ~80% |
| assemble | `assemble/` 目录 | 拼装 + 后处理三件套 + 图片处理 | 有（拷贝图片） | ~90% |

### 2.3 编排层（pipeline.py）

pipeline 不是第 7 个业务模块，而是**串起 6 个模块的编排器**：

- 按数据流顺序调用 6 个阶段
- 统一的进度回调（6 个阶段 × 0~1 进度）
- 统一的异常传递（InputError / ServiceError / ETError）
- 顶层入口 `transform_paper()` 所在位置

## 3. 模块职责详解

### 3.1 ingest（摄取）

**做什么**：识别输入类型（arXiv URL / 本地 PDF），下载或校验 PDF，生成稳定的 paper_id。

**输入**：字符串（URL 或路径）
**输出**：`SourceInfo{kind, ref, pdf_path, paper_id}`

**副作用**：有（下载文件、写文件）
**纯函数部分**：URL 正则、paper_id 生成、PDF 魔术字节校验

**关键能力**：
- arXiv URL 识别与 id 提取
- arXiv PDF 下载
- 本地 PDF 校验（文件存在 + 格式正确）
- 稳定的 paper_id 生成（arXiv 用 id，本地用 SHA-256 前 10 位）

### 3.2 mineru_client（解析）

**做什么**：把 PDF 发给 MinerU 云端 API，轮询等待结果，下载并解压。

**输入**：PDF 文件路径 + 输出目录
**输出**：parsed 目录（content_list.json + full.md + images/）

**副作用**：有（网络请求 + 写文件）
**纯函数部分**：无

**关键能力**：
- 申请上传链接 → PUT 上传 → 轮询状态 → 下载 ZIP 的 4 步流程
- 自动定位 content_list.json（可能带 batch_id 前缀）
- 细粒度错误映射（鉴权失败 / 超时 / 服务端错误 / 格式错误）

### 3.3 normalize（归一化）

**做什么**：把 MinerU 的原生 content_list 转换成我们定义的标准块格式。

**输入**：MinerU content_list 数组
**输出**：NormalizedDoc（标准格式文档）

**副作用**：无（纯函数）
**纯函数部分**：100%

**关键能力**：
- 章节树构建（text_level + 标题编号）
- 块类型映射（text/equation/table/image/chart/ref_text → 标准类型）
- parse_gap 降级机制（个别块损坏不拖垮整篇）
- 纯扫描件检测（无文本层时报友好错误）
- 参考文献聚合（连续 ref_text 合并成一条 reference_list）

### 3.4 planner（规划）

**做什么**：把论文的原始章节映射到 7 章结构，并给每个块分配动作（narrate / fold / drop）。

**输入**：NormalizedDoc
**输出**：Plan（target_order + block_actions）

**副作用**：无（纯函数）
**纯函数部分**：100%

**关键能力**：
- 按标题关键词 + kind 匹配已知章节（abstract → tldr, introduction → intro 等）
- 未分类章节的归属规则（第一大段归 method，后面的归 experiments）
- 块动作规则（表格/参考文献进折叠区，噪声丢掉，其余进主线）
- 实验章的数据图自动进折叠区

> **设计决策**：当前用规则实现，不调用 LLM。按章节标题关键词匹配，零成本零延迟。
> 对结构不标准的论文可能分配不准，后续如需更高准确率，可以加 LLM 辅助规划层，但不改变输出格式。

### 3.5 rewrite（转写）

**做什么**：逐章把「原文块 + 系统提示词 + 章节模板」拼成 prompt，发给 LLM，返回中文教程内容。

**输入**：NormalizedDoc + Plan + 章节 slug
**输出**：章节 Markdown 字符串

**副作用**：有（网络请求）
**纯函数部分**：~80%（prompt_builder + backfill 是纯的，llm.chat 有副作用）

**子模块**：
- `llm.py`：LLM 调用封装（chat 函数）
- `prompt_builder.py`：prompt 构建（块格式化 + 模板替换）
- `backfill.py`：公式回填（从 full.md 补 equation 块的空文本）

**关键能力**：
- 系统提示词 + 章节模板的双层 prompt 结构
- 块级格式化（每种类型的块有不同的呈现方式）
- 折叠块摘要（告诉 LLM 哪些数据被移到了折叠区）
- 公式回填兜底（MinerU 有时不写公式文本）

### 3.6 assemble（拼装）

**做什么**：把 7 章内容串起来，加章标题，拷贝图片，做三层后处理兜底。

**输入**：7 章 Markdown + NormalizedDoc + Plan
**输出**：PaperResult（完整 Markdown + 图片 + 质量检查结果）

**副作用**：有（拷贝图片）
**纯函数部分**：~90%（除了 copy_images，其余都是纯字符串处理）

**子模块**：
- `fold_sections.py`：文末折叠区构建（表格/推导/参考文献）
- `image_utils.py`：图片拷贝 + 死链修复 + 死链检查
- `headings.py`：标题降级兜底

**关键能力**：
- 7 章拼装（统一加章标题，保证层级正确）
- **三层兜底**：
  1. 标题降级（LLM 输出 ## / # 级标题 → 自动降为 ###）
  2. 图片死链修复（最长公共前缀匹配真实文件名）
  3. 死链检查（最终校验，有问题告警）
- 折叠区分组（repro_data / derivation / references 三类）

## 4. 对外接口

### 4.1 顶层入口

```python
def transform_paper(
    source: str | Path,               # arXiv URL 或本地 PDF 路径
    *,
    output_dir: str | Path | None = None,
    mineru_api_key: str | None = None,
    deepseek_api_key: str | None = None,
    fake: bool = False,               # 假模式，不调用真实 API
    progress_cb: ProgressCallback | None = None,
) -> PaperResult: ...
```

### 4.2 子模块独立调用

所有子模块都可以独立导入和调用，方便：
- 前端做流式展示（边解析边显示进度）
- 单章重跑（只改某一章的 prompt，重跑那一章）
- 用已有的解析产物跳过前面阶段
- 调试某个模块

### 4.3 进度回调

```python
ProgressCallback = Callable[[str, float, str], None]
#               (stage, progress_0_1, message)
```

阶段名（stage）固定为 6 个：`ingest`, `parse`, `normalize`, `plan`, `rewrite`, `assemble`

## 5. 设计原则

### 5.1 纯函数优先

- 能写成纯函数的，绝不加副作用
- 副作用集中在模块边界（ingest/mineru_client/llm.chat）
- 核心业务逻辑（normalize / planner / prompt_builder / 后处理）100% 纯函数，方便测试

### 5.2 配置注入，不硬编码

- API Key、base_url、model 等所有配置都优先通过参数传入
- 环境变量仅作 fallback（LLM 层有，MinerU 层没有——两边暂未统一）
- 表现层负责从环境变量/配置文件读取，再传给 core
- 方便换供应商、加 mock、做多租户

### 5.3 数据可序列化

所有中间数据结构（NormalizedDoc / Plan / PaperResult）都可以 JSON 序列化，方便：
- 缓存中间结果
- 调试时 dump 出来看
- 前后端通过 API 传递

### 5.4 优雅降级

个别块出问题不拖垮整篇：
- 公式解析失败 → parse_gap 占位，显式说明缺失
- 图片文件缺失 → 占位提示，不报错中断
- LLM 偶尔输出不合规 → 代码层后处理兜底

## 6. 明确不做的事

以下功能**不属于 core 层**，由上层表现层或服务层实现：

- ❌ 用户认证与权限管理
- ❌ 任务队列与任务持久化
- ❌ 结果缓存策略（core 不决定缓存什么，调用方自己管）
- ❌ 日志框架（core 用 print 简单输出，调用方自己接 logger）
- ❌ 配置文件管理（core 不读 yaml/toml，配置全靠参数传）
- ❌ 错误重试策略（core 抛出异常，调用方决定重试逻辑）

## 7. Prompt 资产说明

`core/prompts/` 目录下的两个文件是整个项目的**质量核心**，产出好坏主要由它们决定。代码只是加载和拼装的脚手架。

### 7.1 system.md（系统提示词）

- **位置**：`core/prompts/system.md`
- **作用**：定义 LLM 的身份、写作取向和 9 条铁律
- **铁律包含**：事实只能来自输入块、缺失必须明说、补充内容必须标注、禁止编造图片、章节内标题从 ### 起、工程性细节不进正文 等
- **改动影响**：全局的，改一个字可能影响所有章节的输出风格

### 7.2 section.md（章节模板）

- **位置**：`core/prompts/section.md`
- **作用**：每章的转写模板，定义了公式卡格式、表格处理、图片处理、术语规则、补充段规则等
- **核心设计**：
  - 公式卡三段式：公式原文 → 符号解释 → 直觉理解
  - 表格不进正文，移到折叠区
  - 术语首次「中文（English）」，之后纯中文
  - 图片文件名必须严格照抄
- **改动影响**：按章生效，改 method 章的写法不影响 tldr 章

### 7.3 改动原则

- **优先改 prompt，其次改代码**——质量问题优先在 prompt 层解决，代码层只做兜底
- 改 prompt 后必须用至少 2 篇论文回归验证，不能只测一篇
- 代码层的后处理三件套（标题降级 / 图片死链修复 / 公式回填）是双保险，不要因为 prompt 写得好就删掉

## 8. 相关文档

- [项目说明与快速开始](README.md) —— 怎么跑、目录结构
- [外部服务接口说明](services.md) —— MinerU / DeepSeek 调用方式、配置项、错误分类
- [核心数据模型](data-model.md) —— 流经各模块的数据结构定义
