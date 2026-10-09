"""T008：内置假模式物料探针——零网络驱动真实 core 跑通六阶段。

物料在 web/fixtures/fake_run/（随仓库提交，UTF-8 无 BOM）。
worker 在 US2 复用同一套物料；本测试是"离线可验收"（宪法原则七）的前置证据。
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from core.pipeline import transform_paper

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "fake_run"
STAGES = ["ingest", "parse", "normalize", "plan", "rewrite", "assemble"]


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    dst = tmp_path / "run"
    shutil.copytree(FIXTURE_DIR, dst)
    return dst


def test_fixture_files_present():
    assert (FIXTURE_DIR / "source.pdf").read_bytes().startswith(b"%PDF")
    data = (FIXTURE_DIR / "parsed" / "content_list.json").read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf"), "content_list.json 不得带 BOM"


def test_fake_pipeline_six_stages_and_seven_sections(run_dir: Path):
    seen: list[tuple[str, float, str]] = []

    result = transform_paper(
        source=run_dir / "source.pdf",
        output_dir=run_dir,
        fake=True,
        progress_cb=lambda stage, p, msg: seen.append((stage, p, msg)),
    )

    seen_stages = [stage for stage, _p, _msg in seen]
    for stage in STAGES:
        assert stage in seen_stages, f"缺少阶段回调：{stage}"
    # 阶段顺序单调（允许同阶段多次回调）
    first_index = [seen_stages.index(s) for s in STAGES]
    assert first_index == sorted(first_index)

    assert result.title
    assert len(result.sections) == 7
    assert result.total_chars > 0
    assert result.dead_links == []
