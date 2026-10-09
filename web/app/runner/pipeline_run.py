"""后台 worker：备料 → 调 core 转写 → 条件更新状态/结果/失败（US2）。

在独立线程执行（anyio.to_thread），每步用短生命周期 SessionLocal 自建会话。
所有离开活动态的写入都用条件 UPDATE（status IN queued/running），
rowcount=0 说明已有一路终态化，后到者不覆盖（US4 竞争规则）。

本文件分三次演进：T022 基线（统一 internal_error 兜底）→ T028 全量错误分类
→ T038 取消接线。
"""

from __future__ import annotations

import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import update

from ..config import WEB_DIR, get_settings
from ..db import SessionLocal
from ..models import Task
from ..services import credentials as cred_svc
from . import cancel_registry
from .errors import classify_error, sanitize_for_log
from .events import build_task_payload, progress_hub
from .paths import source_pdf_path, task_run_dir

logger = logging.getLogger("web")

FAKE_FIXTURE_DIR = WEB_DIR / "fixtures" / "fake_run"
_ACTIVE = ("queued", "running")
_MESSAGE_LIMIT = 300

# 开跑前密钥预检文案（与 errors.MESSAGES["key_missing"] 同源）
_KEY_MISSING_MESSAGE = "服务密钥未配置，请先到密钥设置填写后重试"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _truncate(text: str, limit: int = _MESSAGE_LIMIT) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _publish_state(task_id: str, *, terminal: bool) -> None:
    """提交成功后发布最新任务状态（表现层副作用）。

    任何异常只记 warning：推送失败绝不回滚或影响任务状态。
    """
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None:
            return
        progress_hub.publish(
            task_id,
            build_task_payload(task, progress_hub.current_seq(task_id)),
            terminal=terminal,
        )
    except Exception:  # noqa: BLE001
        logger.warning("任务 %s 状态发布失败（不影响任务状态）", task_id)
    finally:
        db.close()


def persist_progress(task_id: str, stage: str, progress: float, message: str) -> None:
    """短会话写进度快照；仅在 running 时更新（终态后回调不覆盖）。"""
    db = SessionLocal()
    try:
        result = db.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == "running")
            .values(stage=stage, progress=progress, message=_truncate(message or ""))
        )
        db.commit()
        # 先 commit 后 publish：行未更新（已终态）则不发，避免矛盾帧
        if result.rowcount:
            _publish_state(task_id, terminal=False)
    finally:
        db.close()


def _prepare_fake_inputs(run_dir: Path, source_kind: str) -> None:
    """假模式：把内置物料复制进任务私有目录（D6）。

    - parsed/ 始终来自 fixture（content_list.json/full.md）；
    - 链接任务无用户文件，source.pdf 也取 fixture；
    - 上传任务的 source.pdf 是提交时落盘的用户文件，保留不覆盖。
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    dst_pdf = run_dir / "source.pdf"
    if source_kind == "arxiv" or not dst_pdf.exists():
        shutil.copyfile(FAKE_FIXTURE_DIR / "source.pdf", dst_pdf)
    shutil.copytree(FAKE_FIXTURE_DIR / "parsed", run_dir / "parsed", dirs_exist_ok=True)


def _finish_cancelled(db, task_id: str) -> int:
    return db.execute(
        update(Task)
        .where(Task.id == task_id, Task.status.in_(_ACTIVE))
        .values(
            status="cancelled",
            message="任务已取消",
            finished_at=_utcnow(),
            active_slot=None,
        )
    ).rowcount


def _finish_success(db, task_id: str, result) -> int:
    return db.execute(
        update(Task)
        .where(Task.id == task_id, Task.status.in_(_ACTIVE))
        .values(
            status="succeeded",
            stage="assemble",
            progress=1.0,
            message=f"完成：{result.total_chars} 字符",
            result_title=result.title[:300],
            result_section_count=len(result.sections),
            result_total_chars=result.total_chars,
            finished_at=_utcnow(),
            active_slot=None,
        )
    ).rowcount


def _finish_failure(
    db, task_id: str, category: str, code: str, message: str
) -> int:
    return db.execute(
        update(Task)
        .where(Task.id == task_id, Task.status.in_(_ACTIVE))
        .values(
            status="failed",
            failure_category=category,
            failure_code=code,
            failure_message=message,
            finished_at=_utcnow(),
            active_slot=None,
        )
    ).rowcount


def run_task(task_id: str, user_id: str) -> None:
    """worker 主入口（同步，线程内执行）。"""
    settings = get_settings()
    event = cancel_registry.register(task_id)
    db = SessionLocal()
    try:
        task = db.get(Task, task_id)
        if task is None or task.user_id != user_id:
            logger.warning("任务不存在或归属不符，放弃执行：%s", task_id)
            return

        # ⓪ 开跑前先看取消：排队期间被取消则直接收口，绝不调用 core
        if event.is_set():
            if _finish_cancelled(db, task_id):
                db.commit()
                _publish_state(task_id, terminal=True)
            return

        # ① queued → running（条件更新；rowcount=0 表示已被取消/恢复等收口）
        started = db.execute(
            update(Task)
            .where(Task.id == task_id, Task.status == "queued")
            .values(status="running", started_at=_utcnow(),
                    stage="ingest", message="排队完成，开始处理…")
        ).rowcount
        db.commit()
        if not started:
            return

        run_dir = task_run_dir(user_id, task_id)

        # ③ 真实模式先取密钥（排队期间可能已被删除）
        mineru_key = None
        deepseek_key = None
        if not settings.fake_mode:
            mineru_key = cred_svc.get_decrypted(db, user_id, "mineru")
            deepseek_key = cred_svc.get_decrypted(db, user_id, "deepseek")
            if not mineru_key or not deepseek_key:
                if _finish_failure(db, task_id, "service", "key_missing",
                                   _KEY_MISSING_MESSAGE):
                    db.commit()
                    _publish_state(task_id, terminal=True)
                return

        # ④/⑤ 备料
        if settings.fake_mode:
            _prepare_fake_inputs(run_dir, task.source_kind)
            source = source_pdf_path(user_id, task_id)
        elif task.source_kind == "arxiv":
            source = f"https://arxiv.org/abs/{task.source_ref}"
        else:
            source = source_pdf_path(user_id, task_id)

        # 调 core（core 自行下载/解析；fake 模式零外部调用）
        from core.pipeline import transform_paper

        result = transform_paper(
            source=source,
            output_dir=run_dir,
            mineru_api_key=mineru_key,
            deepseek_api_key=deepseek_key,
            mineru_verify_ssl=settings.mineru_verify_ssl,
            fake=settings.fake_mode,
            progress_cb=lambda s, p, m: persist_progress(task_id, s, p, m),
            cancel_event=event,
        )

        # 落 output.md（供 Spec 003 阅读器/下载消费）
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "output.md").write_text(result.full_markdown, encoding="utf-8")

        # ⑥ 成功收口（条件更新，竞争失败不覆盖）
        if _finish_success(db, task_id, result):
            db.commit()
            _publish_state(task_id, terminal=True)
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        cancel_db = SessionLocal()
        try:
            # 取消优先：core 在阶段边界抛 ETError("任务已被用户取消")，
            # 事件置位即收口 cancelled，与成功/失败终态按条件 UPDATE 竞争唯一。
            if event.is_set():
                if _finish_cancelled(cancel_db, task_id):
                    cancel_db.commit()
                    _publish_state(task_id, terminal=True)
                return

            # 三分类收口：用户只见固定模板；日志侧先脱敏再记录异常原文。
            category, code, message = classify_error(exc)
            logger.warning(
                "任务 %s 失败 [%s/%s]：%s",
                task_id,
                category,
                code,
                sanitize_for_log(repr(exc)),
            )
            if _finish_failure(cancel_db, task_id, category, code, message):
                cancel_db.commit()
                _publish_state(task_id, terminal=True)
        finally:
            cancel_db.close()
    finally:
        db.close()
        # ⑧ 注销取消事件
        cancel_registry.discard(task_id)
