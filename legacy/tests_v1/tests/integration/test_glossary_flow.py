"""US1 离线集成测试（T018）：parse→glossary draft→人工改 YAML→confirm 全流程。

全程 Fake：FakeMinerUClient 复制 mini_parsed 夹具，FakeLLMClient 返回录制的
glossary_draft 响应；不发起任何真实网络/API 调用。

关键断言（tasks.md T018 + CP-2）：

- parse 产出 normalized.json/job_state 正常推进；
- draft 种子 8 条 + LLM 建议落盘，status=draft；
- 程序化模拟人工改译名后，confirm 交互取消（退出码 3，无 glossary.yml）→
  --yes 校验通过并落 confirmed；
- 运行路径上不存在"绕过确认"分支：--yes 只跳过交互、不跳过校验；
- 凭据缺失在需要外部服务的路径上真实拦截（退出码 3 + PowerShell 指引，L1）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from essay_transformer.cli import build_parser, main
from essay_transformer.glossary import load_confirmed_glossary
from essay_transformer.errors import GlossaryNotConfirmedError
from essay_transformer.state import JobState

PID = "papertest"
PID_RUN = "paperrun"

DRAFT_RESPONSE = {
    "schema_kind": "glossary_draft",
    "terms": [
        {
            "en": "scaled dot-product attention",
            "zh": "缩放点积注意力",
            "mode": "bilingual",
            "aliases": ["dot-product attention"],
            "reason": "论文核心机制的正式名称，贯穿方法章",
            "first_seen_page": 3,
        },
        {
            "en": "layer normalization",
            "zh": "层归一化",
            "mode": "bilingual",
            "aliases": ["LayerNorm"],
            "reason": "每个子层残差后使用的归一化",
            "first_seen_page": 3,
        },
    ],
}


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    # 真实凭据只允许出现在 Phase 7；离线测试用假值通过启动校验
    monkeypatch.setenv("MINERU_API_KEY", "test-mineru-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")


@pytest.fixture()
def runs_dir(tmp_path: Path) -> Path:
    path = tmp_path / "runs"
    path.mkdir()
    return path


@pytest.fixture()
def sample_pdf(tmp_path: Path) -> Path:
    # ingest 只校验 %PDF 文件头；内容在 FakeMinerU 下不会被真实解析
    path = tmp_path / "sample.pdf"
    path.write_bytes(b"%PDF-1.5\nfake pdf body for offline test\n")
    return path


def test_parse_draft_manual_edit_confirm_full_flow(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_dir = runs_dir / PID
    draft_path = run_dir / "glossary.draft.yml"
    confirmed_path = run_dir / "glossary.yml"

    # --- 1. parse（FakeMinerU）---
    rc = main(
        ["parse", str(sample_pdf), "--paper-id", PID],
        mineru_client=fake_mineru,
        runs_dir=runs_dir,
    )
    assert rc == 0
    assert (run_dir / "source.pdf").is_file()
    assert (run_dir / "parsed" / "content_list.json").is_file()
    assert (run_dir / "normalized.json").is_file()
    state = JobState.load(run_dir, paper_id=PID)
    assert state.stage == "normalized"
    parse_out = capsys.readouterr().out
    assert f"paper_id: {PID}" in parse_out
    assert "块数：" in parse_out and "章节数：" in parse_out

    # --- 2. 闸门：confirm 前 confirmed 词表视为不存在 ---
    assert not confirmed_path.exists()
    with pytest.raises(GlossaryNotConfirmedError):
        load_confirmed_glossary(run_dir)

    # --- 3. glossary draft（FakeLLM；录制两次扫描响应供第 8 节使用）---
    second_draft_response = {
        "schema_kind": "glossary_draft",
        "terms": [
            *DRAFT_RESPONSE["terms"],
            {
                "en": "residual stream",
                "zh": "残差流",
                "mode": "bilingual",
                "reason": "第二次扫描出现的新候选",
                "first_seen_page": 4,
            },
        ],
    }
    llm = make_fake_llm(
        {"glossary_draft": [DRAFT_RESPONSE, second_draft_response]}
    )
    rc = main(["glossary", "draft", PID], llm_client=llm, runs_dir=runs_dir)
    assert rc == 0
    assert llm.call_count == 1
    assert llm.schema_kinds_called() == ["glossary_draft"]
    # prompt 模板占位符已被真实渲染（种子逐条枚举 + normalized 注入）
    system_prompt, user_prompt, _ = llm.calls[0]
    assert "{{NORMALIZED_JSON}}" not in user_prompt
    assert "{{SEED_TERMS}}" not in user_prompt
    assert "self-attention" in user_prompt
    assert "老师" in system_prompt

    draft_out = capsys.readouterr().out
    assert "种子 8 条" in draft_out
    assert "LLM 建议 2 条" in draft_out

    data = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    assert data["meta"]["paper_id"] == PID
    assert data["meta"]["status"] == "draft"
    assert data["meta"]["confirmed_at"] in ("", None)
    assert len(data["terms"]) == 10
    sources = {term["source"] for term in data["terms"]}
    assert sources == {"seed", "llm_suggested"}
    ens = {term["en"] for term in data["terms"]}
    assert "scaled dot-product attention" in ens
    assert "layer normalization" in ens

    # --- 4. 程序化模拟人工编辑：修订种子词 attention 的译名（覆盖冲突）---
    edited = False
    for term in data["terms"]:
        if term["en"] == "attention":
            term["zh"] = "注意力（人工修订译名）"
            edited = True
    assert edited
    draft_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    # --- 5. confirm 交互取消：不产生 glossary.yml，退出码 3 ---
    rc = main(
        ["glossary", "confirm", PID],
        runs_dir=runs_dir,
        input_fn=lambda _prompt: "n",
    )
    assert rc == 3
    assert not confirmed_path.exists()
    declined = capsys.readouterr()
    assert "已取消" in declined.out

    # --- 6. confirm --yes：校验 + 覆盖摘要 + 落 confirmed ---
    rc = main(["glossary", "confirm", PID, "--yes"], runs_dir=runs_dir)
    assert rc == 0
    confirm_out = capsys.readouterr().out
    assert "术语表校验通过" in confirm_out
    assert "种子「注意力机制」" in confirm_out
    assert "单篇「注意力（人工修订译名）」" in confirm_out
    assert "强制种子词覆盖" in confirm_out

    assert confirmed_path.is_file()
    confirmed = yaml.safe_load(confirmed_path.read_text(encoding="utf-8"))
    assert confirmed["meta"]["status"] == "confirmed"
    assert confirmed["meta"]["confirmed_at"]
    by_en = {term["en"]: term for term in confirmed["terms"]}
    assert by_en["attention"]["zh"] == "注意力（人工修订译名）"
    # source 不被自动改写（data-model §3 审计痕迹）
    assert by_en["self-attention"]["source"] == "seed"
    state = JobState.load(run_dir, paper_id=PID)
    assert state.stage == "glossary_confirmed"

    # --- 7. 闸门打开：load_confirmed_glossary 合并种子与单篇 ---
    merged = load_confirmed_glossary(run_dir)
    keys = {key for term in merged.terms for key in [term.en.lower()] +
            [a.lower() for a in getattr(term, "aliases", [])]}
    for required in ("self-attention", "positional encoding", "embedding", "softmax"):
        assert required in keys
    assert len(merged.overrides) == 1

    # --- 8. 再次独立 draft：cli.md §2 无条件重新扫描（不做状态短路）---
    # 必调 LLM；人工稿一字节不动；新候选只进 suggestions 文件。
    before = draft_path.read_bytes()
    rc = main(["glossary", "draft", PID], llm_client=llm, runs_dir=runs_dir)
    assert rc == 0
    second_out = capsys.readouterr().out
    assert llm.call_count == 2  # 独立 draft 每次运行都真实调用 generate_draft
    assert draft_path.read_bytes() == before  # 绝不覆盖人工修改
    suggestions_path = run_dir / "glossary.draft.suggestions.yml"
    assert suggestions_path.is_file()
    suggestions = yaml.safe_load(suggestions_path.read_text(encoding="utf-8"))
    assert [t["en"] for t in suggestions["terms"]] == ["residual stream"]
    assert suggestions["terms"][0]["source"] == "llm_suggested"
    assert "未覆盖" in second_out
    assert "glossary.draft.suggestions.yml" in second_out
    # 人工修订译名原样保留在草稿中
    reloaded_draft = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    attention = [t for t in reloaded_draft["terms"] if t["en"] == "attention"]
    assert attention[0]["zh"] == "注意力（人工修订译名）"

    # --- 9. --yes 不能绕过校验：bilingual 空 zh 仍退出码 3 且不落 glossary.yml ---
    confirmed_path.unlink()
    for term in data["terms"]:
        if term["en"] == "self-attention":
            term["zh"] = ""
    draft_path.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    rc = main(["glossary", "confirm", PID, "--yes"], runs_dir=runs_dir)
    assert rc == 3
    assert not confirmed_path.exists()
    err = capsys.readouterr().err
    assert "校验失败" in err


def test_confirm_without_draft_exits_3(runs_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = main(["glossary", "confirm", "no-such-paper", "--yes"], runs_dir=runs_dir)
    assert rc == 3
    assert not (runs_dir / "no-such-paper" / "glossary.yml").exists()
    assert "glossary draft" in capsys.readouterr().err


def test_run_command_parses_then_drafts_then_stops(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru,
    capsys: pytest.CaptureFixture[str],
) -> None:
    llm = make_fake_llm({"glossary_draft": DRAFT_RESPONSE})
    rc = main(
        ["run", str(sample_pdf), "--paper-id", PID_RUN],
        llm_client=llm,
        mineru_client=fake_mineru,
        runs_dir=runs_dir,
    )
    assert rc == 0
    run_dir = runs_dir / PID_RUN
    assert (run_dir / "normalized.json").is_file()
    assert (run_dir / "glossary.draft.yml").is_file()
    # 人工闸门：run 停止时绝不产出 confirmed 词表
    assert not (run_dir / "glossary.yml").exists()
    state = JobState.load(run_dir, paper_id=PID_RUN)
    assert state.stage == "glossary_drafted"
    assert llm.call_count == 1

    out = capsys.readouterr().out
    # cli.md §5 规定的停机提示逐字出现
    assert f"术语表初稿已生成：runs/{PID_RUN}/glossary.draft.yml" in out
    assert f"python -m essay_transformer glossary confirm {PID_RUN}" in out
    assert f"python -m essay_transformer convert {PID_RUN}" in out

    # 状态短路仅存在于 run 串联路径：二次 run 按 job_state 跳过 parse/draft，
    # 不触任何外部服务（独立 `glossary draft` 子命令不享受此短路，见主流程第 8 节）
    rc = main(
        ["run", str(sample_pdf), "--paper-id", PID_RUN],
        llm_client=llm,
        mineru_client=fake_mineru,
        runs_dir=runs_dir,
    )
    assert rc == 0
    assert llm.call_count == 1
    assert len(fake_mineru.calls) == 1
    second_out = capsys.readouterr().out
    assert "跳过 LLM 扫描（断点续跑）" in second_out
    # 短路提示不得再出现"重跑可看 suggestions"类误导文案
    assert "重新运行 draft" not in second_out


def test_missing_mineru_credential_exits_3_with_powershell_guidance(
    runs_dir: Path,
    sample_pdf: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("MINERU_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    rc = main(
        ["parse", str(sample_pdf), "--paper-id", "no-cred"],
        runs_dir=runs_dir,
    )
    assert rc == 3
    err = capsys.readouterr().err
    assert "$env:MINERU_API_KEY" in err
    assert "$env:DEEPSEEK_API_KEY" in err
    assert "PowerShell" in err
    # 前置校验失败：连 run 目录/正文产物都不应产生
    assert not (runs_dir / "no-cred" / "normalized.json").exists()


def test_draft_missing_deepseek_credential_exits_3(
    runs_dir: Path,
    sample_pdf: Path,
    make_fake_llm,
    fake_mineru,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # 先在双凭据齐全（假值）下完成 parse
    rc = main(
        ["parse", str(sample_pdf), "--paper-id", "need-llm"],
        mineru_client=fake_mineru,
        runs_dir=runs_dir,
    )
    assert rc == 0
    capsys.readouterr()

    # 缺 DEEPSEEK_API_KEY：draft 入口真实校验，退出码 3 且不触 LLM/不产 draft
    llm = make_fake_llm({"glossary_draft": DRAFT_RESPONSE})
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    rc = main(
        ["glossary", "draft", "need-llm"], llm_client=llm, runs_dir=runs_dir
    )
    assert rc == 3
    err = capsys.readouterr().err
    assert "$env:DEEPSEEK_API_KEY" in err
    assert "PowerShell" in err
    assert llm.call_count == 0
    assert not (runs_dir / "need-llm" / "glossary.draft.yml").exists()


def test_parser_has_no_bypass_option_and_yes_defaults_off() -> None:
    def collect_option_strings(parser: argparse.ArgumentParser) -> list[str]:
        found: list[str] = []
        for action in parser._actions:
            found.extend(action.option_strings)
        subparsers = getattr(parser, "_subparsers", None)
        if subparsers is not None:
            for group_action in subparsers._group_actions:
                for child in (group_action.choices or {}).values():
                    found.extend(collect_option_strings(child))
        return found

    option_strings = collect_option_strings(build_parser())

    # 不存在任何 auto-confirm / force-confirm 类绕过开关
    assert not any("auto" in opt.lower() for opt in option_strings)
    # --yes 是 confirm 唯一的确认相关开关（只跳交互、不跳校验）
    assert "--yes" in option_strings

    args_no = build_parser().parse_args(["glossary", "confirm", "x"])
    assert args_no.yes is False
    args_yes = build_parser().parse_args(["glossary", "confirm", "x", "--yes"])
    assert args_yes.yes is True
