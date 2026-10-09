# 数据模型：实时进度与成品阅读

**日期**：2026-09-28 | **关联**：[spec.md](./spec.md)、[research.md](./research.md)
| 上游：`specs/002-task-pipeline/data-model.md`

## 0. 总览：本特性不新增持久化数据

- **不新增表、不新增列、不做迁移**。002 的 `tasks`/`users`/
  `credentials` 三表与状态机原样保留。
- 实时事件是**进程内瞬时消息**：生命周期等于一条 HTTP 流连接，
  不落库、不回放；任务的"真相来源"始终是 002 的 tasks 行
  （最新状态快照）。
- 阅读器内容是对 002 已落盘产物（`output.md`、`images/`）的
  **只读视图模型**，不产生任何新文件。

```text
tasks 行（002，持久化真相）
  │  worker：先 commit 后 publish
  ▼
ProgressHub（本特性，进程内，易失）
  │  subscribe(task_id) → asyncio.Queue
  ▼
SSE 帧（本特性，瞬时）── 首帧 snapshot 来自 tasks 行
  │
  ▼ 任务 succeeded 时另一条只读路径
services/artifacts.py 解析 runs/<uid>/<tid>/output.md
  │
  ▼
ReaderView 视图模型（不持久化，每次请求重建）
```

## 1. 实时事件模型（瞬时）

### 1.1 ProgressHub 内存结构

```text
ProgressHub（app.state.hub 单例）
├── _loop: asyncio.AbstractEventLoop   # lifespan 绑定的主循环
├── _seq: dict[task_id, int]          # 任务内单调递增水位（易失）
└── _subs: dict[task_id, set[Queue]]  # 每任务零到多个订阅者
```

- Queue：`asyncio.Queue(maxsize=256)`；
- 入队在主循环线程执行（worker 线程经
  `call_soon_threadsafe` 调度），seq 在同一临界线内自增，
  保证同一任务发布顺序 == seq 顺序；
- 队列满：丢弃**最旧的非终态**帧；terminal 帧强制保留
  （终态丢失会导致客户端永远等待，为最高优先级）；
- 进程重启后 hub 为空：重连客户端以首帧快照收敛
  （002 recovery 已把残留任务置 failed/interrupted_restart）。

### 1.2 事件帧载荷（与 002 TaskOut 同口径，无路径/密文）

帧为标准 SSE：`event: <类型>\n` + `data: <JSON>\n\n`；
另有 `: ping\n\n` 心跳注释（15 秒，无 data）。

| 帧类型 | 时机 | data 载荷 |
|---|---|---|
| `snapshot` | 连接建立后第一帧（必有且仅有一帧） | TaskEventPayload |
| `update` | 活动任务阶段/进度/状态文字变化 | TaskEventPayload |
| `terminal` | 任务进入 succeeded/failed/cancelled 的终态广播 | TaskEventPayload |

```jsonc
// TaskEventPayload（字段与 002 TaskOut 同源子集，逐字沿用命名）
{
  "id": "uuid",
  "status": "queued | running | succeeded | failed | cancelled",
  "stage": "ingest | parse | normalize | plan | rewrite | assemble | null",
  "progress": 0.0,
  "message": "已入队，等待执行…",
  "fake_mode": false,
  "failure": null,
  "result": null,
  "started_at": null,
  "finished_at": null,
  "seq": 12
}
```

- `seq` 是**帧信封字段**（同任务单调递增，从 1 起），不属于
  tasks 表；snapshot 帧的 seq 为发布时水位（可能为 0，表示该
  任务尚无广播事件，以快照行为准）；
- failure/result 嵌套结构与 002 `TaskFailure`/`TaskResult`
  完全一致（三分类文案仍来自 errors.MESSAGES 固定模板）；
- 载荷中**禁止**出现 run_dir、绝对路径、密钥与原始异常文本。

### 1.3 发布点（002 worker 既有写入点，先 commit 后 publish）

| 写入点（pipeline_run.py） | 发布帧 | 备注 |
|---|---|---|
| `persist_progress(...)` commit 后 | update | stage/progress/message |
| `_finish_success` commit 后 | terminal | 同时携带 result 摘要 |
| `_finish_failure` commit 后 | terminal | 携带 failure 三分类 |
| `_finish_cancelled` commit 后 | terminal | message="任务已取消" |

- 提交端点创建 queued 行**不发事件**：新连接首帧快照即 queued；
- publish 本身异常只记 warning，不得回滚或影响任务状态
  （推送是表现层副作用）；
- queued→running 的跳变由 running 后首个 update 覆盖。

### 1.4 订阅/水位规则（无缺口、无倒退）

```text
连接 → 认证/归属（401/404 在流之前）
     → subscribe(task_id) 登记队列
     → snap_seq = hub.current_seq(task_id)
     → db 读取任务行（snapshot 真相）
     → 发 snapshot(载荷, seq=snap_seq)
     → 循环转发队列中 seq > snap_seq 的帧
     → 收到 terminal / 快照即终态：补发 terminal 后关闭
```

不变式（测试固化）：

1. 任何已 commit 的状态，要么在快照行内，要么存在
   seq > snap_seq 的帧中（先订阅再快照 + 先提交后发布）；
2. 客户端应用顺序：snapshot → 后续仅接受 seq 递增的帧；
   重复/迟到帧不改变界面（不倒退、不重复终态）；
3. 终态必达：晚订阅走"快照即终态→terminal→关闭"；
4. 终态后服务端关闭连接，客户端不再重连该任务。

## 2. 阅读器视图模型（只读、每次重建）

### 2.1 output.md 物理格式（002 已存在，本特性只消费）

```text
# {论文标题}

## 0 · 30 秒摘要
{章正文 Markdown，标题已降级，不含 ##}

## 1 · …（共 7 个固定二级标题，planner.SLUG_ORDER）
…

---

## 附录（折叠区）            ← 可能不存在
<details>
<summary>实验数据与表格原文</summary>
{原始 HTML 表格 / ![](images/xxx.png) / 参考文献文本}
</details>
```

- 图片一律相对引用 `images/{name}`；
- 编码 UTF-8（无 BOM）；行尾不做假设，解析按行切分。

### 2.2 解析规则（services/artifacts.py）

| 规则 | 说明 |
|---|---|
| title | 首个行首 `# `（仅一个井号）去前缀并 trim；缺失时回退 tasks.result_title；再缺失回退 source_ref |
| 章节切点 | 行首恰好 `## `（两个井号+空格）；`### ` 等属于章正文（core 已做标题降级） |
| slug | 按出现顺序 `s0`、`s1` …（稳定、与标题文字无关；附录即最后一个 slug） |
| heading | 去掉行首 `## ` 后的原文 |
| content | 该标题行之后、下一个 `## ` 行之前的全部原文（trim 首尾空行），**原样保留**（含 `<details>`、HTML 表格、`$...$`、相对图片链接） |
| 前导内容 | 一级标题与首个 `## ` 之间的非空文字并入 s0 内容之前（实测为空，规则仅为健壮性） |

解析结果（不持久化）：

```jsonc
{
  "task_id": "uuid",
  "title": "Sample Paper: A Demonstration of the Pipeline",
  "fake_mode": true,
  "total_chars": 923,
  "sections": [
    { "slug": "s0", "heading": "0 · 30 秒摘要", "content": "…" }
    // … s1…s6；若有折叠区则 s7 = 附录（折叠区）
  ]
}
```

- sections 正常为 7（与 result_section_count 一致），含折叠区
  为 8；解析器不对此强校验（历史任务与 core 演进容错），
  `sections` 为空才视为成品不可读；
- 深链锚点：`/tasks/{id}/reader#s3`；非法/越界 slug 回落页首。

### 2.3 图片资源安全模型

```text
runs/<user_id>/<task_id>/images/<name>
```

- 端点只接受 `name`，合法性：
  - 正则 `^[A-Za-z0-9._-]+$`（无 `/ \ ..` 与控制字符）；
  - 扩展名 ∈ {png, jpg, jpeg, gif, webp}（小写比较）；
    **svg 不放行**；
  - `(images_dir / name).resolve()` 必须仍在
    `images_dir.resolve()` 之下；
- 命中：按扩展名返回字节（200，正确图片 content-type，
  可缓存：私有 `Cache-Control: private, max-age=300`——
  objectURL 生命周期之外浏览器不缓存跨用户响应，因仍需
  Authorization 头，不落共享缓存）；
- 未命中/死链：404（前端转占位）；越权/任务不存在：404；
  任务非 succeeded：409 `task_not_ready`。

### 2.4 下载打包模型（zip）

下载端点**不直接回 output.md**，而是在内存中用标准库 zipfile
组一个 zip（不落临时文件）：

```text
<base>/                       # 单层根目录，解压不散落
├── <base>.md                 # output.md 原始字节（逐字一致）
└── images/
    └── <name>…               # 仅任务 images/ 下真实存在且扩展名
                              # 命中白名单（png/jpg/jpeg/gif/webp）的文件
```

- `<base>` 同时是压缩包根目录名与包内 Markdown 文件名（不含
  扩展名部分）；
- 条目 arcname 全部由服务端用安全段拼装：图片条目名固定
  `<base>/images/<name>`，`name` 必须过 §2.3 的
  `^[A-Za-z0-9._-]+$`；不接受外部传入路径，从构造上杜绝
  Zip Slip（条目内不可能出现 `..`/绝对路径/盘符）；
- 死链（Markdown 引用了但磁盘不存在的图片）不产生条目；
- 无图片成品：zip 只含根目录与 .md，仍正常下载。

`<base>` 安全化规则：

| 输入 | 处理 |
|---|---|
| result_title / output.md 一级标题 | safe_filename：去控制字符与 `/ \ : * ? " < > |`、去首尾点空格、折叠空白、限长 80、剔除 Windows 保留名（CON/PRN/AUX/NUL/COMx/LPTx） |
| 结果为空 | `paper-{task_id 前 8 位}` |
| 压缩包文件名 | `<base>.zip`（响应头给出） |
| 响应头 | `Content-Disposition: attachment; filename="ascii.zip"; filename*=UTF-8''<百分号编码 base>.zip`；ASCII 回退名中非 ASCII 字符以 `-` 替代 |

## 3. 状态机影响

无新增状态、无状态迁移变化。仅在读取侧增加一条门禁：

```text
reader 正文 / images / download
  └─ 仅 status == 'succeeded' 放行
      queued/running/failed/cancelled → 409 task_not_ready
```

推送订阅本身**不要求 succeeded**：活动任务需要事件，失败/
取消任务也可能在重连时被订阅——events 端点对任意本人任务
开放（终态即快照+关闭）。

## 4. 错误码增量（并入 002 §6 风格，契约给出文案）

| HTTP | code | 触发 |
|---|---|---|
| 409 | `task_not_ready` | 对非 succeeded 任务读正文/图片/下载 |
| 409 | `artifact_missing` | 任务 succeeded 但 output.md 不存在（被外部删除等）；统一文案不泄露路径 |
| 404 | `任务不存在`（detail，无 code） | 越权/不存在（沿用 002，events 同样在建流前返回） |
| 404 | —（图片） | 资源名合法但文件不存在（死链占位） |

## 5. 假模式物料增量（Web 层）

```text
web/fixtures/fake_run/parsed/
├── content_list.json   # 13 块 → 14 块：+1 figure(blk-0011)
└── images/
    └── sample.png      # 新增：最小合法 PNG
```

- 经真实 core 管线：figure 被 planner 折叠进附录 →
  copy_images 拷贝 → output.md 附录出现
  `![…](images/sample.png)` → 死链为空；
- 阅读器端到端因此具备：公式（既有 equation 块）、图片
  （新增）、折叠区与 HTML 表格（既有）三类要素。
