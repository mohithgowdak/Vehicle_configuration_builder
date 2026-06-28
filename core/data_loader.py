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
import re as _re
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


_PN_COLS       = ["Partnumber", "Part Number", "PartNumber", "Part_Number", "PARTNUMBER", "part_no", "PartNo", "Part number"]
_NOM_COLS      = ["Nomenclature", "NOMENCLATURE", "Description", "Name", "Feature", "Config"]
_ECU_COLS      = ["ECU Qualifier", "ECU_Qualifier", "Variant", "ECU", "Qualifier", "ECUQualifier"]
_TYP_COLS      = ["Type", "TYPE", "Category", "Kind"]
_DOMAIN_COLS   = ["Domain", "DOMAIN", "ParameterGroup", "Parameter Group", "Write Command", "Group"]
_FRAGMENT_COLS = ["Fragment/Default", "Fragment", "Default", "FragmentDefault", "Preset", "Meaning", "Description"]
_PVAL_COLS     = ["Parameter Value", "ParameterValue", "Value", "HexValue", "Hex", "ParamValue"]


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


def domain_to_qualifier_prefix(domain: str) -> str:
    """'CGW_BKAM Timer Write' → 'VCD_CGW_BKAM_Timer'"""
    cleaned = _re.sub(r"\s*Write\s*$", "", domain, flags=_re.IGNORECASE).strip()
    cleaned = cleaned.replace(" ", "_")
    return cleaned if cleaned.startswith("VCD_") else "VCD_" + cleaned


def _norm_key(s: str) -> str:
    """Normalize for fuzzy matching: strip VCD_/CGW_/VCD_CGW_ prefixes, Write suffix, lowercase."""
    s = _re.sub(r"\s*Write\s*$", "", str(s).strip(), flags=_re.IGNORECASE)
    s = s.upper()
    for pfx in ("VCD_CGW_", "VCD_", "CGW_"):
        if s.startswith(pfx):
            s = s[len(pfx):]
            break
    return s.lower().replace("_", "").replace(" ", "").replace("-", "")


def pn_strip_dots(pn: str) -> str:
    """'A.034.447.29.27' → 'A0344472927'"""
    return str(pn).replace(".", "").strip()


def build_qualifier_bridge(
    par_qualifiers: list[str],
    part_df: pd.DataFrame,
    param_df: pd.DataFrame | None,
) -> dict[str, dict]:
    """
    Map each .par qualifier to Excel rows using the Domain → VCD prefix pattern.

    partnumber_parameter_values Domain:  'CGW_BKAM Timer Write'
    Transforms to qualifier prefix:      'VCD_CGW_BKAM_Timer'
    Matches .par qualifier group:        'VCD_CGW_BKAM_Timer.Timeout_Value' → group 'VCD_CGW_BKAM_Timer'

    Part number bridge:
    Config_Partnumbers:   'A.034.447.29.27'  (dots)
    Param_values:         'A0344472927'       (no dots)  ← same number, just remove dots
    """
    bridge: dict[str, dict] = {}

    domain_col    = _find_col(param_df, _DOMAIN_COLS)    if param_df is not None else None
    fragment_col  = _find_col(param_df, _FRAGMENT_COLS)  if param_df is not None else None
    pval_col      = _find_col(param_df, _PVAL_COLS)      if param_df is not None else None
    pn_col_param  = _find_col(param_df, _PN_COLS)        if param_df is not None else None
    pn_col_part   = _find_col(part_df,  _PN_COLS)
    nom_col_part  = _find_col(part_df,  _NOM_COLS)

    # ---- Strategy 1 (primary): normalized Domain ↔ qualifier group fuzzy match ----
    # Strips VCD_/CGW_/VCD_CGW_ prefixes from both sides before comparing so that
    # "CGW_Third Party PT Config Write" matches "VCD_Third_Party_PT_Config"
    if param_df is not None and domain_col:
        # Pre-build: normalized_key → list of param_df rows
        norm_to_rows: dict[str, list] = {}
        for _, row in param_df.iterrows():
            dom = str(row[domain_col]).strip()
            if not dom or dom.lower() == "nan":
                continue
            norm_to_rows.setdefault(_norm_key(dom), []).append(row)

        # Pre-build: no-dots part number → Config_Partnumbers nomenclature
        pn_to_nom: dict[str, str] = {}
        if pn_col_part and nom_col_part:
            for _, pr in part_df.iterrows():
                pn = pn_strip_dots(str(pr[pn_col_part]))
                nom = str(pr[nom_col_part]).strip()
                if pn and nom and nom.lower() != "nan":
                    pn_to_nom[pn] = nom

        for q in par_qualifiers:
            if q in bridge:
                continue
            q_group = q.split(".")[0]          # e.g. "VCD_Third_Party_PT_Config"
            q_norm  = _norm_key(q_group)       # e.g. "thirdpartyptconfig"
            rows = norm_to_rows.get(q_norm)
            if not rows:
                continue
            row = rows[0]
            dom      = str(row[domain_col]).strip()
            fragment = str(row[fragment_col]).strip() if fragment_col else ""
            pval     = str(row[pval_col]).strip()     if pval_col    else ""
            pn_raw   = str(row[pn_col_param]).strip() if pn_col_param else ""
            pn_nodot = pn_strip_dots(pn_raw)

            nom_from_part = pn_to_nom.get(pn_nodot, "")
            feature = nom_from_part or fragment or dom

            bridge[q] = {
                "feature":      feature,
                "domain":       dom,
                "fragment":     fragment,
                "param_value":  pval,
                "part_number":  pn_nodot,
                "nomenclature": nom_from_part,
                "source":       "domain-fuzzy",
                "row":          row.to_dict(),
            }

    # ---- Strategy 2 (fallback): hardcoded built-in map ----
    for q in par_qualifiers:
        if q not in bridge and q in QUALIFIER_TO_FEATURE:
            bridge[q] = {
                "feature":      QUALIFIER_TO_FEATURE[q],
                "domain":       "",
                "fragment":     "",
                "param_value":  "",
                "part_number":  "",
                "nomenclature": QUALIFIER_TO_FEATURE[q],
                "source":       "built-in map",
                "row":          {},
            }

    return bridge


def find_par_qualifiers_for_part_number(
    part_number: str,
    param_df: pd.DataFrame | None,
    bridge: dict[str, dict],
) -> list[str]:
    """
    Given a part number (dotted or not), return all .par qualifiers that belong
    to the same Domain row in param_values.
    """
    if param_df is None:
        return []

    pn_nodot = pn_strip_dots(part_number)
    domain_col   = _find_col(param_df, _DOMAIN_COLS)
    pn_col_param = _find_col(param_df, _PN_COLS)
    if not domain_col or not pn_col_param:
        return []

    # Normalise both sides to no-dots before comparing:
    #   Config_Partnumbers: "A.034.447.29.27" → pn_strip_dots → "A0344472927"
    #   Param_values:       "A0344472927"     → pn_strip_dots → "A0344472927"  (no-op)
    matches = param_df[
        param_df[pn_col_param].astype(str).apply(pn_strip_dots) == pn_nodot
    ]
    if matches.empty:
        return []

    target_norms = {_norm_key(str(d)) for d in matches[domain_col]}

    # Return all bridge qualifiers whose normalized group matches
    return [
        q for q in bridge
        if _norm_key(q.split(".")[0]) in target_norms
    ]


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
