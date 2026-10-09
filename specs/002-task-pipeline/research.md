# 研究记录：论文转写任务流水线

**日期**：2026-09-27 | **对应方案**：[plan.md](./plan.md)

所有 NEEDS CLARIFICATION 在设计前解决；本文件记录决策、理由与被否
方案。探针结论基于仓库真实代码与 `.venv` 实测（2026-09-27）。

---

## D1：每用户第三方密钥的加密方案

**决策**：采用 **Fernet（cryptography 包）** 对称加密；主密钥仅来自
环境变量 `WEB_MASTER_KEY`（标准 urlsafe base64 的 32 字节密钥）。
加密封装集中在唯一模块 `web/app/crypto.py`：
`encrypt_secret(plaintext) → token`、`decrypt_secret(token) → plaintext`、
`mask_secret(plaintext) → "••••" + 末 4 位`。

**理由**：

- 宪法原则四已指定 Fernet；Fernet 自带随机 IV、时间戳与 HMAC 防
  篡改，密文可直接存 VARCHAR，无需设计加密原语组合。
- 单用户本地/内网 v0 无 KMS/HSM 条件；环境变量主密钥 + DB 密文已
  把"拖库即得明文"和"备份泄露"两类主要风险压住。
- 掩码在保存时由明文计算（末 4 位），展示侧永远不需要解密；
  API Key 普遍 32+ 字符，末 4 位可辨认且不泄露有效信息。

**被否方案**：

- AES-GCM 手工拼装：需要自己管 nonce/版本字节，对 v0 是重复造轮子，
  且宪法已点名 Fernet。
- 只哈希不加密（像密码那样）：执行任务时必须取回明文传给 core，
  单向哈希不可行。
- 每用户独立主密钥派生（从用户密码）：用户密码不入库（仅 pbkdf2
  哈希），无法在后台任务时取得派生材料；超出 v0 需要，引入重登后
  密钥不可解等复杂度。

**实施要点**：`credentials` 表唯一键 `(user_id, service)`；明文长度
限制 1–200；加密 token 与 mask 分列存储；更新=覆盖密文/掩码/
updated_at；删除=整行删除。

---

## D2：后台执行模型与线程桥接

**决策**：在应用 lifespan 创建一个**进程内有界
`ThreadPoolExecutor(max_workers=WEB_MAX_CONCURRENT_TASKS，默认 2)`**，
由 `TaskExecutor`（端口）持有；提交接口在 async 路由中以
`asyncio.create_task` + `anyio.to_thread.run_sync(worker, ...)` 的方式
把同步 core 调用桥接进线程池（落实宪法原则五原文）。路由本身在
入队后立即返回。

**状态入队语义**：任务先以 `queued` 落库再入池；池满时由
ThreadPoolExecutor 自身队列排队，worker 开始执行的第一动作是把状态
推进为 `running` 并写 started_at；活动事件（取消用）在提交时即注册，
因此排队中的任务也可取消（worker 开跑先检查事件，置位则直接
cancelled，不调 core）。

**理由**：

- 同步 core + SQLite，线程模型比多进程简单；转写是 IO 等待为主
  （MinerU 轮询、LLM 等待），2 个并发线程对演示场景足够。
- anyio 随 FastAPI 已在依赖树，不新增包；桥接同步 core 是其文档
  推荐场景。
- 路由依赖的是 `TaskExecutor` 的 submit/cancel 形态而非线程池细节，
  未来 Celery/RQ 适配器替换时契约不动。

**被否方案**：

- FastAPI BackgroundTasks：本质在响应发出后于同事件循环线程/有限
  工作线程中跑，长任务会占住服务线程、无有界并发与排队语义，不
  适合数分钟任务。
- 本特性上 Celery/Redis：宪法明确排除，单机演示无收益，重运维。
- 每任务裸 `threading.Thread`：无上限、无队列、不可测、不可替换。

**可测性**：执行器端口允许测试用直接同步执行的 fake executor
（或把 max_workers 设为 1 并等待事件）驱动确定性状态流转；端到端
测试仍用真实线程池 + fake 物料跑真实 core。

---

## D3：每用户单活动任务的强制方式

**决策**：双保险——

1. 提交时在事务内 `SELECT ... 计数` 同用户
   `status IN ('queued','running')`，存在即 409；
2. 数据库层加**部分唯一索引**兜底并发竞争：
   `CREATE UNIQUE INDEX uq_task_one_active_per_user
   ON tasks(user_id, active_slot) WHERE status IN ('queued','running')`
   （用固定值列 `active_slot=1` 的部分索引；SQLite 支持 partial
   index）。竞争失败同 T043 模式：捕获 IntegrityError → 回滚 → 409。

**理由**：与 Spec 001 注册并发问题同构（先查后插有竞态窗口），
DB 约束是唯一可靠兜底；双标签页同时提交必须恰好进一个。

**终态竞争**：取消与完成都由 worker 线程内的状态推进函数收口：
所有 `queued/running → 终态` 的写入用条件更新
`WHERE id=:id AND status IN ('queued','running')`，rowcount=0 说明
已被另一路终态化，后到者不覆盖（满足 US4 场景 2 的唯一终态）。
取消请求只负责 set event，不直接改终态。

---

## D4：取消信号穿过 core 的精确方式（宪法原则一特许项）

**决策**：唯一概念变更="取消信号"，用标准库 `threading.Event`，
在 core 中只做**加参数、加检查**两件事，默认 None 时行为逐字节
不变：

1. `core/pipeline.py :: transform_paper(..., cancel_event=None)`：
   - 在六个阶段边界（ingest 后、parse 前、normalize 前、plan 前、
     rewrite 每章循环开头、assemble 前）调用统一内部检查
     `_check_cancel(cancel_event)`；
   - 置位时抛**既有** `core.errors.ETError("任务已被用户取消")`
     ——复用既有异常基类，不新增任何异常类；
   - 构造处改为 `MinerUClient(api_key=..., cancel_event=cancel_event)`。
2. `core/mineru_client.py :: MinerUClient.__init__(..., cancel_event=None)`：
   - 保存为 `self._cancel_event`；
   - `_poll_until_done` 每轮循环**开头**检查；轮询等待处：事件为
     None 时保持现有单次 `self._sleep(poll_interval)`；非 None 时
     把等待切成 0.5s 小段，每段前后检查，置位即抛同一 ETError。
     → 取消在"轮询循环的等待间隙"生效，不硬中断进行中的 HTTP 请求
     （宪法原则五的原话边界）。

**web 侧如何区分取消与失败**：worker 捕获 ETError/异常后，若该任务
注册的 cancel_event 已置位 → 终态 cancelled；否则进入错误分类。
不依赖异常类型/消息字符串（消息仅用于日志）。

**理由**：

- 宪法原则一的特许措辞是"`transform_paper()` 新增可选参数
  cancel_event"；原则五又要求取消点包含"MinerU 轮询循环"。不把同一
  事件对象透传进 MinerUClient，轮询期间（最长 10 分钟）无法取消，
  直接违反原则五。透传同一个事件对象、同样默认 None，是满足两条
  原则的唯一最小解释。
- 不新增异常类（errors.py 零改动）：复用 ETError 基类 + web 侧事件
  判定，进一步缩小 diff。
- 不硬中断单次外部调用：进行中的一次轮询 HTTP 请求（timeout 60s）
  或单章 LLM 生成自然结束后才检查，避免产生服务端脏状态/半截文件，
  符合"阶段边界"承诺。

**被否方案**：

- 只在 web 层 progress_cb 里 raise：core 在每个阶段开头会调
  progress_cb，能覆盖六阶段边界与章节之间，但**覆盖不到 MinerU
  轮询循环内部**（轮询期间无回调），违反原则五。
- 在 core 新增 `TaskCancelled(ETError)`：语义更清晰，但超出
  "只增一个参数"的特许字面上限，且不带来额外行为价值，被否。
- 硬中断（关闭 socket/线程终止）：Python 无安全的线程 kill；产生
  不可预期脏状态，宪法明确排除。

**验收**：默认参数回归——现有所有调用（含 CLI/测试）签名与行为
不变；新测试覆盖：排队中取消、rewrite 章节间取消、轮询等待取消
（用可注入 sleep 的 MinerUClient 直接单测，无需真实网络）、取消/
完成竞争唯一终态。

---

## D5：core 三分类异常到面向用户结果的映射

**背景（实测代码）**：core 的 errors.py 只有
`InputError / ServiceError / ETError`，但**两类鉴权失败在 core 里被
抛成 InputError**：

- `mineru_client._as_json`：HTTP 401/403 → `InputError("MinerU 鉴权
  失败：…API Key 缺失、错误或已失效。")`
- `rewrite/llm.py`：异常含 401/403/invalid_api_key/authentication →
  `InputError("LLM API Key 无效或无权限: …")`
- 未传 key：`InputError("mineru_api_key 未设置…")` /
  `InputError("DEEPSEEK_API_KEY 未设置…")`

规格 FR-011/边界情况要求"密钥被服务端拒绝"归**服务问题**。core
分类是历史行为，本特性无权改动（D4 之外 core 冻结），因此由
**web 边界适配器** `web/app/runner/errors.py` 做最终归类：

| core 抛出 | 归类 category | code | 用户指引要点 |
|---|---|---|---|
| InputError，消息命中鉴权/密钥类标记（"鉴权失败"、"API Key 无效"、"无权限"、"未设置"） | service | `key_rejected` / `key_missing` | 检查对应服务密钥是否正确/有效，更新后重试 |
| InputError 其他（链接不支持、404、PDF 非法、纯扫描件等） | input | `invalid_source` 等 | 更换合法 arXiv 链接或有效 PDF 后重新提交 |
| ServiceError（超时、5xx、轮询失败、ZIP 损坏等） | service | `service_unavailable` | 外部服务暂时故障，稍后重试 |
| ETError 且取消事件置位 | （非失败） | — | cancelled 终态 |
| ETError 其他 / 未知 Exception | system | `internal_error` | 统一维护者引导，不向前端吐堆栈 |

**消息脱敏（FR-002/原则四）**：分类器生成的用户文案不得拼接原始
异常文本（LLM 异常 `str(e)` 可能包含请求头/key 片段）。映射表为
每个 code 预置固定中文模板；原始异常消息只进服务端日志，且日志
出口对疑似 key 模式（`Bearer `、`sk-`、长十六进制串）做正则脱敏。

**前置校验（任务创建前，不消耗外部额度）**：

- 链接：复用 core 纯函数 `core.ingest.is_arxiv_url()` 判定，非法直接
  422（不创建任务）；
- 上传：扩展名、`%PDF` 魔术字节、50MB 上限三项在落盘前校验；
- 密钥：真实模式提交前检查两个凭据均已配置，缺失返回 400
  `credentials_required` 并引导去设置页（fake 模式跳过）。

---

## D6：假模式如何零网络走通 core 全流程

**实测约束（探针已验证）**：

1. `transform_paper()` 先无条件执行 `ingest(source)`：传 arXiv URL
   会真实联网下载 → 假模式必须避免传 URL；
2. fake=True 时 parse 阶段不调 MinerU，但要求 `output_dir/parsed/`
   已存在 `*content_list*`，否则抛 InputError；
3. fake 下 LLM `chat()` 返回固定占位文本，无网络；
4. ingest 对本地 PDF 只检查文件存在 + 前 5 字节 `%PDF`。

**决策（2026-09-27 探针通过）**：在 `web/fixtures/fake_run/` 内置
一套随仓库提交的样例物料：

- `source.pdf`：仅含 `%PDF-1.4 ` 头的最小文件（ingest 校验足够，
  parse 被跳过不会真正解析它）；
- `parsed/content_list.json`：13 个块（文档标题、Abstract、
  Introduction 含一条公式、Method、Experiments 含表格、References
  含一条 ref_text），覆盖 7 章规划与折叠区（表/参考文献）；
- `parsed/full.md`：简短全文（供公式回填分支）。

worker 在 fake 模式：

1. 建 `runs/<user_id>/<task_id>/` 并把 fixture 的 source.pdf 与
   parsed/ 复制进去（core 的输出目录约定不变）；
2. 调 `transform_paper(source=<任务目录内的 fixture pdf>,
   output_dir=<任务目录>, fake=True, progress_cb=, cancel_event=)`；
3. 用户提交时填的来源（链接字符串或上传文件名）仅记入任务行的
   source_kind/source_ref 用于展示，执行字节全部来自 fixture。

探针结果：六阶段回调完整触发，产出 7 节、789 字符成品、0 死链，
全程零网络。

**零网络断言**：测试以 monkeypatch 替换 `requests.Session` 与
`openai.OpenAI` 的出口为"调用即失败"的哨兵，fake 全流程若触网立即
失败；另断言 fixture 路径是唯一输入。

**被否方案**：

- 让 core 支持 fake ingest：属于 D4 之外的 core 改动，违宪，否。
- 要求验收者自备真实解析产物：违反规格"验收者无需自备物料"假设与
  原则七开箱可验收，否。

**编码坑（实施注意）**：content_list.json 必须 **UTF-8 无 BOM**
（core 以 `encoding="utf-8"` 读取，BOM 会导致 JSONDecodeError）；
Windows 上禁止用 PowerShell 5 默认 `Set-Content -Encoding utf8`
生成，物料用仓库直接提交的现成文件。

---

## D7：服务重启后的中断恢复

**决策**：应用 lifespan 建表后、接收流量前执行一次恢复
（`web/app/recovery.py`）：

```sql
UPDATE tasks
   SET status='failed',
       failure_category='system',
       failure_code='interrupted_restart',
       failure_message='服务曾重启，该任务被中断，请重新提交',
       finished_at=:now
 WHERE status IN ('queued','running')
```

线程池与取消事件注册表是进程内内存态，重启即失——不存在"自动续跑"
（规格假设明确排除）。恢复后用户看到可行动的中断状态（SC-007），
可基于同一来源重新提交（受单活动任务约束，旧任务已是终态，不阻挡）。

**理由**：一行条件更新原子完成，无需迁移表；不假装能恢复实际进度
（MinerU 远端批任务状态与本地输出无法保证一致）。

---

## D8：新增配置项与主密钥策略

**决策**（`web/app/config.py` 增量，全部 `WEB_` 前缀，.env.example
同步）：

| 变量 | 默认 | 说明 |
|---|---|---|
| `WEB_MASTER_KEY` | 空 | Fernet 主密钥；**真实模式必填**（空则启动报错，风格同 JWT_SECRET）。仅 `WEB_FAKE_MODE=1` 时允许留空：启动时生成进程内临时密钥并打 warning（加密的凭据重启后不可解——fake 演示本就不依赖持久凭据） |
| `WEB_FAKE_MODE` | `0` | `1` 时驱动 core fake=True、跳过密钥前置检查、使用内置物料 |
| `WEB_MAX_CONCURRENT_TASKS` | `2` | 全局线程池上限（每用户仍恒为 1） |
| `WEB_RUNS_DIR` | `<仓库根>/runs` | 产物根目录，下含 `<user_id>/<task_id>/` |
| `WEB_UPLOAD_MAX_MB` | `50` | PDF 上传大小上限 |

启动时对 Fernet key 做一次合法性校验（Fernet 构造即校验），非法与
缺失同路径快速失败并给生成命令
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`。

---

## D9：上传通道与大小限制

**决策**：上传走独立端点 `POST /api/tasks/upload`（multipart
`multipart/form-data`，字段 `file`），与 JSON 的链接提交分开，避免
单端点内容协商。服务端流式分块读取（1MB/块）并累计，超过
`WEB_UPLOAD_MAX_MB` 立即停止并返回 413，**不落盘、不建任务行**
（边界情况：超限/中断不留残留）。校验顺序：大小 → 扩展名 →
`%PDF` 魔术字节，全部通过后才创建任务并把文件写入任务目录的
`source.pdf`。

链接端点 `POST /api/tasks/from-url` 为纯 JSON `{"source": "..."}`，
用 `core.ingest.is_arxiv_url` 预校验（422）。

**任务目录创建时机**：目录在 worker 开跑时按
`runs/<user_id>/<task_id>/` 创建（而非提交瞬间），保证排队/取消/
失败不产生孤儿目录；上传原件是例外：提交时需暂存，放在
`runs/<user_id>/<task_id>/source.pdf`（此时建目录），worker 复用。

---

## D10：前端轮询形态（临时手段，003 被 SSE 替换）

**决策**：新增 Pinia `stores/tasks.js` 持有列表与详情；在任务列表页
与详情页 `onMounted` 启动 3 秒 `setInterval` 刷新，当页面上不存在
`queued/running` 任务时停止轮询（终态后零流量），`onUnmounted`
清理。所有请求走 Spec 001 既有 axios 单例（自动带 token、401 统一
处理）。

**理由**：规格明确 SSE 归 003；3s 轮询让"后台执行、稍后可见"在
本特性即可验收，且不引入任何 003 会废弃的长连接基建。003 落地时
仅替换 store 的刷新机制，视图与契约不动。

**导航**：AppLayout 顶栏新增"提交任务 / 任务 / 密钥设置"三个登录后
入口；4 个视图（含详情）全部 `requiresAuth`，复用既有守卫与布局，
不新增第二套外壳。
