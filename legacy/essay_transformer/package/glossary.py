"""术语表资产：装载/合并/校验/匹配 + draft/confirm 落盘（T016，US1）。

契约：``contracts/glossary-format.md`` 全部语义规则与 confirm 校验清单、
``data-model.md`` §3、``contracts/cli.md`` glossary draft/confirm 小节。

层次（宪法原则 I：本模块是核心库，禁止 argparse；只依赖 LLMClient Protocol，
真实 DeepSeekClient 仅由 ``cli.py`` 构造注入）：

- :class:`Term` / :class:`GlossaryDoc`：YAML 文件结构（两文件同一 schema）
- :func:`merge_glossaries`：seed + 单篇分层合并，单篇覆盖种子并留冲突记录
- :func:`validate_glossary` / :func:`validate_meta`：confirm 校验清单的
  确定性实现，任何不过项抛 :class:`~essay_transformer.errors.InputError`
  （退出码 3，不产生 glossary.yml）
- :class:`GlossaryMatcher`：en+aliases 大小写不敏感词边界匹配，最长词优先
- :func:`generate_draft`：LLM 扫描候选 + 合并种子写 draft；draft 已存在时
  新候选写 suggestions 文件，绝不覆盖人工修改
- :func:`review_confirmation` / :func:`write_confirmation`：confirm 的校验
  与落盘（meta.status=confirmed + confirmed_at），交互确认在 cli 适配层
- :func:`load_confirmed_glossary`：US2 convert 的强制闸门（FR-008）；
  glossary.yml 缺失或 status!=confirmed 一律抛
  :class:`~essay_transformer.errors.GlossaryNotConfirmedError`
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import yaml

from . import config
from .errors import GlossaryNotConfirmedError, InputError, LLMError

__all__ = [
    "SCHEMA_VERSION",
    "DRAFT_FILENAME",
    "SUGGESTIONS_FILENAME",
    "CONFIRMED_FILENAME",
    "REQUIRED_SEED_TERMS",
    "Term",
    "GlossaryDoc",
    "Override",
    "FieldDifference",
    "MergeResult",
    "DraftResult",
    "TermMatch",
    "GlossaryMatcher",
    "load_glossary",
    "dump_glossary",
    "merge_glossaries",
    "validate_meta",
    "validate_glossary",
    "generate_draft",
    "review_confirmation",
    "write_confirmation",
    "load_confirmed_glossary",
]

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

DRAFT_FILENAME = "glossary.draft.yml"
SUGGESTIONS_FILENAME = "glossary.draft.suggestions.yml"
CONFIRMED_FILENAME = "glossary.yml"

#: confirm 必须存在的四个种子词（FR-005/SC-001；可由 alias 命中）
REQUIRED_SEED_TERMS: tuple[str, ...] = (
    "self-attention",
    "positional encoding",
    "embedding",
    "softmax",
)

_VALID_MODES = ("bilingual", "zh_only")
_VALID_SCOPES = ("seed", "paper")
_VALID_SOURCES = ("seed", "llm_suggested", "manual")
_VALID_STATUS = ("draft", "confirmed")


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------


@dataclass
class Term:
    """一条术语（glossary-format.md YAML 结构）。"""

    en: str
    zh: str
    mode: str
    scope: str
    source: str
    aliases: list[str] = field(default_factory=list)
    note: str = ""

    def match_keys(self) -> list[str]:
        """匹配键：en + aliases，全部小写归一（大小写不敏感）。"""
        return [self.en.casefold()] + [a.casefold() for a in self.aliases]

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "en": self.en,
            "zh": self.zh,
            "mode": self.mode,
            "scope": self.scope,
            "source": self.source,
        }
        if self.aliases:
            data["aliases"] = list(self.aliases)
        if self.note:
            data["note"] = self.note
        return data

    @classmethod
    def from_dict(cls, raw: Any, *, where: str) -> "Term":
        if not isinstance(raw, dict):
            raise InputError(f"{where}：term 必须是对象，实际为 {type(raw).__name__}")
        en = raw.get("en")
        zh = raw.get("zh")
        mode = raw.get("mode")
        scope = raw.get("scope")
        source = raw.get("source")
        aliases_raw = raw.get("aliases", [])
        if aliases_raw is None:
            aliases_raw = []
        if not isinstance(aliases_raw, list) or not all(
            isinstance(a, str) for a in aliases_raw
        ):
            raise InputError(f"{where}：aliases 必须是字符串数组（en={en!r}）")
        note = raw.get("note", "")
        if note is None:
            note = ""
        return cls(
            en=en if isinstance(en, str) else "",
            zh=zh if isinstance(zh, str) else "",
            mode=mode if isinstance(mode, str) else "",
            scope=scope if isinstance(scope, str) else "",
            source=source if isinstance(source, str) else "",
            aliases=list(aliases_raw),
            note=str(note),
        )


@dataclass
class GlossaryDoc:
    """一份术语表文件（meta + terms）。"""

    meta: dict[str, Any]
    terms: list[Term]
    path: Path | None = None

    @property
    def paper_id(self) -> str:
        value = self.meta.get("paper_id", "")
        return value if isinstance(value, str) else ""

    @property
    def status(self) -> str:
        value = self.meta.get("status", "")
        return value if isinstance(value, str) else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "meta": {
                "paper_id": self.meta.get("paper_id", ""),
                "status": self.meta.get("status", "draft"),
                "confirmed_at": self.meta.get("confirmed_at", "") or "",
                "schema_version": int(self.meta.get("schema_version", SCHEMA_VERSION)),
            },
            "terms": [t.to_dict() for t in self.terms],
        }


@dataclass(frozen=True)
class Override:
    """单篇覆盖种子的一条**译名冲突**记录（confirm 必须打印进冲突摘要）。"""

    seed: Term
    paper: Term


@dataclass(frozen=True)
class FieldDifference:
    """zh/mode 未变、但单篇对种子条目有增补/改写的非译名差异。

    不计入译名冲突摘要；confirm 在"种子覆盖差异"区提示。生效条目以
    单篇为准（aliases 已做大小写不敏感并集、note 采纳非空值）。
    """

    seed: Term
    effective: Term
    added_aliases: list[str]
    note_changed: bool
    en_respelled: bool


@dataclass
class MergeResult:
    """合并结果：生效词表 + 译名冲突覆盖 + 非译名字段差异。"""

    terms: list[Term]
    overrides: list[Override]
    differences: list[FieldDifference] = field(default_factory=list)


@dataclass(frozen=True)
class DraftResult:
    """``glossary draft`` 的产物描述（供 CLI 输出，不掺 UI 逻辑）。"""

    draft_path: Path
    existed: bool
    seed_count: int
    suggested_count: int
    suggestions_path: Path | None
    new_suggestion_count: int


@dataclass(frozen=True)
class TermMatch:
    term: Term
    start: int
    end: int
    matched_text: str


# ---------------------------------------------------------------------------
# YAML 装载 / 写出
# ---------------------------------------------------------------------------


def load_glossary(path: Path | str) -> GlossaryDoc:
    """读取术语表 YAML；不可解析/结构非法抛 InputError（退出码 3）。"""
    path = Path(path)
    if not path.is_file():
        raise InputError(f"术语表文件不存在：{path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise InputError(f"术语表 YAML 无法解析：{path}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise InputError(f"术语表根结构必须是映射对象：{path}")
    meta = raw.get("meta")
    if not isinstance(meta, dict):
        raise InputError(f"术语表缺少 meta 字段：{path}")
    terms_raw = raw.get("terms")
    if not isinstance(terms_raw, list):
        raise InputError(f"术语表 terms 必须是数组：{path}")
    terms = [
        Term.from_dict(item, where=f"{path.name} terms[{idx}]")
        for idx, item in enumerate(terms_raw)
    ]
    return GlossaryDoc(meta=dict(meta), terms=terms, path=path)


def dump_glossary(doc: GlossaryDoc) -> str:
    """渲染为 YAML 文本（键序固定、不转义中文，便于人工编辑）。"""
    return yaml.safe_dump(
        doc.to_dict(), allow_unicode=True, sort_keys=False, default_flow_style=False
    )


def _atomic_write_yaml(path: Path, doc: GlossaryDoc) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(dump_glossary(doc), encoding="utf-8")
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# 合并：seed + 单篇（data-model §3，glossary-format 规则 5/6）
# ---------------------------------------------------------------------------


def _index_seed_layer(terms: list[Term], *, where: str) -> dict[str, Term]:
    index: dict[str, Term] = {}
    for term in terms:
        for key in term.match_keys():
            if key in index and index[key] is not term:
                raise InputError(
                    f"{where}：种子层匹配键 {key!r} 跨条目重复"
                    f"（{index[key].en!r} 与 {term.en!r}）"
                )
            index[key] = term
    return index


def _same_translation(seed_term: Term, other: Term) -> bool:
    """同匹配键条目是否保持种子译名/mode（zh 忽略首尾空白、mode 相同）。"""
    return seed_term.zh.strip() == other.zh.strip() and seed_term.mode == other.mode


def _alias_union(seed_term: Term, paper_term: Term) -> list[str]:
    """aliases 并集：仅在别名序列内部做大小写不敏感去重。

    保留种子已有写法，单篇新增按原写法追加（含与 en 仅大小写不同的人工
    写法——匹配器侧会自然去重，落盘保留人工原写法更忠实）。
    """
    merged: list[str] = []
    seen: set[str] = set()
    for alias in [*seed_term.aliases, *paper_term.aliases]:
        key = alias.casefold()
        if key in seen:
            continue
        seen.add(key)
        merged.append(alias)
    return merged


def _merge_matched_term(seed_term: Term, paper_term: Term) -> Term:
    """同键命中后的生效条目：以单篇为准确认值，不丢种子侧字段。

    - en/zh/mode/scope/source 采纳单篇（保留单篇规范写法与审计痕迹）；
    - aliases 取种子与单篇并集（大小写不敏感去重，CP-2 F-1）；
    - note 采纳非空值：单篇非空用单篇，否则回退种子 note。
    """
    return Term(
        en=paper_term.en,
        zh=paper_term.zh,
        mode=paper_term.mode,
        scope=paper_term.scope,
        source=paper_term.source,
        aliases=_alias_union(seed_term, paper_term),
        note=paper_term.note.strip() or seed_term.note,
    )


def merge_glossaries(seed: GlossaryDoc, paper: GlossaryDoc) -> MergeResult:
    """合并 seed + 单篇（glossary-format 规则 5/6，data-model §3）。

    逐条处理单篇层条目（draft 自包含，带种子参考副本）：

    - en/aliases（大小写不敏感）不命中种子键：单篇新增条目；
    - 命中唯一种子且 **zh 或 mode 不同**：译名冲突，单篇覆盖种子，记入
      :class:`Override`（confirm 译名冲突摘要），生效条目保留种子 aliases
      并集与非空 note；
    - 命中唯一种子、zh/mode 一致但 **en 写法/aliases/note 有增补改写**：
      以合并后的单篇条目生效，记入 :class:`FieldDifference`（不进译名冲突
      摘要，confirm 在种子覆盖差异区提示），杜绝静默丢弃人工新增 alias；
    - 命中且所有字段一致：纯种子参考副本，静默去重；
    - 一个单篇条目同时命中两个不同种子条目属歧义覆盖，抛 InputError。

    其余跨条目重复（含单篇层内部重复、两个单篇条目覆盖同一词）由
    :func:`validate_glossary` 在 confirm 校验时确定性拦截。
    """
    seed_terms = list(seed.terms)
    seed_index = _index_seed_layer(seed_terms, where="seed.yml")

    overrides: list[Override] = []
    differences: list[FieldDifference] = []
    superseded: set[int] = set()  # id() of seed terms removed by paper override
    paper_survivors: list[Term] = []

    for paper_term in paper.terms:
        matched: dict[int, Term] = {}
        for key in paper_term.match_keys():
            seed_term = seed_index.get(key)
            if seed_term is not None and id(seed_term) not in superseded:
                matched[id(seed_term)] = seed_term
        if len(matched) > 1:
            names = ", ".join(sorted(t.en for t in matched.values()))
            raise InputError(
                f"单篇条目 {paper_term.en!r} 的 en/aliases 同时命中多个种子条目"
                f"（{names}），无法确定覆盖关系；请修改后重试。"
            )
        if len(matched) == 1:
            seed_term = next(iter(matched.values()))
            effective = _merge_matched_term(seed_term, paper_term)
            if not _same_translation(seed_term, paper_term):
                # 译名/mode 冲突：种子被替换，单篇合并条目生效
                superseded.add(id(seed_term))
                overrides.append(Override(seed=seed_term, paper=effective))
                paper_survivors.append(effective)
                continue

            # zh/mode 一致：只有非译名字段（en 写法/aliases/note）相异时
            # 才以单篇合并条目生效并提示；无任何差异则为纯副本静默去重，
            # 种子条目原样保留（不加入 superseded）。
            seed_alias_keys = {a.casefold() for a in seed_term.aliases}
            added_aliases = [
                alias
                for alias in effective.aliases
                if alias.casefold() not in seed_alias_keys
            ]
            note_changed = (
                bool(paper_term.note.strip())
                and paper_term.note.strip() != seed_term.note.strip()
            )
            en_respelled = seed_term.en != paper_term.en
            if added_aliases or note_changed or en_respelled:
                superseded.add(id(seed_term))
                differences.append(
                    FieldDifference(
                        seed=seed_term,
                        effective=effective,
                        added_aliases=added_aliases,
                        note_changed=note_changed,
                        en_respelled=en_respelled,
                    )
                )
                paper_survivors.append(effective)
            continue

        paper_survivors.append(paper_term)

    effective = [t for t in seed_terms if id(t) not in superseded] + paper_survivors
    return MergeResult(
        terms=effective, overrides=overrides, differences=differences
    )


# ---------------------------------------------------------------------------
# confirm 校验清单（glossary-format.md；不过项退出码 3）
# ---------------------------------------------------------------------------


def validate_meta(meta: dict[str, Any], *, where: str) -> None:
    problems: list[str] = []
    paper_id = meta.get("paper_id")
    if not isinstance(paper_id, str) or not paper_id.strip():
        problems.append("meta.paper_id 必填且非空")
    status = meta.get("status")
    if status not in _VALID_STATUS:
        problems.append(
            f"meta.status 必须是 {'/'.join(_VALID_STATUS)} 之一（实际 {status!r}）"
        )
    schema_version = meta.get("schema_version", SCHEMA_VERSION)
    if not isinstance(schema_version, int) or schema_version != SCHEMA_VERSION:
        problems.append(f"meta.schema_version 必须为 {SCHEMA_VERSION}（实际 {schema_version!r}）")
    if problems:
        raise InputError(f"{where} meta 校验失败：\n- " + "\n- ".join(problems))


def validate_glossary(
    doc_or_terms: GlossaryDoc | Iterable[Term],
    *,
    require_seed_terms: bool = True,
) -> None:
    """逐条 + 全局校验；聚合全部问题一次性抛 :class:`InputError`。"""
    if isinstance(doc_or_terms, GlossaryDoc):
        terms = list(doc_or_terms.terms)
        where = "术语表"
    else:
        terms = list(doc_or_terms)
        where = "合并词表"

    problems: list[str] = []

    def label(term: Term, idx: int) -> str:
        return term.en if term.en.strip() else f"terms[{idx}]"

    # 逐条字段合法性
    for idx, term in enumerate(terms):
        name = label(term, idx)
        if not isinstance(term.en, str) or not term.en.strip():
            problems.append(f"{name}：en 必填非空")
        if not isinstance(term.zh, str):
            problems.append(f"{name}：zh 必须是字符串")
        elif term.mode == "bilingual" and not term.zh.strip():
            problems.append(f"{name}：mode=bilingual 时 zh 必填非空")
        if term.mode not in _VALID_MODES:
            problems.append(
                f"{name}：mode 必须是 {'/'.join(_VALID_MODES)}（实际 {term.mode!r}）"
            )
        if term.scope not in _VALID_SCOPES:
            problems.append(
                f"{name}：scope 必须是 {'/'.join(_VALID_SCOPES)}（实际 {term.scope!r}）"
            )
        if term.source not in _VALID_SOURCES:
            problems.append(
                f"{name}：source 必须是 {'/'.join(_VALID_SOURCES)}（实际 {term.source!r}）"
            )
        for alias in term.aliases:
            if not isinstance(alias, str) or not alias.strip():
                problems.append(f"{name}：aliases 中存在空值/非字符串项")

    # 全局唯一性：en/aliases 大小写不敏感，跨条目重复即失败
    owner: dict[str, Term] = {}
    for term in terms:
        for key in term.match_keys():
            if not key:
                continue
            previous = owner.get(key)
            if previous is not None and previous is not term:
                problems.append(
                    f"匹配键 {key!r} 跨条目重复（大小写不敏感）："
                    f"{previous.en!r} 与 {term.en!r}"
                )
            else:
                owner[key] = term

    # 四个种子词必须存在（en 或 alias 命中均可）
    if require_seed_terms:
        all_keys = {key for term in terms for key in term.match_keys()}
        missing = [name for name in REQUIRED_SEED_TERMS if name.casefold() not in all_keys]
        if missing:
            problems.append(
                "缺少四个强制种子词（可由 en 或 aliases 命中）："
                + "、".join(missing)
            )

    if problems:
        raise InputError(f"{where}校验失败（退出码 3）：\n- " + "\n- ".join(problems))


# ---------------------------------------------------------------------------
# 术语匹配（glossary-format 规则 1：词边界、大小写不敏感、最长词优先）
# ---------------------------------------------------------------------------


class GlossaryMatcher:
    """对英文自然语言文本做词项匹配。

    每个术语的 ``en`` + ``aliases`` 编成一组词边界正则；全文扫描后按
    "位置升序、词长降序"贪心选取互不重叠命中，因此 ``attention`` 不会
    匹配进 ``attentional``，``self-attention`` 优先于 ``attention``。
    公式 LaTeX / 行内 ``$...$`` 的豁免在 planner/rewrite 输入侧处理，
    本类只负责自然语言文本。
    """

    def __init__(self, terms: Iterable[Term]) -> None:
        self._patterns: list[tuple[Term, re.Pattern[str]]] = []
        for term in terms:
            variants = sorted(
                {term.en, *term.aliases},
                key=len,
                reverse=True,
            )
            variants = [v for v in variants if v.strip()]
            if not variants:
                continue
            pattern = r"\b(?:" + "|".join(re.escape(v) for v in variants) + r")\b"
            self._patterns.append((term, re.compile(pattern, re.IGNORECASE)))

    def find(self, text: str) -> list[TermMatch]:
        hits: list[TermMatch] = []
        for term, pattern in self._patterns:
            for match in pattern.finditer(text):
                hits.append(
                    TermMatch(
                        term=term,
                        start=match.start(),
                        end=match.end(),
                        matched_text=match.group(0),
                    )
                )
        # 同一起点长词优先；跨位置保证区间不重叠
        hits.sort(key=lambda m: (m.start, -(m.end - m.start)))
        selected: list[TermMatch] = []
        cursor = -1
        for hit in hits:
            if hit.start < cursor:
                continue
            selected.append(hit)
            cursor = hit.end
        return selected


# ---------------------------------------------------------------------------
# draft 生成（glossary_draft schema）
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    # 契约：时间一律本地时区
    return datetime.now().isoformat(timespec="seconds")


def _render_prompt(template: str, normalized: dict[str, Any], seed: GlossaryDoc) -> str:
    seed_lines = []
    for term in seed.terms:
        alias_part = f"；变体：{', '.join(term.aliases)}" if term.aliases else ""
        seed_lines.append(f"- {term.en} = {term.zh}（mode={term.mode}{alias_part}）")
    seed_text = "\n".join(seed_lines)
    normalized_text = json.dumps(normalized, ensure_ascii=False, indent=2)
    return (
        template.replace("{{NORMALIZED_JSON}}", normalized_text)
        .replace("{{SEED_TERMS}}", seed_text)
    )


def _llm_term_to_glossary(raw: Any) -> Term:
    if not isinstance(raw, dict):
        raise LLMError(f"glossary_draft.terms[] 项不是对象：{type(raw).__name__}")
    en = raw.get("en")
    zh = raw.get("zh")
    mode = raw.get("mode")
    if not isinstance(en, str) or not en.strip():
        raise LLMError("glossary_draft 返回条目缺少非空 en")
    if not isinstance(zh, str) or not zh.strip():
        raise LLMError(f"glossary_draft 条目 {en!r} 缺少非空 zh")
    if mode not in _VALID_MODES:
        raise LLMError(f"glossary_draft 条目 {en!r} mode 非法：{mode!r}")
    aliases_raw = raw.get("aliases", [])
    if aliases_raw is None:
        aliases_raw = []
    if not isinstance(aliases_raw, list) or not all(
        isinstance(a, str) and a.strip() for a in aliases_raw
    ):
        raise LLMError(f"glossary_draft 条目 {en!r} aliases 必须是字符串数组")
    reason = raw.get("reason", "")
    note = f"建议理由：{reason}" if isinstance(reason, str) and reason.strip() else ""
    return Term(
        en=en.strip(),
        zh=zh.strip(),
        mode=mode,
        scope="paper",
        source="llm_suggested",
        aliases=[a.strip() for a in aliases_raw],
        note=note,
    )


def _doc_keys(doc: GlossaryDoc) -> set[str]:
    return {key for term in doc.terms for key in term.match_keys()}


def generate_draft(
    run_dir: Path | str,
    llm: Any,
    *,
    seed_path: Path | str | None = None,
    prompts_dir: Path | str | None = None,
) -> DraftResult:
    """生成术语表初稿；draft 已存在时新候选落 suggestions，不覆盖人工修改。

    业务模块只依赖 LLMClient Protocol（``complete_json``），真实客户端由
    cli 适配层注入；本函数不构造任何真实客户端。
    """
    run_dir = Path(run_dir)
    seed_path = _resolve_seed_path(seed_path)
    prompts_dir = Path(prompts_dir) if prompts_dir is not None else config.PROMPTS_DIR

    normalized_path = run_dir / "normalized.json"
    if not normalized_path.is_file():
        raise InputError(
            f"缺少 normalized.json：{normalized_path}；请先执行 parse 完成论文解析。"
        )
    try:
        normalized = json.loads(normalized_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"normalized.json 无法读取/解析：{normalized_path}（{exc}）") from exc
    if not isinstance(normalized, dict):
        raise InputError(f"normalized.json 根结构不是对象：{normalized_path}")

    if not seed_path.is_file():
        raise InputError(
            f"种子术语表不存在：{seed_path}；请先创建 glossaries/seed.yml。"
        )
    seed = load_glossary(seed_path)

    system_prompt = (prompts_dir / "system.md").read_text(encoding="utf-8")
    template = (prompts_dir / "glossary_draft.md").read_text(encoding="utf-8")
    user_prompt = _render_prompt(template, normalized, seed)
    response = llm.complete_json(system_prompt, user_prompt, "glossary_draft")
    if not isinstance(response, dict) or response.get("schema_kind") != "glossary_draft":
        raise LLMError(
            "glossary_draft 响应根结构非法：缺少 schema_kind='glossary_draft'"
        )
    raw_terms = response.get("terms")
    if not isinstance(raw_terms, list):
        raise LLMError("glossary_draft 响应缺少 terms 数组")

    paper_id = str(normalized.get("paper_id") or run_dir.name)
    seed_keys = _doc_keys(seed)
    suggestions: list[Term] = []
    seen: set[str] = set()
    for raw in raw_terms:
        term = _llm_term_to_glossary(raw)
        keys = set(term.match_keys())
        # 与种子重复（含 alias、大小写不敏感）的候选不提出（prompt 亦如此要求）
        if keys & seed_keys:
            continue
        # 同次响应内部去重（先出现者为准）
        if keys & seen:
            continue
        seen |= keys
        suggestions.append(term)

    draft_path = run_dir / DRAFT_FILENAME

    # 首次生成：种子全量 + LLM 候选，status=draft
    if not draft_path.is_file():
        draft = GlossaryDoc(
            meta={
                "paper_id": paper_id,
                "status": "draft",
                "confirmed_at": "",
                "schema_version": SCHEMA_VERSION,
            },
            terms=list(seed.terms) + suggestions,
        )
        _atomic_write_yaml(draft_path, draft)
        logger.info("glossary.draft.yml 已写出：%s（%d 种子 + %d 建议）",
                    draft_path, len(seed.terms), len(suggestions))
        return DraftResult(
            draft_path=draft_path,
            existed=False,
            seed_count=len(seed.terms),
            suggested_count=len(suggestions),
            suggestions_path=None,
            new_suggestion_count=0,
        )

    # draft 已存在：绝不覆盖人工修改，仅把新候选写 suggestions 文件
    existing = load_glossary(draft_path)
    existing_keys = _doc_keys(existing)
    fresh = [t for t in suggestions if not (set(t.match_keys()) & existing_keys)]
    suggestions_path: Path | None = None
    if fresh:
        suggestions_path = run_dir / SUGGESTIONS_FILENAME
        suggestions_doc = GlossaryDoc(
            meta={
                "paper_id": paper_id,
                "status": "draft",
                "confirmed_at": "",
                "schema_version": SCHEMA_VERSION,
            },
            terms=fresh,
        )
        _atomic_write_yaml(suggestions_path, suggestions_doc)
        logger.info("glossary.draft.suggestions.yml 已写出：%s（%d 条新候选）",
                    suggestions_path, len(fresh))
    return DraftResult(
        draft_path=draft_path,
        existed=True,
        seed_count=len(seed.terms),
        suggested_count=len(suggestions),
        suggestions_path=suggestions_path,
        new_suggestion_count=len(fresh),
    )


# ---------------------------------------------------------------------------
# confirm：校验 + 落盘
# ---------------------------------------------------------------------------


def _resolve_seed_path(seed_path: Path | str | None) -> Path:
    """None 表示用 config 默认种子路径；调用方也可注入（测试/自定义）。"""
    return Path(seed_path) if seed_path is not None else config.SEED_GLOSSARY_PATH


def _load_seed(seed_path: Path | str | None) -> GlossaryDoc:
    path = _resolve_seed_path(seed_path)
    if not path.is_file():
        # 种子缺失不阻断纯函数级使用（单元测试可注入不存在路径）；draft 生成
        # 路径在 generate_draft 中对种子缺失有显式 InputError 提示。
        return GlossaryDoc(meta={"paper_id": "*", "status": "confirmed"}, terms=[])
    return load_glossary(path)


def review_confirmation(
    run_dir: Path | str,
    *,
    seed_path: Path | str | None = None,
) -> MergeResult:
    """confirm 的校验阶段：装载 seed + draft，跑完整 confirm 清单。

    校验不过（含四种子词缺失、唯一性冲突、bilingual 空 zh 等）抛
    :class:`InputError`（退出码 3）；交互 y/N 由 cli 适配层负责。
    """
    run_dir = Path(run_dir)
    seed = _load_seed(seed_path)
    draft_path = run_dir / DRAFT_FILENAME
    if not draft_path.is_file():
        raise InputError(
            f"未找到术语表初稿：{draft_path}\n"
            "请先执行 `python -m essay_transformer glossary draft <paper_id>`。"
        )
    draft = load_glossary(draft_path)
    validate_meta(draft.meta, where=DRAFT_FILENAME)
    # 文件内字段合法性（四种子词在合并后校验）
    validate_glossary(draft, require_seed_terms=False)
    merged = merge_glossaries(seed, draft)
    validate_glossary(merged.terms, require_seed_terms=True)
    return merged


def write_confirmation(
    run_dir: Path | str,
    merged: MergeResult,
    *,
    paper_id: str | None = None,
) -> Path:
    """校验通过后复制为 glossary.yml，写 status=confirmed 与 confirmed_at。

    条目的 source 不自动改写，保留人工编辑的审计痕迹（data-model §3）。
    """
    run_dir = Path(run_dir)
    pid = paper_id or run_dir.name
    confirmed = GlossaryDoc(
        meta={
            "paper_id": pid,
            "status": "confirmed",
            "confirmed_at": _now_iso(),
            "schema_version": SCHEMA_VERSION,
        },
        terms=list(merged.terms),
    )
    path = run_dir / CONFIRMED_FILENAME
    _atomic_write_yaml(path, confirmed)
    logger.info("glossary.yml 已确认写出：%s（%d 条）", path, len(merged.terms))
    return path


def load_confirmed_glossary(
    run_dir: Path | str,
    *,
    seed_path: Path | str | None = None,
) -> MergeResult:
    """convert 强制闸门（FR-008）：glossary.yml 缺失或未确认一律视为不存在。"""
    run_dir = Path(run_dir)
    path = run_dir / CONFIRMED_FILENAME
    if not path.is_file():
        raise GlossaryNotConfirmedError(
            f"未找到已确认术语表：{path}\n"
            "请依次执行 `glossary draft <paper_id>` → 人工编辑 → "
            "`glossary confirm <paper_id>` 后再运行 convert（FR-008）。"
        )
    doc = load_glossary(path)
    if doc.status != "confirmed":
        raise GlossaryNotConfirmedError(
            f"术语表 {path} 的 meta.status={doc.status!r}，尚未确认；"
            "status!=confirmed 视为不存在，请先执行 `glossary confirm`（FR-008）。"
        )
    seed = _load_seed(seed_path)
    return merge_glossaries(seed, doc)
