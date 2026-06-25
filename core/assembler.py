"""Emit S / H / P lines into a .par file body."""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .data_loader import ParHeader


def assemble_par(header: ParHeader, p_lines: Iterable[str], ecu_variant: str | None = None) -> str:
    s_lines = list(header.session_lines)
    if ecu_variant:
        s_lines = _override_or_append(s_lines, "S,ECU,", f"S,ECU,{ecu_variant}")

    h_lines = list(header.header_lines)
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S") + "000"
    h_lines = _override_or_append(h_lines, "H,TIMESTAMP,", f"H,TIMESTAMP,{ts}")

    body = s_lines + h_lines + list(p_lines)
    return "\r\n".join(body) + "\r\n"


def _override_or_append(lines: list[str], prefix: str, new_line: str) -> list[str]:
    out = [ln for ln in lines if not ln.startswith(prefix)]
    out.append(new_line)
    return out


def format_p_line(qualifier: str, datatype: str, hex_value: str) -> str:
    return f"P,{qualifier},{datatype},{hex_value}"
