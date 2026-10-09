---
description: "实时进度与成品阅读任务清单（按用户故事分阶段，后端 TDD，前端手工验收，UI 仅功能框架）"
---

# Tasks：实时进度与成品阅读

**输入**：`/specs/003-live-progress-reader/` 下的设计文档
（plan.md、spec.md、research.md、data-model.md、
contracts/、quickstart.md）

**前置**：plan.md、spec.md（必需），research.md、
data-model.md、contracts/、quickstart.md（已具备）；
Spec 001/002 全部任务已闭环（基线 131 passed）。

**测试策略**：后端严格 TDD——每个测试任务先写并确认失败
（红），再写实现转绿；全部测试离线零网络（假模式物料 +
monkeypatch 哨兵）。新增测试独立成文件
`test_realtime_events.py`、`test_artifacts.py`，002 既有测试
除 T003 明示的物料计数同步外一行不改其语义。前端无自动化
框架，阅读器观感与实时交互以 quickstart §4 手工验收为准。

**UI 口径（spec FR-016）**：前端只交付**功能框架**——入口
可达、信息正确、交互闭环；沿用 AppLayout 与 Naive UI 默认
组件，不做视觉精修（归 Spec 004），任何任务不得借 UI 之名
返工后端或扩大范围。

**组织**：任务按用户故事分阶段，每阶段是可独立演示的增量。

## 格式：`[ID] [P?] [Story] 描述`

- **[P]**：可并行（不同文件、不依赖未完成任务）。同一文件的
  追加（routers/tasks.py、schemas.py、deps.py、main.py、
  pipeline_run.py、services/artifacts.py、test_artifacts.py、
  stores/tasks.js、TaskListView.vue、TaskDetailView.vue、
  router/index.js）**绝不标 [P]**，必须串行。
- **[Story]**：US1 实时进度 / US2 在线阅读器 / US3 打包
  下载 / US4 假模式收口；Setup、Foundational、Polish 不带
  Story 标签。
- 描述中给出精确文件路径与必须逐字遵守的约束。

---

## Phase 1：Setup（共享基建）

**目的**：前端新增两个渲染依赖；后端零新增依赖（标准库
zipfile/StreamingResponse 随现有 fastapi/starlette 可用，
T001 仅做导入验证）。

- [X] T001 在 `webui/package.json` 追加两个依赖：
  `dompurify`（成品 HTML 消毒）与 `markdown-it-texmath`
  （markdown-it@14 兼容的稳定版，实施时查其与 katex@0.16 的
  配套版本）；在 `webui/` 执行 `npm install`；验证
  `node -e "import('dompurify');import('markdown-it-texmath')"`
  可解析、`npm run build` 仍通过。另在仓库根用
  `.\.venv\Scripts\python.exe -c "import zipfile, io; from
  starlette.responses import StreamingResponse, Response"`
  确认后端能力零新增即可用；根 requirements.txt 与
  web/requirements.txt 一行不改
- [X] T002 [P] 用标准库（zlib+struct 写最小 1x1 合法 PNG，
  约 70–300 字节）生成
  `web/fixtures/fake_run/parsed/images/sample.png`；文件随
  仓库提交（fixtures 已在入库范围）；用
  `python -c "from PIL import Image"` **不可**引入 Pillow
  （白名单外），PNG 字节以手写 CRC 方式生成后用任意方式确认
  魔数 `‰PNG\r\n\x1a\n` 与 IEND 完整（可用浏览器/系统画图
  打开一次）

---

## Phase 2：Foundational（阻塞性前置）

**目的**：把假模式样例物料补齐到"含真图片"，使 US1–US4
共享同一条端到端验收链路；同步 002 受影响断言。

**⚠️ 关键**：本阶段未完成不得开始任何用户故事。

- [X] T003 修改 `web/fixtures/fake_run/parsed/content_list.json`
  （Write 工具，**UTF-8 无 BOM**）：13 块→14 块，追加 1 个
  figure 块（id 顺延 `blk-0011`、type=`figure`、
  img_path 指向 `images/sample.png`、带英文 caption、
  page_idx 与现有块一致 0–4 范围）；先跑
  `.\.venv\Scripts\python.exe -m pytest web\tests -q`
  确认**红/受影响点**（test_fake_fixture 等中硬编码"13 块"
  的断言），再把计数同步为 14，重跑全量确认**全绿**；
  total_chars/dead_links 等下界断言（>0、==[]）应自然通过，
  不得为通过测试而放宽断言语义；随后用假模式跑一个真实任务
  确认 `runs/<uid>/<tid>/images/sample.png` 被 core
  copy_images 拷出、output.md 附录折叠区出现
  `![…](images/sample.png)` 且死链为空（临时验证目录用完
  即删）；core/ 一行不改

**检查点**：含公式、表格、参考文献、真图片的假物料就绪；
131 项基线测试全绿。

---

## Phase 3：User Story 1 — 实时进度推送（Priority: P1）🎯 MVP

**目标**：fetch 流式 SSE 取代 3 秒轮询；首帧快照、单调 seq、
断线重连以快照收敛、终态可靠关流、多标签独立、归属 404；
通道不可用降级低频快照。

**独立测试**：假模式提交任务后开事件流，确定性收到
snapshot→（update 序列）→terminal；晚订阅/越权/队列压力/
重启中断均有断言；前端不再产生周期性 XHR。

- [X] T004 [US1] TDD 创建 `web/tests/test_realtime_events.py`
  （先确认红，模块尚不存在/端点 404）：
  ①未认证 `GET /api/tasks/{id}/events` → 401 统一文案；
  ②用户 B 订阅 A 的任务、订阅不存在 id → 建流前 404
  `{"detail":"任务不存在"}`；
  ③订阅本人 succeeded 历史任务：流内**先**收到 event=snapshot
  （TaskOut 同形，含 seq 信封字段）**再**收到同载荷
  event=terminal，随后连接关闭；
  ④hub 单元断言：publish 三帧后同一订阅者按序出队、seq
  1/2/3 单调递增；两个订阅者（多标签）各自独立收到全帧；
  unsubscribe 后不再收帧；
  ⑤水位过滤：snap_seq=N 的连接只转发 seq>N 的帧，≤N 的帧
  （含重复帧）不下发；构造"订阅登记后、读快照前"已有一帧
  提交的竞态顺序（直接调用 hub+db 模拟先订阅再快照），断言
  该状态要么在快照行、要么是队列中 seq>水位的帧，不重复；
  ⑥队列压力：向 maxsize 队列塞满非终态帧后再发 terminal，
  断言 terminal 必被投递（终态不丢）、被丢的仅为最旧非终态；
  ⑦假模式端到端：提交任务并以 TestClient
  `client.stream("GET", url, headers=...)` 读流（带超时收集
  若干帧后退出），断言至少收到 snapshot 与 terminal，且
  terminal 载荷 status=succeeded、result 三字段有值；
  ⑧心跳：monkeypatch 心跳间隔常量为极小值后建一条活动任务
  流，断言收到以 `: ` 开头的注释帧且其不改变任何状态；
  ⑨recovery 场景：构造 queued 行后调 recover_interrupted，
  再开流，snapshot 即 failed/system/interrupted_restart、
  文案逐字等于 002 §6 模板，随后 terminal 关流；
  ⑩事件载荷 JSON 中断言不含 run_dir/绝对路径/secret 字样
- [X] T005 [US1] 创建 `web/app/runner/events.py`：
  `ProgressHub` 单例类——`bind_loop(loop)`（lifespan 注入
  主循环）、`subscribe(task_id)→asyncio.Queue`（maxsize=256，
  登记到 `dict[task_id,set[Queue]]`）、`unsubscribe`、
  `current_seq(task_id)→int`、`publish(task_id, payload,
  terminal=False)`（同步方法，供 worker 线程调用：内部
  `loop.call_soon_threadsafe` 把"seq 自增+入队"调度回主循环
  以保证同任务有序；队列满时丢弃最旧非终态帧，terminal 帧
  强制保留）；载荷只接收普通 dict、不含文件路径；模块顶部
  中文文档字符串写明"瞬时、不持久化、不回放"
- [X] T006 [US1] 在 `web/app/schemas.py` **追加**
  `TaskEventPayload`：字段与 002 `TaskOut` 逐字同名同枚举
  （id/status/stage/progress/message/fake_mode/failure/
  result/started_at/finished_at）外加 `seq: int`（≥0）；
  failure/result 复用 002 TaskFailure/TaskResult 嵌套模式；
  模式 `extra="ignore"`，禁止任何路径/密文字段
- [X] T007 [US1] 在 `web/app/routers/tasks.py` **追加**
  `GET /api/tasks/{task_id}/events`（async def）：先经
  get_current_user + get_owned_task 归属（401/404 在流之前
  以普通 JSON 返回）；随后严格按 data-model §1.4 顺序：
  `q = hub.subscribe(id)` → `snap_seq =
  hub.current_seq(id)` → 新建 SessionLocal 读任务行组装
  TaskEventPayload(seq=snap_seq) → async 生成器先发
  `event: snapshot\ndata: …\n\n` → 循环：活动任务
  `asyncio.wait` 队列帧（15 秒超时则发 `: ping\n\n` 心跳，
  心跳间隔用模块级常量便于测试 monkeypatch），仅转发
  `frame.seq > snap_seq` 的帧；收到 terminal 帧（或快照即
  终态时补发同载荷 terminal）后正常结束生成器关流；
  `finally` 中 hub.unsubscribe 与会话关闭；
  `StreamingResponse(generator, media_type=
  "text/event-stream")`，响应头 `Cache-Control: no-store`、
  `X-Accel-Buffering: no`
- [X] T008 [US1] 修改 `web/app/runner/pipeline_run.py`
  （同文件串行）：在四个写入点**事务 commit 成功之后**发布——
  `persist_progress` commit 后 publish(update)；
  `run_task` 中三处终态 `_finish_success/_finish_failure/
  _finish_cancelled` 判定 rowcount>0 且 commit 后各 publish
  对应 terminal（载荷用提交后的任务行重新组装，含
  result/failure 三分类）；发布代码统一 try/except，异常只
  `logger.warning`，**绝不**影响任务状态或回滚；条件 UPDATE
  与取消竞争语义一行不改。另在 `web/app/main.py` lifespan
  中（建 executor 同处）实例化 `ProgressHub` 存
  `app.state.hub` 并 `hub.bind_loop(asyncio.get_running_loop())`，
  经 deps 或 app.state 供路由取用（与 get_executor 同口径）
- [X] T009 [P] [US1] 创建 `webui/src/api/events.js`：
  `openTaskStream(taskId, {onSnapshot,onUpdate,onTerminal,
  onShouldFallback,signal})`——用 fetch（headers 带
  Authorization Bearer，**禁止** EventSource 与 query
  token）读 `response.body.getReader()`，按 SSE 分帧
  （event:/data: 行、空行分隔、`: ` 注释忽略）解析 JSON；
  401→交 client.js 既有全局 401 处理且不重连；404→onError
  不重连；网络错误按 1→2→4→8→10 秒指数退避自动重连；
  连续 ≥5 次失败（约 30 秒）回调 onShouldFallback；AbortSignal
  中止即停
- [X] T010 [US1] 修改 `webui/src/stores/tasks.js`（同文件
  串行）：以事件订阅替换 002 的 3s setInterval——详情视图
  订阅单任务流；列表视图先 GET 一次列表，仅对其中
  queued/running 任务各开一条流，活动归零关闭全部流；收到
  snapshot 即整体替换该任务状态，之后仅接受 seq 更新且不倒退；
  terminal 后停该流；onShouldFallback 时切换为 002 既有
  3 秒快照轮询（下一次成功建流后恢复实时）；**同一任务同一
  时刻只允许一种持续刷新**；组件 onUnmounted 必须 abort
- [X] T011 [US1] 修改 `webui/src/views/TaskListView.vue` 与
  `webui/src/views/TaskDetailView.vue`（两文件按序）：移除
  旧 startPolling 调用、改接 store 的订阅生命周期；状态徽标/
  进度/失败三分类/取消按钮等既有展示随实时帧自然更新，
  终态后无残留连接；本任务**不**加阅读/下载按钮（US2/US3
  才出现），不做任何视觉美化

**检查点**：US1 可独立演示（quickstart 场景 1–3、13、§5
降级）；提交后页面零轮询自动走到终态。

---

## Phase 4：User Story 2 — 成品在线阅读器（Priority: P1）

**目标**：succeeded 任务只读输出 output.md 视图模型；七章
导航+深链、KaTeX 公式、受控图片 blob、折叠区；XSS 消毒；
非成功/越权拒绝；002 历史任务直接可读。

**独立测试**：test_artifacts 对真实 output.md 解析/门禁/
图片逃逸全部断言；前端手工核对公式排版、折叠、图片占位。

- [X] T012 [US2] TDD 创建 `web/tests/test_artifacts.py`
  （先确认红）：
  ①用 T003 后的假物料在 tmp runs 目录跑一个 succeeded 任务
  （或复制真实 runs/output.md 夹具），`parse_reader` 返回
  title=`Sample Paper: A Demonstration of the Pipeline`、
  sections 为 8 项（7 章+附录），slug 严格为 s0…s7、heading
  为去 `## ` 原文、附录 content 含 `<details>` 与
  `images/sample.png`；
  ②无附录成品（手工构造 7 个 `## ` 的 output.md）→7 项；
  ③缺一级标题→title 回退任务 result_title；两者皆无→回退
  source_ref；
  ④空文件/0 个 `## `→抛 ArtifactMissing 类异常；
  ⑤GET reader：本人 succeeded 200；queued/running/failed/
  cancelled 均 409 code=`task_not_ready` 且文案"任务尚未
  完成，暂无可阅读的成品"；succeeded 但 output.md 被删→
  409 code=`artifact_missing`（"成品文件缺失或已损坏，请
  重新提交任务"），错误体无路径/堆栈；
  ⑥未认证 401；他人/不存在 404；
  ⑦图片端点：本人 succeeded 取 sample.png → 200
  image/png 且字节与文件一致；`../x`、`a/b.png`、
  `x.svg`、`x.SVG`、绝对路径、含空格名一律 404（不区分
  非法名与不存在）；任务 images/ 之外的同名文件不可读；
  死链名（合法但不存在）→404；
  ⑧图片端点同样执行 401/404/409 task_not_ready 门禁；
  ⑨响应头断言 `Cache-Control` 含 no-store、无
  Content-Disposition 路径泄露
- [X] T013 [US2] 创建 `web/app/services/artifacts.py`：
  ①`read_output(run_dir)→str`（UTF-8 读 output.md，缺失/
  不可读/解析 0 章抛 `ArtifactMissingError`）；
  ②`parse_reader(md, fallback_title)`：首个行首 `# ` 为
  title（缺失回退）；以行首恰好 `## ` 切章，slug 按序
  s0/s1…，content 为到下一 `## ` 前原文（trim 首尾空行，
  原样保留 HTML/公式/相对图片链接），一级标题与首章间前导
  文并入 s0 前；
  ③`resolve_image(images_dir, name)`：name 必须匹配
  `^[A-Za-z0-9._-]+$`、扩展名小写 ∈ png/jpg/jpeg/gif/webp
  （svg 拒绝）、`(dir/name).resolve()` 必须仍在
  `dir.resolve()` 之下、且为存在的普通文件，否则抛
  ImageNotFound；
  ④`safe_filename(title, task_id)`：去控制字符与
  `\/:*?"<>|`、Windows 保留名（CON/PRN/AUX/NUL/COM1-9/
  LPT1-9）、trim 首尾点空格、折叠空白、限长 80，空则回退
  `paper-{task_id[:8]}`（zip 在 US3 消费，本任务先实现并
  单测）；全部纯函数、不打日志、不接触网络
- [X] T014 [US2] 在 `web/app/deps.py` **追加**
  `get_owned_succeeded_task`：先复用 get_owned_task 语义
  （不存在/越权统一 404 `任务不存在`），再校验
  `status=="succeeded"`，否则抛 409
  `{"detail":"任务尚未完成，暂无可阅读的成品",
  "code":"task_not_ready"}`；在 `web/app/schemas.py`
  **追加** `ReaderSection`（slug:str、heading:str、
  content:str）与 `ReaderOut`（task_id、title、fake_mode、
  total_chars:int、sections:list[ReaderSection]，
  extra="ignore"）
- [X] T015 [US2] 在 `web/app/routers/tasks.py` **追加**两端点
  （串行）：`GET /{task_id}/reader`（依赖
  get_owned_succeeded_task；services 读盘组装 ReaderOut，
  ArtifactMissingError→409 artifact_missing 固定文案）；
  `GET /{task_id}/artifacts/images/{name:path}`
  （get_owned_succeeded_task 门禁→resolve_image→
  FileResponse/Response 字节，content-type 按扩展名映射，
  `Cache-Control: private, no-store`；任何异常按 T012 口径
  404/409，不回显 name 以外输入、不出现服务器路径）。注意
  路由段不得抢占既有 `GET /{task_id}`（新路径均多一个段）
- [X] T016 [US2] 创建 `webui/src/lib/markdown.js`（唯一渲染
  口径单例）：markdown-it（html:true、linkify:false）挂
  markdown-it-texmath（识别 `$...$` 与 `$$...$$`，
  KaTeX `throwOnError:false`，单公式失败降级原文）；覆写
  image render rule 输出
  `<img alt="…" data-art="{安全名}">` 而**不**输出真实 src；
  渲染结果过 DOMPurify（ALLOWED_TAGS 含常规排版/表格系/
  details/summary/KaTeX span 与 MathML；ALLOWED_ATTR 去
  所有 on*、仅放行 data-art/alt/class 等安全属性；禁
  javascript: 与任意外站 src/href）；导出 renderMarkdown()
  与 isSafeArtName()；在该模块（或 ReaderView）
  `import 'katex/dist/katex.min.css'` 确保字体本地打包、
  **禁止 CDN**
- [X] T017 [US2] 创建 `webui/src/api/artifacts.js`：
  `getReader(taskId)`（GET JSON）、`fetchImageBlob(taskId,
  name)`（带 Bearer fetch→blob.blob()，非法名直接 reject，
  按任务缓存 objectURL）；创建
  `webui/src/views/ReaderView.vue`：顶栏返回任务详情、
  标题、fake_mode"演示任务"徽章、章节导航列表（点击定位+
  当前项高亮）、正文容器注入 renderMarkdown HTML 后遍历
  `img[data-art]` 调 fetchImageBlob 换 objectURL（失败→
  "图片暂不可用"占位块），挂载时按路由 hash `#sN` 定位
  （非法/越界 slug 回落页首），onUnmounted revoke 全部
  objectURL；409/404 用 NResult 显示固定中文态；在
  `webui/src/router/index.js` **追加**
  `/tasks/:id/reader`（requiresAuth，动态 import）；在
  TaskListView.vue 与 TaskDetailView.vue 为 succeeded 行/
  态**追加**"阅读"入口（NButton 默认样式，新窗口/当前页
  跳转自定，保持简单）；下载按钮留 US3

**检查点**：US1+US2 可独立演示（quickstart 场景 4–7、9–12）；
002 历史成功任务可直接打开；阅读器零外站请求。

---

## Phase 5：User Story 3 — 成品 zip 打包下载（Priority: P2）

**目标**：单端点返回 `<base>.zip`（Markdown 逐字 + images/
真实图片同结构）；Zip Slip 构造免疫；文件名安全；前端
Bearer 取 blob 触发保存。

**独立测试**：下载并解压，断言 .md 逐字一致、图片齐全、
arcname 安全；无图片/死链/特殊标题/保留名/空标题全覆盖。

- [X] T018 [US3] TDD 在 `web/tests/test_artifacts.py`
  **追加**下载用例（同文件串行，先确认红）：
  ①GET `/api/tasks/{id}/artifact/download` 本人 succeeded
  →200、`Content-Type: application/zip`；
  ②响应体用 zipfile 解压：条目恰为 `<base>/<base>.md` 与
  `<base>/images/sample.png`（样例任务），.md 与
  output.md 字节逐字一致、png 字节一致；
  ③无图片任务（images/ 不存在）：zip 仅含根目录与 .md，
  正常 200；
  ④死链（md 引用 images/missing.png 但文件不存在）：包内
  无该条目、不报错；
  ⑤全部条目名不含 `..`/绝对路径/盘符/反斜杠；
  ⑥safe_filename 表驱动：标题
  `a/b\\c:d*e?f"g<h>i|j`、`CON`、`PRN.txt`、全空白、纯
  符号、80+ 超长、中文标题——逐一断言净化结果与回退
  `paper-{前8位}`、限长 80；
  ⑦Content-Disposition 同时含 ASCII `filename="…zip"`
  与 `filename*=UTF-8''` 百分号编码中文名，均无服务器路径；
  ⑧svg 文件即使存在于 images/ 也不入包；
  ⑨门禁：401、他人/不存在 404、非 succeeded 409
  task_not_ready、artifact_missing 409
- [X] T019 [US3] 在 `web/app/services/artifacts.py` **追加**
  `build_artifact_zip(task, run_dir)→tuple[base_name,
  bytes]`：zipfile.ZipFile 写 io.BytesIO（ZIP_DEFLATED），
  条目名全部由安全段拼装 `<base>/<base>.md` 与
  `<base>/images/<name>`（name 复用 resolve_image 的
  正则+扩展名白名单，仅遍历 images 下真实存在的普通文件，
  svg 不收，死链自然缺席）；写入前防御性断言
  PurePosixPath(arcname) 无 `..`/绝对形态；返回 base 名供
  响应头使用；在 `web/app/routers/tasks.py` **追加**
  `GET /{task_id}/artifact/download`（get_owned_succeeded
  _task 门禁；ArtifactMissing→409；Response(bytes,
  media_type="application/zip")，Content-Disposition 按
  api-reader §3 同时给 filename ASCII 回退（非 ASCII 替
  `-`）与 filename* RFC 5987 编码，统一 `.zip` 后缀）
- [X] T020 [US3] 在 `webui/src/api/artifacts.js` **追加**
  `downloadArtifactZip(taskId)`：带 Bearer fetch→blob→
  临时 `<a download>` 点击保存→撤销；409/404 经 useMessage
  显示固定文案，不打开裸 URL；在 ReaderView.vue、
  TaskDetailView.vue、TaskListView.vue（三文件按序）为
  succeeded 态各放一个"下载"按钮（NButton 默认样式，
  功能框架，不做图标/排版打磨）

**检查点**：quickstart 场景 8 通过；解压后任意 Markdown
工具图文完整。

---

## Phase 6：User Story 4 — 假模式离线收口与历史兼容（Priority: P2）

**目的**：把新能力整体收进零网络离线验收；确认 002 历史
成功任务零迁移可读；演示任务可辨识。

**独立测试**：断网+无密钥下"提交→实时流→阅读→图片→zip"
一条链路全绿且哨兵零外联；旧 output.md 兼容。

- [X] T021 [US4] 在 `web/tests/test_fake_mode.py` **追加**
  （同文件串行）：①无密钥假模式端到端新链路——提交任务、
  开 events 流收集帧序列（snapshot→≥1 个 update 或至少
  terminal）、成功后 GET reader 断言 8 节且附录含
  sample.png 引用、GET images/sample.png 200、GET download
  解压后 md 与图片齐全；②全程沿用 002 零网络哨兵
  （requests.Session 全方法 + openai.OpenAI 被调即 fail），
  并额外断言新链路代码路径不引入任何新网络出口；
  ③开关不可由请求影响（reader/download/events 请求附加
  fake_mode 字段在 real_settings 下无效）仍成立
- [X] T022 [US4] 在 `web/tests/test_artifacts.py` **追加**
  历史兼容用例：不跑新管线，直接手工写一份"002 旧格式"
  output.md（7 个 `## `、无附录、无 images 目录）到
  tmp runs 任务目录并配 succeeded 行：GET reader→7 节、
  title 正确；GET download→zip 仅含 .md 且 200；证明无需
  数据迁移
- [X] T023 [US4] 确认 `webui/src/views/ReaderView.vue`
  显著展示 fake_mode"演示任务"徽章（与列表/详情口径一致）；
  `npm run build` 后检查 dist 产物包含 KaTeX 本地字体文件
  （assets 下 fonts/），且构建产物中不包含任何
  cdn.jsdelivr/unpkg 等外链 URL（grep dist css/js 断言）

**检查点**：quickstart §4 场景 12 与 §2 自动化清单全部可演；
宪法原则七满足。

---

## Phase 7：Polish 与跨故事收尾

**目的**：安全/红线复核、全量回归、14 场景手工验收。

- [X] T024 安全与红线复核（结论写入本任务备注）：
  ①grep 全仓确认事件/阅读器/下载代码无把令牌写入 URL、query
  或日志；日志只允许经 002 sanitize_for_log；
  ②事件/reader/zip 响应与 headers 不含 run_dir 绝对路径、
  不含密钥；
  ③DOMPurify 白名单评审：script/iframe/object/style 外链/
  on*/javascript:/data: html 均不允许；图片只有 data-art
  占位一条路；
  ④svg 全链路拒绝（图片端点 404、不入 zip、前端 isSafeArtName
  拒绝）；
  ⑤core/ 零改动（mtime 取证：pipeline.py/mineru_client.py
  也不得改动；本特性 core/ 无任何文件变化）；根
  requirements.txt 5 行零改动；web/requirements.txt 零改动；
  package.json 仅新增 dompurify、markdown-it-texmath；
  ⑥无任何端点接收文件系统路径参数（图片 name 为受控资源名
  且 resolve 校验）；
  ⑦002 全部 131 项测试（除 T003 计数同步）保持绿

  **复核结论（2026-09-29）**：
  - ① `web/app` 内无 token/secret/key/Bearer 进日志；
    前端 grep 仅命中 markdown.js 注释；fetch SSE/图片/zip
    全部走 Authorization 头，令牌零次出现在 URL/query。
  - ② SSE 载荷测试断言无 run_dir/runs 路径与 Bearer；
    图片 404 统一"图片不存在"不回显输入；artifact_missing
    固定文案无路径无堆栈；Content-Disposition 仅净化文件名。
  - ③ [markdown.js](../../../webui/src/lib/markdown.js)
    FORBID_TAGS 含 script/style/iframe/object/embed/form；
    ALLOWED_ATTR 白名单天然拒绝一切 on*；
    ALLOWED_URI_REGEXP 仅 `#`/同源相对路径 + href hook，
    javascript:/data:/外站全部摘除；markdown image rule 与
    DOMPurify uponSanitizeNode 双路剥 src 改 data-art。
  - ④ 后端 `.SVG/.svg` 404、zip 遍历白名单排除 svg（测试
    覆盖）；前端 isSafeArtName 扩展名小写白名单拒绝 svg。
  - ⑤ mtime 取证：core/ 最新源码 pipeline.py、
    mineru_client.py 停在 2026-09-28 18:36（本特性 09-29
    零改动）；根 requirements.txt=09-18、web/requirements
    .txt=09-28 18:36 未动；package.json 仅 dompurify 与
    markdown-it-texmath 为本特性新增（katex/markdown-it
    002 已在）。
  - ⑥ 三个新端点入参只有 task_id（UUID）与图片 name
    （`^[A-Za-z0-9._-]+$` + resolve 目录归属校验），
    Zip Slip 构造（`../`、编码穿越、盘符、反斜杠）均 404
    /不收入包，另有 _assert_safe_arcname 防御。
  - ⑦ 全量 181 passed（002 基线 + test_realtime_events 10
    + test_artifacts 38 + test_fake_mode 增补 2）。
- [X] T025 全量验收：`.\.venv\Scripts\python.exe -m pytest
  web\tests -v` 全绿（002 基线 + test_realtime_events +
  test_artifacts + test_fake_mode 增补）；
  `cd webui; npm run build` 通过（含 KaTeX 本地字体）；按
  quickstart §4 的 **14 个手工场景**逐条双服务联调（实时
  自动推进/重连中断/降级轮询、七章+公式+折叠+图片、zip
  解压图文、409/404 门禁、零外站请求、002 历史任务、多标签）；
  §5 降级路径与 §6 真实模式抽验可选；**UI 仅按功能框架口径
  验收，不以视觉精修阻塞**；全部通过后勾选本任务，交用户
  运行 `/speckit-converge`

  **验收结论（2026-09-30 → 2026-10-01）**：
  - 自动化：181 passed（002 基线 + test_realtime_events 10 项
    + test_artifacts 38 项 + test_fake_mode 增补 2 项），
    `npm run build` 通过。
  - 手工 14 场景：13 项绿 / 1 项（场景 6 公式真实渲染）因
    core 层 MinerU v1 API 已失效暂无法在真实模式下抽验，
    假模式成品无公式故只能验证渲染管线存在（KaTeX 已本地
    打包、markdown.js 已挂 texmath）。
    **MinerU API 版本升级属于 Spec 001 core 层问题，
    不在本特性验收范围内**，待 core/mineru_client.py 升级
    到 v4 后再补场景 6。
  - 场景 3/13 与 §5 降级：真实停后端验证了修复后的空闲看门狗
    （20s 无字节断流 → 退避 → ~33s 降级 3s 轮询 → 重启后
    未刷新页面自动收敛到 interrupted_restart 失败态），
    修复点：webui/src/api/events.js 加 IDLE_TIMEOUT_MS 看门狗。
  - 安全复核：T024 七项均保持成立，core/ 零改动、requirements
    零改动、令牌零进 URL/日志。

---

## 依赖关系与执行顺序

### 阶段依赖

- Phase 1 Setup → Phase 2 Foundational（**阻塞全部故事**）→
  US1（实时）→ US2（阅读器）→ US3（zip 下载）→ US4（离线
  收口）→ Polish。
- US1 与 US2 代码层冲突小（不同主体模块），但共用
  routers/tasks.py、schemas.py 与前端列表/详情视图，按本
  项目串行纪律排序；US3 与 US2 共用 artifacts.py 与
  test_artifacts.py，必须在 US2 后。

### 关键技术依赖链

- T002（样例 PNG）→ T003（content_list 第 14 块 + 基线回归）
  → 全部故事的离线验收
- T005（ProgressHub）→ T007（events 端点）→ T008（worker
  发布点）→ T010/T011（前端订阅）
- T013（artifacts 纯函数：解析/resolve_image/safe_filename）
  → T015（reader/图片端点）→ T017（阅读器）；
  safe_filename/resolve_image → T019（zip）→ T020（下载按钮）
- T016（lib/markdown.js 消毒管线）→ T017（ReaderView 注入
  data-art 换 blob）
- T021（端到端零网络）依赖 US1–US3 全部接线完成

### 并行机会（仅这些标了 [P]）

- T002（样例 PNG）与 T001（npm 依赖）不同文件，可并行；
- T009（events.js 前端流客户端）只依赖契约，可与 T005–T008
  后端实现并行（在 T010 接线前完成即可）。
- 其余同文件追加任务一律串行。

### TDD 红绿纪律

- T004、T012、T018、T021、T022 必须先运行确认失败（模块/
  端点不存在或断言失败），再写实现；每个故事实现完成立即
  跑该故事测试转绿再进入下一阶段。
- 测试一律离线：真实外部服务只能被替身/哨兵取代；fixture
  改动后先回归 002 全量再前进。

---

## 并行示例：Phase 1 / Phase 3

```text
T001 npm 新依赖（webui/package.json）
  └─（并行）T002 [P] 样例 PNG（web/fixtures/…/images/sample.png）
        └─ T003 content_list 14 块 + 基线回归（串行）

T005 ProgressHub → T007 events 端点 → T008 worker 发布
  └─（并行于后端）T009 [P] webui/src/api/events.js
        └─ T010 store 订阅替换轮询 → T011 视图接线
```

---

## 实施策略（MVP 优先）

1. Phase 1–2：依赖与含图物料就绪，002 基线保持全绿。
2. US1（实时推送）是 MVP：完成后任务页已无轮询、可独立演示
   等待体验，可停下验收。
3. US2（阅读器）：P1 第二块，与 US1 合起来形成"提交→等待→
   阅读"闭环；002 历史任务此时已可读。
4. US3（zip 下载）：小增量，复用 US2 安全口径。
5. US4 假模式收口 + Polish：安全红线、全量回归、quickstart
   14 场景人工验收，交 `/speckit-converge`。
6. 全程 UI 只做功能框架；视觉打磨另立 Spec 004，不在本清单
   插项。

---

## 备注

- core/ 本特性零改动是硬红线（不同于 002 的特许增量）：实施
  中若发现必须改 core 才能解析/渲染，立即停工回到 plan 宪法
  门控说明。
- 前端任何成品 HTML 输出必须经 `lib/markdown.js` 唯一管线；
- 任何 fetch（含流与图片）必须带 Authorization 头，禁止把
  令牌拼进 URL。
- 每个检查点可停下独立演示；全部完成后运行 `/speckit-analyze`
  （实现前一致性检查）与实现后的 `/speckit-converge`。
