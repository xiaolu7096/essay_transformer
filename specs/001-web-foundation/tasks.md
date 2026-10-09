---
description: "网页客户端骨架与用户认证 — 任务清单"
---

# 任务：网页客户端骨架与用户认证

**输入**：`/specs/001-web-foundation/` 下的设计文档（spec / plan / research / data-model / contracts / quickstart）

**测试策略**：spec 的 FR-013 强制要求自动化测试，故后端采用测试先行——
每个用户故事的测试任务先写、确认失败，再写实现。前端本阶段不引入测试
框架，以 quickstart 手工场景验收。

**组织方式**：任务按用户故事分组，每个故事完成后可独立验收。

## 格式：`[ID] [P?] [Story] 描述（含确切文件路径）`

- **[P]**：可并行（不同文件、不依赖未完成任务）
- **[Story]**：所属用户故事（US1–US5）；Setup/Foundational/Polish 不带故事标签
- 所有文件路径均相对仓库根 `mvp_test/`

---

## Phase 1：Setup（共享基础设施）

**目的**：建立前后端两个工程的初始结构，互不污染 core

- [X] T001 创建后端目录骨架与专属依赖文件 `web/requirements.txt`（fastapi、uvicorn[standard]、sqlalchemy、pyjwt、passlib、pydantic-settings、httpx、pytest），并建立 `web/__init__.py`、`web/app/__init__.py`、`web/app/routers/__init__.py`、`web/tests/__init__.py`；根 `requirements.txt`（core 五库白名单）一行不改
- [X] T002 [P] 用 Vite 在 `webui/` 脚手架化 Vue 3 工程：`package.json`、`vite.config.js`（dev server 5173，`/api` 代理到 `http://localhost:8000`）、`index.html`、占位版 `src/main.js`、`src/App.vue`（main.js 的实际挂载在 T009 完成）
- [X] T003 [P] 更新根 `.gitignore`：追加 `data/`、`webui/node_modules/`、`webui/dist/`；确认 `.env` 已被忽略且不新增 `web/.env`
- [X] T004 [P] 创建 `web/.env.example`，每项加中文注释：`WEB_JWT_SECRET=`（本特性必填，缺失启动报错）、`WEB_MASTER_KEY=`（**本特性仅预留、不读取不校验，Spec 2 的 Fernet 加密起必填**）、`WEB_TOKEN_EXPIRE_MINUTES=720`、`WEB_DATABASE_URL=`（留空走默认 sqlite:///&lt;仓库根&gt;/data/app.db）、`WEB_CORS_ORIGINS=http://localhost:5173`

---

## Phase 2：Foundational（阻塞性前置）

**目的**：所有用户故事共用的配置、数据库、安全与前端基座

**⚠️ 关键**：本阶段完成前，任何用户故事任务都不得开始

- [X] T005 实现 `web/app/config.py`：用 pydantic-settings BaseSettings 读取 T004 的变量；`WEB_JWT_SECRET` 缺失时启动即报错并给出生成命令（research D5，禁止默认密钥）；数据库 URL 缺省指向仓库根 `data/app.db`
- [X] T006 实现 `web/app/db.py`：SQLAlchemy 2.x 同步 engine 与 SessionLocal、`get_db` 依赖；SQLite 连接启用 `PRAGMA foreign_keys=ON`；启动时确保 `data/` 目录存在
- [X] T007 实现 `web/app/security.py`：全代码库唯一 `CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")`（不装 bcrypt）；JWT HS256 签发/校验函数，claim 为 `sub`=用户 UUID、`ver`=token_version、`iat`、`exp`，密钥只从 T005 配置读取（research D1/D2）
- [X] T008 实现 `web/app/main.py` 应用工厂：创建 FastAPI 应用、按配置注册 CORS、挂载 routers 包、启动时 `metadata.create_all` 建表；本阶段先挂健康检查之外的空路由结构
- [X] T009 [P] 在 `webui/` 安装前端依赖（vue-router@4、pinia、naive-ui、axios、markdown-it、katex），在 T002 的占位 `src/main.js` 中挂载 Pinia、Router 与 Naive UI，并创建 `webui/src/router/index.js`：createRouter(history 模式) 中**一次性注册四条路由** `/`（LandingView）、`/login`（LoginView，公开）、`/register`（RegisterView，公开）、`/home`（HomeView，meta `requiresAuth: true`），组件一律用动态 `import()` 引用，使视图文件可在后续故事逐个创建而不改路由表；本任务**不写** `beforeEach` 守卫（归 T033）。基座检查点只验证 `npm run dev` 能启动，不访问业务路由（视图文件尚未创建，访问会报模块缺失；`npm run build` 待 T040 视图齐备后执行）
- [X] T010 [P] 实现 `webui/src/api/client.js` 基座：唯一 axios 实例；请求拦截器从 localStorage 读取令牌并自动附带 `Authorization: Bearer`；响应拦截器只处理网络不可用（暴露统一"服务不可用"提示通道）。**本任务不实现 401 处理**（清空会话/跳登录依赖 auth store 与守卫，归 T033 接线）；业务页面不得自行处理凭证附带（FR-017）
- [X] T011 [P] 实现 `webui/src/layouts/AppLayout.vue` 静态骨架：顶部栏放置用户名占位、退出登录按钮（先不接线）与一个空的导航菜单位（无激活项，即 FR-015 所说"预留导航位置"）+ 主体 `<router-view>` 内容区；顶栏动态内容由 T027（用户名）、T034（退出动作）接线

**检查点**：基座就绪，用户故事可开始

---

## Phase 3：User Story 1 - 一键本地启动与健康可见（P1）🎯 MVP

**目标**：两条命令启动前后端，浏览器看到后端健康状态；断网可用

**独立测试**：干净检出按 quickstart 第 2、3 节启动，5173 落地页显示后端状态点；停掉后端出现"服务不可用"

### US1 测试（先行，确认失败再实现）

- [X] T012 [P] [US1] 创建 `web/tests/conftest.py`：临时目录 SQLite、配置覆盖、FastAPI TestClient fixture，保证测试零网络
- [X] T013 [P] [US1] 在 `web/tests/test_health.py` 编写 `GET /api/health` 返回 200 且体为 `{"status": "ok"}` 的测试

### US1 实现

- [X] T014 [US1] 实现 `web/app/routers/health.py`（无认证、不依赖数据库与外部服务）并在 `web/app/main.py` 挂载 `/api/health`，使 T013 通过
- [X] T015 [US1] 实现 `webui/src/views/LandingView.vue`（对应 T009 已注册的 `/` 路由）：加载时请求 `/api/health`，成功显示"后端正常"状态，失败/网络错误经 T010 的统一通道显示"服务不可用"（US1 场景 2）
- [X] T016 [US1] 执行本地联调：`uvicorn web.app.main:app --reload --port 8000` 与 `webui` 下 `npm run dev` 同时运行，验证代理联通与断网启动（quickstart 场景 1、2；场景 12 的断网注册—登录全流程待 US3 完成后由 T042 统一验收）

**检查点**：US1 可独立演示——健康骨架 MVP 成立

---

## Phase 4：User Story 2 - 注册新账户（P1）

**目标**：用户名+密码注册，规则校验、重名拒绝、密码不明文

**独立测试**：注册合法用户成功并可登录；重名/弱输入被明确拒绝；库中无明文密码

### US2 测试（先行）

- [X] T017 [US2] 在 `web/tests/test_auth.py` 编写注册测试：合法输入 201 且响应仅含 id/username/created_at；重名 409；`Alice` 与 `alice` 判为同名；用户名/密码规则不合法 422（规则逐字采用 data-model §1.1：用户名规则为字母/数字/下划线/中文、长度 3–30（文档可读形式 `^[A-Za-z0-9_<CJK>]{3,30}$`，其中中文区段 U+4E00–U+9FFF；**代码中必须写 ASCII 转义形式**，不得写中文字面量，规避 Windows 源码编码问题）、密码 8–64 字符）；任何响应与日志不含明文密码

### US2 实现

- [X] T018 [US2] 实现 `web/app/models.py` 的 User 模型，字段逐字按 data-model §1：id VARCHAR(36) 主键（uuid4）、username VARCHAR(30) 非空、username_key VARCHAR(30) 非空唯一索引、password_hash VARCHAR(255) 非空、token_version INTEGER 非空默认 0、created_at DATETIME 非空（naive UTC）
- [X] T019 [US2] 实现 `web/app/schemas.py`：`UserCreate`（username/password 携带与 data-model §1.1 完全一致的校验）与 `UserOut`（id/username/created_at，禁止输出密码字段）
- [X] T020 [US2] 在 `web/app/routers/auth.py` 实现 `POST /api/auth/register`：用 T007 的 CryptContext 哈希、`username_key=username.lower()` 判重、捕获唯一索引 IntegrityError 转 409 `{"detail":"用户名已被占用"}`；成功返回 201 UserOut，不签发令牌；在 main.py 挂载
- [X] T021 [US2] 实现 `webui/src/api/auth.js` 的 register 封装与 `webui/src/views/RegisterView.vue`（对应 T009 已注册的 `/register` 路由）：前端同规则即时校验、409/422 字段级中文回显、成功引导至登录页

**检查点**：US1+US2 均可独立工作

---

## Phase 5：User Story 3 - 登录与受保护身份（P1）

**目标**：凭据正确获令牌访问受保护页；一切非法令牌/凭据被统一拒绝；重启账户不丢

**独立测试**：正确登录进入显示本人身份的 /home；错误/伪造/过期访问 100% 401

### US3 测试（先行）

- [X] T022 [US3] 在 `web/tests/test_auth.py` 追加登录测试（T017 已建该文件，顺序追加，不并行）：正确凭据 200 返回 access_token/token_type=bearer/expires_at；用户不存在与密码错误均返回同一 401 `{"detail":"用户名或密码无效"}`；空字段 422
- [X] T023 [US3] 在 `web/tests/test_auth.py` 追加 `GET /api/auth/me` 测试（与 T022 同文件追加，不并行）：有效令牌 200 返回本人；无令牌、伪造签名、过期、`ver` 与库中不符、结构损坏（乱码或非三段式 JWT，如 `"not-a-jwt"`）五种情况均 401 `{"detail":"未授权或登录已过期"}`（覆盖 spec 边界情况"结构损坏的令牌"与契约"格式错误"分支）

### US3 实现

- [X] T024 [US3] 在 `web/app/schemas.py` 追加 `LoginRequest`（username/password）与 `TokenResponse`（access_token/token_type/expires_at）
- [X] T025 [US3] 实现 `web/app/deps.py` 的 `get_current_user`：解析 `Authorization: Bearer`、用 T007 校验签名与有效期、加载用户并比对 `ver`，任一不符统一 401；这是全项目唯一鉴权依赖（FR-009）
- [X] T026 [US3] 在 `web/app/routers/auth.py` 实现 `POST /api/auth/login`（按 username_key 查找、通用 401、签发带当前 token_version 的 JWT，exp 默认 720 分钟）与 `GET /api/auth/me`（依赖 get_current_user，返回 UserOut）
- [X] T027 [US3] 实现 `webui/src/stores/auth.js`（Pinia）：token 与当前用户状态、localStorage 持久化与启动恢复、login/fetchMe/clear 动作
- [X] T028 [US3] 实现 `webui/src/views/LoginView.vue`（对应 T009 已注册的公开路由 `/login`）：调 login、通用错误文案、成功后读取 `redirect` 查询参数跳转（缺省 /home；redirect 的强制拦截在 T033 守卫落地前，未登录直访 /home 仅由后端 /me 401 兜底，此处不做前端拦截）
- [X] T029 [US3] 实现 `webui/src/views/HomeView.vue`：经 AppLayout 呈现，挂载时请求 `/me` 显示本人用户名（受保护占位页，FR-007）；`/home` 路由及其 `requiresAuth: true` meta 已在 T009 配置
- [X] T030 [US3] 在 `web/tests/test_auth.py` 追加持久化测试：用文件型临时库创建用户后销毁应用实例、重新创建应用与库连接，账户仍存在且可登录（SC-005）

**检查点**：US1–US3 形成可登录的完整闭环

---

## Phase 6：User Story 4 - 路由门禁与退出登录（P2）

**目标**：匿名无法进入内部页、登录后回跳、退出后令牌服务端立即失效

**独立测试**：直输 /home 被拦并回跳；退出后旧令牌调 /me 返回 401

### US4 测试（先行）

- [X] T031 [US4] 在 `web/tests/test_auth.py` 追加退出测试（同文件顺序追加，不并行）：已登录调 `POST /api/auth/logout` 返回 200；此后用户 token_version 加一，旧令牌调 `/me` 返回 401；未带令牌调 logout 返回 401

### US4 实现

- [X] T032 [US4] 在 `web/app/routers/auth.py` 实现 `POST /api/auth/logout`（依赖 get_current_user；token_version 原子 +1；返回 `{"status":"logged_out"}`）
- [X] T033 [US4] 在 T009 已建的 `webui/src/router/index.js` 中补 `beforeEach` 全局守卫：检查目标 `requiresAuth` 与 auth store 状态，匿名跳 `/login?redirect=<原路径>`，登录成功回跳（FR-010）；同时回填 T010 预留的 401 处理：在 `client.js` 响应拦截器中收到 401 时调 auth store 清空会话并携当前路径跳 `/login`，业务页面仍不自行处理（FR-017）
- [X] T034 [US4] 在 auth store 增加 logout 动作（调接口、丢弃本地令牌），并把 AppLayout 顶栏退出按钮接到该动作，退出后跳 `/login`
- [X] T035 [US4] 用 `WEB_TOKEN_EXPIRE_MINUTES=1` 验证令牌过期后任意受保护操作被统一引导至登录（quickstart 场景 14，手工或自动化均可）

**检查点**：US1–US4 的认证与门禁完整

---

## Phase 7：User Story 5 - 统一应用框架与视觉基线（P2）

**目标**：统一布局/主题/反馈与请求口径就位，富文本+公式渲染基建可用

**独立测试**：在各内部页间切换布局连续、反馈一致；占位页的静态公式示例正确渲染

- [X] T036 [US5] 在 `webui/src/App.vue` 接入 Naive UI 全局 Provider（NConfigProvider 中文 locale、NMessageProvider/NDialogProvider），确立成功/失败/加载反馈的统一调用方式，供各视图替换零散提示
- [X] T037 [P] [US5] 实现 `webui/src/components/MarkdownView.vue`：markdown-it 解析 Markdown，接入 KaTeX 处理 `$...$` 与 `$$...$$` 公式及样式（FR-016 的渲染基建，本特性不做阅读器页面）
- [X] T038 [US5] 在 `webui/src/views/HomeView.vue` 加入一段本地静态 Markdown+公式示例，经 MarkdownView 渲染，证明基线可用
- [X] T039 [US5] 把 Landing/Login/Register/Home 的页面结构统一接入路由布局规范（受保护页一律经 AppLayout），完成组件风格与交互走查，确保页面切换无视觉跳变（SC-006）

**检查点**：五个用户故事全部可独立验收

---

## Phase 8：Polish 与跨故事收尾

**目的**：交付形态、安全审计与全量验收

- [X] T040 [P] 在 `web/app/main.py` 增加交付形态静态托管：存在 `webui/dist/` 时挂载 StaticFiles，`/api/*` 优先匹配，其余路径回退 index.html；执行 `npm run build` + 单端口 8000 验证（quickstart 第 6 节）
- [X] T041 [P] 安全自检并修正：全仓 grep 确认密码/密钥不出现在日志、响应体、错误信息中；pbkdf2 为唯一哈希口径；所有 secret 仅来自环境变量；登录失败文案不区分用户是否存在
- [X] T042 运行 `pytest web/tests -v` 全量通过，并按 `specs/001-web-foundation/quickstart.md` 第 5 节 14 个手工场景逐条验收记录（含断网场景）；确认 `core/` 与根 `requirements.txt` 零改动（git diff 复核，FR-014）

---

## 依赖与执行顺序

### 阶段依赖

- Phase 1（Setup）：无依赖，T002/T003/T004 可并行
- Phase 2（Foundational）：依赖 Phase 1；T009/T010/T011 前端三件可并行；T005–T008 后端顺序为 config→db→security→main；**阻塞全部用户故事**
- Phase 3–7：按 P1→P1→P1→P2→P2 顺序推进（US1→US2→US3→US4→US5）
- Phase 8：全部故事完成后执行

### 故事间依赖

- US1：Foundational 完成即可启动，无故事依赖（MVP）
- US2：依赖 US1 的应用骨架与测试基座（conftest）
- US3：依赖 US2 的 User 模型与 schemas.py（同文件追加，故 T024/T026 不标 [P]）
- US4：依赖 US3 的 get_current_user 与 auth store
- US5：依赖 US3/US4 已有视图与布局，做统一接入与基建演示

### 故事内部顺序

- 测试任务先写并确认失败 → 模型 → schema → 路由实现 → 前端页面 → 手工独立验收

### 并行机会

- Setup：T002、T003、T004
- Foundational：T009、T010、T011（T005–T008 顺序执行）
- US1：T012（conftest.py）、T013（test_health.py）——不同文件，可并行
- US3：T022→T023 顺序追加同一 test_auth.py，不并行
- US4：T031 同文件顺序追加，不并行
- Polish：T040、T041
- 注意：同一文件追加任务（schemas.py、routers/auth.py、test_auth.py、main.py）不标 [P]，避免冲突

---

## 并行示例：Foundational 阶段

```text
并行发起：
T009 前端依赖、main.js 挂载、router/index.js 四条基础路由
T010 webui/src/api/client.js 基座（令牌注入 + 网络错误通道）
T011 webui/src/layouts/AppLayout.vue 静态布局骨架
（后端 T005→T006→T007→T008 串行链同步推进）
```

---

## 实施策略

### MVP 先行（仅 US1）

1. 完成 Phase 1 + Phase 2
2. 完成 Phase 3（US1）
3. **停下验收**：健康检查与本地联调独立演示

### 增量交付

1. Setup + Foundational → 基座
2. +US1 → 健康骨架 MVP
3. +US2 → 可注册
4. +US3 → 可登录、有受保护身份（完整认证闭环）
5. +US4 → 门禁与可验证退出
6. +US5 → 统一视觉基线，具备 Spec 2/3 的填页条件

---

## 备注

- [P] = 不同文件、无未完成依赖；同文件追加任务一律不标 [P]
- 后端测试先写并确认失败（FR-013）；前端以 quickstart 手工场景验收
- core/ 与根 requirements.txt 全程零改动，T042 用 git diff 复核
- 每个检查点可停下独立演示；全部完成后进入 `/speckit-analyze`（实现前一致性检查）与 `/speckit-converge`（实现后验收）

---

## Phase 9：Convergence

> 2026-09-24 `/speckit-converge` 对照 spec/plan/契约/宪法评估产出；
> 除下列两项外，FR-001～017、SC-001～006、全部用户故事验收场景与宪法原则均已满足。

- [X] T043 修复注册并发竞争：`web/app/routers/auth.py` 的 register 在保留先查 409 的同时，捕获 commit 时唯一索引抛出的 `sqlalchemy.exc.IntegrityError`，回滚会话并统一返回 409 `{"detail":"用户名已被占用"}`，保证两个并发同名请求恰有一个 201、另一个 409（不是 500）；在 `web/tests/test_auth.py` 追加并发同名回归测试（两线程同时注册同一用户名，断言 201+409 各一）per FR-004、spec 边界情况「两个访问者几乎同时注册同一个用户名」、contracts/api-auth.md §2（partial）
- [X] T044 `webui/src/views/LandingView.vue` 增加健康状态周期轮询（建议 10–15 秒一次 /api/health）：页面打开后后端被停止时状态点自动翻为「服务不可用」，后端恢复后自动翻绿；组件卸载时 `clearInterval` 清理，保留现有 onMounted 立即探测与 onNetworkUnavailable 通道 per US1 验收场景 2、FR-002、contracts/api-auth.md §1（partial）
