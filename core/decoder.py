"""Decode .par P-lines back to human-readable values using the CDD codebook."""
from __future__ import annotations

from dataclasses import dataclass

from core.data_loader import ParamSpec, ParHeader


@dataclass
class DecodedParam:
    qualifier: str
    hex_value: str
    datatype: str
    encoding: str
    decoded_value: str
    feature_name: str
    part_number: str
    detail: str


def reverse_decode(hex_value: str, spec: ParamSpec) -> tuple[str, str]:
    """Return (human_readable_value, detail_string)."""
    h = hex_value.strip().upper()

    if spec.encoding == "enum":
        reverse = {str(v).strip().upper(): k for k, v in spec.rule.items()}
        match = reverse.get(h)
        if match:
            return match, f"enum lookup: {h} → {match}"
        return h, f"unknown enum value {h} (valid: {list(spec.rule.keys())})"

    if spec.encoding == "linear":
        resolution = float(spec.rule.get("resolution", 1.0))
        offset = float(spec.rule.get("offset", 0.0))
        try:
            raw = int(h, 16)
            physical = raw * resolution + offset
            return str(round(physical, 4)), f"linear: {raw} × {resolution} + {offset} = {physical:.4g}"
        except ValueError:
            return h, f"could not parse linear hex {h}"

    if spec.encoding == "bitfield":
        try:
            raw = int(h, 16)
            flags = [name for name, bit in spec.rule.items() if raw & (1 << int(bit))]
            decoded = ", ".join(flags) if flags else "none"
            return decoded, f"bitfield 0x{h}: active flags = [{decoded}]"
        except ValueError:
            return h, f"could not parse bitfield hex {h}"

    return h, f"raw value (no decode rule)"


def parse_par_text(text: str) -> ParHeader:
    """Parse raw .par text into a ParHeader (same logic as load_reference_par)."""
    s, h, p = [], [], []
    for line in text.splitlines():
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


def decode_par_file(
    header: ParHeader,
    cdd: dict[str, ParamSpec],
    qualifier_to_feature: dict[str, str],
    qualifier_to_part: dict[str, str] | None = None,
) -> list[DecodedParam]:
    """Decode all P-lines from a ParHeader into DecodedParam objects."""
    results = []
    for line in header.default_params:
        parts = line.split(",")
        if len(parts) < 4 or parts[0].strip() != "P":
            continue
        qualifier = parts[1].strip()
        datatype = parts[2].strip()
        hex_value = parts[3].strip()

        spec = cdd.get(qualifier)
        feature_name = qualifier_to_feature.get(qualifier, "")
        part_number = (qualifier_to_part or {}).get(qualifier, "")

        if spec:
            decoded_value, detail = reverse_decode(hex_value, spec)
            encoding = spec.encoding
        else:
            try:
                decoded_value = str(int(hex_value, 16))
            except ValueError:
                decoded_value = hex_value
            detail = "hex → decimal"
            encoding = "raw"

        results.append(DecodedParam(
            qualifier=qualifier,
            hex_value=hex_value,
            datatype=datatype,
            encoding=encoding,
            decoded_value=decoded_value,
            feature_name=feature_name,
            part_number=part_number,
            detail=detail,
        ))
    return results


def extract_session_info(header: ParHeader) -> dict[str, str]:
    """Extract key-value pairs from S and H lines."""
    info: dict[str, str] = {}
    for line in header.session_lines + header.header_lines:
        parts = line.split(",", 2)
        if len(parts) == 3:
            info[parts[1].strip()] = parts[2].strip()
    return info


def dehex(hex_value: str) -> str:
    """Convert a hex string to its decimal integer string, e.g. '4B' → '75'."""
    try:
        return str(int(hex_value.strip(), 16))
    except ValueError:
        return hex_value


def lookup_by_part_number(
    part_num: str,
    part_df,           # pd.DataFrame
    decoded: list[DecodedParam],
    qualifier_to_feature: dict[str, str],
) -> dict | None:
    """Return a result dict for a given part number, or None if not found."""
    from core.data_loader import _find_col, _PN_COLS, _NOM_COLS, _ECU_COLS

    pn_col  = _find_col(part_df, _PN_COLS)
    nom_col = _find_col(part_df, _NOM_COLS)
    ecu_col = _find_col(part_df, _ECU_COLS)

    if pn_col is None:
        return None

    mask = part_df[pn_col].astype(str).str.strip().str.upper() == part_num.strip().upper()
    rows = part_df[mask]
    if rows.empty:
        return None

    row = rows.iloc[0]
    nom = str(row.get(nom_col, "") if nom_col else "").strip()
    ecu = str(row.get(ecu_col, "") if ecu_col else "").strip()

    # Parse feature name from nomenclature  (e.g. "TIMER LIST: DEFAULT" → "TIMER LIST")
    feature = nom.split(":")[0].strip().upper() if ":" in nom else nom.upper()

    # Find the matching decoded param
    param = next((p for p in decoded if p.feature_name.upper() == feature), None)

    return {
        "part_number": part_num,
        "nomenclature": nom,
        "ecu": ecu,
        "feature": feature,
        "param": param,
    }


def build_context_text(session_info: dict[str, str], decoded: list[DecodedParam]) -> str:
    """Build a plain-text summary suitable for injection into an LLM prompt."""
    lines = ["=== PAR FILE — ECU/SESSION INFO ==="]
    for k, v in session_info.items():
        lines.append(f"  {k}: {v}")
    lines.append("\n=== DECODED PARAMETERS ===")
    for p in decoded:
        name = p.feature_name or p.qualifier
        lines.append(
            f"  {name} ({p.qualifier}): {p.decoded_value}"
            f"  [hex={p.hex_value}, type={p.datatype}, encoding={p.encoding}]"
        )
    return "\n".join(lines)
