"""任务产物落位的唯一口径：runs/<user_id>/<task_id>/ 双层隔离。

- 路径只能由服务端按 user_id + task_id 拼出；任何 API 不接收路径参数。
- user_id/task_id 必须是服务端生成的安全片段（uuid）；含路径分隔符、
  '..'、空串、绝对路径一律拒绝，防止目录逃逸。
"""

from __future__ import annotations

from pathlib import Path

from ..config import get_settings


def _safe_segment(value: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} 不能为空")
    if ".." in value or "/" in value or "\\" in value or "\x00" in value:
        raise ValueError(f"{name} 含非法路径片段：{value!r}")
    if Path(value).is_absolute():
        raise ValueError(f"{name} 不能是绝对路径：{value!r}")
    return value


def task_run_dir(user_id: str, task_id: str) -> Path:
    """返回 <runs_dir>/<user_id>/<task_id>（不创建目录）。"""
    user = _safe_segment(user_id, "user_id")
    task = _safe_segment(task_id, "task_id")
    return Path(get_settings().runs_dir) / user / task


def source_pdf_path(user_id: str, task_id: str) -> Path:
    return task_run_dir(user_id, task_id) / "source.pdf"


def parsed_dir_path(user_id: str, task_id: str) -> Path:
    return task_run_dir(user_id, task_id) / "parsed"
