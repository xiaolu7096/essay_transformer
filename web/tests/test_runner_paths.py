"""T007：任务产物路径的双层隔离与逃逸防护。"""

from __future__ import annotations

import pytest

from web.app.runner.paths import (
    parsed_dir_path,
    source_pdf_path,
    task_run_dir,
)

USER = "u-11111111-2222-3333-4444-555555555555"
TASK = "t-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def test_run_dir_double_layer(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_RUNS_DIR", str(tmp_path / "runs"))
    run_dir = task_run_dir(USER, TASK)
    assert run_dir == tmp_path / "runs" / USER / TASK
    # 恒在 runs_dir 之下、按 user/task 双层
    assert run_dir.parent.name == USER
    assert run_dir.parent.parent == tmp_path / "runs"


def test_source_and_parsed_paths(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_RUNS_DIR", str(tmp_path))
    assert source_pdf_path(USER, TASK) == task_run_dir(USER, TASK) / "source.pdf"
    assert parsed_dir_path(USER, TASK) == task_run_dir(USER, TASK) / "parsed"


@pytest.mark.parametrize("bad_user,bad_task", [
    ("..", TASK),
    (USER, "../escape"),
    ("a/b", TASK),
    (USER, "x\\y"),
    ("/abs", TASK),
    (USER, "/abs/task"),
    ("", TASK),
    (USER, ""),
    ("a..b", TASK),  # 含 '..' 子串也拒绝，宁可严格
])
def test_unsafe_segments_rejected(monkeypatch, tmp_path, bad_user, bad_task):
    monkeypatch.setenv("WEB_RUNS_DIR", str(tmp_path))
    with pytest.raises(ValueError):
        task_run_dir(bad_user, bad_task)


def test_resolved_path_cannot_escape_runs_root(monkeypatch, tmp_path):
    monkeypatch.setenv("WEB_RUNS_DIR", str(tmp_path / "runs"))
    run_dir = task_run_dir(USER, TASK).resolve()
    assert run_dir.is_relative_to((tmp_path / "runs").resolve())
