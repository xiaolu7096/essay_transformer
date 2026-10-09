# 技术决策研究：网页客户端骨架与用户认证

**日期**：2026-09-23 | **对应方案**：[plan.md](./plan.md)

本特性的技术栈由宪法预先固定，无悬而未决的待澄清项。本文件记录关键
决策的理由与被否决的备选，供后续 spec 与代码评审引用。

## D1. 登录令牌：JWT + 用户表 token_version 实现服务端退出

**决策**：签发 HS256 JWT（claim：`sub`=用户 UUID、`ver`=token_version、
`iat`、`exp`）；受保护请求集中校验签名、有效期与 `ver` 是否等于库中
当前版本；退出登录调用 `POST /api/auth/logout`，将该用户
token_version +1，旧令牌立即失效。

**理由**：JWT 无状态、校验便宜、前后端实现简单，契合单机部署；但纯
无状态 JWT 在过期前无法撤销，满足不了规格 FR-011（退出后令牌不再
授予访问权）。token_version 只用一个整数字段就获得服务端失效能力，
比 jti 黑名单表简单，且重启持久、对"改密码后全部旧会话失效"等未来
需求天然友好。

**备选**：① 纯无状态 JWT，退出仅前端丢弃——服务端旧令牌仍有效，
违反 FR-011，否决；② Redis jti 黑名单——引入额外服务，宪法明确本阶段
不用 Redis，否决；③ 传统服务端会话表——可行但要维护会话存储与 Cookie
 CSRF 细节，对纯 API + 令牌头的前后端分离形态更重，否决。

## D2. 密码哈希：passlib 的 pbkdf2_sha256，不装 bcrypt

**决策**：全代码库唯一一份 `CryptContext(schemes=["pbkdf2_sha256"],
deprecated="auto")` 放在 `web/app/security.py`；不安装 bcrypt。

**理由**：pbkdf2_sha256 是 passlib 的纯 Python 方案，在 Python 3.13
上无原生编译依赖风险；加盐、迭代次数由 passlib 默认策略管理；单一
CryptContext 保证全项目算法口径一致（宪法原则四明确要求）。

**备选**：① bcrypt（passlib[bcrypt]）——原生依赖在新 Python/Windows
组合下易出安装与版本兼容问题，本特性无必须用 bcrypt 的合规要求，
否决；② 标准库 `hashlib.pbkdf2_hmac` 自管盐与参数——零依赖可行，但
会产生自造格式与校验口径，与"唯一 CryptContext"的约定不一致，否决。

**迁移备注**：未来如更换算法，passlib 的 deprecated 机制支持多算法
并存验证与逐步重哈希；禁止直接替换导致存量账户无法登录。

## D3. ORM 与数据库访问：SQLAlchemy 2.x 同步引擎

**决策**：SQLAlchemy 2.x 声明式模型 + 同步 Session（`get_db` 依赖
管理生命周期）；SQLite 启用外键约束（`PRAGMA foreign_keys=ON`，本特性
暂未使用外键但为 Spec 2 预置）。

**理由**：本特性只有登录注册等极低耗时的本地查询，同步栈最简单、
测试稳定；Spec 2 的长任务将在线程池中运行并使用独立 Session，不要求
web 层整体异步化。FastAPI 对同步路由自动使用线程池，两者不冲突。

**备选**：SQLModel（Pydantic 与 ORM 合并）——概念少一层，但把存储
模型与 API 契约耦合，违背原则六"契约独立"的精神，否决；Tortoise/
encode ORM——生态与文档体量不及 SQLAlchemy，否决。

## D4. 用户名规则与大小写不敏感的唯一约束

**决策**：用户名 3–30 字符，允许字母、数字、下划线与中文
（`^[A-Za-z0-9_\u4e00-\u9fff]{3,30}$`）；表中同时存 `username`（原文，
用于展示）与 `username_key`（`username.lower()`，建唯一索引，用于判重
与登录查找）；密码 8–64 字符，前后端双重校验。

**理由**：避免 "Alice" 与 "alice" 注册成两个人；SQLite 对 Unicode
大小写折叠不可控，应用层生成 key 列最确定；保留原文避免展示名被
改写。并发同时注册同名用户时，数据库唯一索引兜底，恰一方成功（对应
spec 边界情况 1）。

**备选**：只允许 `[a-z0-9_]` 且强制小写——对中文用户不友好，否决；
依赖数据库 COLLATE NOCASE——对中文/Unicode 语义不一致，否决。

## D5. 配置：pydantic-settings + .env，缺失密钥快速失败

**决策**：`web/app/config.py` 用 BaseSettings 读取 `WEB_JWT_SECRET`、
`WEB_TOKEN_EXPIRE_MINUTES`（默认 720）、`WEB_DATABASE_URL`（默认
`sqlite:///<仓库根>/data/app.db`）、`WEB_CORS_ORIGINS`（默认
`http://localhost:5173`）。提供 `web/.env.example`；`.env` 不入库。
`WEB_JWT_SECRET` 缺失时启动即报错并给出生成命令，不使用硬编码默认
密钥。

**理由**：落实宪法原则二/四；快速失败优于"静默用默认密钥上线"。
pydantic-settings 原生支持类型转换与环境覆盖，测试中可直接用
`app.dependency_overrides` 或环境变量指向临时库。

**备选**：直接 os.getenv 散读各处——口径分散、难测试，否决；把密钥
提交进配置文件——违反原则四，禁止。

## D6. 前后端联调与交付形态

**决策**：开发态 Vite dev server（5173）把 `/api` 代理到 uvicorn
（8000）；交付态 `npm run build` 产出 `webui/dist/`，由 FastAPI
StaticFiles 同域挂载，`/api/*` 优先匹配，其余路径回退 index.html。
CORS 在开发态允许 5173，同域交付时不需要 CORS。

**理由**：开发态热更新体验好；交付态单端口单域名便于本地用户使用，
且消除跨域 Cookie/凭据复杂度；同一套 API 契约两种部署方式不变。

## D7. 前端语言：JavaScript，不引入 TypeScript

**决策**：Vue 3 `<script setup>` + JavaScript。

**理由**：本阶段重点在打通全链路与多用户骨架，使用者（项目所有者）
为 Python 背景；JS 可减少一层类型工程成本。Naive UI 本身不要求 TS。
未来如规模扩大可平滑迁移（Vite 与 Vue 生态对渐进加 TS 友好）。

## D8. 依赖文件物理分离

**决策**：core 依赖继续锁定在根 `requirements.txt`（5 个库白名单
不动）；web 依赖新建 `web/requirements.txt`。安装时
`pip install -r requirements.txt -r web/requirements.txt`。

**理由**：以物理文件边界落实宪法原则一/二——core 的依赖白名单是
既有约束，web 层的新依赖（fastapi/sqlalchemy 等）不得反向改写它；
未来单独打包 core 也不会拖入 Web 依赖。

## D9. 前端统一请求通道与认证状态

**决策**：唯一 axios 实例（`src/api/client.js`）：请求拦截器从 Pinia
auth store 注入 `Authorization: Bearer`；响应拦截器统一处理 401
（清空会话并跳登录，携带 redirect 目标）与网络不可用（落地页/全局
提示后端不可达）。令牌存 localStorage，Pinia store 初始化时恢复；
路由守卫只认 store 的认证状态。

**理由**：落实 FR-017 与原则六；业务页面零鉴权样板代码。localStorage
对本地单机工具足够；不引入刷新令牌机制（v0 令牌 12 小时，过期重新
登录，符合 US4 场景 4）。

## D10. 富文本渲染基建前置但不交付页面

**决策**：本特性安装 markdown-it + katex 并封装
`MarkdownView.vue`（Markdown 转 HTML、`$...$`/`$$...$$` 公式渲染、
KaTeX 样式接入），在受保护占位页放一个最小演示证明可用；阅读器页面、
7 章导航、`<details>` 交互全部留待 Spec 3。

**理由**：FR-016 要求"具备基础渲染能力供后续直接使用"；在基线特性
验证依赖与渲染管线可用，能避免 Spec 3 才发现公式渲染问题。渲染输出
必须只来自可信内容（论文转写结果），XSS 防护策略（HTML 清理）在
Spec 3 阅读器实现时落地，本特性演示内容为本地静态示例。
