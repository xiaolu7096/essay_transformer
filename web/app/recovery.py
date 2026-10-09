"""重启恢复：进程异常退出后，queued/running 残留任务统一收口（US3 / D7）。

内存执行器与取消事件随进程消亡，这些任务不可能再推进；启动建表后、
开始接收流量前执行一次单条条件 UPDATE：
  failed + system/interrupted_restart + 固定文案 + finished_at + active_slot=NULL。
函数天然幂等（恢复后不再有活动行，第二次 rowcount=0）。
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import update

from .db import SessionLocal
from .models import Task

logger = logging.getLogger("web")

MESSAGE = "服务曾重启，该任务被中断，请重新提交"


def recover_interrupted() -> int:
    """收口所有残留活动任务，返回被改写的行数。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    try:
        result = db.execute(
            update(Task)
            .where(Task.status.in_(("queued", "running")))
            .values(
                status="failed",
                failure_category="system",
                failure_code="interrupted_restart",
                failure_message=MESSAGE,
                finished_at=now,
                active_slot=None,
            )
        )
        db.commit()
        if result.rowcount:
            logger.warning("重启恢复：%d 个残留活动任务已收口为 interrupted_restart",
                           result.rowcount)
        return result.rowcount or 0
    finally:
        db.close()
