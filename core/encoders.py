"""Deterministic value → hex encoders. NEVER call an LLM from here."""
from __future__ import annotations

from typing import Any

_BYTES_PER_DATATYPE = {"B": 1, "W": 2, "L": 4}


class EncodingError(ValueError):
    pass


def encode(value: Any, datatype: str, encoding: str, rule: dict[str, Any]) -> str:
    encoding = encoding.lower()
    if encoding == "enum":
        return _encode_enum(value, datatype, rule)
    if encoding == "linear":
        return _encode_linear(value, datatype, rule)
    if encoding == "bitfield":
        return _encode_bitfield(value, datatype, rule)
    if encoding == "raw":
        return _format_hex(int(value, 16) if isinstance(value, str) else int(value), datatype)
    raise EncodingError(f"Unknown encoding: {encoding}")


def _encode_enum(value: Any, datatype: str, rule: dict[str, Any]) -> str:
    if value is None:
        raise EncodingError("enum value is None")
    key = str(value).strip().upper()
    table = {str(k).strip().upper(): v for k, v in rule.items()}
    if key not in table:
        raise EncodingError(f"value '{value}' not in enum {list(table)}")
    hex_str = str(table[key]).strip().upper().lstrip("0X")
    width = _BYTES_PER_DATATYPE.get(datatype, 1) * 2
    return hex_str.rjust(width, "0")


def _encode_linear(value: Any, datatype: str, rule: dict[str, Any]) -> str:
    resolution = float(rule.get("resolution", 1.0))
    offset = float(rule.get("offset", 0.0))
    if resolution == 0:
        raise EncodingError("linear resolution is 0")
    physical = float(value)
    raw = round((physical - offset) / resolution)
    return _format_hex(raw, datatype)


def _encode_bitfield(value: Any, datatype: str, rule: dict[str, Any]) -> str:
    # value is iterable of flag names; rule maps flag → bit index
    flags = value if isinstance(value, (list, tuple, set)) else [value]
    bits = 0
    for f in flags:
        key = str(f).strip().upper()
        idx = None
        for k, v in rule.items():
            if str(k).strip().upper() == key:
                idx = int(v)
                break
        if idx is None:
            raise EncodingError(f"bitfield flag '{f}' not in rule {list(rule)}")
        bits |= 1 << idx
    return _format_hex(bits, datatype)


def _format_hex(raw: int, datatype: str) -> str:
    n_bytes = _BYTES_PER_DATATYPE.get(datatype, 1)
    if raw < 0:
        raw &= (1 << (n_bytes * 8)) - 1
    return f"{raw:0{n_bytes * 2}X}"
