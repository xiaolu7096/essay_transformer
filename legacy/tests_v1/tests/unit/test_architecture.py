"""架构边界守门测试（T010，宪法原则 I，research R16）。

除 ``cli.py`` 适配层外，核心包任何模块禁止 import argparse 或任何
UI/Web/服务框架；以 AST 扫描固化，不依赖口头约定。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_DIR = REPO_ROOT / "essay_transformer"

#: 禁止出现在核心库（cli.py 除外）的顶层模块名
FORBIDDEN_TOP_LEVEL = {
    "argparse",
    "flask",
    "fastapi",
    "django",
    "tornado",
    "gradio",
    "streamlit",
    "uvicorn",
    "starlette",
    "nicegui",
}


def _core_python_files() -> list[Path]:
    files = sorted(PACKAGE_DIR.glob("*.py"))
    assert files, f"未在 {PACKAGE_DIR} 找到核心模块"
    return [p for p in files if p.name != "cli.py"]


def _imported_top_level_modules(path: Path) -> list[tuple[str, int]]:
    """返回 (顶层模块名, 行号) 列表，覆盖 import x / from x import y。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name.split(".")[0], node.lineno))
        elif isinstance(node, ast.ImportFrom):
            # 相对导入（level>0）不可能是第三方 UI 框架
            if node.level == 0 and node.module:
                found.append((node.module.split(".")[0], node.lineno))
    return found


@pytest.mark.parametrize(
    "path",
    _core_python_files(),
    ids=lambda p: p.relative_to(REPO_ROOT).as_posix(),
)
def test_core_module_has_no_ui_or_cli_imports(path: Path) -> None:
    violations = [
        (name, lineno)
        for name, lineno in _imported_top_level_modules(path)
        if name in FORBIDDEN_TOP_LEVEL
    ]
    assert not violations, (
        f"{path.relative_to(REPO_ROOT)} 出现禁止的 import（宪法原则 I，仅 cli.py 允许）："
        + ", ".join(f"{name}@line {lineno}" for name, lineno in violations)
    )


def test_cli_is_the_only_allowed_adapter() -> None:
    """cli.py 是白名单出口：存在性被其他测试隐式依赖，这里固化命名约定。"""
    # cli.py 在 Phase 3（T017）才创建；Phase 2 阶段只确认扫描排除逻辑使用固定名
    excluded = {p.name for p in PACKAGE_DIR.glob("*.py")} - {"cli.py"}
    assert "errors.py" in excluded
    assert "config.py" in excluded


def _repo_python_sources() -> list[Path]:
    """受版本控制的 Python 源码：核心包 + tests + 根目录脚本。

    显式排除 .pytest_cache / runs / output / .venv 等非源码目录。
    """
    include_dirs = [PACKAGE_DIR, REPO_ROOT / "tests"]
    files: list[Path] = []
    for base in include_dirs:
        files.extend(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)
    files.extend(REPO_ROOT.glob("*.py"))
    return sorted(set(files))


def test_no_hardcoded_api_tokens_in_repo_sources() -> None:
    """T038/CP-6：仓库 Python 源码中禁止任何明文 API key（凭据只能来自环境变量）。

    旧的根目录 parse_paper.py 已删除，此守卫视察全部现存源码，防止 token
    以任何形式回流（含测试与未来新增脚本）。
    """
    token_re = re.compile(r"sk-[A-Za-z0-9]{16,}")
    offenders: list[str] = []
    for path in _repo_python_sources():
        for lineno, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if token_re.search(line):
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{lineno}")
    assert not offenders, "发现硬编码 API key（必须改用环境变量）：" + ", ".join(offenders)


def test_core_modules_use_no_interactive_getpass() -> None:
    """T038 挂账 L4：交互式凭据兜底（getpass）禁止迁入核心包——核心库必须
    可在无交互环境（测试/未来服务形态）中直接驱动，凭据只走环境变量。"""
    offenders: list[str] = []
    for path in _core_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import) and any(
                alias.name == "getpass" for alias in node.names
            ):
                offenders.append(f"{path.name}:{node.lineno}")
            elif isinstance(node, ast.ImportFrom) and node.module == "getpass":
                offenders.append(f"{path.name}:{node.lineno}")
    assert not offenders, "核心包禁止 getpass 交互输入：" + ", ".join(offenders)


def test_legacy_parse_paper_script_removed() -> None:
    """T038：逻辑已全部迁入 mineru_client.py，根目录一次性脚本必须删除。"""
    assert not (REPO_ROOT / "parse_paper.py").exists(), (
        "parse_paper.py 已由 essay_transformer/mineru_client.py 取代，不应保留"
    )
