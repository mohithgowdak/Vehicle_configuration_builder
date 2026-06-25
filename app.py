"""Streamlit chat UI for the Vehicle Project Configuration Builder.

Run with:
    streamlit run app.py
"""
from __future__ import annotations

import base64
import mimetypes
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

from core import data_loader as dl
from core.config import Settings
from core.generator import GenerationResult, generate_from_text

# -------------------------------------------------------------------------- #
# Page + theme
# -------------------------------------------------------------------------- #

st.set_page_config(
    page_title="PAR Builder · Vehicle Config Chatbot",
    page_icon="🚙",
    layout="wide",
    initial_sidebar_state="expanded",
)

_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".ogv"}


def _load_background(path: Path | None) -> tuple[str | None, str | None]:
    """Return (data_uri, kind) where kind is 'image' | 'video' | None."""
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
    """Inject the <video> into the parent document body via JS.

    The wrap is *prepended* to <body> and pinned at z-index:-1 so the entire
    Streamlit DOM paints over it. `html`/`body`/`#root`/the Streamlit containers
    are forced transparent so the negative z-index isn't occluded.
    """
    return f"""
<script>
(function() {{
    try {{
        const doc = window.parent.document;

        // Force background transparency up the chain so z-index:-1 is visible.
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
        video.autoplay = true;
        video.muted = true;
        video.loop = true;
        video.playsInline = true;
        video.setAttribute('playsinline', '');
        video.setAttribute('autoplay', '');
        video.setAttribute('muted', '');
        video.setAttribute('loop', '');
        video.style.cssText = 'position:absolute;top:50%;left:50%;min-width:100%;min-height:100%;width:auto;height:auto;transform:translate(-50%,-50%);object-fit:cover;';

        const source = doc.createElement('source');
        source.src = '{uri}';
        source.type = '{mime}';
        video.appendChild(source);

        const overlayDiv = doc.createElement('div');
        overlayDiv.id = 'bg-video-overlay';
        overlayDiv.style.cssText = 'position:absolute;top:0;left:0;width:100%;height:100%;background:rgba(8,12,28,{overlay});pointer-events:none;';

        wrap.appendChild(video);
        wrap.appendChild(overlayDiv);
        doc.body.insertBefore(wrap, doc.body.firstChild);

        // Try to play (some browsers need an explicit nudge).
        const p = video.play();
        if (p && p.catch) p.catch(() => {{}});
    }} catch (e) {{ console.error('bg video inject failed', e); }}
}})();
</script>
"""


def build_css(bg_uri: str | None, kind: str | None, overlay: float) -> str:
    if kind == "image" and bg_uri:
        bg_layer = (
            f"linear-gradient(rgba(8,12,28,{overlay}), rgba(8,12,28,{overlay})), "
            f"url('{bg_uri}')"
        )
        app_bg = f"background: {bg_layer} center / cover no-repeat fixed;"
        card_bg = "background: rgba(255,255,255,0.92); backdrop-filter: blur(6px);"
        hero_extra = "backdrop-filter: blur(4px);"
        sidebar_bg = "background: rgba(248, 250, 252, 0.92); backdrop-filter: blur(8px);"
    elif kind == "video":
        # Video element + overlay live in fixed-position layers behind the app.
        app_bg = "background: transparent;"
        card_bg = "background: rgba(255,255,255,0.92); backdrop-filter: blur(6px);"
        hero_extra = "backdrop-filter: blur(4px);"
        sidebar_bg = "background: rgba(248, 250, 252, 0.92); backdrop-filter: blur(8px);"
    else:
        app_bg = "background: linear-gradient(180deg, #f1f5f9 0%, #e2e8f0 100%);"
        card_bg = "background: #ffffff;"
        hero_extra = ""
        sidebar_bg = "background: #f8fafc;"

    return f"""
<style>
:root {{
    --accent: #2563eb;
    --ok: #16a34a;
    --warn: #d97706;
    --err: #dc2626;
    --muted: #64748b;
    --card-border: #e2e8f0;
}}
html, body, [class*="css"] {{ font-family: 'Inter', system-ui, sans-serif; }}
html, body {{ background: transparent !important; }}

[data-testid="stAppViewContainer"] {{ {app_bg} }}
[data-testid="stHeader"] {{ background: transparent; }}
section.main > div {{ padding-top: 0.5rem; position: relative; z-index: 1; }}

#bg-video-wrap {{
    position: fixed; inset: 0; z-index: 0;
    overflow: hidden; pointer-events: none;
}}
#bg-video-wrap video {{
    position: absolute; top: 50%; left: 50%;
    min-width: 100%; min-height: 100%;
    width: auto; height: auto;
    transform: translate(-50%, -50%);
    object-fit: cover;
}}
#bg-video-overlay {{
    position: absolute; inset: 0;
}}

.app-hero {{
    background: linear-gradient(120deg, rgba(30,58,138,0.92) 0%, rgba(37,99,235,0.88) 70%, rgba(59,130,246,0.82) 100%);
    color: white;
    padding: 22px 28px;
    border-radius: 16px;
    margin-bottom: 18px;
    box-shadow: 0 10px 30px rgba(15, 23, 42, 0.35);
    {hero_extra}
}}
.app-hero h1 {{ margin: 0; font-size: 1.55rem; font-weight: 700; letter-spacing: -0.01em; }}
.app-hero p  {{ margin: 6px 0 0 0; opacity: 0.9; font-size: 0.92rem; }}

.pill {{
    display: inline-flex; align-items: center; gap: 6px;
    padding: 3px 10px; border-radius: 999px;
    font-size: 0.78rem; font-weight: 500;
    border: 1px solid var(--card-border); background: #f8fafc; color: #1e293b;
    margin: 2px 4px 2px 0;
}}
.pill.ok   {{ background: #ecfdf5; color: #065f46; border-color: #a7f3d0; }}
.pill.warn {{ background: #fffbeb; color: #92400e; border-color: #fcd34d; }}
.pill.err  {{ background: #fef2f2; color: #991b1b; border-color: #fecaca; }}

.kv-card {{
    {card_bg}
    border: 1px solid var(--card-border);
    border-radius: 12px; padding: 14px 16px; margin: 8px 0;
    box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
}}
.kv-card h4 {{ margin: 0 0 8px 0; font-size: 0.85rem; color: var(--muted);
              text-transform: uppercase; letter-spacing: 0.05em; font-weight: 600; }}

.par-block {{
    background: #0f172a; color: #e2e8f0; border-radius: 12px;
    padding: 14px 16px; font-family: 'JetBrains Mono', 'Consolas', monospace;
    font-size: 0.82rem; line-height: 1.55; white-space: pre; overflow-x: auto;
    border: 1px solid #1e293b;
}}

div[data-testid="stChatMessage"] {{
    {card_bg}
    border: 1px solid var(--card-border);
    border-radius: 14px; padding: 14px 16px; margin-bottom: 10px;
    box-shadow: 0 4px 16px rgba(15, 23, 42, 0.08);
}}

div[data-testid="stSidebar"] {{ {sidebar_bg} }}
div[data-testid="stSidebar"] h2 {{ font-size: 1.05rem; }}

.suggest-btn button {{
    width: 100%; text-align: left; font-size: 0.82rem; padding: 8px 12px;
    border-radius: 10px; border: 1px solid var(--card-border) !important;
    background: rgba(255,255,255,0.95) !important; color: #0f172a !important;
}}
.suggest-btn button:hover {{ border-color: var(--accent) !important; color: var(--accent) !important; }}
</style>
"""


st.session_state.settings = Settings.load()
_settings_for_css: Settings = st.session_state.settings
_bg_uri, _bg_kind = _load_background(_settings_for_css.background_image)
st.markdown(
    build_css(_bg_uri, _bg_kind, _settings_for_css.background_overlay),
    unsafe_allow_html=True,
)
if _bg_kind == "video" and _bg_uri:
    # mime sniffed from the data URI prefix
    _mime = _bg_uri.split(";", 1)[0].removeprefix("data:") or "video/mp4"
    from streamlit.components.v1 import html as _components_html
    _components_html(
        build_background_injector(_bg_uri, _mime, _settings_for_css.background_overlay),
        height=0,
    )

# -------------------------------------------------------------------------- #
# State
# -------------------------------------------------------------------------- #

if "messages" not in st.session_state:
    st.session_state.messages = []                       # list[dict]
if "results" not in st.session_state:
    st.session_state.results = {}                        # message_index -> GenerationResult
if "pending_prompt" not in st.session_state:
    st.session_state.pending_prompt = None

settings: Settings = st.session_state.settings

# -------------------------------------------------------------------------- #
# Sidebar — data + engine status
# -------------------------------------------------------------------------- #

with st.sidebar:
    st.markdown("## 🚙 PAR Builder")
    st.caption("Vehicle config → validated `.par`")
    st.divider()

    st.markdown("### Data sources")
    for label, (ok, path) in settings.status().items():
        css = "ok" if ok else "err"
        icon = "✓" if ok else "—"
        st.markdown(
            f'<div class="pill {css}">{icon} {label}</div>'
            f'<div style="font-size:0.72rem;color:#64748b;margin:-2px 0 8px 4px;">{path}</div>',
            unsafe_allow_html=True,
        )

    st.markdown("### Engine")
    st.markdown(f'<div class="pill">🧠 Ollama · {settings.ollama_model}</div>', unsafe_allow_html=True)
    st.caption("Falls back to a deterministic regex parser if Ollama is unreachable.")

    st.divider()
    st.markdown("### Try a prompt")
    suggestions = [
        "Build a CGW05T with timer list default, BS EBS WITH ESP, DRIVE LEFT-HAND",
        "Variant CGW05T: TIMER LIST: DEFAULT, TPM TYPE: TPM2",
        "CGW05T left-hand drive with ESP and TPM2",
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
        st.session_state.results = {}
        st.rerun()

# -------------------------------------------------------------------------- #
# Hero
# -------------------------------------------------------------------------- #

st.markdown(
    """
    <div class="app-hero">
      <h1>Vehicle Project Configuration Builder</h1>
      <p>Describe the vehicle. I'll parse the intent, look up the codebook, encode bytes deterministically, and hand you a validated <code>.par</code> file.</p>
    </div>
    """,
    unsafe_allow_html=True,
)


# -------------------------------------------------------------------------- #
# Render helpers
# -------------------------------------------------------------------------- #

def render_config_card(result: GenerationResult) -> None:
    cfg = result.config
    features_html = "".join(
        f'<span class="pill">{k}: <b>{v}</b></span>' for k, v in cfg.features.items()
    ) or '<span class="pill warn">no features extracted</span>'
    source_pill = (
        f'<span class="pill ok">🧠 {result.llm_source}</span>'
        if result.llm_source == "ollama"
        else '<span class="pill warn">⚙️ fallback parser</span>'
    )
    st.markdown(
        f"""
        <div class="kv-card">
          <h4>Parsed config</h4>
          <span class="pill">Variant: <b>{cfg.variant or '—'}</b></span>
          {source_pill}
          <div style="margin-top:8px;">{features_html}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_param_table(result: GenerationResult) -> None:
    if result.parsed:
        df = pd.DataFrame([asdict(p) for p in result.parsed])
        df = df[["feature", "choice", "qualifier", "datatype", "hex_value", "detail"]]
        df.columns = ["Feature", "Choice", "Qualifier", "Type", "Hex", "Encoding"]
        st.markdown('<div class="kv-card"><h4>Encoded parameters</h4>', unsafe_allow_html=True)
        st.dataframe(df, use_container_width=True, hide_index=True)
        st.markdown("</div>", unsafe_allow_html=True)


def render_review(result: GenerationResult) -> None:
    if not result.review_needed:
        return
    st.markdown('<div class="kv-card"><h4>⚠️ Needs human review</h4>', unsafe_allow_html=True)
    df = pd.DataFrame([asdict(p) for p in result.review_needed])
    df = df[["feature", "choice", "qualifier", "status", "detail"]]
    df.columns = ["Feature", "Choice", "Qualifier", "Status", "Reason"]
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.markdown("</div>", unsafe_allow_html=True)


def render_par(result: GenerationResult) -> None:
    st.markdown('<div class="kv-card"><h4>Generated .par</h4>', unsafe_allow_html=True)
    st.markdown(f'<div class="par-block">{result.par_text}</div>', unsafe_allow_html=True)
    st.download_button(
        "⬇️  Download .par",
        data=result.par_text,
        file_name=result.par_path.name,
        mime="text/plain",
        use_container_width=False,
    )
    st.caption(f"Saved to `{result.par_path}`")
    st.markdown("</div>", unsafe_allow_html=True)


def render_assistant_payload(result: GenerationResult) -> None:
    render_config_card(result)
    render_param_table(result)
    render_review(result)
    render_par(result)


# -------------------------------------------------------------------------- #
# Chat history replay
# -------------------------------------------------------------------------- #

for idx, msg in enumerate(st.session_state.messages):
    avatar = "🧑" if msg["role"] == "user" else "🤖"
    with st.chat_message(msg["role"], avatar=avatar):
        st.markdown(msg["content"])
        result = st.session_state.results.get(idx)
        if result:
            render_assistant_payload(result)


# -------------------------------------------------------------------------- #
# Input handling
# -------------------------------------------------------------------------- #

user_input = st.chat_input("Describe the vehicle config…  (e.g. 'CGW05T, timer list default, BS EBS WITH ESP')")
if not user_input and st.session_state.pending_prompt:
    user_input = st.session_state.pending_prompt
    st.session_state.pending_prompt = None

if user_input:
    st.session_state.messages.append({"role": "user", "content": user_input})
    with st.chat_message("user", avatar="🧑"):
        st.markdown(user_input)

    with st.chat_message("assistant", avatar="🤖"):
        with st.spinner("Parsing intent, looking up CDD, encoding bytes…"):
            try:
                result = generate_from_text(user_input, settings)
            except Exception as exc:  # noqa: BLE001
                st.error(f"Generation failed: {exc}")
                st.session_state.messages.append({"role": "assistant", "content": f"❌ {exc}"})
                st.stop()

        ok_n = len(result.parsed)
        review_n = len(result.review_needed)
        summary = (
            f"Generated **{result.par_path.name}** at {datetime.now():%H:%M:%S}. "
            f"Encoded **{ok_n}** parameter(s)"
            + (f", flagged **{review_n}** for review." if review_n else ".")
        )
        st.markdown(summary)
        render_assistant_payload(result)

    assistant_idx = len(st.session_state.messages)
    st.session_state.messages.append({"role": "assistant", "content": summary})
    st.session_state.results[assistant_idx] = result
