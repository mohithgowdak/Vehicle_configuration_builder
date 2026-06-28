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


# ---------- Q&A for decoded .par files ----------

_QA_SYSTEM = (
    "You are an expert automotive ECU configuration analyst. "
    "The user has loaded a .par ECU configuration file. "
    "Here is the fully decoded configuration:\n\n"
    "{context}\n\n"
    "Answer the user's question about this configuration clearly and concisely. "
    "Only use information from the decoded configuration above. "
    "If something is not in the data, say so honestly."
)


def answer_question(
    question: str,
    context_text: str,
    model: str = "qwen2.5:7b",
    host: str | None = None,
) -> tuple[str, str]:
    """Return (answer, source) where source is 'ollama' or 'fallback'."""
    answer = _try_ollama_qa(question, context_text, model, host)
    if answer is not None:
        return answer, "ollama"
    return _fallback_qa(question, context_text), "fallback"


def _try_ollama_qa(question: str, context_text: str, model: str, host: str | None) -> str | None:
    if OllamaClient is None:
        return None
    try:
        client = OllamaClient(host=host) if host else OllamaClient()
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": _QA_SYSTEM.format(context=context_text)},
                {"role": "user", "content": question},
            ],
            options={"temperature": 0.2},
        )
        return resp["message"]["content"].strip()
    except Exception:
        return None


def _fallback_qa(question: str, context_text: str) -> str:
    """Keyword search over the context text when Ollama isn't available."""
    q = question.lower()
    lines = [l for l in context_text.splitlines() if l.strip()]

    # Check for broad queries first
    if any(w in q for w in ("all", "everything", "full", "list", "summary", "explain", "show", "complete")):
        param_lines = [l for l in lines if "hex=" in l]
        if not param_lines:
            return "No decoded parameters available."
        bullets = []
        for l in param_lines:
            # format: "  Feature (qualifier): VALUE  [hex=...]"
            stripped = l.strip()
            colon_idx = stripped.find(":")
            bracket_idx = stripped.find("[")
            if colon_idx > 0 and bracket_idx > 0:
                label = stripped[:colon_idx].strip()
                value = stripped[colon_idx + 1:bracket_idx].strip()
                bullets.append(f"- **{label}**: {value}")
            else:
                bullets.append(f"- {stripped}")
        return "**Full decoded configuration:**\n\n" + "\n".join(bullets)

    if any(w in q for w in ("variant", "ecu", "version", "header", "app", "session", "cbf", "sapi")):
        info_lines = [l for l in lines if "hex=" not in l and l.strip() and not l.startswith("===")]
        if info_lines:
            return "**ECU / Session Information:**\n\n" + "\n".join(f"- {l.strip()}" for l in info_lines)

    # Keyword search across parameter lines
    param_lines = [l for l in lines if "hex=" in l]
    matches = [l for l in param_lines if any(word in l.lower() for word in q.split() if len(word) > 2)]

    if matches:
        result_parts = []
        for l in matches:
            stripped = l.strip()
            colon_idx = stripped.find(":")
            bracket_idx = stripped.find("[")
            if colon_idx > 0 and bracket_idx > 0:
                label = stripped[:colon_idx].strip()
                value = stripped[colon_idx + 1:bracket_idx].strip()
                meta = stripped[bracket_idx:].strip("[]")
                result_parts.append(f"**{label}**\n- Value: **{value}**\n- {meta}")
            else:
                result_parts.append(stripped)
        return "\n\n".join(result_parts)

    return (
        "I couldn't find a specific match. Try:\n"
        "- **'show all'** — full configuration\n"
        "- **'What is the drive side?'** — specific feature\n"
        "- **'What variant is this?'** — ECU / session info"
    )
