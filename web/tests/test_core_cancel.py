"""T035：core 特许增量 cancel_event 的红测试（T036 实施）。

边界：
- transform_paper 接受 keyword-only cancel_event，置位时在 parse 之前
  抛 ETError("任务已被用户取消")；
- 不传时签名与行为零变化（T008 假模式全流程回归）；
- MinerUClient 轮询支持分段等待：置位 1 秒内中断；event=None 仍单次 sleep。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from core.errors import ETError
from core.mineru_client import MinerUClient
from core.pipeline import transform_paper
from web.app.config import WEB_DIR

FIXTURE_DIR = WEB_DIR / "fixtures" / "fake_run"
CANCEL_MESSAGE = "任务已被用户取消"


# ① 预置置位：ingest 之后、parse 之前即取消，空输出目录不产生 parsed
def test_transform_raises_before_parse(tmp_path):
    event = threading.Event()
    event.set()
    with pytest.raises(ETError) as excinfo:
        transform_paper(
            FIXTURE_DIR / "source.pdf",
            output_dir=tmp_path,
            fake=True,
            cancel_event=event,
        )
    assert str(excinfo.value) == CANCEL_MESSAGE
    assert not (tmp_path / "parsed").exists()


# ② 不传 cancel_event：假模式全流程行为不变（复用 fixture parsed/）
def test_transform_without_event_unchanged(tmp_path):
    import shutil

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    shutil.copyfile(FIXTURE_DIR / "source.pdf", run_dir / "source.pdf")
    shutil.copytree(FIXTURE_DIR / "parsed", run_dir / "parsed")

    result = transform_paper(
        run_dir / "source.pdf",
        output_dir=run_dir,
        fake=True,
    )
    assert len(result.sections) == 7
    assert result.total_chars > 0


# ③a MinerUClient 轮询：事件置位时 1 秒内抛 ETError（poll_interval=10 被分段）
class _AlwaysRunning:
    def get(self, url, headers, timeout):  # noqa: ARG002
        class _Resp:
            status_code = 200

            def json(self_inner):
                return {"code": 0, "data": {"extract_result": [{"state": "running"}]}}

        return _Resp()


def test_mineru_poll_cancel_segmented():
    event = threading.Event()
    event.set()
    sleeps: list[float] = []
    client = MinerUClient(
        api_key="k",
        poll_interval=10.0,
        session=_AlwaysRunning(),
        sleep=lambda s: sleeps.append(s),
        cancel_event=event,
    )
    start = time.monotonic()
    with pytest.raises(ETError) as excinfo:
        client._poll_until_done("batch-1", {})
    elapsed = time.monotonic() - start
    assert str(excinfo.value) == CANCEL_MESSAGE
    assert elapsed < 1.0
    # 分段等待：出现的只能是 0.5s 小段（预置置位时可能首次检查即抛、无 sleep）
    assert all(s <= 0.5 for s in sleeps)
    assert 10.0 not in sleeps


# ③b event=None：保持单次 sleep(poll_interval) 原语义
class _RunningThenDone:
    def __init__(self) -> None:
        self.calls = 0

    def get(self, url, headers, timeout):  # noqa: ARG002
        self.calls += 1
        state = "running" if self.calls == 1 else "done"
        inner = {"state": state}
        if state == "done":
            inner["full_zip_url"] = "http://x/z.zip"

        class _Resp:
            status_code = 200

            def json(self_inner):
                return {"code": 0, "data": {"extract_result": [inner]}}

        return _Resp()


def test_mineru_poll_without_event_single_sleep():
    session = _RunningThenDone()
    sleeps: list[float] = []
    client = MinerUClient(
        api_key="k",
        poll_interval=2.0,
        session=session,
        sleep=lambda s: sleeps.append(s),
    )
    task = client._poll_until_done("batch-1", {})
    assert task["state"] == "done"
    assert sleeps == [2.0]
