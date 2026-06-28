"""Load the three input datasets into in-memory structures.

Real CDD XML parsing is out of scope for this demo — if CDD_XLSX is provided we
treat it as a flat codebook with the columns:

    qualifier, datatype, encoding, rule

`encoding` is one of: enum | linear | bitfield
`rule`     is a JSON string holding the encoder parameters, e.g.
             enum     -> {"DEFAULT": "4B", "LONG": "FF"}
             linear   -> {"resolution": 0.04167, "offset": 0}
             bitfield -> {"ESP": 0, "ABS": 1}

If neither CDD_XLSX nor CDD_XML resolves, we fall back to a tiny built-in
codebook so the UI demo still works end-to-end.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd


@dataclass
class ParamSpec:
    qualifier: str
    datatype: str          # B | W | L | A
    encoding: str          # enum | linear | bitfield | raw
    rule: dict[str, Any]


@dataclass
class ParHeader:
    session_lines: list[str]
    header_lines: list[str]
    default_params: list[str]


# ---------- Part Number Dataset ----------

def load_part_numbers(path: Path | None) -> pd.DataFrame:
    if path and path.exists():
        df = pd.read_excel(path)
    else:
        df = pd.DataFrame(_FALLBACK_PART_ROWS)
    df.columns = [c.strip() for c in df.columns]
    return df


def variants(df: pd.DataFrame) -> list[str]:
    col = _find_col(df, ["ECU Qualifier", "ECU_Qualifier", "Variant"])
    if not col:
        return []
    return sorted({str(v).strip() for v in df[col].dropna() if str(v).strip()})


def parameters_for_variant(df: pd.DataFrame, variant: str) -> pd.DataFrame:
    ecu_col = _find_col(df, ["ECU Qualifier", "ECU_Qualifier", "Variant"])
    type_col = _find_col(df, ["Type"])
    if not ecu_col:
        return df.head(0)
    sub = df[df[ecu_col].astype(str).str.strip() == variant]
    if type_col:
        sub = sub[sub[type_col].astype(str).str.upper().str.strip() == "PARAMETER"]
    return sub.reset_index(drop=True)


# ---------- CDD codebook ----------

def load_cdd(xlsx: Path | None, xml: Path | None) -> dict[str, ParamSpec]:
    if xlsx and xlsx.exists():
        return _cdd_from_xlsx(xlsx)
    if xml and xml.exists():
        # Real XML parsing is a Day-1 task per the spec. For demo, log + fall back.
        return _FALLBACK_CDD
    return _FALLBACK_CDD


def _cdd_from_xlsx(path: Path) -> dict[str, ParamSpec]:
    df = pd.read_excel(path)
    df.columns = [c.strip().lower() for c in df.columns]
    out: dict[str, ParamSpec] = {}
    for _, row in df.iterrows():
        q = str(row.get("qualifier", "")).strip()
        if not q:
            continue
        rule_raw = row.get("rule", "{}")
        try:
            rule = json.loads(rule_raw) if isinstance(rule_raw, str) else dict(rule_raw)
        except json.JSONDecodeError:
            rule = {}
        out[q] = ParamSpec(
            qualifier=q,
            datatype=str(row.get("datatype", "B")).strip().upper(),
            encoding=str(row.get("encoding", "raw")).strip().lower(),
            rule=rule,
        )
    return out


# ---------- Reference .par ----------

def load_reference_par(path: Path | None) -> ParHeader:
    if not path or not path.exists():
        return ParHeader(
            session_lines=[
                "S,ECU,CGW05T",
                "S,DIAGNOSISVARIANT,CGW05T_App_1014",
            ],
            header_lines=[
                "H,APPNAME,Drumroll",
                "H,APPVERSION,8.22.6146.14",
                "H,SAPIVERSION,1.32.888.6",
                "H,CBFVERSION,04.03.80",
                "H,QUALIFIERFORMAT,MCD",
            ],
            default_params=[
                "P,VCD_CGW_BKAM_Timer.Timeout_Value,B,4B",
                "P,VCD_CGW_TrackWidth.Value,W,09FE",
                "P,VCD_CGW_TPM.Type,B,02",
                "P,VCD_CGW_BrakeSystem.Config,B,03",
                "P,VCD_CGW_Drive.Side,B,00",
                "P,VCD_CGW_Steering.Variant,B,11",
            ],
        )
    text = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    s, h, p = [], [], []
    for line in text:
        ln = line.strip()
        if not ln:
            continue
        head = ln.split(",", 1)[0]
        if head == "S":
            s.append(ln)
        elif head == "H":
            h.append(ln)
        elif head == "P":
            p.append(ln)
    return ParHeader(session_lines=s, header_lines=h, default_params=p)


# ---------- helpers ----------

def _find_col(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lookup = {c.lower(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in lookup:
            return lookup[cand.lower()]
    return None


# ---------- fallback fixtures (from spec §11) ----------

_FALLBACK_PART_ROWS = [
    {"Partnumber": "A.034.447.29.27", "Nomenclature": "TIMER LIST: DEFAULT",      "ECU Qualifier": "CGW05T", "Type": "PARAMETER"},
    {"Partnumber": "A.033.447.10.27", "Nomenclature": "TRACKWIDTH: 2550",         "ECU Qualifier": "CGW05T", "Type": "PARAMETER"},
    {"Partnumber": "A.033.447.21.27", "Nomenclature": "TPM TYPE: TPM2",           "ECU Qualifier": "CGW05T", "Type": "PARAMETER"},
    {"Partnumber": "A.033.447.68.27", "Nomenclature": "STEERPARA-EVO-RF",         "ECU Qualifier": "CGW05T", "Type": "PARAMETER"},
    {"Partnumber": "A.034.447.41.27", "Nomenclature": "BS: EBS WITH ESP",         "ECU Qualifier": "CGW05T", "Type": "PARAMETER"},
    {"Partnumber": "A.034.447.96.27", "Nomenclature": "DRIVE: LEFT-HAND",         "ECU Qualifier": "CGW05T", "Type": "PARAMETER"},
]

_FALLBACK_CDD: dict[str, ParamSpec] = {
    "VCD_CGW_BKAM_Timer.Timeout_Value": ParamSpec(
        qualifier="VCD_CGW_BKAM_Timer.Timeout_Value",
        datatype="B", encoding="enum",
        rule={"DEFAULT": "4B", "SHORT": "1E", "LONG": "78"},
    ),
    "VCD_CGW_BKAM_Timer.CreateReset": ParamSpec(
        qualifier="VCD_CGW_BKAM_Timer.CreateReset",
        datatype="B", encoding="enum",
        rule={"DEFAULT": "FF", "OFF": "00"},
    ),
    "VCD_CGW_TrackWidth.Value": ParamSpec(
        qualifier="VCD_CGW_TrackWidth.Value",
        datatype="W", encoding="linear",
        rule={"resolution": 1.0, "offset": 0},
    ),
    "VCD_CGW_TPM.Type": ParamSpec(
        qualifier="VCD_CGW_TPM.Type",
        datatype="B", encoding="enum",
        rule={"NONE": "00", "TPM1": "01", "TPM2": "02"},
    ),
    "VCD_CGW_BrakeSystem.Config": ParamSpec(
        qualifier="VCD_CGW_BrakeSystem.Config",
        datatype="B", encoding="enum",
        rule={"ABS": "01", "EBS": "02", "EBS WITH ESP": "03"},
    ),
    "VCD_CGW_Drive.Side": ParamSpec(
        qualifier="VCD_CGW_Drive.Side",
        datatype="B", encoding="enum",
        rule={"LEFT-HAND": "00", "RIGHT-HAND": "01"},
    ),
    "VCD_CGW_Steering.Variant": ParamSpec(
        qualifier="VCD_CGW_Steering.Variant",
        datatype="B", encoding="enum",
        rule={"EVO-LF": "10", "EVO-RF": "11"},
    ),
}

# Map Nomenclature feature names → CDD qualifiers (demo wiring).
NOMENCLATURE_TO_QUALIFIER: dict[str, str] = {
    "TIMER LIST":  "VCD_CGW_BKAM_Timer.Timeout_Value",
    "TRACKWIDTH":  "VCD_CGW_TrackWidth.Value",
    "TPM TYPE":    "VCD_CGW_TPM.Type",
    "BS":          "VCD_CGW_BrakeSystem.Config",
    "DRIVE":       "VCD_CGW_Drive.Side",
    "STEERPARA":   "VCD_CGW_Steering.Variant",
}

# Reverse map: CDD qualifier → human-readable feature name.
QUALIFIER_TO_FEATURE: dict[str, str] = {v: k for k, v in NOMENCLATURE_TO_QUALIFIER.items()}
