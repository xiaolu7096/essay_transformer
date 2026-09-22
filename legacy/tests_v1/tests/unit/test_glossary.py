"""术语表装载/合并/校验/匹配单元测试（T014，US1）。

契约依据：

- contracts/glossary-format.md 全文（语义规则 1-6 + confirm 校验清单）
- data-model.md §3（装载顺序、单篇覆盖种子、status!=confirmed 视为不存在）

覆盖点（tasks.md T014）：

1. 种子 + 单篇合并
2. 单篇覆盖种子的冲突检测（含 alias 命中、大小写不敏感）
3. en/aliases 大小写不敏感全局唯一，重复即校验失败
4. bilingual 空 zh 校验失败
5. 四种子词（self-attention/positional encoding/embedding/softmax）缺失即失败
6. status != confirmed 的 glossary.yml 视为不存在
7. 最长词优先匹配（attention 不得误匹配 attentional）
"""

from __future__ import annotations

from pathlib import Path

import pytest

from essay_transformer.errors import GlossaryNotConfirmedError, InputError
from essay_transformer.glossary import (
    REQUIRED_SEED_TERMS,
    FieldDifference,
    GlossaryDoc,
    GlossaryMatcher,
    Term,
    dump_glossary,
    generate_draft,
    load_confirmed_glossary,
    merge_glossaries,
    validate_glossary,
)


# ---------------------------------------------------------------------------
# 构造帮手
# ---------------------------------------------------------------------------


def _term(
    en: str,
    zh: str = "占位译名",
    *,
    mode: str = "bilingual",
    scope: str = "paper",
    source: str = "manual",
    aliases: list[str] | None = None,
    note: str = "",
) -> Term:
    return Term(
        en=en,
        zh=zh,
        mode=mode,
        scope=scope,
        source=source,
        aliases=list(aliases) if aliases is not None else [],
        note=note,
    )


def _seed_doc(*terms: Term, paper_id: str = "*", status: str = "confirmed") -> GlossaryDoc:
    return GlossaryDoc(meta={"paper_id": paper_id, "status": status}, terms=list(terms))


def _paper_doc(
    *terms: Term, paper_id: str = "arxiv-test", status: str = "draft"
) -> GlossaryDoc:
    return GlossaryDoc(meta={"paper_id": paper_id, "status": status}, terms=list(terms))


def _four_seed_terms() -> list[Term]:
    return [
        _term("self-attention", "自注意力机制", scope="seed", source="seed"),
        _term("positional encoding", "位置编码", scope="seed", source="seed"),
        _term("embedding", "词嵌入", scope="seed", source="seed"),
        _term("softmax", "softmax", scope="seed", source="seed"),
    ]


# ---------------------------------------------------------------------------
# 1. 种子 + 单篇合并
# ---------------------------------------------------------------------------


def test_seed_and_paper_terms_merge_as_union() -> None:
    seed = _seed_doc(
        _term("attention", "注意力机制", scope="seed", source="seed"),
        _term("Transformer", "Transformer", scope="seed", source="seed"),
    )
    paper = _paper_doc(
        _term("scaled dot-product attention", "缩放点积注意力"),
        _term("multi-head attention", "多头注意力"),
    )

    result = merge_glossaries(seed, paper)

    assert [t.en for t in result.terms] == [
        "attention",
        "Transformer",
        "scaled dot-product attention",
        "multi-head attention",
    ]
    assert result.overrides == []


def test_merge_keeps_seed_layer_fields_and_paper_layer_fields() -> None:
    seed = _seed_doc(_term("encoder", "编码器", scope="seed", source="seed"))
    paper = _paper_doc(_term("decoder", "解码器"))
    result = merge_glossaries(seed, paper)

    by_en = {t.en.lower(): t for t in result.terms}
    assert by_en["encoder"].scope == "seed"
    assert by_en["encoder"].source == "seed"
    assert by_en["decoder"].scope == "paper"
    assert by_en["decoder"].source == "manual"


# ---------------------------------------------------------------------------
# 2. 单篇覆盖种子：冲突检测
# ---------------------------------------------------------------------------


def test_paper_term_overrides_seed_with_same_en_and_conflict_recorded() -> None:
    seed = _seed_doc(_term("attention", "注意力机制", scope="seed", source="seed"))
    paper = _paper_doc(_term("attention", "注意力（本文特定义）"))

    result = merge_glossaries(seed, paper)

    assert len(result.overrides) == 1
    override = result.overrides[0]
    assert override.seed.en == "attention"
    assert override.seed.zh == "注意力机制"
    assert override.paper.zh == "注意力（本文特定义）"
    # 单篇译名生效，被覆盖的种子条目不再重复出现
    assert [t.en for t in result.terms] == ["attention"]
    assert result.terms[0].zh == "注意力（本文特定义）"


def test_override_matching_is_case_insensitive() -> None:
    seed = _seed_doc(_term("self-attention", "自注意力机制", scope="seed", source="seed"))
    paper = _paper_doc(_term("Self-Attention", "自注意力（修订译名）"))

    result = merge_glossaries(seed, paper)

    assert len(result.overrides) == 1
    assert result.terms[0].en == "Self-Attention"  # 保留单篇规范写法
    assert result.terms[0].zh == "自注意力（修订译名）"


def test_paper_alias_hitting_seed_en_is_an_override_not_a_duplicate() -> None:
    # glossary-format.md 语义规则 5：同一 en（含 alias）冲突时单篇覆盖种子
    seed = _seed_doc(
        _term("multi-head attention", "多头注意力", scope="seed", source="seed"),
    )
    paper = _paper_doc(
        _term("MHA", "多头注意力机制", aliases=["multi-head attention"]),
    )

    result = merge_glossaries(seed, paper)

    assert len(result.overrides) == 1
    assert result.overrides[0].seed.en == "multi-head attention"
    assert [t.en for t in result.terms] == ["MHA"]


# ---------------------------------------------------------------------------
# 3. en / aliases 大小写不敏感全局唯一
# ---------------------------------------------------------------------------


def test_duplicate_en_within_one_file_fails_validation() -> None:
    doc = _paper_doc(
        *_four_seed_terms(),
        _term("Transformer", "Transformer"),
        _term("transformer", "变换器"),  # 与上条 en 大小写冲突
    )
    with pytest.raises(InputError) as excinfo:
        validate_glossary(doc)
    assert "transformer" in str(excinfo.value).lower()


def test_alias_clash_with_another_en_fails_validation() -> None:
    doc = _paper_doc(
        *_four_seed_terms(),
        _term("attention", "注意力机制"),
        _term("multi-head attention", "多头注意力", aliases=["Attention"]),
    )
    with pytest.raises(InputError) as excinfo:
        validate_glossary(doc)
    message = str(excinfo.value).lower()
    assert "attention" in message
    assert "重复" in str(excinfo.value)


def test_alias_clash_between_two_entries_fails_validation() -> None:
    doc = _paper_doc(
        *_four_seed_terms(),
        _term("scaled dot-product attention", "缩放点积注意力", aliases=["dot-product attention"]),
        _term("dot-product attention", "点积注意力"),
    )
    with pytest.raises(InputError):
        validate_glossary(doc)


def test_one_paper_term_matching_two_distinct_seed_terms_fails() -> None:
    # 一个单篇条目的 en/alias 同时命中两个不同种子条目：歧义覆盖，校验失败
    seed = _seed_doc(
        _term("attention", "注意力机制", scope="seed", source="seed"),
        _term("attn", "注意力简写", scope="seed", source="seed", aliases=["attention"]),
    )
    paper = _paper_doc(_term("attention", "注意力（新译名）"))
    with pytest.raises(InputError):
        merge_glossaries(seed, paper)


# ---------------------------------------------------------------------------
# 2b. zh/mode 一致时的非译名增补不得丢字段（CP-2 F-1 回归）
# ---------------------------------------------------------------------------


def test_manual_alias_and_note_on_seed_copy_are_merged_not_dropped() -> None:
    # 场景：人工在 draft 的种子参考副本上为 self-attention 增补别名 SA 与 note，
    # zh/mode 未改。合并不得静默丢字段；不记译名冲突，只记字段差异。
    seed = _seed_doc(
        _term(
            "self-attention",
            "自注意力机制",
            scope="seed",
            source="seed",
            aliases=["self attention"],
        ),
    )
    paper = _paper_doc(
        Term(
            en="self-attention",
            zh="自注意力机制",
            mode="bilingual",
            scope="seed",
            source="seed",
            aliases=["self attention", "SA"],
            note="本文特指缩放点积形式",
        ),
    )

    result = merge_glossaries(seed, paper)

    assert result.overrides == []
    assert len(result.differences) == 1
    diff = result.differences[0]
    assert isinstance(diff, FieldDifference)
    assert diff.seed.en == "self-attention"
    assert diff.added_aliases == ["SA"]
    assert diff.note_changed is True
    assert diff.en_respelled is False

    assert len(result.terms) == 1
    effective = result.terms[0]
    assert effective.aliases == ["self attention", "SA"]
    assert effective.note == "本文特指缩放点积形式"

    # 回归核心：人工新增的 SA 必须进入匹配器（此前静默去重会丢 SA）
    matcher = GlossaryMatcher(result.terms)
    assert [m.term.en for m in matcher.find("SA is cheaper than recurrence.")] == [
        "self-attention"
    ]
    assert [m.matched_text for m in matcher.find("self attention block")] == [
        "self attention"
    ]


def test_identical_seed_copy_is_silently_deduplicated() -> None:
    # zh/mode/aliases/note 全部一致：纯参考副本，静默去重无任何记录
    seed = _seed_doc(
        _term(
            "embedding",
            "词嵌入",
            scope="seed",
            source="seed",
            aliases=["embed"],
            note="通用词",
        ),
    )
    paper = _paper_doc(
        Term(
            en="embedding",
            zh="词嵌入",
            mode="bilingual",
            scope="seed",
            source="seed",
            aliases=["embed"],
            note="通用词",
        ),
    )
    result = merge_glossaries(seed, paper)
    assert result.overrides == []
    assert result.differences == []
    assert len(result.terms) == 1
    assert result.terms[0].aliases == ["embed"]
    assert result.terms[0].note == "通用词"


def test_translation_override_also_keeps_seed_aliases_union() -> None:
    # 译名冲突覆盖时同样不得丢种子侧 aliases（attn）与非空 note
    seed = _seed_doc(
        _term(
            "attention",
            "注意力机制",
            scope="seed",
            source="seed",
            aliases=["attn"],
            note="种子备注",
        ),
    )
    paper = _paper_doc(
        _term(
            "attention",
            "注意力（本文特定义）",
            aliases=["ATTENTION"],
        ),
    )
    result = merge_glossaries(seed, paper)
    assert len(result.overrides) == 1
    assert result.differences == []
    effective = result.terms[0]
    assert effective.zh == "注意力（本文特定义）"
    # 大小写不敏感并集：attn 保留、ATTENTION 追加（attention 是 en 不进 aliases）
    assert effective.aliases == ["attn", "ATTENTION"]
    assert effective.note == "种子备注"
    matcher = GlossaryMatcher(result.terms)
    hit_terms = {m.matched_text.lower() for m in matcher.find("attn and ATTENTION both hit")}
    assert hit_terms == {"attn", "attention"}


def test_en_respell_with_same_translation_is_a_field_difference() -> None:
    # zh/mode 一致但单篇改写 en 规范写法（大小写）：以单篇写法生效并提示
    seed = _seed_doc(_term("self-attention", "自注意力机制", scope="seed", source="seed"))
    paper = _paper_doc(_term("Self-Attention", "自注意力机制"))
    result = merge_glossaries(seed, paper)
    assert result.overrides == []
    assert len(result.differences) == 1
    assert result.differences[0].en_respelled is True
    assert result.terms[0].en == "Self-Attention"


def test_valid_glossary_passes_validation() -> None:
    seed = _seed_doc(*_four_seed_terms())
    paper = _paper_doc(
        _term("Transformer", "Transformer"),
        _term("attention", "注意力机制", aliases=["attn"]),
    )
    result = merge_glossaries(seed, paper)
    validate_glossary(result.terms)  # 不抛异常即通过


# ---------------------------------------------------------------------------
# 4. bilingual 空 zh
# ---------------------------------------------------------------------------


def test_bilingual_term_with_empty_zh_fails() -> None:
    doc = _paper_doc(*_four_seed_terms(), _term("layer norm", "", mode="bilingual"))
    with pytest.raises(InputError) as excinfo:
        validate_glossary(doc)
    assert "zh" in str(excinfo.value)
    assert "layer norm" in str(excinfo.value)


def test_blank_zh_fails_even_with_whitespace() -> None:
    doc = _paper_doc(*_four_seed_terms(), _term("layer norm", "   ", mode="bilingual"))
    with pytest.raises(InputError):
        validate_glossary(doc)


def test_illegal_mode_scope_source_fail() -> None:
    doc = _paper_doc(
        *_four_seed_terms(),
        _term("a", "甲", mode="translate"),  # type: ignore[arg-type]
    )
    with pytest.raises(InputError) as excinfo:
        validate_glossary(doc)
    assert "mode" in str(excinfo.value)

    doc = _paper_doc(
        *_four_seed_terms(),
        Term(en="b", zh="乙", mode="bilingual", scope="global", source="manual"),
    )
    with pytest.raises(InputError) as excinfo:
        validate_glossary(doc)
    assert "scope" in str(excinfo.value)

    doc = _paper_doc(
        *_four_seed_terms(),
        Term(en="c", zh="丙", mode="bilingual", scope="paper", source="guessed"),
    )
    with pytest.raises(InputError) as excinfo:
        validate_glossary(doc)
    assert "source" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 5. 四种子词必须存在（可由 alias 命中）
# ---------------------------------------------------------------------------


def test_missing_required_seed_terms_fails() -> None:
    doc = _paper_doc(
        _term("self-attention", "自注意力机制"),
        _term("embedding", "词嵌入"),
        # 缺 positional encoding 与 softmax
    )
    with pytest.raises(InputError) as excinfo:
        validate_glossary(doc)
    message = str(excinfo.value)
    assert "positional encoding" in message
    assert "softmax" in message


def test_required_seed_terms_may_be_hit_via_alias() -> None:
    # 四词通过 en 或 alias 命中均可（glossary-format.md confirm 清单）
    terms = [
        _term("自注意力", "自注意力机制", aliases=["Self Attention", "self-attention"]),
        _term("位置编码", "位置编码", aliases=["positional encoding"]),
        _term("词嵌入", "词嵌入", aliases=["Embedding"]),
        _term("归一化指数函数", "softmax", aliases=["SOFTMAX"]),
        _term("Transformer", "Transformer"),
    ]
    validate_glossary(terms)  # 不抛异常即通过


def test_required_seed_terms_constant_matches_contract() -> None:
    assert tuple(name.lower() for name in REQUIRED_SEED_TERMS) == (
        "self-attention",
        "positional encoding",
        "embedding",
        "softmax",
    )


# ---------------------------------------------------------------------------
# 6. status != confirmed 视为不存在
# ---------------------------------------------------------------------------


def _write_glossary_yml(run_dir: Path, status: str) -> Path:
    import yaml

    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "glossary.yml"
    payload = {
        "meta": {"paper_id": run_dir.name, "status": status, "confirmed_at": ""},
        "terms": [
            {
                "en": "self-attention",
                "zh": "自注意力机制",
                "mode": "bilingual",
                "scope": "seed",
                "source": "seed",
            }
        ],
    }
    path.write_text(yaml.safe_dump(payload, allow_unicode=True), encoding="utf-8")
    return path


def test_unconfirmed_glossary_yml_is_treated_as_absent(tmp_path: Path) -> None:
    _write_glossary_yml(tmp_path, status="draft")
    with pytest.raises(GlossaryNotConfirmedError):
        load_confirmed_glossary(tmp_path, seed_path=tmp_path / "no-seed.yml")


def test_missing_glossary_yml_is_treated_as_absent(tmp_path: Path) -> None:
    with pytest.raises(GlossaryNotConfirmedError):
        load_confirmed_glossary(tmp_path, seed_path=tmp_path / "no-seed.yml")


def test_confirmed_glossary_yml_loads_as_merged(tmp_path: Path) -> None:
    import yaml

    seed_path = tmp_path / "seed.yml"
    seed_path.write_text(
        yaml.safe_dump(
            {
                "meta": {"paper_id": "*", "status": "confirmed", "confirmed_at": ""},
                "terms": [
                    {
                        "en": "softmax",
                        "zh": "softmax",
                        "mode": "bilingual",
                        "scope": "seed",
                        "source": "seed",
                    }
                ],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    _write_glossary_yml(tmp_path, status="confirmed")
    merged = load_confirmed_glossary(tmp_path, seed_path=seed_path)
    ens = {t.en.lower() for t in merged.terms}
    assert "self-attention" in ens
    assert "softmax" in ens


# ---------------------------------------------------------------------------
# 7. 最长词优先匹配 / 词边界
# ---------------------------------------------------------------------------


def test_longest_term_wins_and_word_boundary_respected() -> None:
    terms = [
        _term("attention", "注意力机制", scope="seed", source="seed"),
        _term("self-attention", "自注意力机制", aliases=["self attention"],
              scope="seed", source="seed"),
    ]
    matcher = GlossaryMatcher(terms)

    text = "self-attention is an attention mechanism, while attentional drift is unrelated."
    matches = matcher.find(text)
    matched_pairs = [(m.matched_text.lower(), m.term.en) for m in matches]

    assert ("self-attention", "self-attention") in matched_pairs
    # 独立 attention 仍可命中
    assert any(text[m.start:m.end].lower() == "attention" and m.term.en == "attention"
               for m in matches)
    # attentional 不得被 attention 误匹配（词边界）
    assert all(m.matched_text.lower() != "attentional" for m in matches)
    assert not matcher.find("attentional")


def test_matching_is_case_insensitive_and_uses_aliases() -> None:
    terms = [_term("positional encoding", "位置编码", aliases=["PE"])]
    matcher = GlossaryMatcher(terms)

    matches = matcher.find("We add Positional Encoding (pe) to each layer.")
    matched = {m.matched_text.lower() for m in matches}
    assert "positional encoding" in matched
    assert "pe" in matched


def test_matches_do_not_overlap() -> None:
    terms = [
        _term("scaled dot-product attention", "缩放点积注意力"),
        _term("dot-product attention", "点积注意力"),
        _term("attention", "注意力机制"),
    ]
    matcher = GlossaryMatcher(terms)
    text = "scaled dot-product attention is key."
    matches = matcher.find(text)
    # 最长词整体命中一次，短词不得在其区间内重复命中
    assert len(matches) == 1
    assert matches[0].term.en == "scaled dot-product attention"


# ---------------------------------------------------------------------------
# 8. draft 生成与"不覆盖人工修改"（T016，cli.md glossary draft）
# ---------------------------------------------------------------------------


def test_generate_draft_then_suggestions_never_overwrites_manual_edits(
    tmp_path: Path, make_fake_llm
) -> None:
    import json

    import yaml

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "normalized.json").write_text(
        json.dumps({"paper_id": "paper-x", "sections": [], "blocks": []}),
        encoding="utf-8",
    )
    seed_path = tmp_path / "seed.yml"
    seed_path.write_text(dump_glossary(_seed_doc(*_four_seed_terms())), encoding="utf-8")

    # 首次：种子 4 条 + LLM 建议 1 条 -> glossary.draft.yml
    llm_first = make_fake_llm(
        {"glossary_draft": {"schema_kind": "glossary_draft", "terms": [
            {"en": "sublayer", "zh": "子层", "mode": "bilingual", "reason": "反复出现"},
        ]}}
    )
    outcome = generate_draft(run_dir, llm_first, seed_path=seed_path)
    assert outcome.existed is False
    assert outcome.seed_count == 4
    assert outcome.suggested_count == 1
    draft_path = run_dir / "glossary.draft.yml"
    assert draft_path.is_file()
    assert not (run_dir / "glossary.draft.suggestions.yml").exists()

    # 人工修改初稿中 sublayer 的译名
    draft_data = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    for term in draft_data["terms"]:
        if term["en"] == "sublayer":
            term["zh"] = "子层（人工修订）"
    draft_path.write_text(
        yaml.safe_dump(draft_data, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    # 再次扫描：一个旧候选 + 一个新候选；draft 不动，新候选进 suggestions
    llm_second = make_fake_llm(
        {"glossary_draft": {"schema_kind": "glossary_draft", "terms": [
            {"en": "sublayer", "zh": "子层", "mode": "bilingual", "reason": "已在稿中"},
            {"en": "residual stream", "zh": "残差流", "mode": "bilingual",
             "reason": "新概念"},
        ]}}
    )
    outcome2 = generate_draft(run_dir, llm_second, seed_path=seed_path)
    assert outcome2.existed is True
    assert outcome2.new_suggestion_count == 1

    # 人工修改原样保留，条目数不变
    reloaded = yaml.safe_load(draft_path.read_text(encoding="utf-8"))
    sublayer = [t for t in reloaded["terms"] if t["en"] == "sublayer"]
    assert len(sublayer) == 1
    assert sublayer[0]["zh"] == "子层（人工修订）"
    assert len(reloaded["terms"]) == 5

    suggestions_path = run_dir / "glossary.draft.suggestions.yml"
    assert suggestions_path.is_file()
    suggestions = yaml.safe_load(suggestions_path.read_text(encoding="utf-8"))
    assert [t["en"] for t in suggestions["terms"]] == ["residual stream"]
    assert suggestions["terms"][0]["source"] == "llm_suggested"
    assert suggestions["terms"][0]["scope"] == "paper"


def test_generate_draft_requires_normalized_json(tmp_path: Path, make_fake_llm) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    seed_path = tmp_path / "seed.yml"
    seed_path.write_text(dump_glossary(_seed_doc(*_four_seed_terms())), encoding="utf-8")
    llm = make_fake_llm({"glossary_draft": {"schema_kind": "glossary_draft", "terms": []}})
    with pytest.raises(InputError):
        generate_draft(run_dir, llm, seed_path=seed_path)
