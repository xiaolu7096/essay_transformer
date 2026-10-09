"""取消信号注册表：task_id → threading.Event（进程内内存态，随进程消亡）。

- 提交任务时即注册，排队中的任务也可取消；
- worker 终态收口后 discard；
- 极短窗口下事件可能缺失：set/get 惰性创建，保证取消请求不丢。
"""

from __future__ import annotations

import threading

_events: dict[str, threading.Event] = {}
_lock = threading.Lock()


def register(task_id: str) -> threading.Event:
    with _lock:
        event = _events.get(task_id)
        if event is None:
            event = threading.Event()
            _events[task_id] = event
        return event


def get(task_id: str) -> threading.Event:
    """取事件；不存在则惰性创建（兼容排队未落表的极短窗口）。"""
    with _lock:
        return _events.setdefault(task_id, threading.Event())


def set_event(task_id: str) -> None:
    get(task_id).set()


def discard(task_id: str) -> None:
    with _lock:
        _events.pop(task_id, None)


def reset() -> None:
    """仅供测试：清空全部事件。"""
    with _lock:
        _events.clear()
