"""T035：断点续跑与阶段复用（FR-032，contracts/state-machine.md）。

覆盖：

- parse 缓存：同 PDF 二次 parse 不重复提交 MinerU（``mineru.calls`` 不增），
  ``--force`` 才重新提交；
- planner 失败（超时）后重跑 convert：planner 重新调用一次，随后各阶段
  顺序走完，最终 done；
- rewrite 中断（七段并发某段失败）后重跑：planner 不再调用，七段重做、
  worked example 执行，plan.json/sections 产物纪律正确；
- ``--from-stage assembled``：只重做本地组装与 QA，零 LLM 调用；
- ``--from-stage planned``：planner 及其后全部重做，调用序列完整。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import FakeMinerUClient
from essay_transformer.cli import main
from essay_transformer.errors import ServiceTimeoutError
from essay_transformer.planner import SLUG_ORDER
from essay_transformer.state import JobState

from test_pipeline_offline import (
    PID,
    _SLUG_RE,
    _parse,
    make_recorded_llm,
    planner_response,
    section_responses,
    worked_example_response,
    write_confirmed_glossary,
)


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MINERU_API_KEY", "test-mineru-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")


@pytest.fixture()
def runs_dir(tmp_path: Path) -> Path:
    path = tmp_path / "runs"
    path.mkdir()
    return path


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.5\nfake pdf body for resume test\n")
    return path


def _prepared_run(
    runs_dir: Path, sample_pdf: Path, fake_mineru: FakeMinerUClient
) -> Path:
    run_dir = _parse(runs_dir, sample_pdf, fake_mineru)
    write_confirmed_glossary(run_dir)
    return run_dir


# ---------------------------------------------------------------------------
# parse 阶段缓存
# ---------------------------------------------------------------------------


def test_parse_reuses_cached_mineru_result_and_force_reruns(
    runs_dir: Path, sample_pdf: Path, fake_mineru: FakeMinerUClient
) -> None:
    argv_base = ["parse", str(sample_pdf), "--paper-id", PID]
    assert main(argv_base, mineru_client=fake_mineru, runs_dir=runs_dir) == 0
    assert len(fake_mineru.calls) == 1

    # 断点续跑：ingest 拷贝与 normalize 重做，但不重新提交 MinerU
    assert main(argv_base, mineru_client=fake_mineru, runs_dir=runs_dir) == 0
    assert len(fake_mineru.calls) == 1

    # --force：忽略缓存重新解析
    assert main(
        [*argv_base, "--force"], mineru_client=fake_mineru, runs_dir=runs_dir
    ) == 0
    assert len(fake_mineru.calls) == 2

    assert JobState.load(runs_dir / PID, paper_id=PID).stage == "normalized"


# ---------------------------------------------------------------------------
# planner 失败后重跑
# ---------------------------------------------------------------------------


def test_convert_resumes_after_planner_timeout(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru: FakeMinerUClient,
) -> None:
    _prepared_run(runs_dir, sample_pdf, fake_mineru)
    recorded = section_responses()

    def section_answer(system, user, schema_kind):
        slug = _SLUG_RE.search(user).group("slug")
        return recorded[slug]

    # planner 队列：首次超时（ServiceTimeoutError 不触发阶段内重试），
    # 重跑时弹出合法计划
    llm = make_fake_llm({
        "planner": [ServiceTimeoutError("DeepSeek 504"), planner_response()],
        "section_rewrite": section_answer,
        "worked_example": worked_example_response(),
    })

    rc = main(["convert", PID], llm_client=llm, runs_dir=runs_dir)
    assert rc == 1
    assert llm.schema_kinds_called() == ["planner"]
    state = JobState.load(runs_dir / PID, paper_id=PID)
    assert state.failed is not None
    assert state.failed.failed_stage == "planned"

    # 同一 Fake 客户端重跑：planner 弹第二项成功，后续阶段全部走完
    rc = main(["convert", PID], llm_client=llm, runs_dir=runs_dir)
    assert rc == 0
    kinds = llm.schema_kinds_called()
    assert kinds == (
        ["planner"] + ["planner"] + ["section_rewrite"] * 7 + ["worked_example"]
    )
    assert JobState.load(runs_dir / PID, paper_id=PID).stage == "done"


# ---------------------------------------------------------------------------
# rewrite 中断：重跑不重做 planner
# ---------------------------------------------------------------------------


def test_rewrite_failure_resumes_without_replanning(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru: FakeMinerUClient,
) -> None:
    run_dir = _prepared_run(runs_dir, sample_pdf, fake_mineru)

    def boom(system, user, schema_kind):
        raise ServiceTimeoutError("rewrite worker unavailable")

    first = make_fake_llm({
        "planner": planner_response(),
        "section_rewrite": boom,
        "worked_example": worked_example_response(),
    })
    rc = main(["convert", PID], llm_client=first, runs_dir=runs_dir)
    assert rc == 1
    kinds = first.schema_kinds_called()
    assert kinds.count("planner") == 1
    # ServiceTimeoutError 不触发段内重试：七段各恰好 1 次，worked example 未触达
    assert kinds.count("section_rewrite") == len(SLUG_ORDER)
    assert "worked_example" not in kinds

    state = JobState.load(run_dir, paper_id=PID)
    assert state.failed is not None
    assert state.failed.failed_stage == "rewritten"
    # plan.json 已落盘可复用；sections 不得留半截产物
    assert (run_dir / "plan.json").is_file()
    assert not (run_dir / "sections").is_dir() or not any(
        (run_dir / "sections").iterdir()
    )

    # 重跑：planner 断点复用，七段重做并推进到 done
    second = make_recorded_llm(make_fake_llm)
    rc = main(["convert", PID], llm_client=second, runs_dir=runs_dir)
    assert rc == 0
    assert second.schema_kinds_called() == (
        ["section_rewrite"] * 7 + ["worked_example"]
    )
    section_files = {p.name for p in (run_dir / "sections").iterdir()}
    assert section_files == {f"{slug}.json" for slug in SLUG_ORDER}
    assert JobState.load(run_dir, paper_id=PID).stage == "done"


# ---------------------------------------------------------------------------
# --from-stage 显式回退
# ---------------------------------------------------------------------------


def _full_success(runs_dir: Path, make_fake_llm) -> object:
    llm = make_recorded_llm(make_fake_llm)
    rc = main(["convert", PID], llm_client=llm, runs_dir=runs_dir)
    assert rc == 0
    return llm


def test_from_stage_assembled_reruns_only_local_stages(
    runs_dir: Path, sample_pdf: Path, make_fake_llm, fake_mineru: FakeMinerUClient
) -> None:
    _prepared_run(runs_dir, sample_pdf, fake_mineru)
    _full_success(runs_dir, make_fake_llm)

    rerun = make_recorded_llm(make_fake_llm)
    rc = main(
        ["convert", PID, "--from-stage", "assembled"],
        llm_client=rerun, runs_dir=runs_dir,
    )
    assert rc == 0
    assert rerun.call_count == 0  # 组装与 QA 纯本地，不触 LLM
    assert JobState.load(runs_dir / PID, paper_id=PID).stage == "done"


def test_from_stage_planned_reruns_planner_and_after(
    runs_dir: Path, sample_pdf: Path, make_fake_llm, fake_mineru: FakeMinerUClient
) -> None:
    _prepared_run(runs_dir, sample_pdf, fake_mineru)
    _full_success(runs_dir, make_fake_llm)

    rerun = make_recorded_llm(make_fake_llm)
    rc = main(
        ["convert", PID, "--from-stage", "planned"],
        llm_client=rerun, runs_dir=runs_dir,
    )
    assert rc == 0
    assert rerun.schema_kinds_called() == (
        ["planner"] + ["section_rewrite"] * 7 + ["worked_example"]
    )
    assert JobState.load(runs_dir / PID, paper_id=PID).stage == "done"
