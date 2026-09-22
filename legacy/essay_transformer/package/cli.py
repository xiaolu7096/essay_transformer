"""命令行适配层（T017，contracts/cli.md）。

本模块是核心包中**唯一**允许 import argparse 的模块（宪法原则 I，
``tests/unit/test_architecture.py`` AST 守门）：只做参数解析、stdout/stderr
输出、退出码映射与真实客户端构造；业务全部在核心库（ingest/normalize/
glossary/state …），真实 DeepSeek/MinerU 客户端只在此处构造并以 Protocol
实例注入业务函数。

US1 命令：

- ``parse <input>``（--paper-id/--force）：ingest→MinerU→normalize
- ``glossary draft <paper_id>``：种子 + LLM 扫描初稿
- ``glossary confirm <paper_id>``（--yes）：校验 + 交互确认 + 落 confirmed
- ``run <input>``：parse 成功后自动 glossary draft，然后停止并打印人工
  编辑 + confirm + convert 提示；不存在任何绕过术语表确认的路径

US2 命令：

- ``convert <paper_id>``（--from-stage/--allow-missing-example/--jobs）：
  confirmed 术语表闸门 → planner → rewrite → worked example → assemble →
  确定性 QA 闸门；QA failed 退 2 且成稿仅以 ``.unchecked.md`` 留存

凭据（research R13 启动即校验；挂账 L1 闭环）：凡进入需要外部服务的路径，
handler 入口**真实调用** :func:`config.validate_credentials`，缺失即退出码
3 且错误消息含 PowerShell 设置指引；``glossary confirm`` 是纯本地操作，
不校验凭据。
"""

from __future__ import annotations

import argparse
import builtins
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from . import (
    assemble as assemble_mod,
    config,
    glossary as glossary_mod,
    ingest,
    normalize,
    planner as planner_mod,
    qa as qa_mod,
    rewrite as rewrite_mod,
    worked_example as worked_example_mod,
)
from .errors import (
    EssayTransformerError,
    QAFailedError,
    exit_code_for,
)
from .llm import DeepSeekClient
from .mineru_client import HTTPMinerUClient
from .state import JobState

__all__ = ["build_parser", "main"]


# ---------------------------------------------------------------------------
# 参数解析
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m essay_transformer",
        description="论文易读化转换器：parse / glossary draft|confirm / run",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # parse <input>
    p_parse = subparsers.add_parser("parse", help="摄取并解析论文（ingest→MinerU→normalize）")
    p_parse.add_argument("input", help="本地 PDF 路径，或 arXiv abs/pdf URL")
    p_parse.add_argument("--paper-id", dest="paper_id", default=None, help="自定义 paper_id")
    p_parse.add_argument(
        "--force", action="store_true", help="忽略已缓存解析结果重新提交 MinerU"
    )
    p_parse.set_defaults(handler=cmd_parse)

    # glossary draft|confirm
    p_glossary = subparsers.add_parser("glossary", help="开发期术语表编制")
    g_sub = p_glossary.add_subparsers(dest="glossary_command", required=True)

    g_draft = g_sub.add_parser("draft", help="LLM 扫描全文生成术语表初稿")
    g_draft.add_argument("paper_id", help="parse 阶段产出的 paper_id")
    g_draft.set_defaults(handler=cmd_draft)

    g_confirm = g_sub.add_parser("confirm", help="校验并确认人工编辑后的术语表")
    g_confirm.add_argument("paper_id", help="parse 阶段产出的 paper_id")
    g_confirm.add_argument(
        "--yes",
        dest="yes",
        action="store_true",
        help="跳过覆盖冲突的交互复核（不跳过任何校验）",
    )
    g_confirm.set_defaults(handler=cmd_confirm)

    # run <input>
    p_run = subparsers.add_parser(
        "run", help="开发期一键串联：parse 成功后自动 glossary draft，随后停止"
    )
    p_run.add_argument("input", help="本地 PDF 路径，或 arXiv abs/pdf URL")
    p_run.add_argument("--paper-id", dest="paper_id", default=None, help="自定义 paper_id")
    p_run.add_argument(
        "--force", action="store_true", help="忽略已缓存解析结果重新提交 MinerU"
    )
    p_run.set_defaults(handler=cmd_run)

    # convert <paper_id>（US2）
    p_convert = subparsers.add_parser(
        "convert",
        help="confirmed 术语表闸门之后：规划→七段转写→手算示例→组装→QA 闸门",
    )
    p_convert.add_argument("paper_id", help="parse 阶段产出的 paper_id")
    p_convert.add_argument(
        "--from-stage",
        dest="from_stage",
        choices=("planned", "rewritten", "assembled"),
        default=None,
        help="从指定阶段开始重跑（该阶段及其后全部重做）；默认断点续跑",
    )
    p_convert.add_argument(
        "--allow-missing-example",
        dest="allow_missing_example",
        action="store_true",
        help="手算示例两次复算失败时允许留痕降级（成稿带显式缺失说明，"
             "QA worked-example 将判 failed）",
    )
    p_convert.add_argument(
        "--jobs",
        type=int,
        default=config.DEFAULT_JOBS,
        help=f"七段并发转写线程数（硬上限 4，默认 {config.DEFAULT_JOBS}）",
    )
    p_convert.set_defaults(handler=cmd_convert)

    return parser


# ---------------------------------------------------------------------------
# 依赖装配（默认真实客户端；测试经 main() 关键字注入 Fake）
# ---------------------------------------------------------------------------


def _display_path(path: Path) -> str:
    """stdout 展示路径：位于仓库根下时用相对 POSIX 路径。"""
    try:
        return Path(path).resolve().relative_to(config.REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _get_llm(deps: dict[str, Any]):
    client = deps.get("llm_client")
    if client is not None:
        return client
    # 仅真实运行路径构造；构造时读取 DEEPSEEK_API_KEY（缺失即退出码 3）
    return DeepSeekClient()


def _get_mineru(deps: dict[str, Any]):
    client = deps.get("mineru_client")
    if client is not None:
        return client
    return HTTPMinerUClient()


def _mark_failed(run_dir: Path, stage: str, exc: BaseException) -> None:
    """失败落 job_state.json；状态读写本身不得掩盖原始业务异常。

    同时把失败环节注到异常对象上，供 main() 输出"失败环节+可执行建议"
    （contracts/cli.md 退出码 1：错误信息含阶段与建议，T036）。
    """
    try:
        setattr(exc, "stage", stage)
        JobState.load(run_dir).mark_failed(stage, exc)
    except Exception:  # noqa: BLE001 - 落盘失败仅静默，原始异常优先上抛
        pass


#: 失败环节中文名（state.py 十一阶段 → 用户可读，T036）
_STAGE_LABELS: dict[str, str] = {
    "ingested": "摄取（ingest）",
    "parsed": "解析（MinerU）",
    "normalized": "规范化（normalize）",
    "glossary_drafted": "术语表初稿（glossary draft）",
    "glossary_confirmed": "术语表确认（glossary confirm）",
    "planned": "七段规划（planner）",
    "rewritten": "七段转写（rewrite）",
    "example_verified": "手算示例（worked example）",
    "assembled": "成稿组装（assemble）",
    "qa_passed": "确定性 QA 闸门",
    "done": "交付",
}

#: 退出码 1/2 各 error_type 的可执行建议（退出码 3 的消息本身已含指引）
_ERROR_ADVICE: dict[str, str] = {
    "service_timeout": (
        "外部服务超时、限流或暂时不可用；请稍后直接重跑同一命令，"
        "已完成阶段会自动断点复用（FR-032）。"
    ),
    "llm": (
        "请检查 DEEPSEEK_API_KEY、账户额度与网络后直接重跑同一命令，"
        "已完成阶段会自动断点复用（FR-032）。"
    ),
    "parse": (
        "解析服务未返回可用产物；请确认原 PDF 未损坏且包含文本层，"
        "必要时加 --force 重新提交解析。"
    ),
    "qa": (
        "成稿仅以 .unchecked.md 后缀保留供调试；请依据 qa_report.json 中"
        " failed 项修正后重跑 convert。"
    ),
    "input": (
        "请检查磁盘空间、文件权限与 runs/ 目录下的阶段产物后重试。"
    ),
}


def _error_advice(exc: BaseException) -> str | None:
    etype = getattr(exc, "error_type", None)
    return _ERROR_ADVICE.get(etype) if isinstance(etype, str) else None


def _invalidate_stale_dist(run_dir: Path) -> None:
    """重跑 assembled 前让上轮规范名成稿失效（T036：失败不留"成功"输出）。

    规范名 .md 改名为 .unchecked.md；同名陈旧 .unchecked.md 直接清除
    （新运行开始，上轮调试稿不再具有保留意义；qa_report 也会被覆盖）。
    """
    dist_dir = run_dir / assemble_mod.DIST_DIRNAME
    if not dist_dir.is_dir():
        return
    for path in sorted(dist_dir.glob("*.md")):
        if path.name.endswith(".unchecked.md"):
            path.unlink()
            continue
        target = path.with_name(path.name[:-3] + ".unchecked.md")
        if target.exists():
            target.unlink()
        path.rename(target)


def _clear_unchecked_after_pass(run_dir: Path) -> None:
    """QA 通过后清除残留调试名成稿：成功态 dist/ 只允许规范名（T036）。"""
    dist_dir = run_dir / assemble_mod.DIST_DIRNAME
    if not dist_dir.is_dir():
        return
    for path in dist_dir.glob("*.unchecked.md"):
        path.unlink()


def _advance(state: JobState, stage: str) -> None:
    """幂等推进阶段：已到达/超过该阶段（如开发期命令重跑）时不回退重标。

    状态机只允许单调推进；``glossary draft`` 可在 confirm 后重复扫描、
    ``parse``/``run`` 可在已有产物上重跑，重跑只刷新产物，不改变阶段。
    """
    if not state.has_reached(stage):
        state.mark_reached(stage)


# ---------------------------------------------------------------------------
# 业务编排（核心库调用；本层不含业务规则）
# ---------------------------------------------------------------------------


def _parse_core(raw_input: str, paper_id: str | None, force: bool, deps: dict[str, Any]) -> str:
    """ingest→MinerU 解析→normalize，返回 paper_id。"""
    runs_root = deps["runs_dir"]
    info = ingest.ingest(raw_input, paper_id=paper_id, runs_dir=runs_root, force=force)
    run_dir = runs_root / info.paper_id
    state = JobState.load(run_dir, paper_id=info.paper_id)
    _advance(state, "ingested")

    parsed_dir = run_dir / "parsed"
    if force or not state.has_reached("parsed"):
        try:
            client = _get_mineru(deps)
            client.parse(info.pdf_path, parsed_dir)
        except Exception as exc:  # noqa: BLE001 - 统一失败留痕后原样上抛
            _mark_failed(run_dir, "parsed", exc)
            raise
        _advance(state, "parsed")
    try:
        doc = normalize.normalize_run(
            run_dir, source=info.as_dict(), paper_id=info.paper_id
        )
    except Exception as exc:  # noqa: BLE001 - 统一失败留痕后原样上抛
        _mark_failed(run_dir, "normalized", exc)
        raise
    _advance(state, "normalized")

    counts = Counter(block.get("type", "unknown") for block in doc.get("blocks", []))
    print(f"paper_id: {info.paper_id}")
    print(f"parsed: {_display_path(parsed_dir)}")
    print(f"normalized: {_display_path(run_dir / 'normalized.json')}")
    print(f"块数：{sum(counts.values())}（{', '.join(f'{k}={v}' for k, v in sorted(counts.items()))}）")
    print(f"章节数：{len(doc.get('sections', []))}")
    return info.paper_id


def _draft_core(
    paper_id: str,
    deps: dict[str, Any],
    *,
    short_circuit_if_done: bool = False,
) -> glossary_mod.DraftResult:
    """执行术语表初稿生成。

    - 独立 ``glossary draft`` 子命令（cli.md §2）：**每次运行无条件调用**
      ``generate_draft``——首次建 draft，其后只把新候选写 suggestions，
      绝不覆盖人工稿；状态再新也不短路。
    - ``run`` 串联路径：允许按 job_state 断点短路（FR-032），通过
      ``short_circuit_if_done=True`` 显式开启。
    """
    run_dir = deps["runs_dir"] / paper_id
    state = JobState.load(run_dir, paper_id=paper_id)
    draft_path = run_dir / glossary_mod.DRAFT_FILENAME
    if short_circuit_if_done and state.has_reached("glossary_drafted") and draft_path.is_file():
        print(f"术语表初稿已存在，跳过 LLM 扫描（断点续跑）：{_display_path(draft_path)}")
        return glossary_mod.DraftResult(
            draft_path=draft_path,
            existed=True,
            seed_count=0,
            suggested_count=0,
            suggestions_path=None,
            new_suggestion_count=0,
        )

    try:
        llm = _get_llm(deps)
        outcome = glossary_mod.generate_draft(
            run_dir,
            llm,
            seed_path=deps["seed_path"],
            prompts_dir=deps["prompts_dir"],
        )
        # draft 可在 confirm 之后重复执行：只在尚未到达该阶段时推进，避免回退
        _advance(state, "glossary_drafted")
    except Exception as exc:  # noqa: BLE001 - 统一失败留痕后原样上抛
        _mark_failed(run_dir, "glossary_drafted", exc)
        raise

    if not outcome.existed:
        print(f"术语表初稿已生成：{_display_path(outcome.draft_path)}")
        print(f"条目数：种子 {outcome.seed_count} 条，LLM 建议 {outcome.suggested_count} 条")
    else:
        print(f"检测到已有人工修改的初稿，未覆盖：{_display_path(outcome.draft_path)}")
        if outcome.suggestions_path is not None:
            print(
                f"本轮新候选 {outcome.new_suggestion_count} 条已写入："
                f"{_display_path(outcome.suggestions_path)}（请人工挑选合并）"
            )
        else:
            print("本轮无新增候选（候选均已在初稿中）。")
    print("下一步：人工校对增删初稿后执行 "
          f"`python -m essay_transformer glossary confirm {paper_id}`")
    return outcome


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------


def cmd_parse(args: argparse.Namespace, deps: dict[str, Any]) -> int:
    # L1：进入需要 MinerU 的路径，启动即真实校验凭据
    config.validate_credentials(need_mineru=True, need_deepseek=False)
    _parse_core(args.input, args.paper_id, args.force, deps)
    return 0


def cmd_draft(args: argparse.Namespace, deps: dict[str, Any]) -> int:
    # L1：draft 需要 DeepSeek LLM，不需要 MinerU
    config.validate_credentials(need_mineru=False, need_deepseek=True)
    _draft_core(args.paper_id, deps)
    return 0


def cmd_confirm(args: argparse.Namespace, deps: dict[str, Any]) -> int:
    # 纯本地校验 + 文件操作：不调用任何外部服务，因此不校验凭据
    run_dir = deps["runs_dir"] / args.paper_id
    try:
        merged = glossary_mod.review_confirmation(
            run_dir, seed_path=deps["seed_path"]
        )
    except Exception as exc:  # noqa: BLE001 - 统一失败留痕后原样上抛
        _mark_failed(run_dir, "glossary_confirmed", exc)
        raise

    print("术语表校验通过。")

    # 种子覆盖差异：四个强制种子词在生效词表中的命中情况
    key_owner: dict[str, glossary_mod.Term] = {}
    for term in merged.terms:
        for key in term.match_keys():
            key_owner.setdefault(key, term)
    print("强制种子词覆盖：")
    for required in glossary_mod.REQUIRED_SEED_TERMS:
        owner = key_owner.get(required.casefold())
        if owner is not None:
            print(f"  [x] {required} -> {owner.en}（{owner.zh}）")
        else:
            # 理论上不可达：validate_glossary 已拦截四词缺失
            print(f"  [ ] {required} 缺失")

    # 非译名差异：zh/mode 未变，但人工/单篇增补了 alias、note 或改写了 en
    # 写法（glossary-format 规则 5 的种子覆盖差异；不计入译名冲突摘要）
    if merged.differences:
        print(f"种子覆盖差异 {len(merged.differences)} 条（译名未变，已采纳单篇增补）：")
        for diff in merged.differences:
            details: list[str] = []
            if diff.added_aliases:
                details.append(f"新增别名 {', '.join(diff.added_aliases)}")
            if diff.note_changed:
                details.append(f"补注 note：{diff.effective.note}")
            if diff.en_respelled:
                details.append(f"英文规范写法 {diff.seed.en!r} -> {diff.effective.en!r}")
            print(f"  - {diff.seed.en}：{'；'.join(details)}")

    # 种子/单篇译名冲突摘要（glossary-format 规则 5：confirm 必须打印）
    if merged.overrides:
        print(f"种子/单篇覆盖冲突 {len(merged.overrides)} 条（单篇译名生效）：")
        for override in merged.overrides:
            print(
                f"  - {override.seed.en}：种子「{override.seed.zh}」"
                f" -> 单篇「{override.paper.zh}」"
            )
    else:
        print("种子/单篇覆盖冲突：无")

    if not args.yes:
        answer = deps["input_fn"]("确认以上术语表并写入 glossary.yml？[y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            print("已取消确认，未生成 glossary.yml。")
            return 3

    try:
        path = glossary_mod.write_confirmation(run_dir, merged)
        JobState.load(run_dir, paper_id=args.paper_id).mark_reached("glossary_confirmed")
    except Exception as exc:  # noqa: BLE001 - 统一失败留痕后原样上抛
        _mark_failed(run_dir, "glossary_confirmed", exc)
        raise
    print(f"已确认术语表：{_display_path(path)}（status=confirmed，共 {len(merged.terms)} 条）")
    print(f"下一步：python -m essay_transformer convert {args.paper_id}")
    return 0


def cmd_run(args: argparse.Namespace, deps: dict[str, Any]) -> int:
    # run 串联 parse（MinerU）+ draft（DeepSeek）：启动即校验全部凭据
    config.validate_credentials(need_mineru=True, need_deepseek=True)
    paper_id = _parse_core(args.input, args.paper_id, args.force, deps)
    _draft_core(paper_id, deps, short_circuit_if_done=True)
    # cli.md §5：draft 完成后必须停止，交人工闸门，不提供任何绕过选项
    print("")
    print(f"术语表初稿已生成：runs/{paper_id}/glossary.draft.yml")
    print("请人工校对增删，然后依次执行：")
    print(f"  python -m essay_transformer glossary confirm {paper_id}")
    print(f"  python -m essay_transformer convert {paper_id}")
    return 0


def _locate_dist_markdown(run_dir: Path) -> Path | None:
    """在 dist/ 下定位成稿：优先规范名，其次 .unchecked.md。"""
    dist_dir = run_dir / assemble_mod.DIST_DIRNAME
    if not dist_dir.is_dir():
        return None
    clean = sorted(p for p in dist_dir.glob("*.md")
                   if not p.name.endswith(".unchecked.md"))
    if clean:
        return clean[0]
    unchecked = sorted(dist_dir.glob("*.unchecked.md"))
    return unchecked[0] if unchecked else None


def cmd_convert(args: argparse.Namespace, deps: dict[str, Any]) -> int:
    # convert 只调用 DeepSeek，不调用 MinerU
    config.validate_credentials(need_mineru=False, need_deepseek=True)

    run_dir = deps["runs_dir"] / args.paper_id
    state = JobState.load(run_dir, paper_id=args.paper_id)

    # FR-008 强制闸门：glossary.yml 不存在或未确认一律退出码 3，且不落 failed
    merged = glossary_mod.load_confirmed_glossary(
        run_dir, seed_path=deps["seed_path"]
    )

    from_stage: str | None = args.from_stage
    if from_stage is not None:
        state.reset_from(from_stage)
        print(f"已按 --from-stage {from_stage} 回退状态，该阶段及其后将重跑。")

    # T036：本次将重跑组装阶段时，上轮规范名成稿立即失效——任何后续阶段
    # 失败都不得留下"看似成功"的完整输出（planner/rewrite 失败时尤其关键）
    if state.should_execute("assembled", from_stage=from_stage):
        _invalidate_stale_dist(run_dir)

    llm = _get_llm(deps)
    started = time.perf_counter()

    def mark(stage: str) -> None:
        _advance(state, stage)

    # 1/5 planner -----------------------------------------------------------
    if state.should_execute("planned", from_stage=from_stage):
        print("[1/5] 规划七段映射（planner）……")
        try:
            planner_mod.run_planner(
                run_dir, llm, prompts_dir=deps["prompts_dir"]
            )
        except Exception as exc:  # noqa: BLE001 - 统一失败留痕后原样上抛
            _mark_failed(run_dir, "planned", exc)
            raise
        mark("planned")
        print("      plan.json 已生成并通过校验。")

    # 2/5 rewrite -----------------------------------------------------------
    if state.should_execute("rewritten", from_stage=from_stage):
        print(f"[2/5] 并发转写七段（workers={min(args.jobs, 4)}）……")
        try:
            rewrite_mod.run_rewrite(
                run_dir,
                llm,
                terms=merged.terms,
                seed_path=deps["seed_path"],
                prompts_dir=deps["prompts_dir"],
                jobs=args.jobs,
            )
        except Exception as exc:  # noqa: BLE001 - 统一失败留痕后原样上抛
            _mark_failed(run_dir, "rewritten", exc)
            raise
        mark("rewritten")
        print("      sections/<slug>.json 七段全部落盘。")

    # 3/5 worked example ----------------------------------------------------
    example_missing = False
    if state.should_execute("example_verified", from_stage=from_stage):
        print("[3/5] 生成手算小例子并独立复算（worked example）……")
        try:
            outcome = worked_example_mod.run_worked_example(
                run_dir,
                llm,
                prompts_dir=deps["prompts_dir"],
                allow_missing=args.allow_missing_example,
            )
        except Exception as exc:  # noqa: BLE001 - 统一失败留痕后原样上抛
            _mark_failed(run_dir, "example_verified", exc)
            raise
        example_missing = outcome["status"] == "missing"
        # data-model §9：passed，或 allow 留痕 missing，均视为该阶段有显式结论
        mark("example_verified")
        if example_missing:
            print("      警告：手算示例复算失败，已按 --allow-missing-example "
                  "写入 missing 留痕，成稿对应位置为显式缺失说明。")
        else:
            max_diff = outcome["data"].get("verification", {}).get("max_abs_diff")
            print(f"      复算通过（max_abs_diff={max_diff:.3g}）。")

    # 4/5 assemble ----------------------------------------------------------
    markdown_path: Path | None = None
    if state.should_execute("assembled", from_stage=from_stage):
        print("[4/5] 组装单文件 Markdown 成稿（assemble）……")
        try:
            result = assemble_mod.assemble_run(run_dir)
        except Exception as exc:  # noqa: BLE001 - 统一失败留痕后原样上抛
            _mark_failed(run_dir, "assembled", exc)
            raise
        markdown_path = Path(result["markdown_path"])
        mark("assembled")
        print(f"      成稿：{_display_path(markdown_path)}")

    # 5/5 QA ----------------------------------------------------------------
    print("[5/5] 执行 13 项确定性 QA hard checks……")
    try:
        report = qa_mod.run_qa_report(run_dir)
    except QAFailedError as exc:
        _mark_failed(run_dir, "qa_passed", exc)
        if markdown_path is None:
            markdown_path = _locate_dist_markdown(run_dir)
        if markdown_path is not None:
            # 续跑场景下成稿可能已是 .unchecked.md；规范名成稿改名前先
            # 清除同名陈旧调试稿（Windows rename 不允许覆盖已存在目标）
            if markdown_path.name.endswith(".unchecked.md"):
                unchecked = markdown_path
            else:
                target = markdown_path.with_name(
                    markdown_path.name[:-3] + ".unchecked.md"
                )
                if target.exists():
                    target.unlink()
                markdown_path.rename(target)
                unchecked = target
            print(f"QA 未通过，成稿仅以调试名保留：{unchecked.resolve()}",
                  file=sys.stderr)
        raise

    # QA 通过：恢复规范名（若是从 .unchecked.md 续跑），并清除全部残留
    # 调试名成稿——成功态 dist/ 不允许任何 .unchecked.md（T036）
    if markdown_path is None:
        markdown_path = _locate_dist_markdown(run_dir)
    if markdown_path is not None:
        markdown_path = assemble_mod.rename_for_qa(markdown_path, passed=True)
    _clear_unchecked_after_pass(run_dir)
    mark("qa_passed")
    mark("done")

    passed_n = sum(1 for c in report["checks"] if c["status"] == "passed")
    elapsed = time.perf_counter() - started
    print("")
    print(f"QA 全部通过（{passed_n}/{len(report['checks'])}）。")
    if example_missing:
        print("注意：本次成稿的手算小例子为显式缺失留痕（非合格交付态）。")
    print(f"成稿绝对路径：{markdown_path.resolve() if markdown_path else '（未找到）'}")
    print(f"耗时：{elapsed:.1f}s")
    return 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def main(
    argv: list[str] | None = None,
    *,
    llm_client: Any | None = None,
    mineru_client: Any | None = None,
    runs_dir: Path | str | None = None,
    seed_path: Path | str | None = None,
    prompts_dir: Path | str | None = None,
    input_fn: Callable[[str], str] | None = None,
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    deps: dict[str, Any] = {
        "llm_client": llm_client,
        "mineru_client": mineru_client,
        "runs_dir": Path(runs_dir) if runs_dir is not None else config.RUNS_DIR,
        "seed_path": Path(seed_path) if seed_path is not None else config.SEED_GLOSSARY_PATH,
        "prompts_dir": Path(prompts_dir) if prompts_dir is not None else config.PROMPTS_DIR,
        "input_fn": input_fn or builtins.input,
    }
    try:
        return args.handler(args, deps)
    except EssayTransformerError as exc:
        # 进度在 stdout、错误详情在 stderr（cli.md 全局约定）
        code = exit_code_for(exc)
        print(f"错误（退出码 {code}）：{exc}", file=sys.stderr)
        stage = getattr(exc, "stage", None)
        if isinstance(stage, str):
            print(f"失败环节：{_STAGE_LABELS.get(stage, stage)}", file=sys.stderr)
        # 退出码 1/2 追加可执行建议；退出码 3 的异常消息本身已含操作指引
        advice = _error_advice(exc)
        if code in (1, 2) and advice:
            print(f"建议：{advice}", file=sys.stderr)
        return code
    except Exception as exc:  # noqa: BLE001 - 未知异常确定性落到退出码 1
        print(f"错误（退出码 1）：未预期异常 {type(exc).__name__}: {exc}",
              file=sys.stderr)
        stage = getattr(exc, "stage", None)
        if isinstance(stage, str):
            print(f"失败环节：{_STAGE_LABELS.get(stage, stage)}", file=sys.stderr)
        print(
            "建议：这是程序内部错误，请附带对应 runs/<paper_id>/job_state.json "
            "与复现命令反馈；修正后可直接重跑，已完成阶段会自动断点复用。",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
