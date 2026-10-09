# 接口契约：第三方密钥管理

**日期**：2026-09-27 | **对应**：[spec.md](../spec.md) FR-001～003、US1

- 基址：`/api`；全部端点需要 `Authorization: Bearer <access_token>`
  （Spec 001 单一鉴权依赖）。未带/非法/过期令牌统一
  `401 {"detail":"未授权或登录已过期"}`。
- 内容类型：`application/json`（UTF-8）。
- 服务种类 `service` 为路径参数，取值限定：`mineru`（论文解析）、
  `deepseek`（大模型转写）；其他值一律 422。
- 任何响应、任何错误体都**不得**包含密钥明文或密文；只回掩码与
  是否已配置。

## 1. 查看两个服务的密钥状态

`GET /api/credentials`

**200**

```json
{
  "credentials": [
    { "service": "mineru", "configured": true, "mask": "••••ab3f" },
    { "service": "deepseek", "configured": false, "mask": null }
  ]
}
```

- 数组固定包含两个服务，未配置的排在与否都存在（前端据此渲染两个
  设置块）。
- `mask`：已配置时为 `••••` + 密钥末 4 位；未配置为 null。

## 2. 保存（首次或覆盖）密钥

`PUT /api/credentials/{service}`

请求体：

```json
{ "secret": "用户输入的密钥原文" }
```

校验规则（前后端一致）：

- 去除首尾空白后长度 1–200；空或纯空白 → 422。
- 服务端再做一次同样校验；不信任前端。

**200**（首次与覆盖同响应）

```json
{ "service": "mineru", "configured": true, "mask": "••••ab3f" }
```

**422**（校验失败；错误体沿用 Spec 001 脱敏处理器，不回显提交值）

```json
{ "detail": [ { "loc": ["body", "secret"], "msg": "密钥不能为空，长度 1-200 个字符", "type": "value_error" } ] }
```

> 注意：覆盖保存后立即对后续新任务生效；正在运行的任务不受影响
> （它在开跑时已取用当时的密钥，仅在服务边界内存活）。

## 3. 删除密钥

`DELETE /api/credentials/{service}`

- 已配置：删除整行。
- 未配置：幂等返回成功（不报错）。

**200**

```json
{ "service": "deepseek", "configured": false, "mask": null }
```

## 4. 通用错误

| 场景 | 状态码 | 响应 |
|---|---|---|
| 未认证/令牌失效 | 401 | `{"detail":"未授权或登录已过期"}` |
| service 非 mineru/deepseek | 422 | 标准校验错误体 |
| 服务端加密主密钥未配置（真实模式启动配置错误） | 500 | `{"detail":"服务暂时不可用，请联系维护者"}`（不回显内部原因） |

## 5. 安全自检要点（测试断言）

1. 数据库 `secret_encrypted` 列不以明文包含密钥子串；
2. 任何端点响应体不含密钥明文与 Fernet token；
3. 用户 A 无法读到/改到/删掉用户 B 的凭据（行级归属，B 的存在性
   不暴露——凭据端点是按"当前用户"查询，不带他人标识参数）；
4. 服务端日志在密钥保存/取用路径上不出现明文（日志出口脱敏）。
