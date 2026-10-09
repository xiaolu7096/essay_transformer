# 快速运行与验收手册：网页客户端骨架与用户认证

**日期**：2026-09-23 | **对应方案**：[plan.md](./plan.md)

本手册是 Spec 1 的端到端验收依据：按步骤操作即可在本地跑通前后端，
并逐条核对规格的成功标准。命令以 Windows PowerShell 为准
（Linux/macOS 仅激活脚本路径不同）。

## 1. 前置条件

| 工具 | 版本要求 | 本机已验证 |
|---|---|---|
| Python | 3.11+ | 3.13.7 |
| Node.js | 18+ | v24.16.0 |
| npm | 随 Node 提供 | 11.13.0 |

依赖安装阶段需要联网一次；**安装完成后，运行与全部验收均可断网进行**
（注册/登录/健康检查不调用任何外部服务）。

## 2. 后端启动

在仓库根目录 `mvp_test/` 下：

```powershell
# 2.1 安装依赖（core 白名单 + web 专属依赖，两个文件互不污染）
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt -r web\requirements.txt

# 2.2 准备配置（.env 不入库；生成两个随机密钥）
Copy-Item web\.env.example web\.env
python -c "import secrets; print('WEB_JWT_SECRET=' + secrets.token_urlsafe(32))"
python -c "import secrets; print('WEB_MASTER_KEY=' + secrets.token_urlsafe(32))"
# 把上面两行输出填进 web\.env
# 注意：本特性只读取 WEB_JWT_SECRET；WEB_MASTER_KEY 仅在示例文件中预留，
# 不读取不校验，Spec 2（API Key 加密）起才成为必填项

# 2.3 启动（默认 http://localhost:8000）
uvicorn web.app.main:app --reload --port 8000
```

首次启动自动创建 `data/app.db` 与 `users` 表。

验证：

- 浏览器打开 `http://localhost:8000/api/health`，看到 `{"status":"ok"}`
- 打开 `http://localhost:8000/docs`，能看到 register/login/logout/me、
  health 五个端点（OpenAPI 即契约，见
  [contracts/api-auth.md](./contracts/api-auth.md)）

## 3. 前端启动

新开一个终端：

```powershell
cd webui
npm install
npm run dev
```

打开 Vite 提示的地址（默认 `http://localhost:5173`）。开发态 `/api`
请求自动代理到 8000 端口。

## 4. 自动化测试（离线，后端）

在仓库根目录、虚拟环境激活状态下：

```powershell
pytest web\tests -v
```

覆盖：健康检查、注册成功/重名/规则校验、登录成功/错误凭据通用文案、
me 的 401（缺失/伪造/过期令牌）、logout 后旧令牌失效、临时数据库模拟
重启后账户仍在。全部通过是进入 `/speckit-converge` 的前提。

## 5. 手工验收场景（对照成功标准）

| # | 操作 | 预期结果 | 对应标准 |
|---|---|---|---|
| 1 | 干净机器按本文档第 2、3 节操作 | 10 分钟内两服务启动，5173 落地页显示"后端正常"状态点 | SC-001 |
| 2 | 停掉后端 | 落地页状态点变为"服务不可用"，而非白屏/静默 | US1-2 |
| 3 | 注册新用户 `Alice_01` / 8 位以上密码 | 注册成功并跳转登录页；SQLite 中 password_hash 非明文 | US2-1、SC-004 |
| 4 | 再次注册同名（尝试 `alice_01`） | 提示"用户名已被占用"，无第二账户 | FR-004、边界情况 |
| 5 | 输入 `ab`/空密码等提交 | 表单与后端双重拦截，指出具体规则 | US2-3 |
| 6 | 用错误密码登录 | 提示"用户名或密码无效"；不存在的用户同名提示 | US3-2、边界情况 |
| 7 | 正确登录 | 进入 `/home`，占位页显示本人用户名；2 分钟内完成 3–7 步 | SC-002、US3-1 |
| 8 | 未登录直接访问 `http://localhost:5173/home` | 跳转 `/login?redirect=/home`；登录后自动回到 /home | US4-1/2、SC-003 |
| 9 | 不带令牌直接请求 `GET /api/auth/me`（浏览器/curl） | 401 | FR-008、SC-003 |
| 10 | 点击顶栏"退出登录" | 回到登录页；用浏览器回退或直接访问 /home 均不可达；旧令牌调 /me 返回 401 | US4-3、FR-011 |
| 11 | 重启 uvicorn 后重新登录 | 账户仍存在，可正常登录 | US3-4、SC-005 |
| 12 | 断网后重复注册→登录→进入 /home | 全流程可用 | FR-012、SC-005 |
| 13 | 在 /home 与占位页间切换 | 布局（顶栏含用户名/退出/导航占位）、组件风格、提示方式一致；MarkdownView 最小演示的公式正常渲染 | US5、SC-006、FR-015/016 |
| 14 | 令令牌过期（可把 `WEB_TOKEN_EXPIRE_MINUTES=1` 重试验证）后点任意操作 | 被统一拦截并引导登录，无页面各自报错 | US4-4、FR-017 |

## 6. 交付形态（可选验证）

```powershell
cd webui; npm run build; cd ..
uvicorn web.app.main:app --port 8000
```

打开 `http://localhost:8000/`：FastAPI 同域托管 `webui/dist`，`/api/*`
接口与页面同端口可用（无需 CORS）。这验证后续单机分发的形态。

## 7. 配置项一览（web/.env.example）

| 变量 | 必填 | 默认 | 说明 |
|---|---|---|---|
| WEB_JWT_SECRET | 是 | 无（缺失即启动报错） | JWT HS256 签名密钥 |
| WEB_MASTER_KEY | Spec 2 起必填 | 无 | 第三方 API Key 加密主密钥；本特性仅在 .env.example 预留，不读取不校验 |
| WEB_TOKEN_EXPIRE_MINUTES | 否 | 720 | 令牌有效期 |
| WEB_DATABASE_URL | 否 | sqlite:///&lt;仓库根&gt;/data/app.db | SQLite 位置 |
| WEB_CORS_ORIGINS | 否 | http://localhost:5173 | 开发态允许来源（逗号分隔） |

## 8. 本特性明确不验证的内容

论文提交/转写、API Key 管理页面、进度与历史、阅读器正式页面——
分别属于 Spec 2、Spec 3，不在本手册范围；但 Markdown/KaTeX 基建
（场景 13）在本特性已就位。
