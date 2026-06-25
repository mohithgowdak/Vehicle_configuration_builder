# PAR Builder — Setup Guide

Step-by-step instructions to run the **Vehicle Project Configuration Builder** chatbot on a fresh laptop. Tested on Windows 11; the same commands work on macOS/Linux with the platform notes called out.

---

## 1. Prerequisites

| Tool | Version | Required? | Notes |
|---|---|---|---|
| Python | 3.10+ (3.13 recommended) | ✅ | Get it from <https://www.python.org/downloads/> — tick **"Add python.exe to PATH"** during install. |
| Git | any | ✅ | <https://git-scm.com/downloads> |
| Ollama | latest | ⚪ Optional | Only needed if you want real LLM intent parsing. The app falls back to a deterministic regex parser without it. |

Verify after install:
```powershell
python --version
git --version
```

---

## 2. Get the project

```powershell
git clone <your-repo-url> par-builder
cd par-builder
```

If you don't use git, just copy the project folder to the new machine.

---

## 3. Create a virtual environment (recommended)

**Windows (PowerShell):**
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```
If PowerShell blocks script execution, run once:
```powershell
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned
```

**macOS / Linux:**
```bash
python3 -m venv .venv
source .venv/bin/activate
```

You should see `(.venv)` in your prompt.

---

## 4. Install dependencies

```powershell
pip install --upgrade pip
pip install -r requirements.txt
```

This installs:
- `streamlit` — the chat UI
- `pandas` + `openpyxl` — Excel parsing
- `pydantic` — config validation
- `python-dotenv` — `.env` loader
- `ollama` — local LLM client (used only if Ollama is reachable)

---

## 5. Configure `.env`

Copy the template and edit it:

**Windows:**
```powershell
Copy-Item .env.example .env
notepad .env
```

**macOS / Linux:**
```bash
cp .env.example .env
nano .env
```

### Required paths

```env
# Excel scope: which parameters apply per variant
PART_NUMBER_XLSX=D:/artiMIND/Vehicle_configuration_builder/data/part_numbers.xlsx

# CDD codebook (Excel form). Columns: qualifier, datatype, encoding, rule
CDD_XLSX=D:/artiMIND/Vehicle_configuration_builder/data/cdd.xlsx
CDD_XML=                                       # leave blank for now

# Reference .par used as the S/H header template
REFERENCE_PAR=D:/artiMIND/Vehicle_configuration_builder/data/reference.par

# Where generated .par files land
OUTPUT_DIR=D:/artiMIND/Vehicle_configuration_builder/out
```

**Tip:** use forward slashes (`/`) on Windows paths inside `.env` to avoid backslash-escape surprises.

**No data files yet?** The app ships with seed fixtures from the spec — leave the paths blank and it still runs end-to-end on the built-in sample.

### Optional: Ollama (local LLM)

```env
OLLAMA_MODEL=qwen2.5:7b
OLLAMA_HOST=                                    # blank = http://localhost:11434
```

### Optional: animated background

```env
BACKGROUND_IMAGE=D:/artiMIND/Vehicle_configuration_builder/assets/truck.mp4
BACKGROUND_OVERLAY=0.35                         # 0.0 = no dim, 0.9 = very dark
```

Supported formats:
- **Image:** `.gif`, `.png`, `.jpg`, `.webp` — rendered via CSS `background-image`
- **Video:** `.mp4`, `.webm`, `.mov`, `.m4v` — rendered via injected `<video autoplay muted loop>`

Leave `BACKGROUND_IMAGE` blank for the default gradient look.

---

## 6. (Optional) Install Ollama + pull a model

Skip this if you only need the demo with the fallback parser.

1. Install from <https://ollama.com/download>.
2. In a new terminal:
   ```powershell
   ollama serve              # leave running in this terminal
   ```
3. In another terminal, pull a model:
   ```powershell
   ollama pull qwen2.5:7b    # ~4.5 GB; downloads once
   ```
   Smaller / faster alternatives: `llama3.2:3b`, `qwen2.5:3b`.

Verify:
```powershell
ollama list
```

---

## 7. Run the app

From the project root with the venv active:

```powershell
streamlit run app.py
```

Streamlit prints a URL like `http://localhost:8501`. Open it in any browser.

**Run on a custom port:**
```powershell
streamlit run app.py --server.port 8765
```

**Run headless (no auto-open browser):**
```powershell
streamlit run app.py --server.headless true
```

---

## 8. Try it out

In the chat input at the bottom, paste one of:

- `Build a CGW05T with timer list default, BS EBS WITH ESP, DRIVE LEFT-HAND`
- `Variant CGW05T: TIMER LIST: DEFAULT, TPM TYPE: TPM2`
- `CGW05T left-hand drive with ESP and TPM2`

You should see:
1. **Parsed config** card (variant + features)
2. **Encoded parameters** table (feature → qualifier → hex)
3. **Generated .par** preview with a download button
4. Anything that didn't resolve → flagged in **Needs human review**

Generated files are saved to `OUTPUT_DIR` (default `./out`).

---

## 9. Project layout

```
par-builder/
├── app.py                   # Streamlit chat UI
├── core/
│   ├── config.py            # .env-driven Settings
│   ├── data_loader.py       # Excel + reference .par loaders (+ seed fixtures)
│   ├── encoders.py          # enum / linear / bitfield → hex (pure functions)
│   ├── assembler.py         # emits S/H/P lines
│   ├── llm.py               # Ollama JSON-mode + regex fallback
│   └── generator.py         # orchestration: NL → .par
├── data/                    # your Excel + reference .par live here
├── assets/                  # optional background gif/mp4
├── out/                     # generated .par files
├── .env.example             # copy → .env
├── requirements.txt
├── PAR_BUILDER_SPEC.md      # the original spec
└── SETUP.md                 # this file
```

---

## 10. Troubleshooting

### `ModuleNotFoundError: No module named 'streamlit'`
The venv isn't active. Re-run:
```powershell
.\.venv\Scripts\Activate.ps1
```
Look for `(.venv)` in the prompt.

### `streamlit` command not found
Either the venv isn't active, or pip installed scripts outside PATH. Workaround:
```powershell
python -m streamlit run app.py
```

### `AttributeError: 'Settings' object has no attribute '…'`
Streamlit cached an old `Settings` instance. Hard-restart the server (Ctrl+C, then `streamlit run app.py` again).

### Background video isn't showing
- It must be `.mp4` / `.webm` / `.mov` / `.m4v` — **GIF won't render as video**, and **MP4 won't render via CSS background-image** (that's why we handle them differently).
- The path in `.env` must exist — check with `Get-Item "<path>"`.
- Drop `BACKGROUND_OVERLAY` to `0.0` temporarily to confirm the video is loading.

### Background video plays but covers the UI
Hard-refresh the browser (Ctrl+Shift+R). The fix is z-index: -1 on the video wrap; if it's still wrong, check the browser console for `bg video inject failed`.

### Port already in use
```powershell
streamlit run app.py --server.port 8765
```

### Ollama is installed but the app says "fallback parser"
- Run `ollama serve` in its own terminal and keep it open.
- Check the model is pulled: `ollama list`.
- If `OLLAMA_MODEL` in `.env` is a model you haven't pulled, pull it: `ollama pull <model>`.

### Excel won't load
- Make sure it's `.xlsx`, not legacy `.xls`.
- Required columns for **Part Number xlsx**: `Partnumber`, `Nomenclature`, `ECU Qualifier`, `Type`.
- Required columns for **CDD xlsx**: `qualifier`, `datatype`, `encoding`, `rule` (rule is a JSON string).

---

## 11. Daily workflow (after first setup)

```powershell
cd par-builder
.\.venv\Scripts\Activate.ps1
ollama serve                      # in a separate terminal, if you use Ollama
streamlit run app.py
```

That's it.
