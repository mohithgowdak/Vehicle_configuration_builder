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


# ---------- AI query against decoded .par data ----------

_AI_QUERY_SYSTEM = """\
You are an expert automotive ECU configuration analyst.
The user has loaded a .par configuration file. You have access to the full decoded parameter list below.

Each parameter entry is a JSON object with these fields:
  qualifier  – CDD identifier, e.g. "VCD_CGW_HVAC_Control.Fan_Speed"
  field      – last segment of qualifier, e.g. "Fan_Speed"
  hex        – raw hex from .par file
  decimal    – hex converted to integer
  domain     – write-command group, e.g. "CGW_HVAC Control Write"
  fragment   – preset name, e.g. "HVAC: Auto mode"
  feature    – human-readable feature name

DECODED PARAMETERS:
{params_json}

SESSION INFO:
{session_json}

INSTRUCTIONS:
Analyse the user query and return STRICT JSON with this schema:
{{
  "query_type": "keyword_filter" | "part_number" | "show_all" | "session_info" | "general",
  "keywords":   ["word1", ...],
  "matched_qualifiers": ["qualifier1", ...],
  "answer": "human-readable reply (markdown OK)"
}}

Rules:
- "keyword_filter": user is looking for a specific subsystem or feature by name (e.g. "hvac", "timer", "brake", "can").
  → search qualifier, field, domain, fragment for the keywords.
  → populate matched_qualifiers with every qualifier that contains any keyword (case-insensitive).
  → answer should summarise what you found with hex and decimal values.
- "part_number": query contains a part number pattern (letters + dots/digits like A.034.447.29.27 or A0344472927).
  → set matched_qualifiers to [] (app handles part-number lookup separately).
  → answer: "Looking up part number …"
- "show_all": user wants to see everything ("show all", "list all", "all parameters").
  → matched_qualifiers = [] (app will show full table).
  → answer: brief confirmation.
- "session_info": user asks about ECU, variant, version, app name.
  → matched_qualifiers = [].
  → answer using session info above.
- "general": anything else — answer from the parameter data.
  → populate matched_qualifiers if relevant.

IMPORTANT: matched_qualifiers must be exact qualifier strings from the list above. Never invent qualifiers.
"""

_AI_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "query_type":          {"type": "string"},
        "keywords":            {"type": "array",  "items": {"type": "string"}},
        "matched_qualifiers":  {"type": "array",  "items": {"type": "string"}},
        "answer":              {"type": "string"},
    },
    "required": ["query_type", "matched_qualifiers", "answer"],
}


def _is_keyword_search(text: str) -> bool:
    """True only when input looks like a single identifier/keyword.

    Multi-word inputs almost always go to Ollama. Only short single-token
    technical identifiers (clcs_installed, hvac, tpm) stay in Python.
    """
    text = text.strip()
    if "?" in text:
        return False
    words = text.lower().split()
    if len(words) >= 2:
        return False  # multi-word → let Ollama handle it
    question_words = {"what", "how", "why", "which", "when", "where",
                      "is", "are", "can", "does", "do", "tell", "give",
                      "show", "list", "explain", "describe", "find"}
    if words and words[0] in question_words:
        return False
    return True


def ai_query(
    question: str,
    decoded_params: list[dict],
    session_info: dict,
    model: str = "qwen2.5:7b",
    host: str | None = None,
) -> tuple[str, list[str], str, str]:
    """
    Route the query:
    - Keywords / identifiers → Python filter (fast, precise, no hallucination)
    - Natural language questions → Ollama
    Returns (answer, matched_qualifiers, query_type, source).
    """
    # Always use Python for keyword/identifier searches — Ollama is unreliable for filtering
    if _is_keyword_search(question):
        return _fallback_ai_query(question, decoded_params, session_info)

    # Natural language → try Ollama first
    result = _try_ollama_ai_query(question, decoded_params, session_info, model, host)
    if result is not None:
        # Safety: verify Ollama's matched_qualifiers with Python too
        if result.get("query_type") == "keyword_filter":
            result["matched_qualifiers"] = _python_verify_qualifiers(
                question, result["matched_qualifiers"], decoded_params
            )
        return result["answer"], result["matched_qualifiers"], result["query_type"], "ollama"

    return _fallback_ai_query(question, decoded_params, session_info)


def _python_verify_qualifiers(
    question: str,
    candidates: list[str],
    decoded_params: list[dict],
) -> list[str]:
    """Cross-check Ollama's qualifier list with Python to remove hallucinated matches."""
    q = question.lower()
    tokens = [t for t in re.split(r"[\s_]+", q) if len(t) >= 3]
    search_terms = list({q} | set(tokens))
    param_map = {p["qualifier"]: p for p in decoded_params}
    return [
        qname for qname in candidates
        if qname in param_map and all(
            t in " ".join([
                qname.lower(),
                param_map[qname]["field"].lower(),
                param_map[qname].get("domain",   "").lower(),
                param_map[qname].get("fragment", "").lower(),
                param_map[qname].get("feature",  "").lower(),
            ])
            for t in search_terms
        )
    ]


LAST_OLLAMA_ERROR: str = ""   # surfaced to the UI so failures are visible


def _try_ollama_ai_query(
    question: str,
    decoded_params: list[dict],
    session_info: dict,
    model: str,
    host: str | None,
) -> dict | None:
    global LAST_OLLAMA_ERROR
    if OllamaClient is None:
        LAST_OLLAMA_ERROR = "ollama python package not installed (pip install ollama)"
        return None
    try:
        # Keep param payload bounded — most NL questions need at most a few hundred
        # rows of context; sending 5000 rows can stall a 7B model for 60+ seconds.
        MAX_PARAMS = 250
        compact = [
            {
                "qualifier": p["qualifier"],
                "field":     p["field"],
                "hex":       p["hex"],
                "decimal":   p["decimal"],
                "domain":    p.get("domain", ""),
                "fragment":  p.get("fragment", ""),
                "feature":   p.get("feature", ""),
            }
            for p in decoded_params[:MAX_PARAMS]
        ]
        system = _AI_QUERY_SYSTEM.format(
            params_json=json.dumps(compact, indent=2),
            session_json=json.dumps(session_info, indent=2),
        )
        client = OllamaClient(host=host) if host else OllamaClient()
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": question},
            ],
            format=_AI_RESPONSE_SCHEMA,
            options={"temperature": 0},
        )
        data = json.loads(resp["message"]["content"])
        valid_qs = {p["qualifier"] for p in decoded_params}
        data["matched_qualifiers"] = [q for q in data.get("matched_qualifiers", []) if q in valid_qs]
        LAST_OLLAMA_ERROR = ""
        return data
    except Exception as exc:
        LAST_OLLAMA_ERROR = f"{type(exc).__name__}: {exc}"
        return None


def ollama_status(model: str = "qwen2.5:7b", host: str | None = None) -> tuple[bool, str]:
    """Quick ping to see whether Ollama is reachable AND has the model loaded."""
    if OllamaClient is None:
        return False, "ollama package not installed"
    try:
        client = OllamaClient(host=host) if host else OllamaClient()
        models = client.list().get("models", [])
        names = [m.get("name", m.get("model", "")) for m in models]
        if not any(model.split(":")[0] in n for n in names):
            return False, f"connected, but model '{model}' not pulled — run `ollama pull {model}`"
        return True, f"connected · models: {len(names)} · using {model}"
    except Exception as exc:
        return False, f"unreachable: {type(exc).__name__}: {exc}"


_SESSION_INFO_WORDS = {"variant", "ecu", "version", "header", "app", "session", "cbf"}


def _fallback_ai_query(
    question: str,
    decoded_params: list[dict],
    session_info: dict,
) -> tuple[str, list[str], str, str]:
    """Rule-based fallback when Ollama is not available."""
    q = question.lower().strip()
    q_words = set(q.split())

    # session_info
    if q_words & _SESSION_INFO_WORDS:
        lines = [f"- **{k}**: {v}" for k, v in session_info.items()]
        return "**ECU / Session Info:**\n\n" + "\n".join(lines), [], "session_info", "fallback"

    # keyword_filter — search qualifier + field + domain + fragment for each word in the query
    # Use the raw query tokens (handles underscore-joined identifiers like "clcs_installed")
    # Split on spaces AND underscores so "clcs_installed" becomes ["clcs", "installed"]
    tokens = [t for t in re.split(r"[\s_]+", q) if len(t) >= 3]
    # Also keep the original unsplit query in case it matches verbatim
    search_terms = list({q} | set(tokens))

    matched = [
        p for p in decoded_params
        if all(
            term in " ".join([
                p["qualifier"].lower(),
                p["field"].lower(),
                p.get("domain",   "").lower(),
                p.get("fragment", "").lower(),
                p.get("feature",  "").lower(),
            ])
            for term in search_terms
        )
    ]

    if matched:
        lines = [
            f"**{p['field']}**\n"
            f"- Qualifier: `{p['qualifier']}`\n"
            f"- Hex: `{p['hex']}` → Decimal: **{p['decimal']}**"
            + (f"\n- Domain: {p['domain']}"   if p.get("domain")   else "")
            + (f"\n- Fragment: {p['fragment']}" if p.get("fragment") else "")
            for p in matched
        ]
        answer = f"**{len(matched)} parameter(s) matching `{question}`:**\n\n" + "\n\n".join(lines)
        return answer, [p["qualifier"] for p in matched], "keyword_filter", "fallback"

    return (
        f"No parameters found matching `{question}`.\n\n"
        "Try: **'show all'**, a part number, or a subsystem name like **'hvac'**, **'brake'**, **'timer'**.",
        [], "general", "fallback"
    )


# Keep old answer_question for backward compat — now wraps ai_query with plain context
def answer_question(
    question: str,
    context_text: str,
    model: str = "qwen2.5:7b",
    host: str | None = None,
) -> tuple[str, str]:
    """Legacy wrapper — used when decoded_params aren't available."""
    if OllamaClient is None:
        return _fallback_ai_query(question, [], {})[0], "fallback"
    try:
        client = OllamaClient(host=host) if host else OllamaClient()
        resp = client.chat(
            model=model,
            messages=[
                {"role": "system", "content": (
                    "You are an automotive ECU configuration analyst. "
                    "Answer using only the data below.\n\n" + context_text
                )},
                {"role": "user", "content": question},
            ],
            options={"temperature": 0.1},
        )
        return resp["message"]["content"].strip(), "ollama"
    except Exception:
        return _fallback_ai_query(question, [], {})[0], "fallback"
