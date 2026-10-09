---
description: "任务流水线实现清单（按用户故事分阶段，后端 TDD，前端手工验收）"
---

# Tasks：论文转写任务流水线

**输入**：`/specs/002-task-pipeline/` 下的设计文档（plan.md、spec.md、
research.md、data-model.md、contracts/、quickstart.md）

**前置**：plan.md、spec.md（必需），research.md、data-model.md、
contracts/api-credentials.md、contracts/api-tasks.md（已具备）

**测试策略**：后端严格 TDD——每个测试任务先写并确认失败（红），
再写实现转绿；全部测试离线零网络（假模式物料 + monkeypatch 哨兵）。
前端无自动化框架，以 quickstart §4 手工验收为准。

**组织**：任务按用户故事分阶段，每阶段是可独立演示的增量。

## 格式：`[ID] [P?] [Story] 描述`

- **[P]**：可并行（不同文件、不依赖未完成任务）。同一文件的追加
  （models.py、schemas.py、routers/tasks.py、main.py、pipeline_run.py、
  deps.py、前端 router/index.js、AppLayout.vue、api/tasks.js）**绝不
  标 [P]**，必须串行。
- **[Story]**：US1 密钥管理 / US2 提交与后台执行 / US3 历史与失败
  映射 / US4 取消 / US5 假模式；Setup、Foundational、Polish 不带
  Story 标签。
- 描述中给出精确文件路径与必须逐字遵守的约束。

---

## Phase 1：Setup（共享基建）

**目的**：依赖、配置模板、忽略规则与目录骨架。

- [X] T001 在 `web/requirements.txt` 追加两行依赖 `cryptography`（Fernet
  加密）与 `python-multipart`（multipart 上传解析），根
  requirements.txt（core 五库白名单）一行不改；用
  `.\.venv\Scripts\python.exe -m pip install -r web\requirements.txt`
  安装并 `python -c "import cryptography, multipart"` 验证
- [X] T002 [P] 更新 `web/.env.example`：补 `WEB_FAKE_MODE=0`、
  `WEB_MAX_CONCURRENT_TASKS=2`、`WEB_RUNS_DIR=`（留空走默认
  仓库根 runs/）、`WEB_UPLOAD_MAX_MB=50`；把 WEB_MASTER_KEY 注释
  改为"真实模式必填、WEB_FAKE_MODE=1 时可留空（进程内临时密钥）"，
  附 Fernet 密钥生成命令
- [X] T003 [P] 在根 `.gitignore` 增补 `runs/`（保留既有 `data/`、
  `.env*` 与 `!.env.example` 例外规则不动）；确认 `web/fixtures/`
  不在忽略范围（样例物料必须入库）
- [X] T004 [P] 创建空包与物料目录：`web/app/services/__init__.py`、
  `web/app/runner/__init__.py`（均带中文模块文档字符串）、
  `web/fixtures/fake_run/` 目录

---

## Phase 2：Foundational（阻塞性前置）

**目的**：配置扩展、加密口径、产物路径、假模式物料——所有用户故事
的共同地基。

**⚠️ 关键**：本阶段未完成不得开始任何用户故事。

- [X] T005 TDD 扩展 `web/app/config.py` 的 `Settings`（先写
  `web/tests/test_config.py` 确认红再实现）：新增
  `fake_mode: bool`（alias WEB_FAKE_MODE，默认 False）、
  `max_concurrent_tasks: int=2`（WEB_MAX_CONCURRENT_TASKS，校验 ≥1）、
  `runs_dir: Path`（WEB_RUNS_DIR，空则 REPO_ROOT/"runs"）、
  `upload_max_mb: int=50`（校验 ≥1）；新增 `fernet_key` 属性：
  WEB_MASTER_KEY 非空时做 Fernet 合法性校验后返回；为空且
  fake_mode=True 时进程内 `Fernet.generate_key()` 临时密钥并
  warning 日志；为空且 fake_mode=False 时 model_validator 抛
  RuntimeError 并附生成命令。测试覆盖三种分支与默认值
- [X] T006 [P] TDD 创建 `web/app/crypto.py`（先写
  `web/tests/test_crypto.py` 确认红）：`encrypt_secret(plaintext,
  key_bytes)→str`（Fernet token）、`decrypt_secret(token,
  key_bytes)→str`、`mask_secret(plaintext)→str`（固定前缀 `••••`
  加明文末 4 位；不足 4 位时整体掩码为 `••••`）。断言：密文不含
  明文子串、同明文两次加密结果不同（随机 IV）、解密往返一致、
  掩码不含明文前半段、篡改 token 抛异常
- [X] T007 [P] TDD 创建 `web/app/runner/paths.py`（先写
  `web/tests/test_runner_paths.py` 确认红）：
  `task_run_dir(user_id, task_id)→Path`（解析为
  settings.runs_dir/<user_id>/<task_id> 并禁止 `..`/绝对路径逃逸）、
  `source_pdf_path(...)`、`parsed_dir_path(...)`；断言产物路径恒在
  runs_dir 之下且按 user/task 双层隔离
- [X] T008 [P] 创建假模式物料（research D6，探针已验证）并写
  `web/tests/test_fake_fixture.py`：`web/fixtures/fake_run/source.pdf`
  （仅含 `%PDF-1.4 ` 头的最小合法文件）、
  `web/fixtures/fake_run/parsed/content_list.json`（13 块：1 个
  text_level=1 文档标题、Abstract、1 Introduction 含 1 条 equation、
  2 Method、3 Experiments 含 1 个 table、References 含 1 条
  ref_text，page_idx 0–4，**UTF-8 无 BOM**）、
  `web/fixtures/fake_run/parsed/full.md`（简短全文）。测试把物料
  复制到 tmp_path 后直接调
  `core.pipeline.transform_paper(source=<fixture pdf>,
  output_dir=<tmp 副本目录>, fake=True, progress_cb=收集器)`，
  断言六阶段回调齐全、PaperResult.title 非空、len(sections)==7、
  total_chars>0、dead_links==[]，全程无网络
- [X] T009 扩展 `web/tests/conftest.py`：测试环境统一注入
  WEB_MASTER_KEY（固定测试 Fernet 密钥）、WEB_FAKE_MODE 可控夹具
  （`fake_settings` 默认开、`real_settings` 显式关）、
  WEB_RUNS_DIR 指向 tmp_path（测试产物绝不写仓库 runs/）、
  WEB_UPLOAD_MAX_MB 可按测试覆盖；提供重建 Settings 与 TestClient
  的夹具；保持 Spec 001 既有 24 项测试不受影响（先跑一遍全绿）

**检查点**：地基完成；此后各用户故事可独立推进。

---

## Phase 3：User Story 1 — 管理我的外部服务密钥（Priority: P1）🎯 MVP

**目标**：每用户两服务密钥加密落库、掩码回显、更新/删除、行级归属。

**独立测试**：登录后 PUT 两个密钥→GET 只见掩码→数据库中无明文→
覆盖/删除生效→换用户互不可见。

- [X] T010 [US1] TDD 创建 `web/tests/test_credentials.py`（先确认
  红）：未认证 GET/PUT/DELETE 均 401 统一文案；GET 初始固定返回
  mineru/deepseek 两个服务且 configured=false/mask=null；PUT 合法
  密钥返回 200 与掩码，库中 secret_encrypted 不含明文子串；再 GET
  掩码一致且响应体无 secret/secret_encrypted 字段；PUT 覆盖后掩码
  变化；DELETE 后 configured=false，重复 DELETE 幂等 200；空串/
  纯空白密钥 422 且错误体不回显提交值；service 路径传
  `google` 等非法值 422；用户 A 保存后用户 B 的 GET 仍全 false
- [X] T011 [US1] 在 `web/app/models.py` **追加** Credential ORM
  （不改 User）：id VARCHAR(36) PK uuid4；user_id VARCHAR(36) FK→
  users.id ON DELETE CASCADE 非空；service VARCHAR(20) 非空（仅
  `mineru`/`deepseek`）；secret_encrypted VARCHAR(255) 非空；
  mask VARCHAR(40) 非空；created_at/updated_at DATETIME naive UTC；
  UNIQUE(user_id, service)
- [X] T012 [US1] 创建 `web/app/services/credentials.py`：
  `list_statuses(db, user_id)`（固定两服务，左连接未配置项）、
  `upsert(db, user_id, service, secret)`（strip 后长度 1–200 校验、
  经 web/app/crypto.py 唯一口径加密、算掩码、存在则覆盖密文/掩码/
  updated_at）、`delete(db, user_id, service)`（幂等）、
  `get_decrypted(db, user_id, service)→str|None`（全代码库唯一的
  密钥解密取用处，供 worker 调用）；任何函数不 print/logger 明文
- [X] T013 [US1] 在 `web/app/schemas.py` **追加**：
  CredentialUpsertRequest（secret: str，去空白后 1–200 的校验器）、
  CredentialStatusOut（service: Literal["mineru","deepseek"]、
  configured: bool、mask: str|None）、CredentialListOut
  （credentials: list[...]）
- [X] T014 [US1] 创建 `web/app/routers/credentials.py`：
  `GET /api/credentials`、`PUT /api/credentials/{service}`、
  `DELETE /api/credentials/{service}`；service 用
  Literal["mineru","deepseek"] 路径参数（非法值 FastAPI 自动 422）；
  全部经既有 `get_current_user` 单一鉴权依赖；响应严格为契约
  api-credentials.md §1–3 的形态；加密主密钥异常时 500 统一文案
  "服务暂时不可用，请联系维护者"，不回显内部原因
- [X] T015 [US1] 在 `web/app/main.py` **追加**
  include_router(credentials.router)（挂载顺序：health、auth、
  credentials、tasks）
- [X] T016 [P] [US1] 创建 `webui/src/api/credentials.js`：
  listCredentials()、saveCredential(service, secret)、
  deleteCredential(service)，全部走既有 axios 单例
  （webui/src/api/client.js），不新建请求实例
- [X] T017 [US1] 创建 `webui/src/views/CredentialsView.vue`（Naive
  UI：两个服务卡片，各自显示掩码/未配置状态、密钥输入框、保存/
  更新/删除按钮、删除用 NDialog 二次确认、useMessage 成功失败提示、
  前端空白校验与 422 文案）；在 `webui/src/router/index.js` **追加**
  `/credentials`（meta requiresAuth:true，动态 import）；在
  `webui/src/layouts/AppLayout.vue` 顶栏**追加**"密钥设置"入口

**检查点**：US1 可独立演示（quickstart 场景 1、10 的后端部分）。

---

## Phase 4：User Story 2 — 提交论文并在后台转写（Priority: P1）

**目标**：链接/上传两种提交立即返回；有界线程池后台执行 core；
六阶段进度持久化；成功写结果摘要；产物双层隔离；每用户单活动。

**独立测试**：假模式下 API 提交→201 queued；等待后台 worker
跑完后**直接查库**（本阶段 GET 列表/详情端点尚未建，归 US3
T029）断言 status=succeeded、六阶段 stage/progress 被更新过、
result_title/section_count/total_chars 有值；
runs/<uid>/<tid>/ 落盘；并发双提交恰一个 409。验证手段限定为后端
pytest（测试内用 SessionLocal 读任务行 + 文件系统断言），不依赖
任何 US3 才存在的查询接口或前端页面。

- [X] T018 [US2] TDD 创建 `web/tests/test_tasks_submit.py`（先确认
  红）：①假模式 POST /api/tasks/from-url 合法链接→201，
  status=queued、source_kind=arxiv、source_ref 为规范化 arxiv id；
  ②非 arxiv 链接（https://example.com/x）422 且无任务行残留；
  source 空串/超长 422；③真实模式（real_settings）无凭据提交→
  400 code=credentials_required；④先插一条 queued 行后再提交→
  409 code=active_task_exists；⑤两线程 barrier 并发提交同一用户→
  恰一个 201、一个 409；⑥upload 合法最小 PDF→201，
  source_kind=upload、source_ref=原文件名，且
  runs/<uid>/<tid>/source.pdf 存在、内容与上传一致；⑦.txt 扩展名
  →422 code=invalid_file_type；⑧内容非 %PDF 头→422
  code=invalid_pdf；⑨upload_max_mb 覆盖为极小值→413
  code=file_too_large 且无任务行、无半成品目录；⑩两端点未认证
  401；⑪提交响应耗时断言：mock 掉执行器后提交在 1 秒内返回
  （不等待转写）；⑫**上传传输中断**：用读取到一半即抛异常的假
  UploadFile（/假流式接收）模拟连接中途断开，断言数据库无该任务
  行、runs/<uid>/ 下无本次半成品目录（端点必须清理）；⑬假模式
  端到端（不 mock 执行器）：合法 from-url 提交后在测试内轮询查库
  （带超时），断言最终 status=succeeded、stage 曾按
  ingest→parse→normalize→plan→rewrite→assemble 推进、progress
  到 1.0、result_section_count==7 且 result_total_chars>0、
  failure_* 全为 NULL、任务目录产物齐全（本用例是 US2 检查点的
  权威证据；T040 在此基础上补零网络哨兵）
- [X] T019 [US2] 在 `web/app/models.py` **追加** Task ORM（字段严格
  照 data-model §1.2）：id VARCHAR(36) PK；user_id FK CASCADE；
  source_kind VARCHAR(10)（arxiv/upload）；source_ref
  VARCHAR(500)；status VARCHAR(12) 默认 queued；stage
  VARCHAR(12) nullable；progress FLOAT 默认 0；message
  VARCHAR(300) nullable；failure_category VARCHAR(10) nullable；
  failure_code VARCHAR(40) nullable；failure_message VARCHAR(300)
  nullable；result_title VARCHAR(300)、result_section_count INTEGER、
  result_total_chars 均 nullable；fake_mode BOOLEAN 默认 0；
  active_slot INTEGER nullable；created_at 非空、started_at/
  finished_at nullable。索引：INDEX (user_id, created_at DESC)；
  **部分唯一索引** `uq_tasks_one_active_per_user ON
  tasks(user_id, active_slot) WHERE status IN ('queued','running')`
- [X] T020 [US2] 在 `web/app/schemas.py` **追加**：FromUrlRequest
  （source: str 1–500）、TaskFailure（category: Literal[input,
  service,system]、code、message）、TaskResult（title、
  section_count、total_chars）、TaskOut（全字段；failure/result/
  started_at/finished_at/stage 可空；**不含任何路径或密文字段**）
- [X] T021 [US2] 创建 `web/app/runner/cancel_registry.py`（本阶段
  只做管道，US4 才消费）：内存 `dict[str, threading.Event]`，
  register(task_id)/get(task_id)→Event（不存在则惰性创建）/
  set(task_id)/discard(task_id)；再创建 `web/app/runner/executor.py`：
  定义 TaskExecutor 端口（submit(task_id, user_id)、
  cancel(task_id) 本阶段可先只 set 事件）与 InProcessExecutor
  （lifespan 持有 `ThreadPoolExecutor(max_workers=
  settings.max_concurrent_tasks)`；async submit 内经
  anyio.to_thread.run_sync 调 worker）。**顺序注意（analyze I2）**：
  本任务先于 T022，`web/app/runner/pipeline_run.py` 尚不存在，
  InProcessExecutor 必须在 submit 方法体内部**延迟导入**
  （`from .pipeline_run import run_task` 写在方法内），禁止模块级
  import；T022 创建该模块后导入自然生效。shutdown 时 wait 线程池；
  在 `web/app/deps.py` **追加** get_executor 依赖（从 app.state 取）
- [X] T022 [US2] 创建 `web/app/runner/pipeline_run.py`（worker，
  同步函数、在独立线程跑、用短生命周期 SessionLocal 自建会话）：
  ①开跑重读任务行，queued→running 条件更新并写 started_at；
  ②进度回调 (stage,p,msg) 用独立会话更新 stage/progress/message
  （message 截断 300，附加 status='running' 条件）；③真实模式经
  services.credentials.get_decrypted 取两密钥（缺失则失败基线
  key_missing）；④fake 模式（research D6）：创建
  runs/<uid>/<tid>/，把 web/fixtures/fake_run 的 source.pdf 与
  parsed/ 复制进任务目录，调
  `transform_paper(source=<目录内 fixture pdf>, output_dir=<任务
  目录>, fake=True, progress_cb=...)`，用户提交的 source_ref 仅作
  展示不参与执行；⑤真实链接模式：调
  `transform_paper(source=source_ref, output_dir=run_dir,
  mineru_api_key=..., deepseek_api_key=..., fake=False,
  progress_cb=...)`（core 自行下载落 source.pdf）；真实上传模式：
  source=已保存的 source.pdf；⑥成功→条件更新 succeeded +
  result_title/section_count/total_chars + progress=1.0 +
  finished_at + active_slot=NULL；⑦异常→本阶段先落统一基线
  failed(system/internal_error/固定文案)，US3 T028 替换为全量
  分类器；⑧finally 中 cancel_registry.discard
- [X] T023 [US2] 创建 `web/app/routers/tasks.py`：
  `POST /api/tasks/from-url`（JSON；真实模式先查两凭据，缺失→400
  code=credentials_required；用 core.ingest.is_arxiv_url 预校验，
  非法→422；事务内 count 活动任务→409 code=active_task_exists；
  建 queued 任务行 active_slot=1，catch IntegrityError→rollback→
  409；executor.submit 后返回 201 TaskOut）；
  `POST /api/tasks/upload`（multipart 字段 file；流式 1MB 分块
  接收并累计，超 settings.upload_max_mb 立即 413
  file_too_large 并清理；扩展名空/.pdf 校验→422 invalid_file_type；
  收完读前 5 字节验 %PDF→422 invalid_pdf；真实模式同样 400 预检；
  活动槽同样双保险；创建 runs/<uid>/<tid>/ 落 source.pdf 后建行
  入队，任何失败清理半成品目录与行）；提交时
  cancel_registry.register(task_id)
- [X] T024 [US2] 在 `web/app/main.py` **追加** lifespan 逻辑：
  启动构造 InProcessExecutor 存 app.state、关闭时 wait；
  include_router(tasks.router)
- [X] T025 [US2] 创建 `webui/src/api/tasks.js`：
  createFromUrl(source)（JSON POST）、createUpload(file)
  （FormData，multipart，不手动设 Content-Type 以保留 boundary）；
  创建 `webui/src/views/TaskCreateView.vue`：链接/上传两个 NTab，
  前端先行校验（链接格式提示、文件选择显示大小、超 50MB 拦截），
  提交成功跳转 `/tasks/{id}`（US3 页面就位前可先跳 `/tasks`），
  400 credentials_required 时 show 引导条带"去密钥设置"链接，
  409 active_task_exists 时提示并跳任务列表；在
  `webui/src/router/index.js` **追加** `/tasks/new`；在
  AppLayout.vue **追加**"提交任务"导航项

**检查点**：US2 后端端到端可独立验收（quickstart 场景 2–7 后端
部分）；提交不阻塞、状态与进度落库、成功摘要可查。

---

## Phase 5：User Story 3 — 我的任务历史与可懂失败原因（Priority: P1）

**目标**：列表/详情（仅本人、倒序、同形 TaskOut）、越权统一 404、
三分类错误映射、重启中断恢复、前端轮询与状态页。

**独立测试**：三类故障各造一个任务核对 category/code/文案且无
异常原文泄露；用户 B 访问 A 的 id 得 404；重启恢复后残留任务
system/interrupted_restart。

- [X] T026 [US3] TDD 创建 `web/tests/test_tasks_history.py`（先确认
  红）：GET /api/tasks 仅返回本人任务且 created_at 倒序、信封为
  {tasks:[...]}、列表项与详情同形；GET /api/tasks/{id} 本人 200；
  他人 id 与不存在 id 均 404 `{"detail":"任务不存在"}`；未认证
  401；succeeded 任务 result 三字段正确、failure=null；failed 任务
  failure 三字段非空、result=null；构造替身让 worker 分别遇到：
  core InputError("…不支持的链接…")→input/invalid_source；
  InputError("MinerU 鉴权失败…API Key…错误或已失效")→
  service/key_rejected；InputError("…API Key 未设置")→
  service/key_missing；core ServiceError("超时")→
  service/service_unavailable；未知 ValueError("secret boom xyz")
  →system/internal_error；断言所有用户可见 message 均为固定模板、
  不含 "secret boom"/Bearer/key 片段
- [X] T027 [US3] TDD 创建 `web/app/runner/errors.py`（先写
  `web/tests/test_error_classify.py` 确认红）：
  classify_error(exc)→(category, code, message) 严格按 research D5
  映射表（invalid_source / scan_without_text / key_missing /
  key_rejected / service_unavailable / internal_error，固定中文
  模板与 api-tasks.md §6 逐字对齐）；鉴权识别关键词（"鉴权失败"、
  "API Key 无效"、"无权限"、"未设置"）命中时 InputError 重判为
  service；另写 sanitize_for_log(text)（脱敏 `Bearer\s+\S+`、
  `sk-[A-Za-z0-9]+`、连续 24+ 位十六进制/base64 长串）
- [X] T028 [US3] 修改 `web/app/runner/pipeline_run.py` 的 except
  分支（同文件，串行）：删除 T022 的基线 internal_error 兜底，
  改为先判 cancel_event 置位（US4 后生效，本阶段保留默认 False
  分支）→ 否则走 errors.classify_error 写 failure_category/code/
  message；原始异常仅 logger.warning(sanitize_for_log(repr(exc)))
- [X] T029 [US3] TDD 在 `web/app/deps.py` **追加**
  get_owned_task 依赖（按 task_id + 当前 user_id 查询，查不到
  统一抛 404 `任务不存在`，不区分不存在与越权）；在
  `web/app/routers/tasks.py` **追加** `GET /api/tasks`
  （user_id 过滤、created_at desc）与 `GET /api/tasks/{task_id}`
  （用 get_owned_task），响应严格 TaskOut
- [X] T030 [US3] TDD 创建 `web/app/recovery.py`（先写
  `web/tests/test_recovery.py` 确认红）：recover_interrupted()
  单条条件 UPDATE 把 status IN (queued,running) 的残留行置
  failed + system/interrupted_restart + 固定文案"服务曾重启，
  该任务被中断，请重新提交" + finished_at=now + active_slot=NULL；
  测试构造 queued/running 与若干终态行，调用后仅活动行被改、
  终态行不变；在 `web/app/main.py` lifespan 建表后、开始接收前
  调用
- [X] T031 [US3] 创建 `webui/src/stores/tasks.js`（Pinia）：
  tasks 列表、currentTask、loading/error；fetchList/fetchDetail/
  startPolling（3 秒间隔，仅当存在 queued/running 任务时刷新，
  全部终态后自动停止）/stopPolling；组件 onUnmounted 必须能停止
  （research D10）
- [X] T032 [US3] 在 `webui/src/api/tasks.js` **追加** listTasks()、
  getTask(id)（取消函数留 US4 追加）
- [X] T033 [US3] 创建 `webui/src/views/TaskListView.vue`：倒序卡片/
  表格行，NTag 状态五色徽标（queued/running/succeeded/failed/
  cancelled）、NProgress 显示 progress、stage 中文名映射、失败行
  按 category 配色并显示可行动 message、fake_mode 行显示"演示
  任务"小标、空态引导去提交、点击行入详情；进入页面启动轮询、
  离开停止
- [X] T034 [US3] 创建 `webui/src/views/TaskDetailView.vue`：大状态
  徽标、六阶段进度（当前阶段高亮 + 百分比 + message）、成功结果
  面板（标题/章节数/总字数）、失败面板（分类色块 + code + 固定
  中文指引，无堆栈）、fake_mode"演示任务"徽章；在
  router/index.js **追加** `/tasks` 与 `/tasks/:id`（均
  requiresAuth）；在 AppLayout.vue **追加**"任务"导航项

**检查点**：US1–US3 全部可独立演示；quickstart §4 场景 5–7、11
后端与前端链路打通。

---

## Phase 6：User Story 4 — 阶段边界取消（Priority: P2）

**目的**：宪法原则一特许的 core 增量落地；排队/运行中可取消，
仅在阶段边界与 MinerU 轮询间隙生效；终态竞争唯一。

**独立测试**：假模式任务不同阶段取消→cancelled 且无 result；
终态取消 409；他人取消 404；取消与完成竞争不矛盾。

- [X] T035 [US4] TDD 创建 `web/tests/test_core_cancel.py`（先确认
  红：当前无 cancel_event 参数，调用应 TypeError）：
  ①transform_paper 传 cancel_event=预置置位的 Event、source 指向
  fixture 的 source.pdf、fake=True、output_dir=tmp 空目录，断言抛
  core.errors.ETError 且消息为"任务已被用户取消"，且在 parse 之前
  停止（parsed 产物未被生成/复用）；②不传 cancel_event 时签名与
  行为不变（T008 假模式全流程回归）；③MinerUClient 单测：构造
  cancel_event 预置置位、session 为始终返回 state="running" 的
  假会话、poll_interval=10 的 client，调用其轮询逻辑，断言 1 秒内
  抛 ETError（分段等待生效），event=None 时仍走单次 sleep 语义
  （用可记录调用次数的假 sleep）
- [X] T036 [US4] 实施宪法特许 core diff，**严格限定两个文件、不改
  任何其他 core 文件/prompts**：①`core/pipeline.py`
  transform_paper 增加 keyword-only `cancel_event:
  threading.Event | None = None`；新增模块内私有
  `_check_cancel(event)`（None 直接返回；is_set 则
  `raise ETError("任务已被用户取消")`）；在六个阶段边界（ingest
  之后、parse 之前、normalize 之前、plan 之前、rewrite 每章循环
  开头、assemble 之前）调用；构造 MinerUClient 时透传
  cancel_event。②`core/mineru_client.py` MinerUClient.__init__
  增加 `cancel_event=None` 存为 self._cancel_event；
  _poll_until_done 循环开头检查；等待处 event=None 保持
  self._sleep(self.poll_interval) 原样；event 非 None 时把
  poll_interval 切成 0.5s 小段循环，每段前后检查，置位即抛同一
  ETError。不新增异常类、不动 errors.py、不改默认值
- [X] T037 [US4] TDD 创建 `web/tests/test_tasks_cancel.py`（先确认
  红）：①queued 任务在 worker 开跑前 set 事件→最终 cancelled、
  无 result_*、finished_at 非空；②running 中（用可在 rewrite 阶段
  被置位的事件/可控 fake 执行）取消→cancelled 且不进入下一未开始
  阶段；③succeeded/failed/cancelled 任务调取消→409
  code=task_not_active；④他人任务→404；⑤取消与成功竞争：直接
  对终态条件更新函数做两路并发，断言终态唯一（不出现
  result+failure 并存或状态回退），rowcount=0 一路不覆盖；
  ⑥cancelled 任务的活动槽已释放（active_slot=NULL），同用户可再
  提交
- [X] T038 [US4] 修改 `web/app/runner/pipeline_run.py`（同文件
  串行）：开跑注册/取得该任务的 cancel Event 并**先检查**置位→
  直接 cancelled（不调 core）；调 transform_paper 时传入
  cancel_event=event；except 中先判 event.is_set()→cancelled
  终态，否则 classify_error；所有离开活动态的更新统一用条件
  UPDATE `WHERE id=:id AND status IN ('queued','running')`，
  rowcount=0 则放弃写入（竞争失败者不覆盖）；succeeded 路径同样
  条件化；在 `web/app/routers/tasks.py` **追加**
  `POST /api/tasks/{task_id}/cancel`（get_owned_task→404 语义；
  非活动态→409 task_not_active；registry.set 后立即返回当前
  TaskOut 快照，不假装已终态）
- [X] T039 [US4] 在 `webui/src/api/tasks.js` **追加** cancelTask(id)；
  TaskListView.vue 与 TaskDetailView.vue 为活动任务接"取消任务"
  按钮（NDialog 二次确认、成功提示"取消请求已受理"、依赖现有
  3 秒轮询自然刷新到 cancelled、终态按钮消失，409/404 给出相应
  消息）

**检查点**：US1–US4 全部可演示；quickstart §7 core 精确 diff
核对通过、默认参数行为零变化。

---

## Phase 7：User Story 5 — 零密钥断网的完整离线演示（Priority: P2）

**目的**：把假模式从"测试夹具"收口为可验收的产品行为：免密钥、
零外部调用、开关只能由运维控制、演示任务可辨识。

**独立测试**：断网+无任何凭据，from-url 与 upload 都能一路
succeeded；零网络哨兵测试通过；请求无法影响开关。

- [X] T040 [US5] 创建 `web/tests/test_fake_mode.py`（若已有行为在
  T018/T022 覆盖，此任务为收口与补强，先跑确认缺口再补断言）：
  ①fake 模式且两凭据均不存在：from-url 与 upload 均 201 并最终
  succeeded、TaskOut.fake_mode=true、result 为样例论文；
  ②零网络哨兵：monkeypatch `requests.Session` 的 get/post/put 与
  `openai.OpenAI` 相关出口为"被调用即 pytest.fail"的哨兵，完整
  跑一个假模式任务仍成功；③开关不可由请求影响：from-url 请求体
  附加额外字段（如 fake_mode:true）在 real_settings 下仍被拒绝
  （400/422，且服务端绝不读取请求中的该类字段——Pydantic 模式
  extra=forbid 或忽略均可，断言以服务端配置为唯一来源）；
  ④real_settings 无凭据提交仍 400 credentials_required；
  ⑤fake 模式产物目录内容来自 web/fixtures（content_list 与
  fixture 字节一致）

**检查点**：quickstart §4 场景 2、3、12 可在完全断网机器上端到端
走通；宪法原则七满足。

---

## Phase 8：Polish 与跨故事收尾

**目的**：安全自检、红线复核、全量回归与手工验收。

- [X] T041 安全自检与日志收口：全仓检索（grep）确认密钥明文不进
  任何 logger/print/响应；在 services/credentials.py、
  runner/pipeline_run.py、runner/errors.py 的异常日志点统一过
  sanitize_for_log；核对 422 脱敏处理器对凭据/上传端点同样生效
  （不回显 secret 与文件内容）；补必要断言到既有测试文件
- [X] T042 红线复核：①根 requirements.txt 零改动（与 Spec 001
  后白名单逐行一致）；②`core/` 改动仅限 pipeline.py、
  mineru_client.py 两个文件的 cancel_event 增量（errors.py、
  prompts/、ingest.py、normalize.py、planner.py、rewrite/、
  assemble/ 零改动），默认参数下既有行为不变；③web 层新增依赖
  仅 cryptography、python-multipart；④无任何端点接收文件系统
  路径参数；⑤产物目录全部 runs/<user_id>/<task_id>/ 双层隔离；
  将结论写入任务备注

  **T042 核验结论（2026-09-28）**：
  ①根 requirements.txt 仍为白名单 5 行（requests 2.32.5 / openai 2.17.0 /
  pyyaml 6.0.2 / numpy 2.4.2 / pytest 9.1.1），本 spec 零改动；
  ②mtime 取证 core/ 仅 pipeline.py、mineru_client.py 于今日改动，
  其余文件停留在 2026-09-22 冻结线；默认参数行为由 131 项 web 测试
  （含 T035② 不传 cancel_event 假模式全流程）回归；
  ③web/requirements.txt 本 spec 仅新增 cryptography>=43.0、
  python-multipart>=0.0.12；
  ④全部路由路径参数仅 task_id（UUID）与 service（Literal 枚举），
  无任何端点接收文件系统路径；
  ⑤所有落盘经 runner.paths.task_run_dir → runs/<uuid>/<uuid>/，
  _safe_segment 拒绝 `..`/分隔符/绝对路径，test_runner_paths 12 项覆盖。
- [X] T043 全量验收：`.\.venv\Scripts\python.exe -m pytest
  web\tests -v` 全绿（Spec 001 的 24 项 + 本特性全部新增）；
  `cd webui; npm run build` 通过；按 quickstart §4 的 12 个手工
  场景逐条目验（双服务 uvicorn 5173/8000 联调：无密钥提交、六阶段
  进度、跨用户 404、并发 409、三类取消、非法/超限上传、掩码
  无回显、重启中断、日志无密钥无外联）；§6 真实模式抽验可选；
  全部通过后勾选本任务，交用户运行 `/speckit-converge`

---

## 依赖关系与执行顺序

### 阶段依赖

- Phase 1 Setup → Phase 2 Foundational（**阻塞全部故事**）→
  US1 → US2 → US3 → US4 → US5 → Polish。
- 故事间存在代码层面的同文件追加（models/schemas/main/tasks
  router/pipeline_run），故本项目按优先级串行推进；[P] 仅表示
  同一阶段内不同文件可同时动手。

### 关键技术依赖链

- T005（config.fernet_key）→ T006/T009 → 全部后端故事
- T008（fake 物料+core 探针）是 T018 起所有离线测试的前置证据
- T019（Task 表+部分唯一索引）→ T021/T022/T023
- T022（worker）→ T028（换分类器）→ T038（取消接线），同一文件
  严格按序
- T036（core 增量）是 T038 应用层取消接线的前置；T035 测试先行
- T031（tasks store）→ T033/T034（视图）→ T039（取消按钮）

### 并行机会（仅这些标了 [P]）

- T002/T003/T004（不同配置文件/目录）
- T006/T007/T008（crypto、paths、fixtures 互不相同文件与测试）
- T016（前端 credentials.js 与 US1 后端 T013–T015 不同文件）

### TDD 红绿纪律

- 每个后端"先写测试"任务必须先运行确认失败，再写实现；实现任务
  完成后立即全量跑该故事相关测试转绿，再进入下一任务。
- 测试一律离线：真实外部服务只能被替身/哨兵取代，禁止在测试中
  访问 MinerU/DeepSeek/arXiv。

---

## 并行示例：Phase 2

```text
T005 config 扩展（先行，单独）
  ├─ T006 [P] web/app/crypto.py + test_crypto.py
  ├─ T007 [P] web/app/runner/paths.py + test_runner_paths.py
  └─ T008 [P] web/fixtures/fake_run/* + test_fake_fixture.py
（T009 conftest 扩展在 T005 之后、US1 之前收口）
```

---

## 实施策略（MVP 优先）

1. 完成 Phase 1–2：加密口径、配置、物料、测试地基就位。
2. 完成 US1：密钥端到端可用——这是 MVP，可停下独立演示。
3. 增量 US2：假模式下提交→后台→成功；再增量 US3：历史/失败/
   恢复。三个 P1 完成后产品已可完整离线演示主链路。
4. US4 取消（含唯一一次 core 特许增量）→ US5 假模式收口。
5. Polish：安全自检、红线复核、quickstart 12 场景人工验收，
   交 `/speckit-converge`。

---

## 备注

- 每个检查点可停下独立演示；全部完成后进入 `/speckit-analyze`
  （实现前一致性检查，本文件生成后立即执行一次）与实现后的
  `/speckit-converge`。
- core diff 以 T036 描述为严格上限；实现中若发现必须超出
  （例如不得不改 errors.py），立即停工，回到 plan 宪法门控说明。
- 前端所有新页面必须挂在既有 AppLayout 与 requiresAuth 守卫内，
  走既有 axios 单例；不得新建第二套布局/请求通道。
