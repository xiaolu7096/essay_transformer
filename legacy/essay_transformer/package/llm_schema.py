"""LLM 输出的完整 JSON Schema 校验（L2 挂账闭环，T022 起各业务模块共用）。

宪法白名单只允许五个第三方库（requests/openai/pyyaml/numpy/pytest），不能引入
``jsonschema``，因此本模块实现契约 ``llm-output.schema.json`` 实际用到的
JSON Schema（draft 2020-12）**子集**解释器：

    type / const / enum / required / properties / additionalProperties
    items / minItems / maxItems / minLength / minimum / maximum
    oneOf / allOf / if-then / $ref（仅 ``#/$defs/...``）

契约 schema 原样入包（``essay_transformer/llm_output.schema.json``），由
守门测试保证与 specs 下契约逐字节一致，杜绝"校验规则与契约漂移"。

- :func:`validate_llm_output` 供 :class:`~essay_transformer.llm.DeepSeekClient`
  在每次返回后调用，也供 planner/rewrite/worked_example 对注入的 Protocol
  实现（含离线 Fake）做一致校验；不符一律抛 :class:`~essay_transformer.errors.LLMError`
  （按服务错误处理，触发重试）。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from .errors import LLMError

__all__ = ["SCHEMA_PATH", "load_schema", "validate_llm_output", "validate_instance"]

SCHEMA_PATH = Path(__file__).resolve().parent / "llm_output.schema.json"

#: schema_kind -> schema 根 oneOf 中的 $def 名
_DEF_FOR_KIND: dict[str, str] = {
    "planner": "PlannerOutput",
    "glossary_draft": "GlossaryDraftOutput",
    "section_rewrite": "SectionRewriteOutput",
    "worked_example": "WorkedExampleOutput",
}


@lru_cache(maxsize=1)
def load_schema() -> dict[str, Any]:
    """读取入包契约 schema（只读一次）。"""
    data = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise LLMError(f"入包 LLM 输出 schema 根结构非法：{SCHEMA_PATH}")
    return data


def schema_directive_for_kind(schema_kind: str) -> str:
    """返回可直接拼进 LLM user prompt 的契约指令文本（真实 API 修复，T040）。

    DeepSeek ``json_object`` 模式只保证合法 JSON、不保证字段结构；真实模型
    会自造字段名（section_id/goal）、把 object 输出成 array。把完整契约
    schema（约 9KB）随每次请求注入，并指明根对象必须符合哪个 ``$def``，
    让"字段名/类型/必填/额外字段禁止"在首次调用时就无歧义。
    """
    def_name = _DEF_FOR_KIND.get(schema_kind)
    if def_name is None:
        raise LLMError(
            f"未知 schema_kind={schema_kind!r}，允许值：{', '.join(_DEF_FOR_KIND)}"
        )
    schema_text = json.dumps(load_schema(), ensure_ascii=False, indent=2)
    return (
        "## 输出契约（硬性，违反将被拒绝并重试）\n"
        f"只输出一个 JSON 对象，该对象必须严格符合下面 JSON Schema 中 "
        f"`$defs/{def_name}` 的定义（顶层必填 `schema_kind` 字段且值为 "
        f'"{schema_kind}"）：字段名、类型、枚举值逐字遵守；未在 schema '
        "中声明的字段一律禁止；不要输出 schema 之外的任何解释文字。\n\n"
        f"```json\n{schema_text}\n```"
    )


def validate_llm_output(data: Any, schema_kind: str) -> None:
    """按 schema_kind 对应 $def 完整校验 LLM 返回；聚合全部问题后抛 LLMError。"""
    def_name = _DEF_FOR_KIND.get(schema_kind)
    if def_name is None:
        raise LLMError(
            f"未知 schema_kind={schema_kind!r}，允许值：{', '.join(_DEF_FOR_KIND)}"
        )
    root = load_schema()
    defs = root.get("$defs", {})
    schema = defs.get(def_name)
    if not isinstance(schema, dict):
        raise LLMError(f"契约 schema 缺少 $defs/{def_name}：{SCHEMA_PATH}")
    problems = validate_instance(data, schema, defs=defs, path="$")
    if problems:
        raise LLMError(
            f"{schema_kind} 输出不符合 JSON Schema 契约（{len(problems)} 处）：\n- "
            + "\n- ".join(problems)
        )


# ---------------------------------------------------------------------------
# 子集解释器
# ---------------------------------------------------------------------------

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    # JSON Schema：bool 不是 integer/number
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
}


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _resolve_ref(ref: str, defs: dict[str, Any]) -> dict[str, Any]:
    prefix = "#/$defs/"
    if not ref.startswith(prefix):
        raise LLMError(f"契约 schema 使用了不支持的 $ref：{ref!r}（仅支持 #/$defs/*）")
    target = defs.get(ref[len(prefix):])
    if not isinstance(target, dict):
        raise LLMError(f"契约 schema 的 $ref 目标不存在：{ref!r}")
    return target


def validate_instance(
    instance: Any,
    schema: dict[str, Any],
    *,
    defs: dict[str, Any],
    path: str,
) -> list[str]:
    """按子集规则校验，返回人类可读的问题路径列表（空列表=通过）。"""
    problems: list[str] = []

    # $ref 先解析
    if "$ref" in schema:
        schema = _resolve_ref(str(schema["$ref"]), defs)

    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_TYPE_CHECKS.get(t, lambda _v: False)(instance) for t in allowed):
            problems.append(
                f"{path}：类型应为 {('/'.join(allowed))}，实际为 {_type_name(instance)}"
            )
            # 类型不符后不再做结构内检查，避免噪音级联
            return problems

    if "const" in schema and instance != schema["const"]:
        problems.append(f"{path}：必须恒为 {schema['const']!r}，实际为 {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        problems.append(f"{path}：值 {instance!r} 不在枚举 {schema['enum']!r} 中")

    if isinstance(instance, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in instance:
                problems.append(f"{path}：缺少必填字段 {key!r}")

        properties = schema.get("properties", {})
        for key, subschema in properties.items():
            if key in instance:
                problems.extend(
                    validate_instance(
                        instance[key], subschema, defs=defs, path=f"{path}.{key}"
                    )
                )

        additional = schema.get("additionalProperties")
        if additional is False:
            for key in instance:
                if key not in properties:
                    problems.append(f"{path}：出现契约外字段 {key!r}")
        elif isinstance(additional, dict):
            for key, value in instance.items():
                if key not in properties:
                    problems.extend(
                        validate_instance(
                            value, additional, defs=defs, path=f"{path}.{key}"
                        )
                    )

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            problems.append(
                f"{path}：数组长度 {len(instance)} < minItems {schema['minItems']}"
            )
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            problems.append(
                f"{path}：数组长度 {len(instance)} > maxItems {schema['maxItems']}"
            )
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for idx, item in enumerate(instance):
                problems.extend(
                    validate_instance(
                        item, item_schema, defs=defs, path=f"{path}[{idx}]"
                    )
                )

    if isinstance(instance, str) and "minLength" in schema:
        if len(instance) < schema["minLength"]:
            problems.append(
                f"{path}：字符串长度 {len(instance)} < minLength {schema['minLength']}"
            )

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            problems.append(f"{path}：{instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            problems.append(f"{path}：{instance} > maximum {schema['maximum']}")

    # 组合规则
    for branch in schema.get("allOf", []):
        problems.extend(
            validate_instance(instance, branch, defs=defs, path=path)
        )

    if "oneOf" in schema:
        matched = 0
        for branch in schema["oneOf"]:
            if not validate_instance(instance, branch, defs=defs, path=path):
                matched += 1
        if matched != 1:
            problems.append(
                f"{path}：oneOf 要求恰好匹配 1 个分支，实际匹配 {matched} 个"
            )

    if "if" in schema:
        cond = schema["if"]
        if not validate_instance(instance, cond, defs=defs, path=path):
            then_schema = schema.get("then")
            if isinstance(then_schema, dict):
                problems.extend(
                    validate_instance(instance, then_schema, defs=defs, path=path)
                )

    if "not" in schema:
        # not 下的子模式必须**不**匹配
        nested = validate_instance(instance, schema["not"], defs=defs, path=path)
        if not nested:
            problems.append(f"{path}：命中了声明为 not 的子模式 {schema['not']!r}")

    return problems
