# HTTP 接口契约：健康检查与用户认证

**日期**：2026-09-23 | **对应方案**：[plan.md](../plan.md)

本文件是 Spec 1 范围内前后端的唯一接口契约，与 FastAPI 自动生成的
OpenAPI（`/docs`）保持一致；如二者冲突，以本文件为准并同步修正代码。

- Base URL（开发态）：`http://localhost:8000`，前端经 Vite 代理同源访问 `/api`
- 所有路径以 `/api` 前缀
- 请求/响应体：`application/json; charset=utf-8`
- 认证方式：除健康检查与注册/登录外，均需请求头
  `Authorization: Bearer <access_token>`

通用错误体：`{"detail": "中文错误信息"}`；输入校验错误为 FastAPI
标准 422 结构（`detail` 为字段错误数组）。

---

## 1. 健康检查

### `GET /api/health`

**认证**：否

**200 响应**

```json
{ "status": "ok" }
```

用途：落地页轮询/加载时探测后端可达性；请求失败（网络层错误）即表示
后端不可达，前端显示"服务不可用"（对应 US1 场景 2）。本端点不依赖
数据库与任何外部服务，必须在断网时可用。

---

## 2. 注册

### `POST /api/auth/register`

**认证**：否

**请求体**

| 字段 | 类型 | 必填 | 规则 |
|---|---|---|---|
| username | string | 是 | 3–30 字符；字母、数字、下划线、中文 |
| password | string | 是 | 8–64 字符 |

```json
{ "username": "Alice_01", "password": "correct horse battery" }
```

**201 响应**

```json
{
  "id": "3f8c1a2e-7b4d-4e9a-9c21-6a0f5d8e7b11",
  "username": "Alice_01",
  "created_at": "2026-09-23T10:00:00"
}
```

**错误**

| 状态 | 场景 | 响应示例 |
|---|---|---|
| 409 | username_key 已存在（含并发竞争失败者） | `{"detail": "用户名已被占用"}` |
| 422 | 字段为空/长度或字符集不合法 | FastAPI 字段级错误数组 |

成功后不自动签发令牌，引导用户去登录。

---

## 3. 登录

### `POST /api/auth/login`

**认证**：否

**请求体**

```json
{ "username": "Alice_01", "password": "correct horse battery" }
```

登录查找按 `username.lower()` 匹配 username_key。

**200 响应**

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_at": "2026-09-23T22:00:00"
}
```

`expires_at` 为 UTC ISO8601（与令牌 exp 一致，默认签发后 12 小时）。

**错误**

| 状态 | 场景 | 响应 |
|---|---|---|
| 401 | 用户不存在 **或** 密码错误（同一文案，不区分） | `{"detail": "用户名或密码无效"}` |
| 422 | 字段为空或类型错误 | 标准 422 |

---

## 4. 获取当前用户

### `GET /api/auth/me`

**认证**：是

**200 响应**

```json
{
  "id": "3f8c1a2e-7b4d-4e9a-9c21-6a0f5d8e7b11",
  "username": "Alice_01",
  "created_at": "2026-09-23T10:00:00"
}
```

**错误**：令牌缺失/格式错误/过期/签名无效/`ver` 与库中不一致，
统一返回 401：`{"detail": "未授权或登录已过期"}`。

用途：前端启动与路由守卫恢复会话；受保护占位页展示本人身份
（FR-007）。

---

## 5. 退出登录

### `POST /api/auth/logout`

**认证**：是

**请求体**：无

**200 响应**

```json
{ "status": "logged_out" }
```

副作用：当前用户 `token_version` +1，使该用户此前签发的所有 JWT
立即失效（服务端可验证的退出，FR-011）。前端同时丢弃本地令牌。

**错误**：401（同 `/me`）。注：对已过期令牌调用 logout 仍返回 401，
前端对此按"已退出"处理，不阻塞用户跳登录。

---

## 6. 前端配套约定（非 HTTP，但属本契约）

- 路由：`/login`、`/register` 公开；`/`（落地页，公开，显示健康状态）；
  `/home`（受保护占位页）。其他 `requiresAuth` 路由由后续 spec 增补。
- 未认证访问受保护路由：跳 `/login?redirect=<原路径>`，登录成功后
  回跳（FR-010）。
- 401 响应由 axios 响应拦截器统一处理：清空 auth store → 携 redirect
  跳登录；业务页面不自行处理 401（FR-017）。
- 网络错误（后端不可达）：统一提示"服务不可用"；落地页据此渲染后端
  状态点。
