#!/usr/bin/env python3
"""Small TOML compatibility layer for the orchestration configuration grammar."""
from __future__ import annotations

import json
import re
from typing import Any, BinaryIO

try:
    import tomllib as _tomllib
except ModuleNotFoundError:  # Python 3.9 and 3.10
    _tomllib = None


class FallbackTOMLDecodeError(ValueError):
    pass


TOMLDecodeError = _tomllib.TOMLDecodeError if _tomllib else FallbackTOMLDecodeError
_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_INTEGER = re.compile(r"^[+-]?(?:0|[1-9](?:_?\d)*)$")
_FLOAT = re.compile(
    r"^[+-]?(?:(?:\d(?:_?\d)*)?\.\d(?:_?\d)*|\d(?:_?\d)*[eE][+-]?\d(?:_?\d)*)$"
)


def _error(line_number: int, message: str) -> FallbackTOMLDecodeError:
    return FallbackTOMLDecodeError(f"line {line_number}: {message}")


def _without_comment(line: str, line_number: int) -> str:
    quote = None
    escaped = False
    result = []
    for character in line:
        if escaped:
            result.append(character)
            escaped = False
            continue
        if quote == '"' and character == "\\":
            result.append(character)
            escaped = True
            continue
        if character in {"'", '"'}:
            if quote is None:
                quote = character
            elif quote == character:
                quote = None
            result.append(character)
            continue
        if character == "#" and quote is None:
            break
        result.append(character)
    if quote is not None:
        raise _error(line_number, "unterminated quoted string")
    return "".join(result).strip()


def _parse_string(value: str, line_number: int) -> str:
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise _error(line_number, f"invalid quoted string: {error.msg}") from error
        if not isinstance(parsed, str):
            raise _error(line_number, "expected a quoted string")
        return parsed
    if value.startswith("'") and value.endswith("'") and len(value) >= 2:
        if "'" in value[1:-1]:
            raise _error(line_number, "single-quoted strings cannot contain a single quote")
        return value[1:-1]
    raise _error(line_number, "invalid quoted string")


def _parse_value(value: str, line_number: int) -> Any:
    if not value:
        raise _error(line_number, "missing value")
    if value[0] in {"'", '"'}:
        return _parse_string(value, line_number)
    if value == "true":
        return True
    if value == "false":
        return False
    if value.startswith("["):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise _error(line_number, f"invalid array: {error.msg}") from error
        if not isinstance(parsed, list) or any(
            not isinstance(item, (str, bool, int, float)) for item in parsed
        ):
            raise _error(line_number, "arrays may contain only strings, booleans, or numbers")
        return parsed
    if _INTEGER.fullmatch(value):
        return int(value.replace("_", ""))
    if _FLOAT.fullmatch(value):
        return float(value.replace("_", ""))
    raise _error(
        line_number,
        "unsupported value syntax; use quoted strings, booleans, integers, or floats",
    )


def fallback_loads(text: str) -> dict[str, Any]:
    if not isinstance(text, str):
        raise TypeError("TOML input must be text")
    root: dict[str, Any] = {}
    current = root
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        line_number = index + 1
        raw_line = lines[index]
        index += 1
        if re.match(r"^[A-Za-z0-9_-]+\s*=\s*\"\"\"", raw_line.strip()):
            line = raw_line.strip()
        else:
            line = _without_comment(raw_line, line_number)
        if not line:
            continue
        if line.startswith("["):
            if not line.endswith("]") or line.startswith("[["):
                raise _error(line_number, "unsupported table syntax")
            section = line[1:-1].strip()
            if not _KEY.fullmatch(section):
                raise _error(line_number, "section names must be simple bare keys")
            if section in root:
                raise _error(line_number, f"duplicate table {section!r}")
            current = {}
            root[section] = current
            continue
        if "=" not in line:
            raise _error(line_number, "expected key = value")
        key, value = (part.strip() for part in line.split("=", 1))
        if not _KEY.fullmatch(key):
            raise _error(line_number, "keys must be simple bare keys")
        if key in current:
            raise _error(line_number, f"duplicate key {key!r}")
        if value.startswith('"""'):
            remainder = value[3:]
            pieces = []
            while '"""' not in remainder:
                pieces.append(remainder)
                if index >= len(lines):
                    raise _error(line_number, "unterminated multiline string")
                remainder = lines[index]
                index += 1
            before, after = remainder.split('"""', 1)
            if after.strip():
                raise _error(index, "content after multiline string is unsupported")
            pieces.append(before)
            if pieces and pieces[0] == "":
                pieces.pop(0)
            current[key] = "\n".join(pieces)
        else:
            current[key] = _parse_value(value, line_number)
    return root


def loads(text: str, *, force_fallback: bool = False) -> dict[str, Any]:
    if _tomllib is not None and not force_fallback:
        return _tomllib.loads(text)
    return fallback_loads(text)


def load(handle: BinaryIO, *, force_fallback: bool = False) -> dict[str, Any]:
    if _tomllib is not None and not force_fallback:
        return _tomllib.load(handle)
    content = handle.read()
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    return fallback_loads(content)
