"""任务执行器：端口 + 进程内有界线程池适配器（宪法原则五）。

- 路由只依赖 TaskExecutor 形态（submit/cancel），将来换 Celery/RQ 只换适配器；
- 经 anyio 容量限流器把并发限制在 WEB_MAX_CONCURRENT_TASKS；
- submit 立即返回（后台 asyncio 任务驱动 worker），路由零阻塞；
- 取消只置位事件，终态由 worker 在阶段边界收口。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

import anyio

from . import cancel_registry

logger = logging.getLogger("web")


class TaskExecutor(Protocol):
    async def submit(self, task_id: str, user_id: str) -> None: ...

    def cancel(self, task_id: str) -> None: ...


class InProcessExecutor:
    """进程内执行器：anyio 有界工作线程 + 后台任务，shutdown 时等待收尾。"""

    def __init__(self, max_workers: int) -> None:
        self._limiter = anyio.CapacityLimiter(max_workers)
        self._bg: set[asyncio.Task[None]] = set()

    async def submit(self, task_id: str, user_id: str) -> None:
        # 注意（analyze I2）：pipeline_run 在 T022 才创建，必须函数内延迟导入
        from .pipeline_run import run_task

        async def _run() -> None:
            try:
                await anyio.to_thread.run_sync(
                    run_task, task_id, user_id, limiter=self._limiter
                )
            except Exception:  # noqa: BLE001
                # worker 自身会收口失败终态；到这里属意外，仅记录不吞成无日志
                logger.exception("任务 %s 执行线程意外退出", task_id)

        task = asyncio.create_task(_run())
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)

    def cancel(self, task_id: str) -> None:
        cancel_registry.set_event(task_id)

    async def aclose(self) -> None:
        """等待已入池任务收尾（关闭流程调用）。"""
        if self._bg:
            await asyncio.gather(*self._bg, return_exceptions=True)
