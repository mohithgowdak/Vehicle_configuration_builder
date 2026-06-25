"""Natural language → structured VehicleConfig.

Primary path: Ollama with JSON-mode. Fallback: regex-based extractor that pulls
`FEATURE: CHOICE` pairs out of the prompt so the demo always runs even when
Ollama isn't installed.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

try:
    from ollama import Client as OllamaClient
except Exception:  # ollama is optional at runtime
    OllamaClient = None  # type: ignore


class VehicleConfig(BaseModel):
    program: str = Field(default="")
    variant: str = Field(default="CGW05T")
    features: dict[str, str] = Field(default_factory=dict)


@dataclass
class ParseResult:
    config: VehicleConfig
    source: str  # "ollama" | "fallback"
    notes: list[str] = field(default_factory=list)


_SYSTEM_PROMPT = (
    "You extract a vehicle ECU configuration from a free-text request. "
    "Return STRICT JSON matching this schema:\n"
    '{"program": str, "variant": str, "features": {FEATURE_NAME: CHOICE}}\n'
    "Rules:\n"
    "- variant looks like CGW05T, CGW06T, etc. Default to CGW05T if not stated.\n"
    "- features come from the user's text only. Do NOT invent values.\n"
    "- FEATURE_NAME and CHOICE should be uppercase, e.g. {\"TIMER LIST\": \"DEFAULT\"}.\n"
    "- If a request is vague, return whatever features are stated and leave the rest out."
)

_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "program": {"type": "string"},
        "variant": {"type": "string"},
        "features": {"type": "object", "additionalProperties": {"type": "string"}},
    },
    "required": ["variant", "features"],
}


def parse_request(
    text: str,
    model: str = "qwen2.5:7b",
    host: str | None = None,
) -> ParseResult:
    cfg, notes = _try_ollama(text, model, host)
    if cfg is not None:
        return ParseResult(config=cfg, source="ollama", notes=notes)
    return ParseResult(config=_fallback_parse(text), source="fallback", notes=notes)


def _try_ollama(text: str, model: str, host: str | None) -> tuple[VehicleConfig | None, list[str]]:
    if OllamaClient is None:
        return None, ["ollama package not installed"]
    try:
        client = OllamaClient(host=host) if host else OllamaClient()
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            format=_JSON_SCHEMA,
            options={"temperature": 0},
        )
        raw = resp["message"]["content"]
        data: dict[str, Any] = json.loads(raw)
        return VehicleConfig.model_validate(data), [f"parsed by {model}"]
    except Exception as exc:
        return None, [f"ollama unavailable: {exc}"]


# ---------- deterministic fallback ----------

_FEATURE_PAT = re.compile(r"^\s*([A-Z][A-Z0-9 \-]{1,30}?)\s*[:=]\s*([A-Z0-9][A-Z0-9 \-_]{0,40})\s*$", re.IGNORECASE)
_VARIANT_PAT = re.compile(r"\bCGW\d{2}[A-Z]\b", re.IGNORECASE)
_KNOWN_FEATURES = {"TIMER LIST", "TRACKWIDTH", "TPM TYPE", "BS", "DRIVE", "STEERPARA", "PROGRAM"}


def _fallback_parse(text: str) -> VehicleConfig:
    variant_match = _VARIANT_PAT.search(text)
    variant = variant_match.group(0).upper() if variant_match else "CGW05T"

    # Strip variant tokens before chunking so they don't get glued onto a feature name.
    cleaned = _VARIANT_PAT.sub(" ", text)
    chunks = re.split(r"[,;\n]", cleaned)

    features: dict[str, str] = {}
    for chunk in chunks:
        m = _FEATURE_PAT.match(chunk.strip(" .:"))
        if not m:
            continue
        feat = _normalize_feature(m.group(1).strip().upper())
        choice = m.group(2).strip().upper()
        if feat in {"PROGRAM", "VARIANT", "ECU"} or not feat:
            continue
        features[feat] = choice

    # Cheap keyword sweeps so casual phrasing still lands something.
    lower = text.lower()
    if "timer list" in lower and "TIMER LIST" not in features:
        for choice in ("default", "short", "long"):
            if choice in lower:
                features["TIMER LIST"] = choice.upper()
                break
    if "left" in lower and "DRIVE" not in features:
        features["DRIVE"] = "LEFT-HAND"
    if "right-hand" in lower and "DRIVE" not in features:
        features["DRIVE"] = "RIGHT-HAND"
    if "esp" in lower and "BS" not in features:
        features["BS"] = "EBS WITH ESP"
    if "tpm2" in lower and "TPM TYPE" not in features:
        features["TPM TYPE"] = "TPM2"

    return VehicleConfig(program="", variant=variant, features=features)


def _normalize_feature(raw: str) -> str:
    """Trim noisy prefixes so 'WITH TIMER LIST' resolves to 'TIMER LIST'."""
    for known in _KNOWN_FEATURES:
        if raw.endswith(known):
            return known
    return raw
