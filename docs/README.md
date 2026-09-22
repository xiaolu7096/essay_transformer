# 论文易读化转换器（Paper Reader）

把英文学术论文转写成中文教程式阅读材料——不是翻译，是「换个讲法让初学者能懂」。

## 这是什么

你拿到一篇英文论文 PDF，从头到尾读完可能要半天，还不一定看懂。这个工具帮你把论文重新组织成「先给结论、再讲过程」的中文教程，适合：

- 想快速了解一篇论文在讲什么、值不值得读
- 刚进入一个领域，读原文太吃力需要中文导读
- 不想花时间抠硬件参数、训练天数这种工程细节，只想理解核心思想

## 核心特色

### 倒叙式 7 章结构

不是从 Abstract 顺着读到 Conclusion，而是按「读者理解顺序」重新编排：

| # | 章节 | 作用 |
|---|---|---|
| 0 | 30 秒摘要 | 一句话讲清这篇论文干了啥 |
| 1 | 引言精讲 | 为什么要做这个、解决了什么问题 |
| 2 | 结论前置 | 先告诉你答案，再看过程 |
| 3 | 背景与相关工作 | 前人做了什么、还差什么 |
| 4 | 核心方法 | 教程式展开，结构图+公式精讲 |
| 5 | 实验说明了什么 | 只讲结论级对比，完整数据放折叠区 |
| 6 | 总评 | 贡献、局限、适用边界 |

### 质量双保险

产出质量的核心是 `core/prompts/` 下的两份提示词资产（9 条铁律 + 章节模板），代码只是加载和拼装的脚手架。除此之外还有代码层兜底：

- **Prompt 层**：9 条铁律严格约束 LLM 输出（不编造、不硬塞工程细节、不乱标题层级）
- **代码层**：图片死链自动修复、标题降级兜底、公式缺失回填——LLM 偶尔犯错，代码层兜住

### 克制的补充

为理解所必需的背景知识会补充，但每章最多 1~2 处，且明确标注「💡 补充（非论文原文）」，绝不喧宾夺主。

## 快速开始

```python
from core import transform_paper

result = transform_paper(
    "https://arxiv.org/abs/1706.03762",  # arXiv 链接或本地 PDF
    mineru_api_key="你的 MinerU Key",
    deepseek_api_key="你的 DeepSeek Key",
    progress_cb=lambda stage, p, msg: print(f"{p*100:.0f}% {msg}"),
)

print(result.full_markdown)  # 完整 Markdown
# 或逐章取
for sec in result.sections:
    print(sec["heading"], len(sec["content"]))
```

> **开发测试**：加 `fake=True` 不调用真实 API，只跑流程。

## 项目结构

```
core/                    ← 核心业务逻辑（纯 Python，无框架依赖）
├── pipeline.py          — 全流程编排
├── ingest.py            — 输入摄取（arXiv 下载 / PDF 校验）
├── mineru_client.py     — MinerU 解析客户端
├── normalize.py         — 内容块归一化
├── planner.py           — 7 章结构规划
├── rewrite/             — LLM 转写
├── assemble/            — 拼装与后处理
├── prompts/             — 提示词资产（系统设定 + 章节模板）
└── errors.py            — 异常分类

docs/                    ← 设计文档（架构/接口/数据模型）

legacy/                  ← 旧版代码归档（仅参考，不依赖）
```

## 技术栈

- **语言**：Python 3.11+
- **解析服务**：MinerU 云端 API（PDF 结构化解析）
- **大模型**：DeepSeek `deepseek-chat`（默认），可替换为任何 OpenAI 兼容接口
- **依赖**：仅 `requests` + `openai`（标准库之外只有这两个）

## 文档索引

| 文档 | 内容 |
|---|---|
| [架构与边界](architecture.md) | core 层 6 模块架构、职责边界、设计原则 |
| [服务接口契约](services.md) | 外部服务抽象、配置项、错误分类 |
| [数据模型](data-model.md) | 流经各模块的数据结构定义 |

## 致谢

解析能力由 [MinerU](https://mineru.net/) 提供，大模型由 [DeepSeek](https://deepseek.com/) 提供。
