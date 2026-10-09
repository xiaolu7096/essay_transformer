# 接口契约：任务事件实时推送

**日期**：2026-09-28 | **对应**：[spec.md](../spec.md) FR-001～004、US1
| 上游：`002-task-pipeline/contracts/api-tasks.md`

- 基址 `/api`；本通道需要与普通接口**完全相同**的 Bearer 令牌
  （未认证统一 `401 {"detail":"未授权或登录已过期"}`）。
- **令牌只经 `Authorization` 请求头传递**：浏览器端使用 fetch
  流式读取而非原生 EventSource，因此令牌不出现在 URL、查询串
  或任何日志面（research D2）。
- 归属规则沿用 002：不存在或属于他人的任务，在建立流之前统一
  `404 {"detail":"任务不存在"}`，不泄露存在性。
- 事件不持久化、不支持历史回放；重连的真相来源是任务行快照。

## 1. 订阅任务事件

`GET /api/tasks/{task_id}/events`

- 请求头：`Authorization: Bearer <token>`、
  `Accept: text/event-stream`
- 成功响应：**200 OK**
  `Content-Type: text/event-stream; charset=utf-8`
  `Cache-Control: no-store`
  `Connection: keep-alive`（开发态由 Vite 代理透传）

### 1.1 帧格式

标准 SSE 帧：以空行分隔；`event:` 行给类型，`data:` 行为 JSON
（单行 JSON）；另有心跳注释行。

```text
event: snapshot
data: {"id":"…","status":"running","stage":"parse", …,"seq":3}

: ping

event: update
data: {"id":"…","status":"running","stage":"normalize", …,"seq":4}

event: terminal
data: {"id":"…","status":"succeeded", …,"seq":9}
```

### 1.2 帧语义与顺序（不可协商）

1. **snapshot**：连接后第一帧，必有且仅有一帧。载荷是该任务
   当前完整快照（等同 002 `TaskOut` 字段口径 + 信封字段
   `seq`）。
2. **update**：活动任务的阶段/进度/状态文字变化。`seq` 严格
   递增；客户端只接受 seq 更大的帧，重复/迟到帧忽略。
3. **terminal**：进入 succeeded/failed/cancelled 终态的广播；
   载荷含 result 或 failure（形态同 002 TaskOut）。服务端在
   发出本帧后**正常关闭连接**；客户端收到后不得再重连本任务。
4. 若 snapshot 已是终态：发送 snapshot 后再发一帧同载荷
   terminal，然后关闭（晚订阅也立即知终态）。
5. 活动连接每 15 秒收到一帧 `: ping` 注释，无 data、不改变
   任何状态，仅用于探活。

### 1.3 事件载荷

```json
{
  "id": "9a4b…",
  "status": "running",
  "stage": "rewrite",
  "progress": 0.72,
  "message": "分章转写（4/7）…",
  "fake_mode": true,
  "failure": null,
  "result": null,
  "started_at": "2026-09-28T13:02:27",
  "finished_at": null,
  "seq": 7
}
```

- 字段命名、枚举、failure/result 嵌套与 002 `TaskOut`
  **逐字一致**；
- `seq`（int，≥0）：本信封新增字段，任务内单调；snapshot 的
  seq 为当前水位（0 表示尚无广播事件）；
- 载荷不得包含文件系统路径、密文、异常原文；失败文案始终是
  002 §6 固定中文模板（含 interrupted_restart）。

### 1.4 鉴权失败与错误

错误在 HTTP 建立阶段以普通 JSON 返回（不是事件帧）：

| 状态码 | body | 触发 |
|---|---|---|
| 401 | `{"detail":"未授权或登录已过期"}` | 无/过期令牌 |
| 404 | `{"detail":"任务不存在"}` | 任务不存在或属于他人 |

- 连接建立后的中断（网络断开/服务重启）不返回错误体：客户端
  按 §2 重连；重连后若任务已被 002 恢复逻辑标记为中断，
  snapshot 即 `failed/system/interrupted_restart`，随后
  terminal 关流。

## 2. 客户端行为约定（前端必须实现）

1. fetch 打开连接并流式解析；**指数退避自动重连**
   1→2→4→8→10 秒封顶，仅在页面仍需要该任务时进行。
2. 每次重连重新走 snapshot 对齐；不使用 Last-Event-ID。
3. 401：交全局 401 处理（清会话、跳登录），不重连。
4. 404：按"任务不存在"呈现，不重连。
5. 连续约 30 秒无法建立流（≥5 次失败）：降级为低频快照轮询
   （3 秒 GET 任务详情/列表），恢复条件为下一次成功建流；
   **同一任务同一时刻只允许一种持续刷新**。
6. 组件卸载/导航离开：中止连接（AbortController），停止重连。
7. 列表页仅对其中状态为 queued/running 的任务各开一条流；
   活动归零后无残留连接；详情页固定一条。

## 3. 本契约明确不提供

- 跨任务的用户级多路复用流；
- 历史事件回放、时间线查询、Last-Event-ID；
- WebSocket 与任何客户端→服务器的消息（取消仍走 002 的
  `POST /{id}/cancel`）；
- 通过查询串传令牌的鉴权方式。
