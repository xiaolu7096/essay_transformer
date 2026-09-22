# 外部服务接口说明

本文档说明 core 层当前如何调用外部服务（MinerU 解析、DeepSeek 大模型），以及未来的扩展方向。

## 1. 当前架构：参数直传 + 具体实现

MVP 阶段的 core 层直接依赖两个具体实现：

```
pipeline.py
    │
    ├── 直接 import MinerUClient ──► 调用 MinerU HTTP API
    └── 直接调用 chat() ──────────► 调用 DeepSeek HTTP API（OpenAI 兼容）
```

**当前特点**：
- API Key 通过 `mineru_api_key` / `deepseek_api_key` 参数直接传入
- 没有 Protocol 抽象层，直接用具体类
- `fake=True` 时走内置假输出，不发网络请求
- LLM 层有环境变量 fallback（参数没传时读 `DEEPSEEK_API_KEY`）

> **为什么 MVP 阶段不做抽象？**
> 只有一个实现、又只有一个调用方的时候，抽象是过度设计。等真的需要换供应商的时候再抽也不迟。

---

## 2. 解析服务（MinerU）

### 2.1 调用方式

```python
from core.mineru_client import MinerUClient

client = MinerUClient(api_key="your_key")
client.parse("paper.pdf", "output/parsed/")
```

`parse()` 返回后，output_dir 里会有：
- `*_content_list.json` —— 结构化块列表（核心产物格式见下文）
- `full.md` —— MinerU 直出的 Markdown（用于公式回填）
- `images/` —— 提取的图片

### 2.2 content_list 格式约定

每个块至少包含：

| 字段 | 类型 | 说明 |
|---|---|---|
| `type` | str | 块类型：text / equation / table / image / chart / ref_text / page_number / ... |
| `text` | str | 文本内容（如果有） |
| `page_idx` | int | 页码（从 0 开始） |
| `text_level` | int | 标题级别（仅 text 类型的标题块有） |
| `img_path` | str | 图片相对路径（仅 image / chart 类型） |
| `table_body` | str | 表格 HTML（仅 table 类型） |

MinerU 的 content_list 格式作为**事实标准**。未来如果接入其他解析服务，输出也要转换成相同格式。

### 2.3 配置项

| 参数 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| api_key | str | — | MinerU API Key（必需） |
| base_url | str | `https://mineru.net/api/v1` | API 基地址 |
| model_version | str | `vlm` | 解析模型版本 |
| timeout | float | `60.0` | 普通请求超时（秒） |
| upload_timeout | float | `300.0` | 上传/下载超时（秒） |
| poll_interval | float | `3.0` | 轮询间隔（秒） |
| max_poll_seconds | float | `600.0` | 最大轮询时间（秒） |

### 2.4 调用流程

1. `POST /file-urls/batch` 申请上传链接
2. `PUT <upload_url>` 上传 PDF
3. `GET /extract-results/batch/<batch_id>` 轮询，直到 done/failed
4. 下载 `full_zip_url` 并解压到 output_dir

---

## 3. 大模型服务（DeepSeek）

### 3.1 调用方式

```python
from core.rewrite.llm import chat

result = chat(
    system_prompt="...",
    user_prompt="...",
    api_key="your_key",
    # base_url="https://api.deepseek.com",
    model="deepseek-chat",
    temperature=0.3,
)
```

### 3.2 当前模型

- **默认模型**：`deepseek-chat`
- **默认温度**：`0.3`（偏低，保证事实准确性）
- 兼容 OpenAI 兼容接口，任何兼容 OpenAI 格式的服务（Ollama / vLLM / 通义 / 豆包等）都可以用，改 `base_url` 和 `model` 就行。

### 3.3 行为约定

- **只返回文本**：不返回 JSON 信封、不返回多余解释
- **一次返回完整结果**：不做流式输出（流式留给上层做）
- fake 模式：`fake=True` 时返回占位文本，不发请求

---

## 4. 错误分类

所有外部服务的错误统一映射到 3 类异常：

| 异常类 | 含义 | 典型场景 | 用户该怎么做 |
|---|---|---|---|
| `InputError` | 输入有问题，用户改了就能好 | API Key 错、文件格式不对、论文不存在、404 | 检查输入/配置，改了重试 |
| `ServiceError` | 服务端/网络问题，重试可能好 | 超时、500、连接失败、限流 | 等一会儿再试 |
| `ETError` | 其他未知错误 | 不应该出现的情况 | 提 issue / 联系开发 |

**当前状态**：
- ✅ MinerUClient 已实现 3 类异常映射
- ⚠️ LLM 的 `chat()` 部分场景还抛 `RuntimeError`，统一到 3 类异常是后续待办

---

## 5. 配置传递

### 5.1 当前方式

```python
from core import transform_paper

result = transform_paper(
    "paper.pdf",
    mineru_api_key="xxx",
    deepseek_api_key="yyy",
)
```

API Key 直接通过参数传入。表现层可以从环境变量读，再传进去。

### 5.2 原则

- API Key 不写死在代码里、不写进配置文件
- core 层优先用参数传入，环境变量仅作 fallback（兼容用）
- 表现层负责从环境变量/配置文件读取，再传给 core

---

## 6. 未来扩展方向（未实现）

如果以后需要换供应商、加缓存、做限流，可以引入 Protocol 抽象：

```
表现层
    │
    ├── Parser (Protocol)
    │       ├── MinerUClient（实现）
    │       └── DoclingParser（实现）
    │
    └── LLMProvider (Protocol)
            ├── DeepSeekProvider（实现）
            ├── FakeLLM（实现）
            └── CachedLLM（装饰器）
```

**什么时候该做？** 当满足以下任一条件时再加：
- 需要支持第 2 个解析服务或第 2 个大模型供应商
- 需要加缓存/重试/限流等横切关注点，且不想碰业务逻辑
- 单元测试需要大量 mock 外部依赖

在那之前，「参数直传 + 具体实现」足够用了。

### 6.1 接入新解析服务的步骤

1. 写一个新的解析类，实现和 MinerUClient 一样的 `parse(pdf_path, output_dir)` 接口
2. 输出对齐 content_list 格式
3. 在 pipeline 里替换调用
4. 等有 2 个以上实现时再抽 Protocol

### 6.2 接入新大模型的步骤

1. 如果是 OpenAI 兼容的，直接改 `base_url` 和 `model` 参数就行
2. 如果不兼容，写一个包装函数，签名和 `chat()` 一致
3. 在 pipeline 里替换调用

---

## 7. 相关文档

- [架构与边界](architecture.md) —— 6 模块架构、设计原则
- [数据模型](data-model.md) —— 流经各模块的数据结构
