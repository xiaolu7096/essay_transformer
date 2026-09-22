"""T034：错误分类、退出码与"失败环节+可执行建议"留痕（contracts/cli.md）。

覆盖：

- 退出码 3（前置/输入类）：坏链、文件不存在、非 PDF、损坏 PDF、
  纯扫描无文本层（OCR 提示）、术语表未确认（零 LLM 调用）、凭据缺失
  （消息含 PowerShell ``$env:`` 指引）；
- 退出码 1（运行时/外部服务）：MinerU 解析失败（failed_stage=parsed）、
  LLM 超时（failed_stage=planned），stderr 必须含"失败环节"与"建议"，
  job_state.json 同步留痕；
- T036 产物纪律：重跑 assembled 前上轮规范名成稿立即改 .unchecked.md；
  QA 失败仅留 .unchecked.md；修复后续跑 QA 通过，调试名清除、规范名恢复。

全程 Fake 客户端离线运行。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import FakeMinerUClient
from essay_transformer.assemble import DIST_DIRNAME
from essay_transformer.cli import main
from essay_transformer.errors import ParseServiceError, ServiceTimeoutError
from essay_transformer.state import JobState

from test_pipeline_offline import (
    PID,
    _parse,
    make_recorded_llm,
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
    path.write_bytes(b"%PDF-1.5\nfake pdf body for offline test\n")
    return path


def _dist_md_files(run_dir: Path) -> tuple[list[str], list[str]]:
    dist = run_dir / DIST_DIRNAME
    files = sorted(p.name for p in dist.glob("*.md")) if dist.is_dir() else []
    clean = [n for n in files if not n.endswith(".unchecked.md")]
    unchecked = [n for n in files if n.endswith(".unchecked.md")]
    return clean, unchecked


# ---------------------------------------------------------------------------
# 退出码 3：输入/前置条件
# ---------------------------------------------------------------------------


def test_unsupported_link_exit_3(
    runs_dir: Path, fake_mineru: FakeMinerUClient, capsys: pytest.CaptureFixture[str]
) -> None:
    rc = main(
        ["parse", "https://example.com/abs/1706.03762"],
        mineru_client=fake_mineru, runs_dir=runs_dir,
    )
    assert rc == 3
    assert "仅支持 arXiv 链接" in capsys.readouterr().err
    assert fake_mineru.calls == []  # 输入校验失败不得触达解析服务


def test_missing_local_file_exit_3(
    runs_dir: Path, fake_mineru: FakeMinerUClient
) -> None:
    rc = main(
        ["parse", str(runs_dir.parent / "nope.pdf")],
        mineru_client=fake_mineru, runs_dir=runs_dir,
    )
    assert rc == 3


def test_non_pdf_suffix_exit_3(
    tmp_path: Path, runs_dir: Path, fake_mineru: FakeMinerUClient
) -> None:
    txt = tmp_path / "paper.txt"
    txt.write_text("not a pdf", encoding="utf-8")
    rc = main(["parse", str(txt)], mineru_client=fake_mineru, runs_dir=runs_dir)
    assert rc == 3


def test_corrupt_pdf_content_exit_3(
    tmp_path: Path, runs_dir: Path, fake_mineru: FakeMinerUClient
) -> None:
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"this is definitely not a pdf")
    rc = main(["parse", str(bad)], mineru_client=fake_mineru, runs_dir=runs_dir)
    assert rc == 3
    assert fake_mineru.calls == []


def test_scanned_pdf_without_text_layer_exit_3(
    tmp_path: Path, runs_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 构造"纯扫描件"解析产物：只有图片条目，无任何文本承载块
    scanned = tmp_path / "scanned_parsed"
    (scanned / "images").mkdir(parents=True)
    (scanned / "images" / "a.jpg").write_bytes(b"\xff\xd8fake-jpeg")
    content_list = [
        {"type": "image", "img_path": "images/a.jpg", "page_idx": 0},
        {"type": "image", "img_path": "images/missing.jpg", "page_idx": 1},
    ]
    (scanned / "content_list.json").write_text(
        json.dumps(content_list), encoding="utf-8"
    )
    client = FakeMinerUClient(source_parsed_dir=scanned)

    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.5\nscanned\n")
    rc = main(
        ["parse", str(pdf), "--paper-id", "scan-demo"],
        mineru_client=client, runs_dir=runs_dir,
    )
    assert rc == 3
    err = capsys.readouterr().err
    assert "文本层" in err and "OCR" in err

    # 失败留痕：环节 normalized，异常自带 error_type=input（不回落 parse）
    state = JobState.load(runs_dir / "scan-demo", paper_id="scan-demo")
    assert state.failed is not None
    assert state.failed.failed_stage == "normalized"
    assert state.failed.error_type == "input"


def test_unconfirmed_glossary_exit_3_with_zero_llm_calls(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru: FakeMinerUClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _parse(runs_dir, sample_pdf, fake_mineru)
    llm = make_recorded_llm(make_fake_llm)  # 不写 confirmed 词表
    rc = main(["convert", PID], llm_client=llm, runs_dir=runs_dir)
    assert rc == 3
    assert llm.call_count == 0
    assert "FR-008" in capsys.readouterr().err


def test_missing_credentials_exit_3_with_powershell_hint(
    runs_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    rc = main(["convert", PID], runs_dir=runs_dir)
    assert rc == 3
    err = capsys.readouterr().err
    assert "DEEPSEEK_API_KEY" in err
    assert "$env:" in err  # PowerShell 设置指引


# ---------------------------------------------------------------------------
# 退出码 1：外部服务/运行时错误，失败环节 + 建议留痕
# ---------------------------------------------------------------------------


def test_mineru_failure_exit_1_marks_parsed_stage(
    runs_dir: Path,
    sample_pdf: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeMinerUClient(
        failure=ParseServiceError("MinerU 任务终态 failed：文档解析失败")
    )
    rc = main(
        ["parse", str(sample_pdf), "--paper-id", PID],
        mineru_client=client, runs_dir=runs_dir,
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "失败环节：解析（MinerU）" in err
    assert "建议：" in err  # error_type=parse 的可执行建议

    state = JobState.load(runs_dir / PID, paper_id=PID)
    assert state.failed is not None
    assert state.failed.failed_stage == "parsed"
    assert state.failed.error_type == "parse"


def test_mineru_rejected_credentials_exit_3(
    runs_dir: Path,
    sample_pdf: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """T048/挂账 L3：运行时 key 被 MinerU 拒绝（401/403）属凭据问题→退出码 3，
    不得伪装成解析服务故障（退出码 1）；建议含 PowerShell 轮换指引。"""
    from types import SimpleNamespace

    from essay_transformer.mineru_client import HTTPMinerUClient

    class _UnauthorizedSession:
        """首个鉴权请求即返回 401（不触网）。"""

        def post(self, url, **kwargs):
            return SimpleNamespace(status_code=401, text="unauthorized")

    client = HTTPMinerUClient(session=_UnauthorizedSession())
    rc = main(
        ["parse", str(sample_pdf), "--paper-id", PID],
        mineru_client=client, runs_dir=runs_dir,
    )
    assert rc == 3
    err = capsys.readouterr().err
    assert "失败环节：解析（MinerU）" in err
    assert "MINERU_API_KEY" in err and "$env:" in err

    state = JobState.load(runs_dir / PID, paper_id=PID)
    assert state.failed is not None
    assert state.failed.failed_stage == "parsed"
    assert state.failed.error_type == "credentials"


def test_mineru_client_missing_pdf_is_input_error(tmp_path: Path) -> None:
    """T048：直接调用核心库时 PDF 不存在归输入类（InputError，退出码 3 家族）。"""
    from essay_transformer.errors import InputError
    from essay_transformer.mineru_client import HTTPMinerUClient

    client = HTTPMinerUClient(api_key="dummy-key")
    with pytest.raises(InputError, match="不存在或不可读"):
        client.parse(tmp_path / "missing.pdf", tmp_path / "parsed")


def test_llm_timeout_at_planner_exit_1(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru: FakeMinerUClient,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _parse(runs_dir, sample_pdf, fake_mineru)
    write_confirmed_glossary(runs_dir / PID)
    llm = make_fake_llm(failure=ServiceTimeoutError("DeepSeek 504 gateway timeout"))

    rc = main(["convert", PID], llm_client=llm, runs_dir=runs_dir)
    assert rc == 1
    assert llm.call_count >= 1  # 确实尝试过调用
    err = capsys.readouterr().err
    assert "失败环节：七段规划（planner）" in err
    assert "建议：" in err

    state = JobState.load(runs_dir / PID, paper_id=PID)
    assert state.failed is not None
    assert state.failed.failed_stage == "planned"
    assert state.failed.error_type == "service_timeout"


# ---------------------------------------------------------------------------
# T036：产物纪律——不允许"伪成功"成稿残留
# ---------------------------------------------------------------------------


def test_stale_dist_invalidated_when_planner_rerun_fails(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru: FakeMinerUClient,
) -> None:
    run_dir = _parse(runs_dir, sample_pdf, fake_mineru)
    write_confirmed_glossary(run_dir)

    # 第一轮：全链路成功，dist/ 只有规范名成稿
    good_llm = make_recorded_llm(make_fake_llm)
    rc = main(["convert", PID], llm_client=good_llm, runs_dir=runs_dir)
    assert rc == 0
    clean, unchecked = _dist_md_files(run_dir)
    assert len(clean) == 1 and unchecked == []

    # 第二轮：--from-stage planned 且 planner 立即超时；
    # assembled 注定重跑，旧规范名成稿必须在任何失败发生前失效
    bad_llm = make_fake_llm(failure=ServiceTimeoutError("planner down"))
    rc = main(
        ["convert", PID, "--from-stage", "planned"],
        llm_client=bad_llm, runs_dir=runs_dir,
    )
    assert rc == 1
    clean, unchecked = _dist_md_files(run_dir)
    assert clean == []  # 不存在任何"看似成功"的规范名成稿
    assert len(unchecked) == 1

    state = JobState.load(run_dir, paper_id=PID)
    assert state.failed is not None
    assert state.failed.failed_stage == "planned"


def test_qa_fail_then_fix_and_rerun_restores_clean_name(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru: FakeMinerUClient,
) -> None:
    run_dir = _parse(runs_dir, sample_pdf, fake_mineru)
    write_confirmed_glossary(run_dir)

    # 第一轮：手算示例 setup 结构损坏（Q 行数不一致），确定性重算无法
    # 修复、复算失败 → 退出码 2，仅留 .unchecked.md
    bad_example = worked_example_response()
    bad_example["setup"]["Q"] = bad_example["setup"]["Q"][:2]
    bad_llm = make_recorded_llm(make_fake_llm, worked_queue=[bad_example, bad_example])
    rc = main(
        ["convert", PID, "--allow-missing-example"],
        llm_client=bad_llm, runs_dir=runs_dir,
    )
    assert rc == 2
    clean, unchecked = _dist_md_files(run_dir)
    assert clean == [] and len(unchecked) == 1

    # 修复后续跑：--from-stage rewritten 重做转写/示例/组装/QA（录制良好响应）
    good_llm = make_recorded_llm(make_fake_llm)
    rc = main(
        ["convert", PID, "--from-stage", "rewritten"],
        llm_client=good_llm, runs_dir=runs_dir,
    )
    assert rc == 0
    clean, unchecked = _dist_md_files(run_dir)
    assert len(clean) == 1 and unchecked == []
    assert JobState.load(run_dir, paper_id=PID).stage == "done"
