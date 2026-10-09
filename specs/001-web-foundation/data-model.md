# 数据模型：网页客户端骨架与用户认证

**日期**：2026-09-23 | **对应方案**：[plan.md](./plan.md)

本特性只有一张持久化表 `users`，加上一个不落库的登录令牌结构。
Spec 2 的 `api_keys`、`tasks` 表将在各自数据模型中追加，并以
`user_id` 外键引用本表。

## 1. 表：users

| 字段 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | VARCHAR(36) | 主键 | 用户 UUID（应用层 uuid4 生成，字符串存储）；不作为可枚举序号暴露 |
| username | VARCHAR(30) | 非空 | 用户名原文，仅用于展示 |
| username_key | VARCHAR(30) | 非空、唯一、索引 | `username.lower()`，登录查找与判重的唯一键 |
| password_hash | VARCHAR(255) | 非空 | passlib pbkdf2_sha256 输出（含算法标识、迭代次数、盐、摘要） |
| token_version | INTEGER | 非空，默认 0 | 令牌版本；logout +1 使旧 JWT 失效（见 research D1） |
| created_at | DATETIME | 非空 | 注册时间，UTC，应用层以 naive UTC 写入、ISO8601 输出 |

**建表方式**：SQLAlchemy 2.x 声明式模型 `web/app/models.py:User`；
`main.py` 应用启动时 `metadata.create_all`（v0 不引入迁移工具，Spec 2
增表时仍用 create_all；等表结构需要演进时再评估 Alembic）。

### 1.1 校验规则（写入前）

| 规则 | 值 | 失败行为 |
|---|---|---|
| 用户名字符集 | `^[A-Za-z0-9_\u4e00-\u9fff]{3,30}$` | 422，指明具体规则 |
| 用户名唯一 | username_key 全表唯一 | 注册 409；并发插入靠唯一索引兜底 |
| 密码长度 | 8–64 字符 | 422 |
| 密码存储 | 只存 `CryptContext.hash()` 的结果 | 明文不得出现在日志/响应中 |

前后端做相同的用户名/密码格式校验（前端即时反馈，后端为最终门）；
后端不得以前端已校验为由省略校验。

### 1.2 与后续特性的关系（本特性不实现，仅固定方向）

- Spec 2 `api_keys.user_id` → `users.id`（一对一）
- Spec 2 `tasks.user_id` → `users.id`（一对多）
- 所有后代表查询必须带 `WHERE user_id = :current_user`（原则三）。

## 2. 登录令牌（JWT，不落库）

| claim | 内容 | 说明 |
|---|---|---|
| sub | 用户 id（UUID 字符串） | get_current_user 据此加载用户 |
| ver | 签发时的 token_version | 与库中不一致即拒绝（已退出/已升级） |
| iat | 签发时间（UTC 秒） | — |
| exp | 过期时间（UTC 秒） | iat + `WEB_TOKEN_EXPIRE_MINUTES`，默认 720（12 小时） |

- 算法：HS256；签名密钥仅来自 `WEB_JWT_SECRET`。
- 传输：`Authorization: Bearer <token>` 请求头；前端经统一 axios 通道
  附带。
- 失效途径：① exp 到期；② logout 使 token_version 递增；③ 密钥更换。
  三种情况服务端一律返回相同的 401，不区分原因。

### 令牌状态转换

```text
注册成功 ──► （无令牌，未登录）
                 │ 登录凭据正确
                 ▼
            [有效令牌 ver=n] ──退出登录──► ver=n+1，令牌作废 ──► 未登录
                 │ exp 到期 / 密钥更换
                 ▼
              401，引导重新登录
```

## 3. API 错误响应形态（契约级约定）

沿用 FastAPI 标准形态，全特性统一：

- 业务拒绝：`{"detail": "中文提示"}`，状态码 401/409 等。
- 输入校验失败：FastAPI 默认 422 体（字段级错误数组），前端按字段
  回显。
- 登录失败（用户不存在、密码错误、令牌 ver 不符、过期、伪造、缺失）
  全部为 401 + 通用文案"用户名或密码无效"（登录端点）或
  "未授权或登录已过期"（受保护端点），不透露用户是否存在。

字段细节与示例见 [contracts/api-auth.md](./contracts/api-auth.md)。
