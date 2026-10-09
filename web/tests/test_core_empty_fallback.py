"""T010(e)：七章齐全的标准论文 —— 全部走真实（fake）LLM 内容，固定文案零出现。

survey_run/fake_run 夹具本身缺节，无法覆盖"标准论文零降级"；本文件用合成
content_list 在 tmp_path 里组装一个七章全非空的 parsed/，跑 transform_paper。
"""

from __future__ import annotations

import json
import shutil

import pytest

from core.normalize import normalize_content_list
from core.planner import build_plan, EMPTY_FALLBACK
from core.pipeline import transform_paper
from web.app.config import WEB_DIR

FAKE_FIXTURE = WEB_DIR / "fixtures" / "fake_run"


def _heading(text: str, level: int, page: int) -> dict:
    return {"type": "text", "text_level": level, "text": text, "page_idx": page}


def _para(text: str, page: int) -> dict:
    return {"type": "text", "text": text, "page_idx": page}


# 七章全有实质内容：Abstract / §1 Introduction / §2 Related Work /
# §3 Method(+3.1) / §4 Experimental Setup / §5 Discussion and Conclusion /
# Appendix A → wrapup
STANDARD_CONTENT_LIST = [
    _heading("A Complete Paper: All Seven Slots", 1, 0),
    _heading("Abstract", 2, 0),
    _para("This abstract summarizes the problem, method and findings.", 0),
    _heading("1 Introduction", 2, 1),
    _para("We introduce the motivation and the problem tackled by this work.", 1),
    _heading("2 Related Work", 2, 1),
    _para("Prior systems and background needed to understand this paper.", 1),
    _heading("3 The Proposed Method", 2, 2),
    _para("The method consists of several components described as follows.", 2),
    _heading("3.1 Component Design", 3, 2),
    _para("Each component transforms the input representation.", 2),
    _heading("4 Experimental Setup", 2, 3),
    _para("We evaluate on a benchmark and compare accuracy with baselines.", 3),
    _heading("5 Discussion and Conclusion", 2, 4),
    _para("We conclude with the main claims and their implications.", 4),
    _heading("Appendix A Overall Assessment", 2, 5),
    _para("This appendix recaps contributions, limitations and scope.", 5),
    _heading("References", 2, 5),
    {"type": "ref_text",
     "text": "[1] A. Author. A foundational study. Journal, 2024.",
     "page_idx": 5},
]


@pytest.fixture()
def standard_result(tmp_path, monkeypatch):
    # 组装最小 run 目录：source.pdf + parsed/content_list.json
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    shutil.copyfile(FAKE_FIXTURE / "source.pdf", run_dir / "source.pdf")
    parsed = run_dir / "parsed"
    parsed.mkdir()
    (parsed / "content_list.json").write_text(
        json.dumps(STANDARD_CONTENT_LIST, ensure_ascii=False), encoding="utf-8"
    )

    # 计数 chat 调用（patch pipeline 模块内绑定的 chat）
    import core.pipeline as pipeline_mod

    calls: list[str] = []
    orig = pipeline_mod.chat

    def counting_chat(prompt_system, prompt_user, **kwargs):  # noqa: ANN001
        calls.append(prompt_user)
        return orig(prompt_system, prompt_user, **kwargs)

    monkeypatch.setattr(pipeline_mod, "chat", counting_chat)

    result = transform_paper(run_dir / "source.pdf", output_dir=run_dir, fake=True)
    return result, calls


def test_standard_plan_has_no_empty_slugs():
    norm = normalize_content_list(STANDARD_CONTENT_LIST, paper_id="std")
    plan = build_plan(norm)
    assert plan["empty_slugs"] == []
    assert all(t["empty"] is False for t in plan["target_order"])


def test_standard_paper_seven_llm_calls(standard_result):
    _result, calls = standard_result
    assert len(calls) == 7


def test_standard_paper_no_fallback_text(standard_result):
    result, _calls = standard_result
    assert len(result.sections) == 7
    for sec in result.sections:
        assert sec["content"].startswith("[FAKE OUTPUT]")
        assert sec["content"] not in EMPTY_FALLBACK.values()
    for line in EMPTY_FALLBACK.values():
        assert line not in result.full_markdown
