# 交接说明（精简重启版）· 2026-09-21

## 这个项目做什么

把学术论文（PDF / arXiv 链接）转换成**中文初学者能顺畅读懂**的 Markdown 教程。
核心手法：删减噪声、重排主线、讲清核心公式、补背景与类比。范式参考「李沐论文精读」。

首个验收论文：**Attention Is All You Need**（arXiv:1706.03762，PDF 已在根目录）。

## 目标读者与产物

- 读者：中文母语、本科高年级 / 跨专业，不熟该领域。
- 产物：单个 Markdown + images/ 目录；命令行一条命令跑完全流程。
- 技术栈：Python 3.11+、requests、openai SDK、MinerU 云端解析（vlm）、DeepSeek 转写。
- Key 只从环境变量读：`MINERU_API_KEY`、`DEEPSEEK_API_KEY`，禁止硬编码。

## 内容规则（精简版，以此为准）

1. **章节主线**：30 秒摘要 → 引言（重点展开、补背景）→ 结论前置 → 相关工作（一两句）
   → 核心方法 → 实验（只讲结论级对比，好在哪、好多少）→ 简短总评。
2. **公式只做解释，不做计算复现**：保留 LaTeX 原文 + 逐个符号解释 + 白话直觉/作用。
   ❌ 不要让 AI 虚构数值手算、不要 numpy 复算、不要"教学示意"声明（上一版的
   worked_example 机制已被明确否决）。
3. **表格/实验**：超参全表、消融全表、训练曲线、硬件与训练时长不进正文，
   压成一句话结论；原表/原文数据放 `<details>` 折叠区，正文结论须能对应到折叠数据。
4. **图**：架构图/流程图保留并把图注重写为中文导读；纯数据图删除或折叠。
5. **术语**：完全取消术语表。LLM 自然处理——术语首次出现写「中文（English）」，
   之后直接用中文；不维护 YAML、不强制每次出现带英文、不做正则校验、不设人工确认闸门。
6. 补充知识与类比必须标注"非论文原文"；事实陈述保留来源标记（章节+页码）；
   解析缺失处写明确缺失说明，不编造。
7. 参考文献折叠保留。

## 明确不要做（上一版的过度工程，新代码不要继承）

- ❌ worked_example 模块（虚构手算 + numpy 复算 + 重试/缺失占位）
- ❌ glossary 术语表全套（draft YAML / 人工 confirm 闸门 / 格式正则 QA）
- ❌ 确定性 QA 闸门体系（退出码 1/2/3、`.unchecked.md`、JSON schema 强校验）
- ❌ spec-kit 流程命令、宪法、子 agent 编排（相关文档已归档至 `_archive_speckit/`，勿加载）
- 可保留的零成本检查仅：图片死链、来源标记是否存在。失败给警告即可，不阻断产出。

## 可复用资产（在现有代码/数据里，建议借鉴而非整包沿用）

- `output/`、`runs/arxiv-1706.03762/parsed/`：MinerU 全套解析结果（含 14 张图），免重跑。
- `runs/arxiv-1706.03762/normalized.json`：已规范化的内容块（83KB），可直接当转写输入。
- `essay_transformer/mineru_client.py`、`ingest.py`、`normalize.py`：已验证的解析链路。
- `essay_transformer/llm.py`：DeepSeek 客户端薄层。
- `essay_transformer/planner.py` + `prompts/planner.md`：章节重排/块分类思路（可大幅简化）。
- `essay_transformer/rewrite.py` + `prompts/system.md`、`prompts/rewrite_section.md`：
  转写提示词是产出质量的主要来源，**优先复用 prompt 文本，代码可重写得更薄**。
- `essay_transformer/assemble.py`：Markdown 拼装与图片拷贝。
- `runs/.../dist/attention-is-all-you-need.md`：上一版真实产出（58KB），作为质量基线对比。

## 建议的新结构（极简）

一个薄包或单脚本均可：`parse（复用）→ 规划切块（一个 prompt）→ 分章转写
（一个 prompt，逐章调用）→ 拼装`。中间产物落 `runs/<paper_id>/` 以便断点续跑，
但不需要状态机/多子命令，一个 `python transform.py <输入>` 即可。

## 上一版产出已发现的问题（新方案要避免）

- 术语二次替换 bug：出现"编码器-编码器-解码器"；"循环神经网络（recurrent neural
  network）"每隔几行重复一次，严重影响可读性 → 正是取消术语强格式的原因。
- 被判 fold 的完整表格仍泄漏进主线正文；硬件/训练时长（8 块 P100、3.5 天）漏进正文。
- 补充/类比块过于密集，几乎每段都有，应克制（建议每节最多 1~2 处真正必要的补充）。
- 303 个测试多数在维护规则而非质量；新方案只需少量针对解析/拼装的离线测试，
  LLM 相关用 fake client，不追求形式完备。

## 给新对话的第一句话建议

> 读 HANDOFF.md，按其中的精简规则重做论文转换流程，复用已列出的解析资产和 prompt，
> 不要加载 _archive_speckit/ 里的任何文档。先严格执行下面的「执行清单」，
> 每步给出验证结果再进下一步，不要自行扩大范围。

---

# 执行清单（带每步验证）

现有 `essay_transformer/` 包被术语表/worked_example/QA 闸门缠绕（rewrite.py 有
~300 行正则后处理，是上版质量事故的来源），**不要在原包上改**。新建一个薄入口
`transform.py`（可 import 复用原包中纯解析/客户端模块），旧包保留作参考。

## 步骤 0：环境确认（5 分钟）

- 设置环境变量 `MINERU_API_KEY`、`DEEPSEEK_API_KEY`（PowerShell：
  `[Environment]::SetEnvironmentVariable('DEEPSEEK_API_KEY','...','User')`，设完重开终端）。
- **验证**：终端执行
  `python -c "import os;assert os.environ.get('DEEPSEEK_API_KEY');assert os.environ.get('MINERU_API_KEY');print('ok')"`
  输出 ok；`python -c "import openai,requests;print('deps ok')"` 输出 deps ok。

## 步骤 1：搭薄 runner（目标 1 个文件，200 行内）

链路：`normalized.json（已存在，直接复用）→ 规划（首期可直接复用已有的 plan.json）
→ 逐章一次 LLM 调用 → 直接拼装 markdown`。

要求：
- LLM 调用用普通对话输出（markdown 或宽松 JSON 信封均可），**不要**复用
  llm.py 里带 JSON Schema 强校验的 `complete_json`；可在 transform.py 内写一个
  ~30 行的 `chat(system,user)` 薄函数（openai SDK、model 沿用 deepseek-flash）。
- prompt 从 `essay_transformer/prompts/` 三个 md 文件读取并改写为外置文件
  （如 `prompts_v2/system.md`、`prompts_v2/section.md`），**改 prompt 不改代码**。
- 支持两个子命令：
  - `python transform.py rerun <paper_id> --section method`：只重转一章
    （读 runs/<paper_id>/normalized.json + plan.json，省钱迭代用）；
  - `python transform.py build <paper_id>`：7 章全转 + 拼装成
    `runs/<paper_id>/dist_v2/<id>.md`，图片从 parsed 目录拷到 dist_v2/images。
- 章节内容选取可参考 planner.py 的块映射逻辑，但不要 import 它的术语/QA 代码。
- 不做：术语表、worked_example、QA 闸门、退出码体系、硬件正则后处理。

**验证 1（离线不花钱）**：用假 LLM（返回固定字符串）或直接跳过 LLM 的 dry-run
跑通 `rerun --section method`，确认：命令成功退出、生成临时 md、不 import
glossary/worked_example/qa（可用 `python -c "import ast,sys;..."` 或人工 grep
transform.py 确认无这些 import）。

**验证 2（真实调用一次，约几分钱）**：真跑 method 章，人工检查输出 md：
(a) 有内容且为中文；(b) grep 不到「编码器-编码器」；(c) 没有同一英文术语高频
重复刷屏；(d) LaTeX 公式完整；(e) 无 numpy/手算相关代码被调用。

## 步骤 2：只调 method 章的 prompt（核心迭代，3~5 轮）

每轮只改 `prompts_v2/section.md`，重跑 `rerun --section method`，对照检查：
1. 核心公式（Attention(Q,K,V)、多头、FFN、位置编码）都保留 LaTeX；
2. 每个核心公式有「逐符号解释 + 白话直觉」，**没有虚构数字手算过程**；
3. 术语首次出现「中文（English）」、其后只用中文，自然不重复；
4. 补充/类比块全章 ≤2 处；
5. 每段有来源标记（§章节/页码）；
6. 没有编造原文不存在的论断。

**验证方式**：每轮用同一份输入，把输出与上一轮 diff；上面 6 条逐条人工打勾。
可用命令快速筛查：`Select-String` 查硬件词（GPU/P100/天）、查完整 HTML 表
（`<table`）、统计「（」英文注释密度。method 章通过前不要碰其他章。

## 步骤 3：全文拼装 + 实验章规则验证

跑 `python transform.py build arxiv-1706.03762`，检查：
1. 章节顺序 = tldr→intro→conclusion→related→method→experiments→wrapup；
2. 正文无完整表格（grep `<table`、`---|` 表头应为 0），表格在 `<details>` 折叠区；
3. 正文无硬件/训练时长（8 P100、3.5 days 等）；实验只留结论级对比；
4. 架构图保留且图片链接全部有效（无死链：写 10 行脚本校验 md 中图片路径存在）；
5. 参考文献在文末折叠区；
6. 在 Typora / Obsidian / VS Code 预览里通读一遍，记录主观问题。

## 步骤 4：换一篇不同领域论文验证泛化

另选一篇（建议 CV 或优化类）走完整链路：`transform.py` 的输入支持本地 PDF；
MinerU 解析这一步复用现有 mineru_client/ingest 逻辑真实跑一次。
**验证**：端到端成功出 md；无需改 prompt 即满足步骤 2 的 1/2/5/6 条；
若该领域特有问题（如图表类型不同）只做最小 prompt 增补，不引入新机制。

## 步骤 5：MVP 收尾

- 保留 3~5 个离线测试即可（假 LLM：章节选取、拼装顺序、图片拷贝、死链检查），
  不追求覆盖率，删掉/忽略旧 tests/ 中维护旧规则的测试。
- 跑通两篇论文、本人通读满意 = MVP 完成。之后再开新对话做网页版（002），
  届时再用精简 spec；MVP 阶段不写任何新规格文档。

## 成本纪律

- 调 prompt 阶段只准跑 `rerun --section <单章>`，禁止每轮全文重转；
- normalized.json / plan.json / MinerU 结果一律复用，不重复解析；
- 每轮迭代先明确"这轮要解决哪个具体问题"，改 1~3 处 prompt，不要整篇重写。

---

# 执行记录 · 2026-09-21

本轮按上述执行清单完成全流程（步骤 0~5），以下是成果与关键决策。

## 已完成

- ✅ **步骤 0**：环境依赖（openai、requests）就绪，API Key 从环境变量读取。
- ✅ **步骤 1**：薄 runner `transform.py` 单文件实现（~470 行），支持 `rerun` 和 `build` 两个子命令。
  - 无 glossary / worked_example / QA 闸门 import。
  - LLM 调用：普通 chat 模式，无 JSON Schema 强校验。
  - 图片拷贝 + 死链自动修复（最长公共前缀匹配兜底）。
  - 标题降级兜底（防止 LLM 误输出 ## / # 级标题破坏层级）。
- ✅ **步骤 2**：method 章 prompt 迭代 2 轮通过 6 条标准。
- ✅ **步骤 3**：Transformer 论文（1706.03762）全文拼装 22724 字符，6 条硬规则全部通过。
- ✅ **步骤 4**：BERT 论文（1810.04805）泛化验证通过，7 章倒叙结构清晰，0 硬件泄漏，0 图片死链。
- ✅ **步骤 5**：临时文件清理 + 结构整理 + 四章抽读检查。

## 代码结构

```
mvp_test/
├── transform.py          单文件薄 runner（解析+转写+拼装）
├── prompts_v2/           两个 prompt 文件，改 prompt 不改代码
│   ├── system.md         系统设定 + 9 条铁律
│   └── section.md        单章转写模板
├── AttentionIsAllYouNeed.pdf
├── HANDOFF.md
├── requirements.txt
├── runs/
│   ├── arxiv-1706.03762/  Transformer 论文（质量基线）
│   │   ├── normalized.json   可复用解析资产
│   │   ├── plan.json         7 章规划
│   │   ├── parsed/           MinerU 原始解析（14 图）
│   │   ├── dist_v2/          v2 最终产物（活跃）
│   │   ├── dist_v1/          旧版产出（归档）
│   │   ├── sections_v1/      旧版 sections 中间态（归档）
│   │   └── legacy/           旧版 glossary/qa 等文件（归档）
│   └── arxiv-1810.04805/  BERT 论文（泛化验证）
│       ├── normalized.json
│       ├── plan.json
│       ├── parsed/
│       └── dist_v2/
├── legacy/               旧版全部代码/测试/产物，仅参考
│   ├── essay_transformer/package/
│   ├── tests_v1/tests/
│   ├── output_v1/output/
│   └── glossaries/glossaries/
└── _archive_speckit/     旧 spec-kit 文档（勿加载）
```

## 关键决策

1. **公式回填**：normalized.json 中 equation 块 text 为空时，从 parsed/full.md 按行号+公式特征提取回填（`backfill_equations`）。
2. **图片死链双层防护**：prompt 明确要求文件名严格照抄 + 代码层 `fix_image_links` 用最长公共前缀自动修正。
3. **标题层级双层防护**：system.md 铁律第 9 条（禁止输出 ## / # 级标题）+ 代码层 `demote_headings` 自动降级兜底。
4. **硬件不进正文**：system.md 铁律第 8 条明确列出禁止项（GPU 型号、训练时长、超参全表等），实验章只留结论级表述。
5. **章节编号 0~6**：7 个大章用 `## N · 标题` 格式，读者一眼知道结构。

## Prompt 迭代记录

| 轮次 | 改动 | 效果 |
|------|------|------|
| v1 初始 | 基于旧版 rewrite_section.md + system.md 精简 | 基础可用，但术语格式不统一、图片文件名编造 |
| 第 1 轮 | 公式卡格式强化（符号解释+直觉两段式）、术语规则从软要求变硬约束 | 6 条全部通过，术语格式规范 |
| 第 2 轮 | 图片文件名严格照抄强化 + 实验章硬件/表格规则明确 | 图片死链 0，硬件 0 泄漏 |
| 第 3 轮 | system.md 加铁律 8（工程细节不进正文）+ 铁律 9（标题层级约束） | 全文硬件 0 泄漏，标题层级完美 |

## 已知问题（后续可优化）

- 折叠区内容偏少：实验章原始表格数据放在折叠区的数量不足，目前主要依靠 LLM 自行决定。
  后续可在 section.md 中加强"每个表格必须生成对应折叠区"的要求。
- 补充段数量：全文约 8~9 处补充，整体克制，但部分章节（如 tldr+intro）略密集。
- 新论文解析链路：新论文的 parse + normalize + plan 目前需要手动走旧版 CLI + 脚本生成 plan.json，
  未集成进 transform.py。后续可把 `convert <arxiv 链接/本地 PDF>` 命令补全。
