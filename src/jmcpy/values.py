"""容错取值。

服务端同一字段的类型并不稳定（``"177"`` 与 ``177``、``null`` 与 ``""`` 混用），
所有解析都经过这里，把「类型噪音」挡在模型之外：拿不到就给默认值，绝不因为
某个字段格式意外而让整个实体解析失败。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = [
    "as_bool",
    "as_int",
    "as_mapping",
    "as_optional_str",
    "as_str",
    "as_str_tuple",
    "pick",
    "text_or_none",
]

_TRUE_WORDS = frozenset({"1", "true", "yes", "on"})
_FALSE_WORDS = frozenset({"0", "false", "no", "off", "", "none", "null"})


def as_int(value: Any, default: int | None = None) -> int | None:
    """转成整数；失败或缺失返回 ``default``。

    ``"12,345"``、``" 42 "`` 这类带分隔符或空白的文本也能解析。
    """
    if value is None or isinstance(value, bool):
        return default if value is None else int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "")
        if not text:
            return default
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                return default
    return default


def as_str(value: Any, default: str = "") -> str:
    """转成字符串；``None`` 返回 ``default``。"""
    if value is None:
        return default
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def as_optional_str(value: Any) -> str | None:
    """转成字符串；``None``/空串/``"null"`` 都算「没有值」。"""
    text = as_str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    return text


def as_bool(value: Any, default: bool = False) -> bool:
    """转成布尔；``"1"``/``"true"``/``1`` 为真，``"0"``/``"false"``/``0`` 为假。"""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text in _TRUE_WORDS:
            return True
        if text in _FALSE_WORDS:
            return False
    return default


def as_str_tuple(value: Any) -> tuple[str, ...]:
    """把标签类字段统一成字符串元组。

    支持 ``["a", "b"]``、``"a b c"``（空格分隔，章节详情用）、``"a,b"``（逗号分隔，网页端用）。
    """
    if value is None:
        return ()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        separator = "," if "," in text else None
        parts = text.split(separator) if separator else text.split()
        return tuple(part.strip() for part in parts if part.strip())
    if isinstance(value, Sequence):
        return tuple(text for item in value if (text := as_str(item).strip()))
    return ()


def as_mapping(value: Any) -> Mapping[str, Any]:
    """确保拿到一个映射，便于继续取字段。"""
    if isinstance(value, Mapping):
        return value
    return {}


def pick(source: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    """按顺序取第一个「有值」的键。"""
    for key in keys:
        if key in source:
            value = source[key]
            if value is not None and value != "":
                return value
    return default


def text_or_none(value: Any) -> str | None:
    """``as_optional_str`` 的别名，语义更贴近「这个字段可能没有」。"""
    return as_optional_str(value)
