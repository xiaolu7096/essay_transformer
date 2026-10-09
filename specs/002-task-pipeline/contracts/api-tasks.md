# 接口契约：任务提交、列表、详情与取消

**日期**：2026-09-27 | **对应**：[spec.md](../spec.md) FR-004～013、US2–US5

- 基址 `/api`；全部端点需要 Bearer 令牌（同 Spec 001 口径，未认证
  统一 `401 {"detail":"未授权或登录已过期"}`）。
- 数据模型见 [data-model.md](../data-model.md)；状态机见其 §2。
- **归属规则（不可协商）**：详情与取消路径虽带任务 id，但服务端
  一律按"当前登录用户 + id"查询。查不到（不存在或属于他人）统一
  返回 `404 {"detail":"任务不存在"}`——不区分两种情况，不泄露
  存在性。

## 1. 提交：arXiv 链接

`POST /api/tasks/from-url`

```json
{ "source": "https://arxiv.org/abs/1706.03762" }
```

服务端动作（顺序即拒绝优先级）：

1. 登录态；
2. 真实模式校验两个密钥已配置（fake 模式跳过）；
3. 用 core 的链接识别规则校验 arXiv 链接/ID；
4. 事务内检查并占用该用户活动槽（部分唯一索引兜底）。

**201 Created** —— 返回 `TaskOut`（形态见 §5），初始
`status="queued"`、`source_kind="arxiv"`、`source_ref` 为规范化后的
arXiv id。

**错误**：

| 状态码 | body | 触发 |
|---|---|---|
| 400 | `{"detail":"尚未配置论文解析/大模型服务的密钥，请先到密钥设置完成配置","code":"credentials_required"}` | 真实模式缺任一密钥 |
| 409 | `{"detail":"你已有一个进行中的任务，请等待其完成或取消后再提交","code":"active_task_exists"}` | 该用户存在 queued/running 任务（含并发竞争失败方） |
| 422 | 标准校验错误体（不回显提交值） | source 为空/超长，或不是受支持的 arXiv 链接 |

> 非法链接在创建任务前拒绝，不产生任务行、不调外部服务。

## 2. 提交：PDF 上传

`POST /api/tasks/upload`

- `Content-Type: multipart/form-data`
- 字段：`file`（单个 PDF；原始文件名记为 `source_ref`，长度截断
  至 255；同名无关）

服务端流式接收并按 **大小 → 扩展名 → `%PDF` 魔术字节** 顺序校验，
任一不过立即终止：

- 超过 `WEB_UPLOAD_MAX_MB`（默认 50MB）→
  **413** `{"detail":"文件超过 50MB 上限","code":"file_too_large"}`
- 非 PDF 扩展名（或无扩展名且内容非 PDF）→
  **422** `{"detail":"仅支持 PDF 文件","code":"invalid_file_type"}`
- 内容缺少 `%PDF` 头 →
  **422** `{"detail":"文件不是合法 PDF（缺少 PDF 文件头）","code":"invalid_pdf"}`
- 真实模式缺密钥 → 400 `credentials_required`（同 §1）
- 已有活动任务 → 409 `active_task_exists`（同 §1）

校验全部通过后：创建 `queued` 任务行、把文件保存到
`runs/<user_id>/<task_id>/source.pdf`，返回 **201 + TaskOut**
（`source_kind="upload"`）。传输中断/超限/非法均不留任务行与文件
残留（服务端清理半成品目录）。

## 3. 任务列表

`GET /api/tasks`

**200**

```json
{
  "tasks": [
    { "id": "...", "source_kind": "upload", "source_ref": "paper.pdf",
      "status": "succeeded", "stage": null, "progress": 1.0,
      "message": "完成：12345 字符", "fake_mode": false,
      "failure": null,
      "result": { "title": "Attention Is All You Need", "section_count": 7, "total_chars": 12345 },
      "created_at": "...", "started_at": "...", "finished_at": "..." }
  ]
```

- 仅返回当前用户的任务，按 `created_at` 倒序；
- 不分页（v0 单用户任务量小；契约保留数组信封，未来可加游标字段而
  不破坏既有字段）；
- 列表项与详情同形（§5），前端无需二次适配。

## 4. 任务详情 / 取消

### 4.1 详情

`GET /api/tasks/{task_id}`

**200** + TaskOut（§5）；不存在或不属本人 → **404**
`{"detail":"任务不存在"}`。

### 4.2 取消

`POST /api/tasks/{task_id}/cancel`

- 属主校验（同上 404 语义）；
- 仅 `queued`/`running` 可取消；终态任务 →
  **409** `{"detail":"该任务已结束，无法取消","code":"task_not_active"}`；
- 请求在置位取消事件后**立即**返回当前 TaskOut（通常仍是
  running/queued，`message` 不变；终态由后台在下个阶段边界写入）。
  前端继续轮询直到看到 `cancelled`。

**200** + TaskOut（取消已受理的当前快照）。

## 5. TaskOut 统一形态

```json
{
  "id": "uuid",
  "source_kind": "arxiv | upload",
  "source_ref": "1706.03762 | paper.pdf",
  "status": "queued | running | succeeded | failed | cancelled",
  "stage": "ingest | parse | normalize | plan | rewrite | assemble | null",
  "progress": 0.61,
  "message": "转写 method (5/7)...",
  "fake_mode": false,
  "failure": {
    "category": "input | service | system",
    "code": "invalid_source | key_rejected | key_missing | service_unavailable | internal_error | interrupted_restart",
    "message": "面向用户的固定中文文案"
  },
  "result": {
    "title": "论文标题",
    "section_count": 7,
    "total_chars": 12345
  },
  "created_at": "2026-09-27T10:00:00",
  "started_at": "2026-09-27T10:00:02",
  "finished_at": null
}
```

字段规则：

- `failure` 仅在 `status=failed` 非空；`result` 仅在
  `status=succeeded` 非空；
- `progress/status/stage/message` 是最新快照（本特性前端 3 秒轮询；
  SSE 在 Spec 003）；
- 永不出现文件系统路径、密钥、堆栈。

## 6. 失败分类与文案（failure 字段）

| category | code | 用户可见 message（固定模板，实施时可润色但不得拼接异常原文） |
|---|---|---|
| input | invalid_source | 论文链接或文件无法识别，请使用合法的 arXiv 链接或有效的 PDF 文件后重新提交 |
| input | scan_without_text | 该 PDF 没有可提取的文本层（可能是纯扫描件），请更换文件 |
| service | key_missing | 服务密钥未配置，请先到密钥设置填写后重试 |
| service | key_rejected | 外部服务拒绝了密钥（缺失、错误或已失效），请到密钥设置检查对应密钥后重试 |
| service | service_unavailable | 外部解析或模型服务暂时不可用（超时或服务故障），请稍后重试 |
| system | interrupted_restart | 服务曾重启，该任务被中断，请重新提交 |
| system | internal_error | 任务因系统内部问题终止，请联系维护者 |

分类规则与"core 把鉴权失败抛成 InputError"的边界修正见
[research.md D5](../research.md)；原始异常只进服务端日志（脱敏后）。

## 7. 假模式行为差异（`WEB_FAKE_MODE=1`，运维侧开关）

- 提交两类端点均**跳过密钥检查**（400 credentials_required 不触发）；
- 任意合法来源（链接或任意通过 PDF 校验的上传）都创建任务并立即
  排队；执行使用 `web/fixtures/fake_run/` 内置物料，六阶段、进度、
  状态结构与真实模式完全一致；
- TaskOut 中 `fake_mode=true`，成功结果标题为样例论文标题；
- 开关只来自服务端环境变量，请求体/请求头中任何字段都不能开启或
  关闭假模式。

## 8. 本契约明确不提供（留给 Spec 003）

- SSE/任何实时推送通道；
- 成品 Markdown 在线读取、分章内容、图片资源、.md 文件下载端点；
- 任务删除/重命名/重试按钮级端点（重新提交=新建任务，本特性
  已可完成）。
