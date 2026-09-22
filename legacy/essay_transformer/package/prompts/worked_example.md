# 任务：关键公式手算示例生成（schema_kind = worked_example）

## 输入

- `{{FORMULA_BLOCK_ID}}`：关键公式的 normalized 块 id
- `{{FORMULA_LATEX}}`：关键公式 LaTeX（与原文一致）
- `{{EXAMPLE_SPEC}}`：planner 给出的 toy 设定（`tokens` 3–5 个词、`steps`）

## 目标

用一组**教学构造的 toy 数据**，把关键公式（self-attention 的
`softmax(QK^T/√d_k)·V`）用 3 个词完整走一遍，让读者能照着独立复算。

**你只负责两件事：**

1. **选一组简单、有教学意义的 Q/K/V 小整数矩阵**作为 toy 输入（优先
   0/1/2 等小整数，让分数与权重便于读者心算，且避免三行完全雷同导致
   softmax 权重毫无区分度）；
2. **为固定四步各写一句中文讲解**（这一步在做什么、为什么需要它）。

**你不需要、也不允许做任何矩阵算术**：四步结果矩阵与最终 result 一律由
系统用 numpy 从你给的 Q/K/V 确定性计算，输出 schema 中也没有
`matrix`/`result` 字段——请不要输出它们。

## 设定要求

- `setup.tokens`：恰好 3 个中文 toy 词（按 example_spec，3–5 范围内）。
- `setup.d_k`：固定为 `3`；Q/K 均为 3×3 数值矩阵（3 行对应 3 个 token，
  每行 3 列对应 d_k=3），V 为 3 行矩阵（每行对应该 token 的值向量）。
- 矩阵只允许使用 JSON 嵌套数组表达，元素为数字（不要字符串、不要 LaTeX）。
- 这是教学示意，**不是论文真实数据**：
  - `is_toy_data` 必须为 `true`；
  - `disclaimer` 必须逐字为 `"教学示意/非论文真实数据"`。

## 四步（`steps` 恰好 4 步，顺序固定，不可增删）

1. 计算相关性分数：`Q K^T`
2. 缩放：除以 `sqrt(d_k)`（d_k=3）
3. 逐行 softmax 得注意力权重：`softmax(...)`（每行权重和约为 1）
4. 加权求和：`weights · V` 得到输出

每个 step 只含三个字段：

- `name`：简短中文步骤名；
- `expr`：该步数学表达式；
- `explain_zh`：一句话中文解释这一步在做什么、为什么需要它。
  **只讲含义，不要罗列或心算任何数字**（成稿会单独渲染系统计算的矩阵）。

## 事实纪律

- Q/K/V 数值是你为教学设计的自造数据，不声称来自论文；除公式本身外不得
  编造论文中的数字或结论。
- 公式 LaTeX 保持原文，不改符号。

## 输出形态

只输出符合 schema_kind=worked_example 的 JSON 对象（`formula_block_id`、
`is_toy_data`、`disclaimer`、`setup`、`steps`），无其他文本。
示例骨架（数字仅为格式示意，实际请自行挑选有教学意义的整数）：

```json
{
  "schema_kind": "worked_example",
  "formula_block_id": "blk-0073",
  "is_toy_data": true,
  "disclaimer": "教学示意/非论文真实数据",
  "setup": {
    "tokens": ["思考", "是", "力量"],
    "d_k": 3,
    "Q": [[1, 0, 1], [0, 1, 1], [1, 1, 0]],
    "K": [[1, 0, 0], [0, 1, 0], [1, 1, 1]],
    "V": [[1, 2, 0], [0, 1, 1], [1, 0, 1]]
  },
  "steps": [
    {"name": "计算相关性分数", "expr": "Q K^T",
     "explain_zh": "每个查询向量与所有键向量做点积，得到逐对相关性分数。"},
    {"name": "缩放", "expr": "scores / sqrt(d_k)",
     "explain_zh": "除以根号 d_k，防止点积随维度增大而偏大。"},
    {"name": "行 softmax 得注意力权重", "expr": "softmax(scaled)",
     "explain_zh": "逐行归一化为非负、和为 1 的注意力权重。"},
    {"name": "加权求和", "expr": "weights · V",
     "explain_zh": "用注意力权重对值向量加权求和，得到融合上下文的输出。"}
  ]
}
```
