# 技术方案：网页客户端骨架与用户认证

**分支**：`001-web-foundation` | **日期**：2026-09-23 | **规格**：[spec.md](./spec.md)

**输入**：来自 `/specs/001-web-foundation/spec.md` 的特性规格

## 摘要

在不改 `core/` 一行代码的前提下，新增两个彼此独立的工程：`web/`
（FastAPI 后端：健康检查、用户注册/登录、JWT 认证、SQLite 持久化）与
`webui/`（Vue 3 单页应用：落地/登录/注册/受保护占位页、统一布局与请求
通道、富文本渲染基建）。后端开发态经 Vite 代理联调，交付态可由 FastAPI
同域托管前端构建产物。全部流程离线可运行、可测试。

## 技术上下文

**语言/版本**：Python 3.11+（本机 3.13.7）；前端 JavaScript（ES2022，
Vue 3 SFC，不引入 TypeScript 以降低本阶段心智负担）；Node.js 18+（本机
v24.16.0）+ npm 11。

**后端依赖**（独立文件 `web/requirements.txt`，不碰 core 的根
requirements.txt 白名单）：

- fastapi、uvicorn[standard]：HTTP 服务
- sqlalchemy 2.x：ORM 与 SQLite 访问（同步引擎；本特性无并发写压力）
- pyjwt：JWT 签发/校验（HS256）
- passlib：仅用其纯 Python 的 `pbkdf2_sha256` CryptContext，**不安装
  bcrypt**，避开 3.13 下 bcrypt 原生依赖兼容坑
- pydantic-settings：环境变量/.env 配置注入
- pytest、httpx：自动化测试（FastAPI TestClient 基于 httpx）

**前端依赖**：vue@3、vue-router@4、pinia、naive-ui、axios；以及富文本
基建 markdown-it + katex（供 Spec 3 阅读器直接复用，本特性只做组件不做
页面）。

**存储**：SQLite 单文件 `data/app.db`（连接串可配置）；SQLAlchemy 建表，
首次启动自动初始化。数据库文件随 cwd 漂移的问题通过配置中默认写死
仓库根下 `data/` 绝对路径规避。

**测试**：后端 pytest（离线、零网络）：健康检查、注册/登录/受保护访问、
错误凭据、伪造与过期令牌、重启持久化（用临时目录数据库）。前端以
quickstart 手工验收为准，不引入前端测试框架（宪法未要求）。

**目标平台**：本地开发机（Windows/PowerShell 为主，命令兼容
Linux/macOS）。浏览器为 evergreen Chrome/Edge。

**项目类型**：前后端分离 Web 应用（web-service + spa）。

**性能目标**：单机开发负载，无吞吐指标；注册/登录/me 常规亚秒级响应；
pytest 全套在 10 秒内完成。

**约束**：运行期零外部网络调用（注册/登录/健康检查/测试全部离线）；
core/ 零改动；所有密钥只来自环境变量（支持 .env，.env 不入库）。

**规模/范围**：2 个新工程；后端约 8 个模块 + 2 个路由 + 2 个测试文件；
前端 4 个视图 + 1 个布局 + 1 个渲染基建组件 + 统一请求封装。

## 宪法检查

*门控：Phase 0 研究前必须通过；Phase 1 设计后复核。*

| 宪法原则 | 本特性符合性 | 说明 |
|---|---|---|
| 一、core 冻结 | ✅ 通过 | 不新增/修改 `core/` 任何文件；web 依赖独立成 `web/requirements.txt`，不污染 core 白名单；本特性甚至不调用 core |
| 二、继承架构边界 | ✅ 通过 | 配置全部经 `web/app/config.py` 注入；HTTP/持久化/auth 只存在于 web 层，不向 core 反向渗透 |
| 三、多租户隔离 | ✅ 通过 | 单一 `get_current_user` 依赖鉴权；`/me` 只返回令牌本人；用户主键为 UUID，避免可枚举；为 Spec 2 的归属校验铺好身份根基 |
| 四、凭据与密钥安全 | ✅ 通过 | pbkdf2_sha256 加盐哈希（唯一 CryptContext）；JWT 密钥仅从 `WEB_JWT_SECRET` 读取；通用登录失败文案；本特性无第三方 API Key（Fernet 在 Spec 2 引入） |
| 五、长任务后台化 | ➖ 不适用 | 本特性无论文转写/长任务 |
| 六、契约先行 | ✅ 通过 | Pydantic 模式 + OpenAPI 为唯一契约（见 contracts/）；前端只用 axios 统一通道，不碰文件系统/数据库/core |
| 七、离线可验收 | ✅ 通过 | 注册/登录/健康检查纯本地；pytest 零网络；quickstart 含断网验收步骤。`WEB_FAKE_MODE` 在 Spec 2 才消费，本特性不建空开关 |

**门控结论**：无违例，无需复杂度豁免。Phase 1 设计后复核结论不变
（见文末）。

## 项目结构

### 本特性文档

```text
specs/001-web-foundation/
├── plan.md              # 本文件
├── research.md          # Phase 0：技术决策记录
├── data-model.md        # Phase 1：用户与令牌数据设计
├── quickstart.md        # Phase 1：本地运行与验收手册
├── contracts/
│   └── api-auth.md      # Phase 1：HTTP 接口契约
└── tasks.md             # /speckit-tasks 产出（本命令不创建）
```

### 源码结构（仓库根）

```text
core/                     # 既有，零改动
docs/                     # 既有
web/                      # 新增：FastAPI 后端
├── requirements.txt      # web 专属依赖（与根 requirements.txt 分离）
├── .env.example          # 配置模板（.env 实际文件不入库）
├── __init__.py
├── app/
│   ├── __init__.py
│   ├── main.py           # 应用创建、路由挂载、静态托管、建表
│   ├── config.py         # pydantic-settings 配置（密钥/DB/令牌时长/CORS）
│   ├── db.py             # engine、SessionLocal、get_db 依赖
│   ├── models.py         # User ORM（含 token_version）
│   ├── schemas.py        # 请求/响应/错误 Pydantic 模式
│   ├── security.py       # 密码哈希、JWT 签发与校验（唯一 CryptContext）
│   ├── deps.py           # get_current_user 单一鉴权依赖
│   └── routers/
│       ├── __init__.py
│       ├── health.py     # GET /api/health
│       └── auth.py       # register/login/logout/me
└── tests/
    ├── __init__.py
    ├── conftest.py       # 临时 DB + TestClient + 配置覆盖
    ├── test_health.py
    └── test_auth.py

webui/                    # 新增：Vue 3 SPA
├── package.json
├── vite.config.js        # 开发态 /api 代理到 8000
├── index.html
├── .gitignore            # node_modules/dist 等
└── src/
    ├── main.js
    ├── App.vue
    ├── router/index.js   # 路由表 + 全局守卫（requiresAuth、回跳）
    ├── stores/auth.js    # Pinia：令牌/当前用户/登录/退出/持久化
    ├── api/
    │   ├── client.js     # axios 实例：注入令牌、401 统一处理、网络错误提示
    │   └── auth.js       # register/login/logout/me 接口封装
    ├── layouts/
    │   └── AppLayout.vue # 顶栏（用户名/退出/导航占位）+ 内容区
    ├── components/
    │   └── MarkdownView.vue  # markdown-it + KaTeX 基建（本特性仅演示）
    └── views/
        ├── LandingView.vue   # 落地页 + 后端健康状态
        ├── LoginView.vue
        ├── RegisterView.vue
        └── HomeView.vue      # 受保护占位页（显示本人身份）

data/                     # 新增：app.db（gitignore；首启自动创建）
```

**结构决策**：采用 Web 应用前后端双工程结构。后端不沿用 `backend/`
而用任务说明书已约定的 `web/`；core 依赖白名单（根 requirements.txt）
与 web 依赖严格分离，体现原则一/二的物理隔离。前端构建产物
`webui/dist/` 在交付形态下由 FastAPI StaticFiles 同域挂载（`/api` 优先
匹配，其余回退 index.html），开发态用 Vite 代理，两套形态共用同一套
API 契约。

**仓库配套改动**（仅非源码配置）：根 `.gitignore` 增补 `data/`、
`webui/node_modules/`、`webui/dist/`（`.env` 已在忽略列表）。

## 复杂度跟踪

无宪法违例，无需豁免。

---

## Phase 1 后宪法复核

设计产物（research.md、data-model.md、contracts/api-auth.md、
quickstart.md）完成后逐项复核：原则一（core 零改动）、二（配置注入、
分层）、三（UUID 身份 + 集中鉴权 + logout 经 token_version 服务端失效）、
四（pbkdf2 + 环境变量密钥 + 笼统错误文案）、六（契约与前端通道）、
七（离线测试/断网验收）均已在设计中落实；原则五不适用。**复核通过，
无新增违例。**
