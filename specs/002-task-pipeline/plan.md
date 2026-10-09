# 技术方案：论文转写任务流水线

**分支**：`002-task-pipeline` | **日期**：2026-09-27 | **规格**：[spec.md](./spec.md)

**输入**：来自 `/specs/002-task-pipeline/spec.md` 的特性规格

## 摘要

在 Spec 001 的 FastAPI + Vue 骨架之上新增任务执行内核：每用户第三方
密钥经 Fernet 加密落库、掩码回显；支持 arXiv 链接与 PDF 上传两种来源
提交任务；转写在有界后台线程池中执行，进度经 core 既有进度回调写入
任务状态；任务列表/详情经 REST 快照获取（前端轮询），失败按
输入/服务/系统三分类映射为可行动中文提示；支持阶段边界取消；服务
重启后把残留运行中任务标记为中断。全程提供 `WEB_FAKE_MODE=1` 零密钥、
零外部调用的端到端离线演示。SSE 实时推送、在线阅读器与成品下载归
Spec 003，本特性不做。

core 层仅发生宪法预先批准的那一类增量变更：默认关闭的可选取消信号
（精确 diff 见"宪法检查"与 research.md D4），其余逐字节不动。

## 技术上下文

**语言/版本**：Python 3.11+（本机 3.13.7，沿用仓库 `.venv`）；前端
JavaScript ES2022 + Vue 3 SFC（不引入 TypeScript）；Node 18+。

**后端新增依赖**（只加进 `web/requirements.txt`，根 requirements.txt
core 白名单一行不改）：

- `cryptography`：Fernet 对称加密（AES-128-CBC + HMAC），加密每用户
  API Key
- `python-multipart`：FastAPI 文件上传表单解析（UploadFile 必需）
- 其余全部复用既有：fastapi、sqlalchemy 2.x（同步）、pydantic-settings、
  pyjwt、passlib、httpx、pytest；后台桥接用 FastAPI 自带的 anyio

**存储**：

- SQLite 仍为单文件 `data/app.db`；新增 `credentials`、`tasks` 两张表
- 运行产物落盘到 `runs/<user_id>/<task_id>/`（`source.pdf`、
  `parsed/`、`images/`、`output.md` 等 core 产物）；根目录可配置，
  默认仓库根 `runs/`，已在 .gitignore 增补（实施时确认）
- fake 模式的样例物料内置于 `web/fixtures/fake_run/`（随仓库提交，
  非临时文件）

**测试**：后端 pytest 全程离线零网络：凭据加密/掩码/越权、链接与
上传提交、状态机流转（用 fake 物料驱动真实 core 六阶段）、每用户
单运行约束（含并发提交）、三分类错误映射、阶段边界取消（含取消/
完成竞争）、重启中断恢复、假模式零外部调用断言。前端按 quickstart
手工验收。

**目标平台**：本地开发机（Windows/PowerShell，命令兼容 Linux/macOS），
可信内网；evergreen Chrome/Edge。

**项目类型**：前后端分离 Web 应用（在 001 两工程上增量）。

**性能目标**：提交请求 2 秒内返回（仅落库+入队，不等转写）；任务
列表/详情亚秒级；单机演示负载，全局并发默认 2（可配置），每用户
并发 1。

**约束**：

- 运行期真实模式仅在用户显式提交任务时调用用户自备密钥对应的外部
  服务；测试与假模式零外部网络
- core 除 D4 枚举的增量 diff 外零改动；提示词资产不动
- PDF 上传上限 50MB；仅支持 arXiv 链接
- 不引入 Celery/Redis/外部队列；执行器接口先行，进程内实现可替换

**规模/范围**：后端新增约 8 个模块（凭据加密服务、任务 ORM/模式、
执行器端口+进程内实现、取消信号注册表、错误分类器、2 个路由、
启动恢复）+ core 2 个文件的增量参数；前端新增 4 个视图 + 2 个 API
封装 + 导航/路由增量；新增 fake 样例物料一套。

## 宪法检查

*门控：Phase 0 研究前必须通过；Phase 1 设计后复核。*

| 宪法原则 | 本特性符合性 | 说明 |
|---|---|---|
| 一、core 冻结 | ✅ 通过（含精确枚举的特许增量） | 唯一变更是"取消信号"这一个特性的增量参数，全部默认 `None`、默认路径行为逐字节不变。精确 diff（共 2 个文件、不新增类、不改 prompts）：① `core/pipeline.py` 的 `transform_paper()` 增加 `cancel_event=None`，在六个阶段边界与每章转写前检查，置位时抛**既有** `ETError("任务已被取消")`（不新增异常类）；构造 MinerUClient 时透传该参数。② `core/mineru_client.py` 的 `MinerUClient.__init__` 增加 `cancel_event=None`，仅在轮询循环中使用：事件为 None 时保持现有单次 `sleep(poll_interval)`；非 None 时把等待切成 0.5s 小段并在每段前后检查置位，置位即抛同一既有 `ETError`。不新增/删除任何符号、不动 errors.py、不改任何既有默认值。批准点见 research.md D4 |
| 二、继承架构边界 | ✅ 通过 | 密钥加密、ORM、HTTP、线程池、取消注册表全部在 web 层；core 仍只接收注入的 key/output_dir/fake/回调；web 只调用 core 公共入口 `transform_paper()` 与纯函数 `ingest.is_arxiv_url`（提交前校验），不反向渗透 |
| 三、多租户隔离 | ✅ 通过 | 任务与凭据所有查询在服务边界过滤 user_id；详情/取消对他人标识一律 404（不承认存在）；产物目录 `runs/<user_id>/<task_id>/`，无任何裸路径访问端点（本特性甚至不提供下载端点） |
| 四、凭据与密钥安全 | ✅ 通过 | Fernet 加密落库，主密钥仅来自 `WEB_MASTER_KEY`（fake 模式下缺省才允许进程内临时密钥并打 warning，见 D8）；接口仅回掩码与 configured；日志、进度文案、错误信息严禁明文密钥（错误分类器对含 key 的异常消息做脱敏，见 D5）；登录密码体系沿用 001 唯一 CryptContext |
| 五、长任务后台化 | ✅ 通过 | `TaskExecutor` 端口（submit/cancel）+ 进程内 `ThreadPoolExecutor`（有界，大小取配置）实现；经 `anyio.to_thread.run_sync` 桥接，路由零阻塞；每用户 1 个活动任务（DB 部分唯一索引兜底）；全局上限配置项；取消仅在阶段边界（MinerU 轮询等待间隙、章节之间）生效，不硬中断单次外部调用 |
| 六、契约先行 | ✅ 通过 | Pydantic + OpenAPI 为唯一契约（contracts/）；前端只用 001 的 axios 统一通道，不碰 core/DB/文件系统；鉴权沿用单一 `get_current_user`；新页面全部挂在既有 requiresAuth 路由守卫与 AppLayout 之内 |
| 七、离线可验收 | ✅ 通过 | `WEB_FAKE_MODE=1` 驱动 `fake=True`，内置样例物料；提交→六阶段→完成全链路零外部调用（测试断言不发起任何网络请求：fake 物料直接喂给 core，并以 monkeypatch 守住 requests/OpenAI 出口）；core 三分类异常在 web 边界映射为可区分的中文失败提示 |

**门控结论**：无违例，无需复杂度豁免。原则一的特许变更以上表枚举的
2 文件增量 diff 为严格上限，tasks 阶段逐项对照；若实现中发现需要
超出（例如必须改 errors.py），立即停工回到本门控修订。

## 项目结构

### 本特性文档

```text
specs/002-task-pipeline/
├── plan.md              # 本文件
├── research.md          # Phase 0：技术决策 D1–D10
├── data-model.md        # Phase 1：credentials/tasks 表与任务状态机
├── quickstart.md        # Phase 1：本地运行与离线验收手册
├── contracts/
│   ├── api-credentials.md   # 密钥管理接口契约
│   └── api-tasks.md         # 任务提交/查询/取消接口契约
└── tasks.md             # /speckit-tasks 产出（本命令不创建）
```

### 源码结构（仓库根，仅列新增/改动）

```text
core/                        # 仅 2 个文件的增量参数（见宪法检查），其余冻结
├── pipeline.py              # [改] transform_paper +cancel_event，边界检查
└── mineru_client.py         # [改] __init__ +cancel_event，轮询可中断等待

web/
├── requirements.txt         # [改] +cryptography、+python-multipart
├── .env.example             # [改] WEB_MASTER_KEY 必填说明、新增三项配置
├── fixtures/
│   └── fake_run/            # 新增：假模式样例物料（随仓库提交）
│       ├── source.pdf       # 仅含 %PDF 头的最小合法 PDF（ingest 校验足够）
│       └── parsed/
│           ├── content_list.json   # 13 块：标题/摘要/引言/公式/方法/实验/表/参考文献
│           └── full.md
└── app/
    ├── config.py            # [改] +master_key 校验、fake_mode、max_concurrent_tasks、
    │                        #     runs_dir、upload_max_mb
    ├── crypto.py            # 新增：Fernet 加密封装（唯一加解密口径）、掩码生成
    ├── models.py            # [改] +Credential、+Task 两张表（含部分唯一索引）
    ├── schemas.py           # [改] +凭据/任务请求响应模式（含错误体结构）
    ├── deps.py              # [改] 新增 get_owned_task 归属校验依赖（404 语义）
    ├── services/
    │   ├── __init__.py
    │   └── credentials.py   # 新增：凭据存取（加密/掩码/upsert/delete/取用明文）
    ├── runner/
    │   ├── __init__.py
    │   ├── executor.py      # TaskExecutor 端口 + 进程内线程池实现（anyio 桥接）
    │   ├── cancel_registry.py  # task_id → threading.Event（内存态，随进程消亡）
    │   ├── pipeline_run.py  # worker：备料→调 core→回写状态/结果→异常三分类
    │   ├── errors.py        # core 异常 → (category, code, 中文文案)；脱敏
    │   └── paths.py         # runs/<user_id>/<task_id>/ 目录与文件落位
    ├── recovery.py          # 新增：启动时把 queued/running 残留标记为中断
    └── routers/
        ├── credentials.py   # 新增：GET/PUT/DELETE /api/credentials[/{service}]
        └── tasks.py         # 新增：任务 from-url/upload、列表、详情、取消

webui/src/
├── api/
│   ├── credentials.js       # 新增：凭据接口封装
│   └── tasks.js             # 新增：任务接口封装（含 multipart 上传）
├── stores/
│   └── tasks.js             # 新增：列表/详情状态与 3s 轮询（仅活动任务时）
├── layouts/AppLayout.vue    # [改] 顶栏导航 +"提交任务/任务/密钥设置"
├── router/index.js          # [改] +3 条 requiresAuth 路由
└── views/
    ├── CredentialsView.vue  # 新增：两服务密钥掩码/保存/更新/删除
    ├── TaskCreateView.vue   # 新增：链接/上传两个 Tab + 缺密钥引导
    ├── TaskListView.vue     # 新增：状态徽标、进度、失败分类、取消、入口
    └── TaskDetailView.vue   # 新增：阶段进度快照、结果摘要、失败可行动提示

runs/                        # 新增（运行期生成；.gitignore）：<user_id>/<task_id>/…
```

**结构决策**：

1. 执行器按"端口 + 进程内适配器"分层（`runner/executor.py` 定义
   submit/cancel 接口形态，路由只依赖端口），未来换 Celery/RQ 只换
   适配器，不动路由与契约——落实原则五的可替换要求，但不为假想未来
   过度抽象（不引入消息序列化层、不拆包）。
2. core 取消信号用标准库 `threading.Event`，不自定义事件类型；取消
   与失败的区分在 web worker 侧依据"事件是否置位"完成，core 只负责
   在边界抛既有 ETError——把 core diff 压到最小（见宪法检查）。
3. fake 物料放在 web 层（`web/fixtures/`）而非 core/runs：core 不感知
   Web 的演示需求；worker 在 fake 模式忽略用户来源的联网副作用，
   改把内置物料复制进本次任务私有目录后调 core（D6）。
4. 凭据加密单一口径集中在 `web/app/crypto.py`，凭据业务在
   `services/credentials.py`；执行 worker 是全代码库唯一解密取明文的
   位置，明文仅作为参数进入 core，不落日志（D1/D5）。

## 复杂度跟踪

无宪法违例，无需豁免。

---

## Phase 1 后宪法复核

设计产物（research.md、data-model.md、contracts/api-credentials.md、
contracts/api-tasks.md、quickstart.md）完成后逐项复核：

- **原则一**：core 变更仍严格限定为取消信号增量（D4），数据模型与
  契约均无 core 改动诉求；通过。
- **原则二/六**：所有新能力在 web 层，契约为 Pydantic/OpenAPI，
  前端仅经既有 axios 通道；通过。
- **原则三**：任务/凭据表均以 user_id 归属，`get_owned_task` 统一
  404，产物目录双重隔离且本特性不开放文件端点；通过。
- **原则四**：Fernet + 环境变量主密钥、掩码回显、错误脱敏在数据
  模型与契约层落实（D1/D5）；通过。
- **原则五**：状态机（data-model.md）覆盖 queued→running→
  succeeded/failed/cancelled 与启动中断恢复；执行器端口可替换；
  取消点与契约一致；通过。
- **原则七**：fake 物料经实测可零网络走通六阶段（plan 阶段探针已
  验证：13 块物料 → 7 章成品，789 字符，0 死链）；错误三分类在
  契约 failure.category 中可区分；通过。

**复核结论：通过，无新增违例，无复杂度豁免。**
