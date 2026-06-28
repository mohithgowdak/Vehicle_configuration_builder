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


_PN_COLS  = ["Partnumber", "Part Number", "PartNumber", "Part_Number", "PARTNUMBER", "part_no", "PartNo"]
_NOM_COLS = ["Nomenclature", "NOMENCLATURE", "Description", "Name", "Feature", "Config"]
_ECU_COLS = ["ECU Qualifier", "ECU_Qualifier", "Variant", "ECU", "Qualifier", "ECUQualifier"]
_TYP_COLS = ["Type", "TYPE", "Category", "Kind"]


def variants(df: pd.DataFrame) -> list[str]:
    col = _find_col(df, _ECU_COLS)
    if not col:
        return []
    return sorted({str(v).strip() for v in df[col].dropna() if str(v).strip()})


def parameters_for_variant(df: pd.DataFrame, variant: str) -> pd.DataFrame:
    ecu_col = _find_col(df, _ECU_COLS)
    type_col = _find_col(df, _TYP_COLS)
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


# ---------- Parameter Values (second Excel) ----------

def load_param_values(path: Path | None) -> pd.DataFrame | None:
    """Load the second Excel (partnumber_parameter_values). Returns None if not found."""
    if not path or not path.exists():
        return None
    df = pd.read_excel(path)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def build_qualifier_bridge(
    par_qualifiers: list[str],
    part_df: pd.DataFrame,
    param_df: pd.DataFrame | None,
) -> dict[str, dict]:
    """
    Try to map each .par qualifier to a row in the Excel files.

    Returns dict[qualifier] = {
        "feature": str,       # human-readable name
        "part_number": str,
        "nomenclature": str,
        "source": str,        # which strategy found it
        "row": dict,          # raw row for display
    }
    """
    bridge: dict[str, dict] = {}

    # ---- Strategy 1: param_df has a column whose values match par qualifiers ----
    if param_df is not None:
        for col in param_df.columns:
            col_vals = param_df[col].astype(str).str.strip()
            for q in par_qualifiers:
                if q in bridge:
                    continue
                matches = param_df[col_vals == q]
                if not matches.empty:
                    row = matches.iloc[0]
                    nom_col = _find_col(param_df, _NOM_COLS)
                    pn_col  = _find_col(param_df, _PN_COLS)
                    bridge[q] = {
                        "feature":      str(row[nom_col]).strip() if nom_col else "",
                        "part_number":  str(row[pn_col]).strip()  if pn_col  else "",
                        "nomenclature": str(row[nom_col]).strip() if nom_col else "",
                        "source":       f"param_values[{col}]",
                        "row":          row.to_dict(),
                    }

    # ---- Strategy 2: match tail of qualifier against any column value in param_df ----
    if param_df is not None:
        for q in par_qualifiers:
            if q in bridge:
                continue
            tail = q.split(".")[-1]   # e.g. "Timeout_Value"
            for col in param_df.columns:
                col_vals = param_df[col].astype(str).str.strip()
                matches = param_df[col_vals.str.lower() == tail.lower()]
                if not matches.empty:
                    row = matches.iloc[0]
                    nom_col = _find_col(param_df, _NOM_COLS)
                    pn_col  = _find_col(param_df, _PN_COLS)
                    bridge[q] = {
                        "feature":      str(row[nom_col]).strip() if nom_col else tail,
                        "part_number":  str(row[pn_col]).strip()  if pn_col  else "",
                        "nomenclature": str(row[nom_col]).strip() if nom_col else "",
                        "source":       f"tail-match[{col}]",
                        "row":          row.to_dict(),
                    }
                    break

    # ---- Strategy 3: link via part_number shared between both Excels ----
    if param_df is not None:
        pn_col_part   = _find_col(part_df, _PN_COLS)
        pn_col_param  = _find_col(param_df, _PN_COLS)
        q_col_param   = _find_col(param_df, ["Qualifier", "ECU Qualifier", "CDD Qualifier",
                                              "Parameter", "QualifierName", "Param"])
        if pn_col_part and pn_col_param and q_col_param:
            for q in par_qualifiers:
                if q in bridge:
                    continue
                # Find rows in param_df whose qualifier column matches q
                matches_param = param_df[
                    param_df[q_col_param].astype(str).str.strip() == q
                ]
                if matches_param.empty:
                    continue
                pn = str(matches_param.iloc[0][pn_col_param]).strip()
                # Find that part number in part_df
                matches_part = part_df[
                    part_df[pn_col_part].astype(str).str.strip() == pn
                ]
                nom_col = _find_col(part_df, _NOM_COLS)
                feature = ""
                if not matches_part.empty and nom_col:
                    feature = str(matches_part.iloc[0][nom_col]).strip()
                bridge[q] = {
                    "feature":      feature,
                    "part_number":  pn,
                    "nomenclature": feature,
                    "source":       "part_number-bridge",
                    "row":          matches_param.iloc[0].to_dict(),
                }

    # ---- Strategy 4: hardcoded fallback ----
    for q in par_qualifiers:
        if q not in bridge and q in QUALIFIER_TO_FEATURE:
            bridge[q] = {
                "feature":      QUALIFIER_TO_FEATURE[q],
                "part_number":  "",
                "nomenclature": QUALIFIER_TO_FEATURE[q],
                "source":       "built-in map",
                "row":          {},
            }

    return bridge


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
    """Case-insensitive, space/underscore-normalised column lookup."""
    def _norm(s: str) -> str:
        return s.lower().replace(" ", "").replace("_", "").replace("-", "")

    lookup = {_norm(c): c for c in df.columns}
    for cand in candidates:
        hit = lookup.get(_norm(cand))
        if hit:
            return hit
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
