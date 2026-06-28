"""Streamlit chatbot — reads, decodes and explains .par ECU configuration files.

Run with:
    streamlit run app.py
"""
from __future__ import annotations

import base64
import io
import mimetypes
from dataclasses import asdict
from pathlib import Path

import pandas as pd
import streamlit as st

from core import data_loader as dl
from core.config import Settings
from core.decoder import (
    DecodedParam,
    build_context_text,
    decode_par_file,
    extract_session_info,
    lookup_by_part_number,
    parse_par_text,
)
from core.data_loader import find_par_qualifiers_for_part_number, pn_strip_dots
from core.llm import ai_query, answer_question


# -------------------------------------------------------------------------- #
# Page config
# -------------------------------------------------------------------------- #

st.set_page_config(
    page_title="PAR Explorer · Vehicle Config Analyser",
    page_icon="🚛",
    layout="wide",
    initial_sidebar_state="expanded",
)

_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".ogv"}


# -------------------------------------------------------------------------- #
# Background helpers (unchanged from previous version)
# -------------------------------------------------------------------------- #

def _load_background(path: Path | None) -> tuple[str | None, str | None]:
    if not path or not path.exists():
        return None, None
    ext = path.suffix.lower()
    if ext in _VIDEO_EXTS:
        mime = {".mp4": "video/mp4", ".webm": "video/webm",
                ".mov": "video/quicktime", ".m4v": "video/mp4",
                ".ogv": "video/ogg"}.get(ext, "video/mp4")
        kind = "video"
    else:
        mime, _ = mimetypes.guess_type(path.name)
        mime = mime or "image/gif"
        kind = "image"
    b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{b64}", kind


def build_background_injector(uri: str, mime: str, overlay: float) -> str:
    return f"""
<script>
(function() {{
    try {{
        const doc = window.parent.document;
        const transparents = [
            doc.documentElement, doc.body,
            doc.getElementById('root'),
            doc.querySelector('[data-testid="stApp"]'),
            doc.querySelector('[data-testid="stAppViewContainer"]'),
            doc.querySelector('[data-testid="stMain"]'),
            doc.querySelector('[data-testid="stHeader"]'),
        ];
        transparents.forEach(el => {{ if (el) el.style.background = 'transparent'; }});

        const existing = doc.getElementById('bg-video-wrap');
        if (existing) existing.remove();

        const wrap = doc.createElement('div');
        wrap.id = 'bg-video-wrap';
        wrap.style.cssText = 'position:fixed;top:0;left:0;width:100vw;height:100vh;z-index:-1;overflow:hidden;pointer-events:none;';

        const video = doc.createElement('video');
        video.autoplay = true; video.muted = true; video.loop = true; video.playsInline = true;
        video.setAttribute('playsinline',''); video.setAttribute('autoplay','');
        video.setAttribute('muted',''); video.setAttribute('loop','');
        video.style.cssText = 'position:absolute;top:50%;left:50%;min-width:100%;min-height:100%;width:auto;height:auto;transform:translate(-50%,-50%);object-fit:cover;';

        const source = doc.createElement('source');
        source.src = '{uri}'; source.type = '{mime}';
        video.appendChild(source);

        const overlayDiv = doc.createElement('div');
        overlayDiv.id = 'bg-video-overlay';
        overlayDiv.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;background:rgba(8,12,28,{overlay});pointer-events:none;';

        wrap.appendChild(video); wrap.appendChild(overlayDiv);
        doc.body.insertBefore(wrap, doc.body.firstChild);

        const p = video.play();
        if (p && p.catch) p.catch(() => {{}});
    }} catch (e) {{ console.error('bg video inject failed', e); }}
}})();
</script>
"""


def build_css(bg_uri: str | None, kind: str | None, overlay: float) -> str:
    if kind == "image" and bg_uri:
        app_bg = f"background: linear-gradient(rgba(8,12,28,{overlay}),rgba(8,12,28,{overlay})),url('{bg_uri}') center/cover no-repeat fixed;"
        card_bg = "background: rgba(255,255,255,0.93); backdrop-filter: blur(6px);"
        hero_extra = "backdrop-filter: blur(4px);"
        sidebar_bg = "background: rgba(248,250,252,0.93); backdrop-filter: blur(8px);"
    elif kind == "video":
        app_bg = "background: transparent;"
        card_bg = "background: rgba(255,255,255,0.93); backdrop-filter: blur(6px);"
        hero_extra = "backdrop-filter: blur(4px);"
        sidebar_bg = "background: rgba(248,250,252,0.93); backdrop-filter: blur(8px);"
    else:
        app_bg = "background: linear-gradient(180deg,#f1f5f9 0%,#e2e8f0 100%);"
        card_bg = "background: #ffffff;"
        hero_extra = ""
        sidebar_bg = "background: #f8fafc;"

    return f"""
<style>
:root {{
    --accent: #2563eb; --ok: #16a34a; --warn: #d97706;
    --err: #dc2626; --muted: #64748b; --card-border: #e2e8f0;
}}
html, body, [class*="css"] {{ font-family: 'Inter', system-ui, sans-serif; }}
html, body {{ background: transparent !important; }}
[data-testid="stAppViewContainer"] {{ {app_bg} }}
[data-testid="stHeader"] {{ background: transparent; }}
section.main > div {{ padding-top: 0.5rem; position: relative; z-index: 1; }}

.app-hero {{
    background: linear-gradient(120deg,rgba(30,58,138,0.92) 0%,rgba(37,99,235,0.88) 70%,rgba(59,130,246,0.82) 100%);
    color: white; padding: 20px 26px; border-radius: 16px; margin-bottom: 16px;
    box-shadow: 0 10px 30px rgba(15,23,42,0.35); {hero_extra}
}}
.app-hero h1 {{ margin: 0; font-size: 1.45rem; font-weight: 700; letter-spacing: -0.01em; }}
.app-hero p  {{ margin: 5px 0 0; opacity: 0.9; font-size: 0.88rem; }}

.pill {{
    display: inline-flex; align-items: center; gap: 5px;
    padding: 3px 10px; border-radius: 999px; font-size: 0.77rem; font-weight: 500;
    border: 1px solid var(--card-border); background: #f8fafc; color: #1e293b;
    margin: 2px 4px 2px 0;
}}
.pill.ok   {{ background: #ecfdf5; color: #065f46; border-color: #a7f3d0; }}
.pill.warn {{ background: #fffbeb; color: #92400e; border-color: #fcd34d; }}
.pill.err  {{ background: #fef2f2; color: #991b1b; border-color: #fecaca; }}
.pill.blue {{ background: #eff6ff; color: #1d4ed8; border-color: #bfdbfe; }}

.kv-card {{
    {card_bg} border: 1px solid var(--card-border);
    border-radius: 12px; padding: 14px 16px; margin: 8px 0;
    box-shadow: 0 4px 16px rgba(15,23,42,0.08);
}}
.kv-card h4 {{
    margin: 0 0 10px; font-size: 0.82rem; color: var(--muted);
    text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600;
}}

.par-block {{
    background: #0f172a; color: #e2e8f0; border-radius: 12px;
    padding: 14px 16px; font-family: 'JetBrains Mono','Consolas',monospace;
    font-size: 0.80rem; line-height: 1.6; white-space: pre; overflow-x: auto;
    border: 1px solid #1e293b;
}}

div[data-testid="stChatMessage"] {{
    {card_bg} border: 1px solid var(--card-border);
    border-radius: 14px; padding: 14px 16px; margin-bottom: 10px;
    box-shadow: 0 4px 16px rgba(15,23,42,0.08);
}}

div[data-testid="stSidebar"] {{ {sidebar_bg} }}
div[data-testid="stSidebar"] h2 {{ font-size: 1.05rem; }}

.suggest-btn button {{
    width: 100%; text-align: left; font-size: 0.80rem; padding: 7px 11px;
    border-radius: 10px; border: 1px solid var(--card-border) !important;
    background: rgba(255,255,255,0.95) !important; color: #0f172a !important;
}}
.suggest-btn button:hover {{
    border-color: var(--accent) !important; color: var(--accent) !important;
}}

.upload-prompt {{
    text-align: center; padding: 32px 24px;
    border: 2px dashed #cbd5e1; border-radius: 16px;
    color: #64748b; margin: 24px 0;
}}
.upload-prompt h3 {{ color: #1e293b; margin-bottom: 8px; font-size: 1.1rem; }}
</style>
"""


# -------------------------------------------------------------------------- #
# Settings + CSS
# -------------------------------------------------------------------------- #

st.session_state.settings = Settings.load()
# Clear resource cache whenever settings change so bridge rebuilds with fresh data
if "last_settings_hash" not in st.session_state or st.session_state.last_settings_hash != str(st.session_state.settings):
    st.cache_resource.clear()
    st.session_state.last_settings_hash = str(st.session_state.settings)
    st.session_state.decoded_params = None   # force reload
_s: Settings = st.session_state.settings
_bg_uri, _bg_kind = _load_background(_s.background_image)
st.markdown(build_css(_bg_uri, _bg_kind, _s.background_overlay), unsafe_allow_html=True)
if _bg_kind == "video" and _bg_uri:
    _mime = _bg_uri.split(";", 1)[0].removeprefix("data:") or "video/mp4"
    from streamlit.components.v1 import html as _components_html
    _components_html(
        build_background_injector(_bg_uri, _mime, _s.background_overlay),
        height=0,
    )

# -------------------------------------------------------------------------- #
# Session state
# -------------------------------------------------------------------------- #

if "messages" not in st.session_state:
    st.session_state.messages = []
if "decoded_params" not in st.session_state:
    st.session_state.decoded_params = None        # list[DecodedParam] | None
if "session_info" not in st.session_state:
    st.session_state.session_info = {}
if "par_filename" not in st.session_state:
    st.session_state.par_filename = None
if "par_raw_text" not in st.session_state:
    st.session_state.par_raw_text = None
if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None
if "part_df" not in st.session_state:
    st.session_state.part_df = None
if "param_df" not in st.session_state:
    st.session_state.param_df = None
if "qualifier_bridge" not in st.session_state:
    st.session_state.qualifier_bridge = {}

settings: Settings = st.session_state.settings


# -------------------------------------------------------------------------- #
# Data loading helpers
# -------------------------------------------------------------------------- #

@st.cache_resource
def _load_all_data(cdd_xlsx, cdd_xml, pn_xlsx, pv_xlsx):
    cdd      = dl.load_cdd(cdd_xlsx, cdd_xml)
    part_df  = dl.load_part_numbers(pn_xlsx)
    param_df = dl.load_param_values(pv_xlsx)
    return cdd, part_df, param_df




def _do_decode(par_text: str, filename: str) -> None:
    """Parse + decode a .par text block and store results in session state."""
    cdd, part_df, param_df = _load_all_data(
        settings.cdd_xlsx, settings.cdd_xml,
        settings.part_number_xlsx, settings.param_values_xlsx,
    )
    header = parse_par_text(par_text)

    # Build dynamic qualifier map from both Excels first
    par_qualifiers = [
        ln.split(",")[1].strip()
        for ln in header.default_params
        if ln.startswith("P,") and len(ln.split(",")) >= 2
    ]
    bridge = dl.build_qualifier_bridge(par_qualifiers, part_df, param_df)
    # Merge bridge feature names into QUALIFIER_TO_FEATURE for the decoder
    q_to_feature = {**dl.QUALIFIER_TO_FEATURE, **{q: v["feature"] for q, v in bridge.items() if v["feature"]}}

    decoded = decode_par_file(header, cdd, q_to_feature)
    session_info = extract_session_info(header)

    st.session_state.decoded_params = decoded
    st.session_state.session_info = session_info
    st.session_state.par_filename = filename
    st.session_state.par_raw_text = par_text
    st.session_state.part_df = part_df
    st.session_state.param_df = param_df
    st.session_state.qualifier_bridge = bridge
    st.session_state.messages = []


def _auto_load_reference_par() -> None:
    """Load the reference .par from settings on first run."""
    if st.session_state.decoded_params is not None:
        return
    cdd, part_df, param_df = _load_all_data(
        settings.cdd_xlsx, settings.cdd_xml,
        settings.part_number_xlsx, settings.param_values_xlsx,
    )
    if settings.reference_par and settings.reference_par.exists():
        text = settings.reference_par.read_text(encoding="utf-8", errors="ignore")
        header = parse_par_text(text)
        filename = settings.reference_par.name
    else:
        from core.data_loader import load_reference_par
        header = load_reference_par(None)
        filename = "demo_sample.par (built-in)"
        text = "\n".join(header.session_lines + header.header_lines + header.default_params)

    par_qualifiers = [
        ln.split(",")[1].strip()
        for ln in header.default_params
        if ln.startswith("P,") and len(ln.split(",")) >= 2
    ]
    bridge = dl.build_qualifier_bridge(par_qualifiers, part_df, param_df)
    q_to_feature = {**dl.QUALIFIER_TO_FEATURE, **{q: v["feature"] for q, v in bridge.items() if v["feature"]}}

    decoded = decode_par_file(header, cdd, q_to_feature)
    session_info = extract_session_info(header)

    st.session_state.decoded_params = decoded
    st.session_state.session_info = session_info
    st.session_state.par_filename = filename
    st.session_state.par_raw_text = text
    st.session_state.part_df = part_df
    st.session_state.param_df = param_df
    st.session_state.qualifier_bridge = bridge


def _keyword_filter(keyword: str, decoded: list[DecodedParam], bridge: dict) -> str | None:
    """Search qualifier / feature / domain for a keyword. Returns None if no matches."""
    kw = keyword.strip().lower()
    if len(kw) < 2:
        return None

    matches = [
        p for p in decoded
        if kw in p.qualifier.lower()
        or kw in p.feature_name.lower()
        or kw in bridge.get(p.qualifier, {}).get("domain", "").lower()
        or kw in bridge.get(p.qualifier, {}).get("fragment", "").lower()
        or kw in bridge.get(p.qualifier, {}).get("nomenclature", "").lower()
    ]
    if not matches:
        return None

    lines = [f"**{len(matches)} parameter(s) matching `{keyword}`:**\n"]
    for p in matches:
        b = bridge.get(p.qualifier, {})
        try:
            dec = str(int(p.hex_value, 16))
        except ValueError:
            dec = p.hex_value
        field = p.qualifier.split(".")[-1]
        domain   = b.get("domain", "")
        fragment = b.get("fragment", "")
        lines.append(
            f"**{field}**\n"
            f"- Qualifier: `{p.qualifier}`\n"
            f"- Hex: `{p.hex_value}` → Decimal: **{dec}** | Type: {p.datatype}"
            + (f"\n- Domain: {domain}" if domain else "")
            + (f"\n- Fragment: {fragment}" if fragment else "")
        )
    return "\n\n".join(lines)


def _answer_part_number(part_num: str, decoded: list[DecodedParam], part_df, param_df, bridge: dict) -> str:
    """Handle a chat message that is a part number lookup."""
    stripped = part_num.strip()
    pn_nodot = pn_strip_dots(stripped)

    # --- Look up in Config_Partnumbers ---
    pn_col  = dl._find_col(part_df, dl._PN_COLS)
    nom_col = dl._find_col(part_df, dl._NOM_COLS)
    ecu_col = dl._find_col(part_df, dl._ECU_COLS)

    nom_text, ecu_text = "", ""
    if pn_col:
        mask = part_df[pn_col].astype(str).str.replace(".", "").str.strip() == pn_nodot
        rows = part_df[mask]
        if not rows.empty:
            row = rows.iloc[0]
            nom_text = str(row[nom_col]).strip() if nom_col else ""
            ecu_text = str(row[ecu_col]).strip() if ecu_col else ""

    # --- Find matching .par qualifiers via Domain bridge ---
    matching_qualifiers = find_par_qualifiers_for_part_number(stripped, param_df, bridge)

    # Also try direct feature-name match (fallback)
    if not matching_qualifiers and nom_text:
        feature = nom_text.split(":")[0].strip().upper() if ":" in nom_text else nom_text.upper()
        matching_qualifiers = [q for q, info in bridge.items() if info.get("feature", "").upper() == feature]

    if not matching_qualifiers and not nom_text:
        return (
            f"Part number `{stripped}` was not found in the loaded Excel files.\n\n"
            "Make sure both `PART_NUMBER_XLSX` and `PARAM_VALUES_XLSX` are set in `.env` "
            "and the part number is in the dataset."
        )

    lines = [
        f"**Part Number:** `{stripped}`",
    ]
    if nom_text:
        lines.append(f"**Nomenclature:** {nom_text}")
    if ecu_text:
        lines.append(f"**ECU / Variant:** {ecu_text}")

    # Get domain/fragment from bridge for first matching qualifier
    if matching_qualifiers:
        first_info = bridge.get(matching_qualifiers[0], {})
        if first_info.get("domain"):
            lines.append(f"**Write Command (Domain):** {first_info['domain']}")
        if first_info.get("fragment"):
            lines.append(f"**Fragment/Default:** {first_info['fragment']}")

    lines.append("")

    decoded_map = {p.qualifier: p for p in decoded}
    if matching_qualifiers:
        lines.append(f"**Parameters in .par file ({len(matching_qualifiers)} found):**")
        for q in matching_qualifiers:
            p = decoded_map.get(q)
            if p:
                try:
                    dec = str(int(p.hex_value, 16))
                except ValueError:
                    dec = p.hex_value
                field = q.split(".")[-1]   # e.g. "Timeout_Value"
                lines.append(f"- `{field}` → Hex: `{p.hex_value}` | Decimal: **{dec}** | Type: {p.datatype}")
            else:
                lines.append(f"- `{q.split('.')[-1]}` → not found in .par file")
    else:
        lines.append("_No matching P-lines found in the loaded .par file for this part number._")

    return "\n".join(lines)


_auto_load_reference_par()

# -------------------------------------------------------------------------- #
# Sidebar
# -------------------------------------------------------------------------- #

with st.sidebar:
    st.markdown("## 🚛 PAR Explorer")
    st.caption("Upload a `.par` file to decode and chat about it.")
    st.divider()

    uploaded = st.file_uploader(
        "Upload .par file",
        type=["par", "txt"],
        help="Any S/H/P format .par ECU parameter file",
    )
    if uploaded is not None:
        text = uploaded.read().decode("utf-8", errors="ignore")
        _do_decode(text, uploaded.name)
        st.rerun()

    st.divider()
    st.markdown("### Data sources")
    for label, (ok, path) in settings.status().items():
        css = "ok" if ok else "warn"
        icon = "✓" if ok else "○"
        note = "" if ok else " (demo data)"
        st.markdown(
            f'<div class="pill {css}">{icon} {label}{note}</div>',
            unsafe_allow_html=True,
        )

    # Excel inspector — shows columns + sample rows from both files
    part_df_sb  = st.session_state.get("part_df")
    param_df_sb = st.session_state.get("param_df")
    bridge_sb   = st.session_state.get("qualifier_bridge", {})

    with st.expander("📊 Excel Inspector", expanded=False):
        if part_df_sb is not None:
            st.caption(f"**Config_Partnumbers** — {len(part_df_sb)} rows")
            st.code(", ".join(part_df_sb.columns.tolist()))
            st.dataframe(part_df_sb.head(3), use_container_width=True, hide_index=True)
        else:
            st.caption("Part Number xlsx not loaded")

        if param_df_sb is not None:
            st.caption(f"**Param Values** — {len(param_df_sb)} rows")
            st.code(", ".join(param_df_sb.columns.tolist()))
            st.dataframe(param_df_sb.head(3), use_container_width=True, hide_index=True)
        else:
            st.caption("Param Values xlsx not loaded / not set in .env")

    with st.expander("🔗 Qualifier mapping debug", expanded=False):
        _pdf = st.session_state.get("param_df")
        if _pdf is not None:
            from core.data_loader import _find_col, _DOMAIN_COLS, _FRAGMENT_COLS, _norm_key
            _dcol = _find_col(_pdf, _DOMAIN_COLS)
            _fcol = _find_col(_pdf, _FRAGMENT_COLS)
            st.caption(f"Domain column detected: **`{_dcol}`**")
            st.caption(f"Fragment column detected: **`{_fcol}`**")
            st.caption(f"All columns in param_values: `{list(_pdf.columns)}`")
            if _dcol:
                _doms = _pdf[_dcol].dropna().astype(str).str.strip().unique()[:5]
                st.caption("Sample domains & their norm keys:")
                for _d in _doms:
                    st.code(f"{_d!r:45s} → {_norm_key(_d)!r}")
        _dec = st.session_state.get("decoded_params")
        if _dec:
            _grps = list({p.qualifier.split(".")[0] for p in _dec})[:5]
            st.caption("Sample PAR qualifier groups & norm keys:")
            for _g in _grps:
                st.code(f"{_g!r:45s} → {_norm_key(_g)!r}")
        _br = st.session_state.get("qualifier_bridge", {})
        matched   = sum(1 for v in _br.values() if v.get("domain"))
        unmatched = len(_br) - matched
        st.caption(f"Bridge: **{matched} matched** / {unmatched} unmatched out of {len(_br)} qualifiers")

    if st.button("🔄 Force rebuild mapping", use_container_width=True):
        st.session_state.decoded_params = None
        st.session_state.qualifier_bridge = {}
        st.cache_resource.clear()
        st.rerun()

    st.divider()
    st.markdown("### Try it")
    suggestions = [
        "A.034.447.29.27",
        "A.034.447.96.27",
        "A.033.447.21.27",
        "Show all parameters",
        "What variant / ECU is this?",
        "What brake system is configured?",
    ]
    for i, s in enumerate(suggestions):
        with st.container():
            st.markdown('<div class="suggest-btn">', unsafe_allow_html=True)
            if st.button(s, key=f"sug_{i}", use_container_width=True):
                st.session_state.pending_prompt = s
                st.rerun()
            st.markdown("</div>", unsafe_allow_html=True)

    st.divider()
    if st.button("🗑️  Clear chat", use_container_width=True):
        st.session_state.messages = []
        st.rerun()

# -------------------------------------------------------------------------- #
# Hero
# -------------------------------------------------------------------------- #

st.markdown(
    """
    <div class="app-hero">
      <h1>🚛 Vehicle PAR Configuration Explorer</h1>
      <p>Upload a <code>.par</code> file to decode all hex parameter values to decimal.
         Enter a <strong>part number</strong> to look up what that feature is set to,
         or ask any natural language question about the configuration.</p>
    </div>
    """,
    unsafe_allow_html=True,
)

# -------------------------------------------------------------------------- #
# Decoded config summary card
# -------------------------------------------------------------------------- #

decoded: list[DecodedParam] | None = st.session_state.decoded_params
session_info: dict = st.session_state.session_info

if decoded is not None:
    fname = st.session_state.par_filename or "unknown.par"

    # ---- Session / header info ----
    with st.expander(f"📄 Loaded: `{fname}` — ECU / Session Info", expanded=True):
        col_left, col_right = st.columns(2)
        items = list(session_info.items())
        half = (len(items) + 1) // 2
        with col_left:
            for k, v in items[:half]:
                st.markdown(f'<span class="pill blue">**{k}**: {v}</span>', unsafe_allow_html=True)
        with col_right:
            for k, v in items[half:]:
                st.markdown(f'<span class="pill blue">**{k}**: {v}</span>', unsafe_allow_html=True)

    # ---- Decoded parameters table ----
    st.markdown('<div class="kv-card"><h4>Decoded Parameters — Hex → Decimal</h4>', unsafe_allow_html=True)
    table_filter = st.text_input(
        "Filter table",
        placeholder="Type to filter by qualifier / domain / field name…",
        label_visibility="collapsed",
        key="table_filter",
    )
    if decoded:
        _bridge = st.session_state.qualifier_bridge
        _tf = table_filter.strip().lower()
        rows = []
        for p in decoded:
            b = _bridge.get(p.qualifier, {})
            # Apply table filter if set
            if _tf and not any(
                _tf in s.lower() for s in [
                    p.qualifier, p.feature_name,
                    b.get("domain", ""), b.get("fragment", ""),
                    p.qualifier.split(".")[-1],
                ]
            ):
                continue
            try:
                raw_dec = str(int(p.hex_value, 16))
            except ValueError:
                raw_dec = p.hex_value
            rows.append({
                "Domain (Write Command)": b.get("domain", ""),
                "Fragment / Default":     b.get("fragment", ""),
                "Field":                  p.qualifier.split(".")[-1],
                "Qualifier":              p.qualifier,
                "Hex":                    p.hex_value,
                "Decimal":                raw_dec,
                "Type":                   p.datatype,
            })
        df = pd.DataFrame(rows)
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No P-lines found in the loaded file.")
    st.markdown("</div>", unsafe_allow_html=True)

    # ---- Raw .par view ----
    with st.expander("Raw .par file"):
        raw = st.session_state.par_raw_text or ""
        st.markdown(f'<div class="par-block">{raw}</div>', unsafe_allow_html=True)

    st.divider()

    # -------------------------------------------------------------------------- #
    # Chat Q&A
    # -------------------------------------------------------------------------- #

    st.markdown("### 💬 Ask about this configuration")

    for msg in st.session_state.messages:
        avatar = "🧑" if msg["role"] == "user" else "🤖"
        with st.chat_message(msg["role"], avatar=avatar):
            st.markdown(msg["content"])

    # Resolve pending prompt from sidebar buttons
    user_input = None
    if st.session_state.pending_prompt:
        user_input = st.session_state.pending_prompt
        st.session_state.pending_prompt = None

    # Input form — reliable Enter-key submission across all Streamlit versions
    with st.form("chat_form", clear_on_submit=True):
        col_inp, col_btn = st.columns([5, 1])
        with col_inp:
            typed = st.text_input(
                "query",
                placeholder="Enter a part number (e.g. A.034.447.29.27) or ask a question…",
                label_visibility="collapsed",
                key="chat_text_input",
            )
        with col_btn:
            send = st.form_submit_button("Send →", use_container_width=True)

    if send and typed:
        user_input = typed

    if user_input:
        st.session_state.messages.append({"role": "user", "content": user_input})
        with st.chat_message("user", avatar="🧑"):
            st.markdown(user_input)

        with st.chat_message("assistant", avatar="🤖"):
            try:
                stripped = user_input.strip()
                reply = None
                source_badge = ""

                # Always try part number lookup first — no regex gate
                part_df   = st.session_state.part_df
                param_df  = st.session_state.param_df
                bridge    = st.session_state.qualifier_bridge

                if part_df is not None:
                    with st.spinner("Looking up in part number database…"):
                        pn_result = lookup_by_part_number(
                            stripped, part_df, decoded, dl.QUALIFIER_TO_FEATURE
                        )
                        # Also check if it maps via domain bridge
                        domain_qs = find_par_qualifiers_for_part_number(stripped, param_df, bridge)
                    if pn_result is not None or domain_qs:
                        reply = _answer_part_number(stripped, decoded, part_df, param_df, bridge)
                        source_badge = '<span class="pill blue">📋 Part Number lookup</span>'

                # AI query — Ollama classifies + filters; fallback is deterministic
                if reply is None:
                    # Build compact param dicts for the prompt
                    _param_dicts = []
                    for _p in decoded:
                        _b = bridge.get(_p.qualifier, {})
                        try:
                            _dec = str(int(_p.hex_value, 16))
                        except ValueError:
                            _dec = _p.hex_value
                        _param_dicts.append({
                            "qualifier": _p.qualifier,
                            "field":     _p.qualifier.split(".")[-1],
                            "hex":       _p.hex_value,
                            "decimal":   _dec,
                            "domain":    _b.get("domain", ""),
                            "fragment":  _b.get("fragment", ""),
                            "feature":   _p.feature_name,
                        })

                    with st.spinner("AI is analysing your query…"):
                        reply, matched_qs, qtype, ai_src = ai_query(
                            user_input,
                            _param_dicts,
                            session_info,
                            model=settings.ollama_model,
                            host=settings.ollama_host,
                        )

                    src_label = "🧠 Ollama" if ai_src == "ollama" else "⚙️ rule-based"
                    source_badge = f'<span class="pill {"ok" if ai_src == "ollama" else "warn"}">{src_label} · {qtype}</span>'

                st.markdown(reply)
                st.markdown(source_badge, unsafe_allow_html=True)
                full_reply = reply

            except Exception as exc:
                st.error(f"Error processing input: {exc}")
                full_reply = f"❌ Error: {exc}"

        st.session_state.messages.append({"role": "assistant", "content": full_reply})

else:
    st.markdown(
        """
        <div class="upload-prompt">
          <h3>No .par file loaded</h3>
          <p>Use the <strong>Upload .par file</strong> button in the sidebar,<br>
          or set <code>REFERENCE_PAR</code> in your <code>.env</code> file.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )
