"""任务提交端点（US2）：POST /api/tasks/from-url、POST /api/tasks/upload。

口径（契约 contracts/api-tasks.md）：
- 成功 201 返回 TaskOut；路由立即返回，转写在后台执行；
- 错误体形如 {"detail": 中文文案, "code": 机器码}，413/422 等不泄露路径/密文；
- 每用户至多一个 queued/running：先查后插 + 部分唯一索引兜底竞态；
- 上传全程内存分块累计，超限即止；失败路径不留任务行与产物目录。
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile

from ..config import get_settings
from ..db import SessionLocal, get_db
from ..deps import (
    TaskAccessError,
    get_current_user,
    get_executor,
    get_hub,
    get_owned_succeeded_task,
    get_owned_task,
)
from ..models import Task, User
from ..runner import cancel_registry
from ..runner.events import ProgressHub, build_task_payload
from ..runner.paths import task_run_dir
from ..schemas import FromUrlRequest, ReaderOut, TaskListOut, TaskOut
from ..services import credentials as cred_svc
from ..services.artifacts import (
    IMAGE_CONTENT_TYPES,
    ArtifactMissingError,
    ImageNotFoundError,
    build_artifact_zip,
    parse_reader,
    read_output,
    resolve_image,
)

logger = logging.getLogger("web")

router = APIRouter(prefix="/api/tasks", tags=["tasks"])

_CHUNK_SIZE = 1024 * 1024  # 1MB 分块流式接收
_ACTIVE_STATUSES = ("queued", "running")
_TERMINAL_STATUSES = ("succeeded", "failed", "cancelled")
# 活动连接心跳间隔（模块级常量，测试可 monkeypatch 为极小值）
PING_INTERVAL_SECONDS = 15.0


class _UploadRejected(Exception):
    """接收/校验阶段的确定性拒绝（携带 HTTP 状态与机器码）。"""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


def _error(status_code: int, message: str, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"detail": message, "code": code}
    )


async def _receive_upload_bytes(upload: StarletteUploadFile, max_bytes: int) -> bytes:
    """分块读取上传内容并累计大小；超限立即拒绝（先收后校验大小口径）。"""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            limit_mb = max_bytes // (1024 * 1024)
            raise _UploadRejected(
                413, "file_too_large", f"文件超过 {limit_mb}MB 上限，请压缩后再上传"
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _task_out(task: Task) -> TaskOut:
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
    return TaskOut(
        id=task.id,
        source_kind=task.source_kind,
        source_ref=task.source_ref,
        status=task.status,
        stage=task.stage,
        progress=task.progress,
        message=task.message,
        fake_mode=task.fake_mode,
        failure=failure,
        result=result,
        created_at=task.created_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
    )


def _has_active_task(db: Session, user_id: str) -> bool:
    return (
        db.scalar(
            select(Task.id)
            .where(Task.user_id == user_id, Task.status.in_(_ACTIVE_STATUSES))
            .limit(1)
        )
        is not None
    )


def _credentials_ready(db: Session, user_id: str) -> bool:
    return all(item["configured"] for item in cred_svc.list_statuses(db, user_id))


def _insert_task(
    db: Session, user: User, source_kind: str, source_ref: str, fake: bool
) -> Task:
    """插入活动任务；竞态下部分唯一索引冲突由调用方转 409。"""
    task = Task(
        user_id=user.id,
        source_kind=source_kind,
        source_ref=source_ref[:500],
        status="queued",
        progress=0.0,
        message="已入队，等待执行…",
        fake_mode=fake,
        active_slot=1,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("", response_model=TaskListOut)
def list_tasks(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """本人任务列表：created_at 倒序，列表项与详情同形。"""
    rows = db.scalars(
        select(Task)
        .where(Task.user_id == user.id)
        .order_by(Task.created_at.desc())
    ).all()
    return {"tasks": [_task_out(task) for task in rows]}


@router.get("/{task_id}", response_model=TaskOut)
def get_task(task: Task = Depends(get_owned_task)):
    return _task_out(task)


def _sse_frame(event: str, payload: dict) -> bytes:
    return (
        f"event: {event}\n"
        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
    ).encode("utf-8")


@router.get("/{task_id}/events")
async def task_events(
    task: Task = Depends(get_owned_task),
    hub: ProgressHub = Depends(get_hub),
):
    """SSE：先订阅再快照（无缺口），按 seq 水位转发；终态后关流。

    401/404 在依赖阶段以普通 JSON 返回（不升级为事件流）。
    快照读取使用独立短会话：避免依赖阶段的读事务持有旧快照，
    看不到 worker 已提交的状态。

    客户端断开时 Starlette 取消响应任务，finally 负责退订；
    不主动 await is_disconnected（TestClient 下该 receive 永不返回，
    会导致生成器挂死）。
    """

    async def event_gen():
        queue = hub.subscribe(task.id)
        db = SessionLocal()
        try:
            # 先登记订阅，再取水位，再读真相行（research D4 不变式）
            snap_seq = hub.current_seq(task.id)
            fresh = db.get(Task, task.id)
            snapshot = build_task_payload(fresh, snap_seq).model_dump(mode="json")
            yield _sse_frame("snapshot", snapshot)

            if fresh.status in _TERMINAL_STATUSES:
                # 晚订阅：终态快照 + 同载荷 terminal 后关流
                yield _sse_frame("terminal", snapshot)
                return

            while True:
                try:
                    frame = await asyncio.wait_for(
                        queue.get(), timeout=PING_INTERVAL_SECONDS
                    )
                except asyncio.TimeoutError:
                    # 客户端若已断开，向已关闭连接再次 yield 会触发取消，
                    # finally 退订，无需显式探测。
                    yield b": ping\n\n"
                    continue

                if frame["seq"] <= snap_seq:
                    continue  # 已被快照覆盖的迟到/重复帧
                yield _sse_frame(frame["event"], frame["payload"])
                if frame["event"] == "terminal":
                    return
        finally:
            hub.unsubscribe(task.id, queue)
            db.close()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# --------------------------------------------------------------------------
# Spec 003 US2/US3：成品阅读器、受控图片、zip 下载（仅 succeeded）
# --------------------------------------------------------------------------

_ARTIFACT_MISSING_DETAIL = "成品文件缺失或已损坏，请重新提交任务"


@router.get("/{task_id}/reader", response_model=ReaderOut)
def get_reader(task: Task = Depends(get_owned_succeeded_task)):
    run_dir = task_run_dir(task.user_id, task.id)
    try:
        md = read_output(run_dir)
        parsed = parse_reader(md, task.result_title or task.source_ref)
    except ArtifactMissingError:
        raise TaskAccessError(409, _ARTIFACT_MISSING_DETAIL, "artifact_missing")
    return {
        "task_id": task.id,
        "title": parsed["title"],
        "fake_mode": task.fake_mode,
        "total_chars": parsed["total_chars"],
        "sections": parsed["sections"],
    }


@router.get("/{task_id}/artifacts/images/{name:path}")
def get_artifact_image(
    name: str,
    task: Task = Depends(get_owned_succeeded_task),
):
    images_dir = task_run_dir(task.user_id, task.id) / "images"
    try:
        image_path = resolve_image(images_dir, name)
        data = image_path.read_bytes()
    except (ImageNotFoundError, OSError):
        # 非法名/越界/不存在统一 404，不回显输入、不带服务器路径
        raise HTTPException(status_code=404, detail="图片不存在")
    return Response(
        content=data,
        media_type=IMAGE_CONTENT_TYPES[image_path.suffix],
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{task_id}/artifact/download")
def download_artifact(task: Task = Depends(get_owned_succeeded_task)):
    run_dir = task_run_dir(task.user_id, task.id)
    try:
        base, payload = build_artifact_zip(task, run_dir)
    except ArtifactMissingError:
        raise TaskAccessError(409, _ARTIFACT_MISSING_DETAIL, "artifact_missing")

    # ASCII 回退名：非 ASCII 一律以 '-' 替代；RFC 5987 给 UTF-8 原名
    ascii_base = "".join(
        c if 0x20 <= ord(c) < 0x7F and c not in '"\r\n\\' else "-"
        for c in base
    ) or f"paper-{task.id[:8]}"
    disposition = (
        f'attachment; filename="{ascii_base}.zip"; '
        f"filename*=UTF-8''{quote(base + '.zip')}"
    )
    return Response(
        content=payload,
        media_type="application/zip",
        headers={
            "Content-Disposition": disposition,
            "Cache-Control": "private, no-store",
        },
    )


@router.post("/{task_id}/cancel", response_model=TaskOut)
async def cancel_task(
    task: Task = Depends(get_owned_task),
    executor=Depends(get_executor),
):
    """请求取消：仅置位事件并立即返回当前快照，不假装已终态。

    终态由 worker 在阶段边界（或开跑前）条件更新为 cancelled。
    """
    if task.status not in _ACTIVE_STATUSES:
        return _error(409, "任务已结束，无法取消", "task_not_active")
    executor.cancel(task.id)
    return _task_out(task)


@router.delete("/{task_id}", status_code=204)
def delete_task(
    task: Task = Depends(get_owned_task),
    db: Session = Depends(get_db),
):
    """删除本人任务：仅终态可删，先删库行再清理 runs 目录。

    queued/running 返回 409，需先取消并等待终态，
    避免与后台 worker 竞争产生孤儿产物。
    """
    if task.status in _ACTIVE_STATUSES:
        return _error(409, "任务进行中，请先取消并等待结束后再删除", "task_active")
    user_id, task_id = task.user_id, task.id
    db.delete(task)
    db.commit()
    cancel_registry.discard(task_id)
    shutil.rmtree(task_run_dir(user_id, task_id), ignore_errors=True)
    return Response(status_code=204)


@router.post("/from-url", response_model=TaskOut, status_code=201)
async def submit_from_url(
    payload: FromUrlRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    executor=Depends(get_executor),
):
    settings = get_settings()
    source = payload.source.strip()

    # arXiv 链接预校验（核心库已有口径，避免无效链接入队后才失败）
    from core.ingest import is_arxiv_url, normalize_arxiv_id
    from core.errors import InputError

    if not is_arxiv_url(source):
        return _error(422, "目前仅支持 arXiv 论文链接", "invalid_source")
    try:
        source_ref = normalize_arxiv_id(source)
    except InputError:
        return _error(422, "无法识别的 arXiv 链接", "invalid_source")

    if not settings.fake_mode and not _credentials_ready(db, user.id):
        return _error(
            400, "真实模式需先配置 MinerU 与 DeepSeek 密钥", "credentials_required"
        )
    if _has_active_task(db, user.id):
        return _error(409, "您已有一个进行中的任务，请等待其结束", "active_task_exists")

    try:
        task = _insert_task(db, user, "arxiv", source_ref, settings.fake_mode)
    except IntegrityError:
        db.rollback()
        return _error(409, "您已有一个进行中的任务，请等待其结束", "active_task_exists")

    # 排队期间也要能收到取消信号
    cancel_registry.register(task.id)
    await executor.submit(task.id, user.id)
    return _task_out(task)


@router.post("/upload", response_model=TaskOut, status_code=201)
async def submit_upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    executor=Depends(get_executor),
):
    settings = get_settings()
    max_bytes = settings.upload_max_mb * 1024 * 1024

    # 接收（含大小流式上限）；传输中断不产生任何落盘/落库
    try:
        data = await _receive_upload_bytes(file, max_bytes)
    except _UploadRejected as exc:
        return _error(exc.status_code, exc.message, exc.code)
    except Exception:  # noqa: BLE001 —— 客户端中断/流异常
        logger.warning("上传接收中断 user=%s filename=%r", user.id, file.filename)
        return _error(400, "上传过程中断，请重试", "upload_interrupted")
    finally:
        await file.close()

    filename = file.filename or ""
    # 扩展名
    if Path(filename).suffix.lower() != ".pdf":
        return _error(422, "仅支持 .pdf 文件", "invalid_file_type")
    # %PDF 魔数
    if not data.startswith(b"%PDF"):
        return _error(422, "文件内容不是有效的 PDF（缺少 %PDF 头）", "invalid_pdf")

    if not settings.fake_mode and not _credentials_ready(db, user.id):
        return _error(
            400, "真实模式需先配置 MinerU 与 DeepSeek 密钥", "credentials_required"
        )
    if _has_active_task(db, user.id):
        return _error(409, "您已有一个进行中的任务，请等待其结束", "active_task_exists")

    try:
        task = _insert_task(db, user, "upload", filename, settings.fake_mode)
    except IntegrityError:
        db.rollback()
        return _error(409, "您已有一个进行中的任务，请等待其结束", "active_task_exists")

    run_dir = task_run_dir(user.id, task.id)
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
        (run_dir / "source.pdf").write_bytes(data)
    except Exception:
        logger.exception("上传落盘失败，回滚任务 %s", task.id)
        db.rollback()
        shutil.rmtree(run_dir, ignore_errors=True)
        return _error(500, "文件保存失败，请重试", "write_failed")

    # 排队期间也要能收到取消信号
    cancel_registry.register(task.id)
    await executor.submit(task.id, user.id)
    return _task_out(task)
