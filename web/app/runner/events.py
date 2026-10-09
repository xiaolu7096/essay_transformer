"""进程内实时事件中枢（Spec 003 US1，research D3）。

- 事件是**瞬时**消息：生命周期等于一条 SSE 连接，不持久化、不回放；
  任务真相来源始终是 tasks 行（首帧快照即读任务行）。
- worker 在 anyio 线程内执行，SSE 连接在 asyncio 主循环线程：
  publish() 经 ``loop.call_soon_threadsafe`` 把"seq 自增 + 入队"
  调度回主循环，保证同一任务的发布顺序与 seq 顺序严格一致。
- 每个订阅者一个 asyncio.Queue（上限 256）：队列满时丢弃最旧的
  **非终态**帧；terminal 帧强制保留（终态丢失会让客户端永远等待）。
- 进程重启后内存清空：重连客户端以首帧快照收敛
  （002 recovery 已把残留任务置 failed/interrupted_restart）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ..models import Task
from ..schemas import TaskEventPayload

logger = logging.getLogger("web")

QUEUE_MAXSIZE = 256


def build_task_payload(task: Task, seq: int) -> TaskEventPayload:
    """从任务行组装事件载荷（口径同 002 TaskOut + 信封 seq，无路径/密文）。"""
    failure = None
    if task.failure_code is not None:
        failure = {
            "category": task.failure_category,
            "code": task.failure_code,
            "message": task.failure_message,
        }
    result = None
    if task.result_section_count is not None:
        result = {
            "title": task.result_title,
            "section_count": task.result_section_count,
            "total_chars": task.result_total_chars,
        }
    return TaskEventPayload(
        id=task.id,
        status=task.status,
        stage=task.stage,
        progress=task.progress,
        message=task.message,
        fake_mode=task.fake_mode,
        failure=failure,
        result=result,
        started_at=task.started_at,
        finished_at=task.finished_at,
        seq=seq,
    )


class ProgressHub:
    """task_id → 订阅者集合的进程内发布/订阅中枢。"""

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._seq: dict[str, int] = {}
        self._subs: dict[str, set[asyncio.Queue]] = {}

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """lifespan 注入主循环（worker 线程据此跨线程调度）。"""
        self._loop = loop

    def reset(self) -> None:
        """清空瞬时状态（lifespan 启动时调用，等价进程重启语义）。"""
        self._seq.clear()
        self._subs.clear()

    def subscribe(self, task_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subs.setdefault(task_id, set()).add(q)
        return q

    def unsubscribe(self, task_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(task_id)
        if not subs:
            return
        subs.discard(q)
        if not subs:
            self._subs.pop(task_id, None)

    def current_seq(self, task_id: str) -> int:
        """任务当前水位；从未发布过事件为 0。"""
        return self._seq.get(task_id, 0)

    def publish(
        self,
        task_id: str,
        payload: TaskEventPayload | dict,
        *,
        terminal: bool = False,
    ) -> None:
        """worker 线程同步入口：把入队调度回主循环执行。

        调度失败（循环关闭等）只记 warning，绝不影响调用方任务状态。
        """
        loop = self._loop
        if loop is None:
            logger.warning("ProgressHub 未绑定事件循环，丢弃任务 %s 的事件", task_id)
            return
        try:
            loop.call_soon_threadsafe(
                self._publish_on_loop, task_id, payload, terminal
            )
        except RuntimeError:
            logger.warning("事件循环不可用，丢弃任务 %s 的事件", task_id)

    def _publish_on_loop(
        self, task_id: str, payload: TaskEventPayload | dict, terminal: bool
    ) -> None:
        seq = self._seq.get(task_id, 0) + 1
        self._seq[task_id] = seq
        # 统一存 JSON 安全 dict（datetime 等经 pydantic mode="json" 序列化）
        if hasattr(payload, "model_dump"):
            data = payload.model_dump(mode="json")
        else:
            data = dict(payload)
        data["seq"] = seq
        frame: dict[str, Any] = {
            "event": "terminal" if terminal else "update",
            "seq": seq,
            "payload": data,
        }
        for q in list(self._subs.get(task_id, ())):
            self._enqueue(q, frame)

    def _enqueue(self, q: asyncio.Queue, frame: dict[str, Any]) -> None:
        try:
            q.put_nowait(frame)
            return
        except asyncio.QueueFull:
            pass
        # 满队：依次丢弃最旧的非终态帧，为新帧腾位
        while q.full():
            oldest = q._queue[0]  # asyncio.Queue 底层即 deque，队首为最旧帧
            if oldest.get("event") == "terminal":
                # 队列已被终态占据（理论上终态后不再有发布）：保底不覆盖
                logger.warning("订阅队列已满且含终态帧，丢弃新帧 seq=%s",
                               frame["seq"])
                return
            q.get_nowait()
        q.put_nowait(frame)


# 模块级单例：lifespan 绑定主循环后存 app.state.hub；
# worker（无请求上下文）直接导入此实例，路由经 deps.get_hub 取同一对象。
progress_hub = ProgressHub()
