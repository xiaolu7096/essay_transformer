# 数据模型：论文转写任务流水线

**日期**：2026-09-27 | **对应规格**：[spec.md](./spec.md) | **方案**：[plan.md](./plan.md)

沿用 Spec 001 的 SQLite + SQLAlchemy 2.x（同步引擎，
`PRAGMA foreign_keys=ON`）。本特性新增两张表，外键关联既有
`users(id)`；不修改 users 表结构。时间列一律 naive UTC（ISO 字符串
经 JSON 输出）。

## 1. 表结构

### 1.1 `credentials` —— 每用户每服务一份的加密密钥

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | VARCHAR(36) | PK | uuid4 |
| `user_id` | VARCHAR(36) | FK→users.id ON DELETE CASCADE，非空 | 属主 |
| `service` | VARCHAR(20) | 非空 | 服务种类：`mineru` \| `deepseek`（Pydantic Literal 约束） |
| `secret_encrypted` | VARCHAR(255) | 非空 | Fernet token（`gAAAAA...`）；唯一明文口径经 `web/app/crypto.py` |
| `mask` | VARCHAR(40) | 非空 | 展示掩码，`••••` + 明文末 4 位，保存时算好 |
| `created_at` | DATETIME | 非空 | naive UTC |
| `updated_at` | DATETIME | 非空 | naive UTC，覆盖更新时刷新 |

**索引/约束**：

- `UNIQUE(user_id, service)`：每用户每服务至多一份；保存=upsert。

**安全口径**：

- 任何 SELECT 列表接口都不返回 `secret_encrypted`；序列化模式只有
  `service / configured / mask`。
- 解密取明文只有 worker 一处（services 层提供
  `get_decrypted(user_id, service)`），明文只作为参数进入 core。
- 删除即整行 DELETE；删除后 GET 返回 `configured=false, mask=null`。

### 1.2 `tasks` —— 转写任务

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| `id` | VARCHAR(36) | PK | uuid4，对外任务标识（不可枚举） |
| `user_id` | VARCHAR(36) | FK→users.id ON DELETE CASCADE，非空 | 属主；所有查询必带 |
| `source_kind` | VARCHAR(10) | 非空 | `arxiv` \| `upload`（Pydantic Literal） |
| `source_ref` | VARCHAR(500) | 非空 | arXiv id/链接原文，或上传时的原始文件名 |
| `status` | VARCHAR(12) | 非空，默认 `queued` | 状态机当前态，取值见 §2 |
| `stage` | VARCHAR(12) | 可空 | 六阶段之一：`ingest`/`parse`/`normalize`/`plan`/`rewrite`/`assemble`；排队中为 NULL |
| `progress` | FLOAT | 非空默认 0 | 0.0–1.0，直接来自 core progress_cb |
| `message` | VARCHAR(300) | 可空 | core 回调的中文状态文字（如"转写 method (5/7)..."） |
| `failure_category` | VARCHAR(10) | 可空 | `input` \| `service` \| `system`（仅 failed 时非空） |
| `failure_code` | VARCHAR(40) | 可空 | 机器码，见契约表（如 `invalid_source`/`key_rejected`/`service_unavailable`/`internal_error`/`interrupted_restart`） |
| `failure_message` | VARCHAR(300) | 可空 | **固定模板**中文可行动文案；不含原始异常、不含密钥片段 |
| `result_title` | VARCHAR(300) | 可空 | 成功：论文标题（PaperResult.title） |
| `result_section_count` | INTEGER | 可空 | 成功：章节数（恒为 7，来自 len(sections)） |
| `result_total_chars` | INTEGER | 可空 | 成功：成品总字符数 |
| `fake_mode` | BOOLEAN | 非空默认 0 | 该任务是否由假模式执行（展示"演示任务"标记） |
| `active_slot` | INTEGER | 可空 | 活动任务恒为 1，终态置 NULL——部分唯一索引的槽位列（D3） |
| `created_at` | DATETIME | 非空 | naive UTC |
| `started_at` | DATETIME | 可空 | worker 实际开跑（queued→running） |
| `finished_at` | DATETIME | 可空 | 进入任一终态时 |

**索引**：

- `INDEX ix_tasks_user_created (user_id, created_at DESC)`：列表主
  查询（按属主倒序）。
- **部分唯一索引**（每用户单活动任务，D3）：

```sql
CREATE UNIQUE INDEX uq_tasks_one_active_per_user
    ON tasks(user_id, active_slot)
    WHERE status IN ('queued', 'running');
```

  活动行 `active_slot=1`；任何终态化更新同时 `active_slot=NULL` 释放
  槽位。并发双提交时竞争失败方收到 IntegrityError → 服务端回滚并
  返回 409。

## 2. 任务状态机

```text
                提交成功
                  │
                  ▼
              ┌────────┐  worker 开跑（先查取消事件）   ┌─────────┐
              │ queued │ ─────────────────────────────► │ running │
              └────────┐                                 └────┬────┘
                       │ 开跑前发现取消事件已置位            │
                       └──────────────┐                     │
                                      ▼                     │
                                 ┌───────────┐               │
              取消事件置位，       │ cancelled │ ◄───────────┤ 阶段边界检查到置位
              尚未进入终态都来这里 └───────────┘               │
                                                              │
              ┌──────────────────────────────┬────────────────┴───────────┐
              ▼                              ▼                            ▼
        ┌───────────┐                 ┌────────┐                  ┌──────────┐
        │ succeeded │                 │ failed │                  │ cancelled│
        │（终态）   │                 │（终态）│                  │（终态）  │
        └───────────┘                 └────────┘                  └──────────┘
```

**合法转换**：

| 从 | 到 | 触发者 | 附加动作 |
|---|---|---|---|
| —（新建） | queued | 提交端点（事务内） | active_slot=1；上传时已落 source.pdf |
| queued | running | worker 首动作 | started_at=now，stage/message 更新 |
| queued | cancelled | worker 首动作发现事件置位 | finished_at，active_slot=NULL |
| running | cancelled | worker：core 在边界抛 ETError 且事件置位 | finished_at，active_slot=NULL；不写 result_* |
| running | succeeded | worker：PaperResult 正常返回 | 写 result_title/section_count/total_chars，progress=1.0，finished_at，active_slot=NULL |
| running | failed | worker：捕获异常且事件未置位 | failure_category/code/message（分类表见 research D5），finished_at，active_slot=NULL |
| queued/running（残留） | failed(system/interrupted_restart) | 启动恢复 SQL | finished_at=重启时刻，active_slot=NULL |

**终态竞争规则（US4 场景 2）**：所有离开活动态的更新必须是条件
更新 `WHERE id=:id AND status IN ('queued','running')`；rowcount=0
说明已有一路终态化，后到者不覆盖、不重复写产物。

**取消请求语义**：取消端点只做 ① 属主校验 ② 状态必须是
queued/running（否则 409）③ `cancel_registry.set(task_id)`（事件不
存在则补建——极短窗口下兼容排队未落注册表），不直接修改状态行；
终态由 worker 收口写入。

## 3. 进度数据口径

- 完全使用 core 既有回调 `(stage, progress_0_1, message)`：六阶段
  英文名写 `stage`，0–1 浮点写 `progress`，中文 message 截断 300 字
  内写 `message`。
- 回调发生在 worker 线程：每次回调用短生命周期 `SessionLocal()`
  独立会话写库（engine 已 `check_same_thread=False`），只更新
  stage/progress/message 且加 `status='running'` 条件。
- 不新增进度事件表（SSE 与时间线在 Spec 003 设计）；本特性只保留
  最新快照。

## 4. 文件产物布局

```text
<WEB_RUNS_DIR>/                        # 默认 仓库根/runs/
└── <user_id>/                         # 用户隔离第一层（uuid，不可枚举）
    └── <task_id>/                     # 任务隔离第二层（uuid）
        ├── source.pdf                 # 上传原件；链接任务为 ingest 下载件；
        │                              # fake 任务为内置 fixture 复制件
        ├── parsed/                    # MinerU 产物（真实）/ fixture（fake）
        │   ├── content_list.json
        │   ├── full.md
        │   └── images/
        ├── images/                    # assemble 拷贝出的图片
        └── (core 其余输出)            # PaperResult 对应文件；output.md 在
                                       # 003 由阅读器/下载端点消费
```

- 目录路径只能由服务端按 user_id+task_id 拼出（`runner/paths.py`
  单一函数），任何 API 不接收路径参数。
- 本特性不提供任何文件下载/读取端点；产物安全落盘即可，访问端点
  在 Spec 003 并同样过登录+归属校验。
- `.gitignore` 增补 `runs/`（实施时确认根 .gitignore；fixture 在
  `web/fixtures/`，不受影响）。

## 5. JSON 序列化视图（与契约对应）

`TaskOut`（列表项与详情同形，列表天然是其数组）：

```json
{
  "id": "uuid",
  "source_kind": "arxiv",
  "source_ref": "1706.03762",
  "status": "running",
  "stage": "rewrite",
  "progress": 0.61,
  "message": "转写 conclusion (3/7)...",
  "fake_mode": false,
  "failure": null,
  "result": null,
  "created_at": "2026-09-27T10:00:00",
  "started_at": "2026-09-27T10:00:02",
  "finished_at": null
}
```

- `failure` 为 null 或 `{category, code, message}`；
- `result` 为 null 或 `{title, section_count, total_chars}`；
- 不含任何文件系统路径、不含任何密钥/密文列。

`CredentialStatusOut`：

```json
{ "service": "mineru", "configured": true, "mask": "••••ab3f" }
```
