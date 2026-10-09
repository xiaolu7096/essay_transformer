"""FastAPI 应用工厂。

路由随各用户故事增量在此挂载；启动时建表。交付形态的前端静态托管在
Polish 阶段（T040）加入。
"""

from __future__ import annotations

import asyncio
import importlib
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .config import REPO_ROOT, get_settings
from .db import Base, engine
from .deps import TaskAccessError
from .recovery import recover_interrupted
from .runner.events import progress_hub
from .runner.executor import InProcessExecutor
from .routers import auth, credentials, health, tasks

logger = logging.getLogger("web")

DIST_DIR = REPO_ROOT / "webui" / "dist"


def _init_db() -> None:
    # US1 阶段模型尚未创建；模型模块存在时导入以注册表结构后再建表
    try:
        importlib.import_module("web.app.models")
    except ModuleNotFoundError:
        logger.warning("web.app.models 尚不存在，跳过建表（US1 基座阶段属正常）")
    Base.metadata.create_all(bind=engine)


def create_app() -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        _init_db()
        # 建表后、接收流量前：上次异常退出的 queued/running 残留统一收口
        recover_interrupted()
        # 实时事件中枢：清空上次进程的瞬时状态并绑定本进程主循环
        progress_hub.reset()
        progress_hub.bind_loop(asyncio.get_running_loop())
        app.state.hub = progress_hub
        # 进程内有界执行器：并发 = WEB_MAX_CONCURRENT_TASKS；测试可替换 state
        app.state.executor = InProcessExecutor(settings.max_concurrent_tasks)
        try:
            yield
        finally:
            # 等待已入池任务走完自身收口（成功/失败条件更新）；
            # 测试替身可能只有 submit/cancel，鸭子判定
            close = getattr(app.state.executor, "aclose", None)
            if close is not None:
                await close()

    app = FastAPI(
        title="纸慧 PaperWise Web 客户端",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def _sanitize_validation_errors(_request: Request, exc: RequestValidationError):
        # 安全：422 错误默认在 input 字段回显提交原文（可能含密码/密钥），统一剔除；
        # ctx 可能携带原始 ValueError 对象（不可 JSON 序列化且可能含内部细节），一并剔除
        safe_errors = [
            {key: value for key, value in err.items() if key not in ("input", "ctx")}
            for err in exc.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": safe_errors})

    @app.exception_handler(TaskAccessError)
    async def _task_access_error(_request: Request, exc: TaskAccessError):
        # 扁平错误体：{"detail": "中文文案", "code": "机器码"}
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "code": exc.code},
        )

    # 路由挂载（顺序敏感：API 必须先于 SPA 回退匹配）
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(credentials.router)
    app.include_router(tasks.router)

    _mount_spa(app)

    return app


def _mount_spa(app: FastAPI) -> None:
    """交付形态：webui/dist 存在时单端口托管前端。

    - /api/* 已由上方路由优先匹配；未知 /api 路径仍返回 404
    - 命中 dist 内真实文件（如 /assets/*.js）则返回文件
    - 其余路径一律回退 index.html（history 模式深链，如 /home）
    dist 不存在（开发模式走 Vite 5173）时不注册，避免干扰。
    """
    if not DIST_DIR.is_dir():
        return

    index_file = DIST_DIR / "index.html"

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> FileResponse:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (DIST_DIR / full_path).resolve()
        # 防路径穿越：解析后必须仍在 dist 之内
        if DIST_DIR in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        # index.html 不缓存：确保新版前端发布后用户即时拿到最新入口
        # （/assets/*.js 均为内容哈希文件名，可长期缓存）
        return FileResponse(
            index_file,
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )


app = create_app()
