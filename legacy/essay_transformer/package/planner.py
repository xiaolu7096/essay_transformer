"""处置规划（T022，data-model.md §4，FR-012/014/017/019/020/027）。

planner 是整个转写的唯一决策源：一次 LLM 调用输出 plan.json——

- ``target_order``：原文章节重排为李沐式固定七段；
- ``block_actions``：每个非噪声块的处置动作
  （narrate/condense/fold/drop/keep_figure/keep_table_explained）；
- ``key_formulas``：关键公式定位，恰好 1 条需要手算示例。

两道校验：

1. JSON Schema（:mod:`essay_transformer.llm_schema`，结构契约）；
2. :func:`validate_plan` 确定性业务规则（七段顺序、section 覆盖、
   appendix 限制、action 完备、fold_group 与 reference_list/chart
   归类硬规则、关键公式约束）。

任一不过按服务错误处理：用同一份输入再试一次（共 2 次尝试），仍失败抛
:class:`~essay_transformer.errors.LLMError`（退出码 1），不落 plan.json。

FR-014 尾段抽取：真实论文可能没有独立的相关工作/总评章节，planner 允许用
合成 section id ``<真实节 id>-tail`` / ``<真实节 id>-extract`` 表示
"从该节结尾/抽取的素材"；合成 id 不占用真实节的"恰好一次"覆盖名额，
但其基底必须是真实存在的非 appendix 节。
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

from . import config
from .errors import InputError, LLMError
from .llm_schema import validate_llm_output

__all__ = [
    "SLUG_ORDER",
    "ACTIONS",
    "FOLD_GROUPS",
    "PLAN_FILENAME",
    "SYNTHETIC_SECTION_RE",
    "validate_plan",
    "run_planner",
    "load_plan",
]

logger = logging.getLogger(__name__)

#: 固定七段（顺序即动机优先顺序，FR-012）
SLUG_ORDER: tuple[str, ...] = (
    "tldr",
    "intro",
    "conclusion",
    "related",
    "method",
    "experiments",
    "wrapup",
)

ACTIONS: tuple[str, ...] = (
    "narrate",
    "condense",
    "fold",
    "drop",
    "keep_figure",
    "keep_table_explained",
)

FOLD_GROUPS: tuple[str, ...] = ("derivation", "repro_data", "references")

PLAN_FILENAME = "plan.json"

#: FR-014 合成抽取节 id：<真实节 id>-tail / -extract
SYNTHETIC_SECTION_RE = re.compile(r"^(?P<base>.+)-(?P<kind>tail|extract)$")


# ---------------------------------------------------------------------------
# 产物装载
# ---------------------------------------------------------------------------


def load_plan(run_dir: Path | str) -> dict[str, Any]:
    """读取 plan.json；缺失/损坏抛 InputError（退出码 3，提示先跑规划阶段）。"""
    path = Path(run_dir) / PLAN_FILENAME
    if not path.is_file():
        raise InputError(f"缺少 plan.json：{path}；请先完成 planner 阶段。")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"plan.json 无法读取/解析：{path}（{exc}）") from exc
    if not isinstance(plan, dict):
        raise InputError(f"plan.json 根结构不是对象：{path}")
    return plan


def load_normalized(run_dir: Path | str) -> dict[str, Any]:
    path = Path(run_dir) / "normalized.json"
    if not path.is_file():
        raise InputError(
            f"缺少 normalized.json：{path}；请先执行 parse 完成论文解析。"
        )
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"normalized.json 无法读取/解析：{path}（{exc}）") from exc
    if not isinstance(doc, dict):
        raise InputError(f"normalized.json 根结构不是对象：{path}")
    return doc


# ---------------------------------------------------------------------------
# 确定性业务校验（T019）
# ---------------------------------------------------------------------------


def _index(normalized: dict[str, Any]) -> tuple[dict[str, dict], dict[str, str]]:
    blocks = {b["id"]: b for b in normalized.get("blocks", []) if isinstance(b, dict)}
    owner: dict[str, str] = {}
    for section in normalized.get("sections", []):
        for bid in section.get("blocks", []):
            # reference_list 允许跨节引用；先占者保留（normalize 已保证结构合法）
            owner.setdefault(bid, section["id"])
    return blocks, owner


def _canonicalize_target_order(plan: dict[str, Any]) -> bool:
    """把 target_order 七项按固定 SLUG_ORDER 确定性归位（T040 真实首跑）。

    七段顺序是规格写死的常量（动机优先），模型唯一贡献的是每段的
    source_section_ids/treatment；真实模型会把 conclusion 排到 intro 前，
    让一个本无信息量的排列问题导致整单重试。因此：只要 7 项齐全、slug 无
    重复无缺失，就按 SLUG_ORDER 重排（就地修改）；集合不合法时不动，
    留给 validate_plan 报真实问题（缺段/重复段）。

    返回是否执行了归位。
    """
    target_order = plan.get("target_order")
    if not isinstance(target_order, list) or len(target_order) != 7:
        return False
    entries: dict[str, dict[str, Any]] = {}
    for entry in target_order:
        if not isinstance(entry, dict):
            return False
        slug = entry.get("target_slug")
        if slug not in SLUG_ORDER or slug in entries:
            return False
        entries[slug] = entry
    plan["target_order"] = [entries[slug] for slug in SLUG_ORDER]
    return True


def _repair_plan(plan: dict[str, Any], normalized: dict[str, Any]) -> list[str]:
    """对机械性违规做确定性修复（T040 真实首跑，2026-09-20）。

    真实大模型在 ~150 块、5 张图的真实论文上会稳定漏写个别图片 rationale、
    并自相矛盾地把自己选中的关键公式 fold/drop；这类错误不含语义判断价值，
    靠重试只会"修一处坏一处"。修复只使用 normalized 中的真实证据，可审计、
    不编造；语义性规则（取材覆盖/折叠归类/parse_gap 等）仍由
    :func:`validate_plan` 严格拦截。就地修改 plan，返回修复说明列表。

    1. 图片块（type=figure，含 chart）缺非空 rationale：用该块真实
       mineru_type 与 caption 自动补全，前缀"（自动补全）"标明非模型产出。
    2. 关键公式块（key_formulas 引用）被 fold/drop（违反 FR-017）：强制
       改 narrate 并清除 fold_group——公式入选关键公式即必须上主线。
    """
    notes: list[str] = []
    blocks, _owner = _index(normalized)
    actions = plan.get("block_actions")
    if not isinstance(actions, dict):
        return notes

    # 1. 图片 rationale 自动补全
    for bid, block in blocks.items():
        if block.get("type") != "figure":
            continue
        decision = actions.get(bid)
        if not isinstance(decision, dict):
            continue
        rationale = decision.get("rationale")
        if isinstance(rationale, str) and rationale.strip():
            continue
        mineru_type = block.get("mineru_type") or "image"
        caption = str(block.get("caption") or "").strip().replace("\n", " ")
        action = decision.get("action", "narrate")
        if caption:
            basis = f'mineru_type={mineru_type}、图注"{caption[:40]}"'
        else:
            basis = f"mineru_type={mineru_type}、无图注"
        decision["rationale"] = f"（自动补全）依据{basis}判定：{action}"
        notes.append(f"block_actions[{bid}]：自动补全图片 rationale（FR-027）")

    # 2. 关键公式强制上主线
    key_formulas = plan.get("key_formulas")
    if isinstance(key_formulas, list):
        for kf in key_formulas:
            if not isinstance(kf, dict):
                continue
            bid = kf.get("block_id")
            decision = actions.get(bid) if isinstance(bid, str) else None
            if isinstance(decision, dict) and decision.get("action") in (
                "fold", "drop"
            ):
                decision["action"] = "narrate"
                decision.pop("fold_group", None)
                notes.append(
                    f"block_actions[{bid}]：关键公式自动改 narrate 上主线"
                    "（FR-017，禁止 fold/drop）"
                )
    return notes


def validate_plan(plan: dict[str, Any], normalized: dict[str, Any]) -> list[str]:
    """返回全部业务规则问题（空列表=通过）；不做 JSON Schema 结构检查。"""
    problems: list[str] = []
    blocks, owner = _index(normalized)
    sections = {s["id"]: s for s in normalized.get("sections", [])}
    appendix_ids = set(normalized.get("appendix_section_ids", []))

    # --- target_order：七段固定顺序 ---
    target_order = plan.get("target_order")
    real_usage: dict[str, int] = {sid: 0 for sid in sections if sid not in appendix_ids}

    if not isinstance(target_order, list):
        problems.append("target_order：必须是数组（恰含固定七段）")
    else:
        actual_slugs = [
            t.get("target_slug") if isinstance(t, dict) else None for t in target_order
        ]
        if len(actual_slugs) != 7 or tuple(actual_slugs) != SLUG_ORDER:
            problems.append(
                "target_order：必须恰为固定七段且顺序为 "
                + ",".join(SLUG_ORDER)
                + f"（实际 {actual_slugs}）"
            )

        for idx, entry in enumerate(target_order or []):
            if not isinstance(entry, dict):
                continue
            slug = entry.get("target_slug", f"#{idx}")
            source_ids = entry.get("source_section_ids")
            if not isinstance(source_ids, list):
                continue
            for sid in source_ids:
                if not isinstance(sid, str):
                    continue
                if sid in appendix_ids:
                    problems.append(
                        f"target_order[{slug}]：appendix 节 {sid} 不得进入主线，"
                        "其内容只能 fold 或不映射"
                    )
                    continue
                if sid in sections:
                    real_usage[sid] = real_usage.get(sid, 0) + 1
                    continue
                match = SYNTHETIC_SECTION_RE.match(sid)
                if match and match.group("base") in sections:
                    base = match.group("base")
                    if base in appendix_ids:
                        problems.append(
                            f"target_order[{slug}]：合成抽取 id {sid} 的基底 "
                            f"{base} 是 appendix，不允许进入主线"
                        )
                    # 合成 id 不占用真实节覆盖名额
                    continue
                problems.append(
                    f"target_order[{slug}]：source_section_ids 含未知节 id {sid}"
                    "（非真实节，也不符合 <节id>-tail/-extract 合成规则，FR-014）"
                )

        for sid, count in real_usage.items():
            if count == 0:
                problems.append(f"source section {sid} 未出现在任何 target 段中（要求恰好一次）")
            elif count > 1:
                problems.append(
                    f"source section {sid} 在 target_order 中出现 {count} 次（要求恰好一次；"
                    "尾段抽取请使用 <节id>-tail/-extract 合成 id）"
                )

    # --- block_actions ---
    actions = plan.get("block_actions")
    if not isinstance(actions, dict):
        problems.append("block_actions：必须是对象（每个非噪声块一个 action）")
    else:
        for bid, decision in actions.items():
            if bid not in blocks:
                problems.append(f"block_actions[{bid}]：该块 id 在 normalized 中不存在")
                continue
            if not isinstance(decision, dict):
                problems.append(f"block_actions[{bid}]：必须是对象")
                continue
            action = decision.get("action")
            if action not in ACTIONS:
                problems.append(
                    f"block_actions[{bid}]：非法 action={action!r}，允许值 {ACTIONS}"
                )
                continue
            group = decision.get("fold_group")
            if action == "fold" and group not in FOLD_GROUPS:
                problems.append(
                    f"block_actions[{bid}]：action=fold 必须带 fold_group（{FOLD_GROUPS}）"
                )
            if action != "fold" and group is not None:
                problems.append(
                    f"block_actions[{bid}]：fold_group 只能出现在 action=fold 上"
                    f"（实际 action={action}）"
                )
            # FR-022/023：reference_list 只能进 references 折叠（原文条目由
            # assemble 以 normalized.references 权威渲染），禁止主线转写或丢弃
            if blocks[bid].get("type") == "reference_list" and not (
                action == "fold" and group == "references"
            ):
                problems.append(
                    f"block_actions[{bid}]：reference_list 参考文献块必须 "
                    "action=fold 且 fold_group=references（FR-022/023："
                    "参考文献完整保留在折叠区，不得进主线或 drop）"
                )
            # FR-020：训练曲线/工程图（chart）只能进 repro_data 折叠
            if blocks[bid].get("mineru_type") == "chart" and not (
                action == "fold" and group == "repro_data"
            ):
                problems.append(
                    f"block_actions[{bid}]：训练曲线/工程图（mineru_type=chart）"
                    "必须 action=fold 且 fold_group=repro_data（FR-020）"
                )
            # FR-027：图片（含 chart）的保留/折叠/丢弃决策必须可解释，
            # rationale 随 plan.json 落盘审计（T047）
            if blocks[bid].get("type") == "figure":
                rationale = decision.get("rationale")
                if not isinstance(rationale, str) or not rationale.strip():
                    problems.append(
                        f"block_actions[{bid}]：图片决策必须给非空 rationale"
                        "（依据 caption/上下文说明保留、折叠或丢弃理由，FR-027）"
                    )
            # FR-026（T037）：parse_gap 是解析降级占位块，只能进主线
            # narrate/condense——由组装层在成稿对应位置渲染固定缺失说明，
            # 禁止 fold/drop 造成静默丢失，也不允许 keep_* 伪保留
            if blocks[bid].get("type") == "parse_gap" and action not in (
                "narrate", "condense"
            ):
                problems.append(
                    f"block_actions[{bid}]：parse_gap 解析占位块只能 "
                    "narrate/condense 进主线（成稿对应位置渲染固定缺失说明，"
                    f"FR-026），实际 action={action}"
                )
            # T046（FR-021/022）：appendix 内公式块 fold 时必须进 derivation
            # （完整证明进推导折叠区，不得混入 repro_data/references）
            if (
                blocks[bid].get("type") == "equation"
                and owner.get(bid) in appendix_ids
                and action == "fold"
                and group != "derivation"
            ):
                problems.append(
                    f"block_actions[{bid}]：appendix 节 {owner.get(bid)} 的公式块"
                    " fold 时 fold_group 必须为 derivation（完整证明进推导折叠区，"
                    f"FR-021/022），实际 fold_group={group!r}"
                )
            owned = owner.get(bid)
            if owned in appendix_ids and action not in ("fold", "drop"):
                problems.append(
                    f"block_actions[{bid}]：appendix 节 {owned} 的块只能 fold/drop，"
                    f"不得进入主线（实际 action={action}）"
                )

        for block in normalized.get("blocks", []):
            bid = block.get("id")
            if block.get("type") == "noise":
                continue
            decision = actions.get(bid) if isinstance(actions, dict) else None
            if not isinstance(decision, dict) or "action" not in decision:
                problems.append(f"block_actions[{bid}]：每个非噪声块必须给出 action")

    # --- key_formulas ---
    key_formulas = plan.get("key_formulas")
    required_count = 0
    if not isinstance(key_formulas, list) or not key_formulas:
        problems.append("key_formulas：至少 1 条关键公式")
    else:
        for kf in key_formulas:
            if not isinstance(kf, dict):
                problems.append("key_formulas[]：必须是对象")
                continue
            bid = kf.get("block_id")
            block = blocks.get(bid) if isinstance(bid, str) else None
            if block is None:
                problems.append(f"key_formulas[{bid}]：块 id 不存在")
            elif block.get("type") != "equation":
                problems.append(
                    f"key_formulas[{bid}]：关键公式块必须是 equation 类型"
                    f"（实际 {block.get('type')}）"
                )
            else:
                latex = kf.get("latex")
                if not isinstance(latex, str) or not latex.strip():
                    problems.append(f"key_formulas[{bid}]：latex 必填非空")
                elif latex.strip() != str(block.get("latex", "")).strip():
                    problems.append(
                        f"key_formulas[{bid}]：latex 与原文块不一致"
                        "（公式必须逐字符保持原文，FR-003）"
                    )
            decision = (
                actions.get(bid)
                if isinstance(actions, dict) and isinstance(bid, str)
                else None
            )
            if isinstance(decision, dict) and decision.get("action") in ("drop", "fold"):
                problems.append(
                    f"key_formulas[{bid}]：关键公式不得被 drop/fold（FR-017）"
                )
            if kf.get("worked_example_required") is True:
                required_count += 1
                spec = kf.get("example_spec")
                tokens_ok = (
                    isinstance(spec, dict)
                    and isinstance(spec.get("tokens"), int)
                    and not isinstance(spec.get("tokens"), bool)
                    and 3 <= spec["tokens"] <= 5
                )
                steps_ok = (
                    isinstance(spec, dict)
                    and isinstance(spec.get("steps"), list)
                    and all(isinstance(s, str) and s.strip() for s in spec["steps"])
                )
                if not tokens_ok or not steps_ok:
                    problems.append(
                        f"key_formulas[{bid}]：worked_example_required=true 时必须给"
                        " example_spec（tokens 为 3–5 的整数、steps 为非空字符串数组）"
                    )
        if required_count != 1:
            problems.append(
                f"key_formulas：worked_example_required=true 必须恰好 1 条"
                f"（实际 {required_count} 条，FR-017）"
            )

    return problems


# ---------------------------------------------------------------------------
# LLM 规划
# ---------------------------------------------------------------------------


def _render_prompt(template: str, normalized: dict[str, Any]) -> str:
    return template.replace(
        "{{NORMALIZED_JSON}}",
        json.dumps(normalized, ensure_ascii=False, indent=2),
    )


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def run_planner(
    run_dir: Path | str,
    llm: Any,
    *,
    prompts_dir: Path | str | None = None,
    max_attempts: int = 3,
) -> dict[str, Any]:
    """执行一次规划 LLM 调用 + 双校验 + 落盘 plan.json；返回 plan dict。

    schema/业务校验失败时在本阶段内重试，总尝试次数 ``max_attempts``（默认 3，
    即首调 + 2 次带问题回喂的重试）；全部失败抛 :class:`LLMError`，不产生
    plan.json。七段顺序由 :func:`_canonicalize_target_order` 确定性归位，
    不消耗重试。
    """
    run_dir = Path(run_dir)
    prompts_root = Path(prompts_dir) if prompts_dir is not None else config.PROMPTS_DIR
    normalized = load_normalized(run_dir)

    system_prompt = (prompts_root / "system.md").read_text(encoding="utf-8")
    template = (prompts_root / "planner.md").read_text(encoding="utf-8")
    user_prompt = _render_prompt(template, normalized)

    last_error: Exception | None = None
    plan: dict[str, Any] | None = None
    # 业务校验问题回喂：下一次 complete_json 的 user 文本追加具体问题清单，
    # 让模型定点修正（与 llm 层 schema 错误回喂同一策略，T040 真实首跑）。
    feedback = ""
    for attempt in range(1, max_attempts + 1):
        try:
            response = llm.complete_json(
                system_prompt, user_prompt + feedback, "planner"
            )
            # 注入的 Protocol 实现可能不做 schema 校验（如离线 Fake），这里统一兜底
            validate_llm_output(response, "planner")
        except LLMError as exc:
            last_error = exc
            logger.warning("planner schema 校验失败（第 %d/%d 次）", attempt, max_attempts)
            continue
        # 固定七段顺序不含模型信息量：七项齐全即确定性归位；机械性违规
        # （图片缺 rationale/关键公式被 fold）确定性修复，均不消耗重试
        _canonicalize_target_order(response)
        repairs = _repair_plan(response, normalized)
        for note in repairs:
            logger.info("planner 确定性修复：%s", note)
        problems = validate_plan(response, normalized)
        if not problems:
            plan = response
            break
        last_error = LLMError(
            f"planner 第 {attempt} 次输出未通过确定性校验：\n- "
            + "\n- ".join(problems)
        )
        logger.warning("planner 校验失败（第 %d/%d 次）：%d 处问题",
                       attempt, max_attempts, len(problems))
        feedback = (
            "\n\n## 上一次规划未通过确定性校验（请逐条修正后重新输出完整 JSON）\n- "
            + "\n- ".join(problems)
        )

    if plan is None:
        assert last_error is not None
        raise LLMError(
            f"planner 在 {max_attempts} 次尝试后仍未产出合法 plan.json：{last_error}"
        )

    out_path = run_dir / PLAN_FILENAME
    _atomic_write_json(out_path, plan)
    logger.info("plan.json 已写出：%s", out_path)
    return plan
