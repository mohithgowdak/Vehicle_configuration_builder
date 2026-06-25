# Vehicle Project Configuration Builder — Build Spec

A chatbot that turns a natural-language vehicle-config request into a **validated ECU `.par` parameter file**. The LLM parses intent and orchestrates; **deterministic code does all byte encoding** (a hallucinated hex value = a corrupted ECU flash, so the model must never invent values).

---

## 1. The problem

Test teams need ECU `.par` parameter files matched to a specific vehicle config. Today they're hand-extracted from vehicles or chased from component owners — slow, inconsistent, untraceable. Goal: an agent that **generates, validates, and version-controls** `.par` files from a config request.

---

## 2. The three input datasets and their roles

| Dataset | Role | Answers |
|---|---|---|
| **Part Number Dataset** (Excel) | Scope: which parameters apply to a variant/program. Columns: `Partnumber`, `Nomenclature`, `ECU Qualifier`, `Type`. | *What's in scope* |
| **CDD — CANdela Diagnostic Description** (XML) | Codebook: per-qualifier datatype + encoding rule (enum / scale / bitfield). **This is where hex values come from.** | *Value → hex* |
| **ECU Release Management Dataset** | Baseline: software/CBF version, header metadata, default param set. | *Header + defaults* |

**Output:** a `.par` file.

> Critical: the `.par` and Excel alone CANNOT produce valid bytes. The encoding rule (e.g. "5 min → `0x4B`") lives ONLY in the CDD. The CDD is the heart of the system.

---

## 3. The `.par` file format

Flat CSV, one record per line, first letter = record type.

```
S,ECU,CGW05T                          # Session: target ECU
S,DIAGNOSISVARIANT,CGW05T_App_1014    # Session: diagnostic variant
H,APPNAME,Drumroll                    # Header: metadata (regenerated, not flashed)
H,APPVERSION,8.22.6146.14
H,SAPIVERSION,1.32.888.6
H,CBFVERSION,04.03.80                 # which codebook baseline
H,TIMESTAMP,20260302135036950         # yyyyMMddHHmmssSSS
H,UTCOFFSET,01:00:00
H,QUALIFIERFORMAT,MCD
P,VCD_CGW_BKAM_Timer.Timeout_Value,B,4B   # Parameter payload
P,VCD_CGW_BKAM_Timer.CreateReset,B,FF
```

### The `P` line — 4 fields
```
P , VCD_CGW_BKAM_Timer.Timeout_Value , B , 4B
↑           ↑                          ↑    ↑
type   qualifier (struct.field)    datatype value(hex)
```
1. **Record type** — `P` = parameter.
2. **Qualifier** — fully-qualified name. Before the dot = domain/struct, after = field. One domain emits multiple `P` lines.
3. **Datatype** — `B`=byte(1), `W`=word(2), `L`=long(4), `A`=array/blob (long `GVCData` hex strings).
4. **Value** — hex, length must match datatype. ⚠️ Confirm **endianness** for multi-byte (need one known Excel↔par pair to pin down).

---

## 4. Mapping logic

### Forward (`.par` → readable, for validation)
- **Domain** ← qualifier prefix before the dot.
- **Value** ← `P` value field (concatenated across a domain's fields).
- **Meaning** ← CDD decode of the hex (e.g. `4B`+`FF` → "BKAM: 5 min, no reset").
- **Part Number** ← join decoded meaning against Excel `Nomenclature`, dots stripped (`A.033.447.29.27` → `A0334472927`).

### Reverse (config → `.par`, the generator)
1. **Filter scope** — Excel rows where `Type == PARAMETER` for the target variant.
2. **Parse Nomenclature** as `<FEATURE>: <CHOICE>` (`TIMER LIST: DEFAULT`, `TRACKWIDTH: 2550`).
3. **Lookup CDD** by qualifier (key on **Partnumber**, not the fuzzy/truncated Nomenclature) → datatype + encoding rule.
4. **Encode** choice → hex via the rule (enum / linear-scale / bitfield).
5. **Emit** `P,<qualifier>,<datatype>,<hex>`; prepend `S`/`H` block from Release Mgmt.

---

## 5. Architecture

```
NL request ("program X, variant CGW05T, timer list default, EBS with ESP")
      │
 [LLM] parse intent → structured config  ── model only parses, never encodes
      │
 [Agent] resolve scope            ← Part Number Dataset
      │   → applicable parameters
 [Agent] per-param lookup         ← CDD
      │   → qualifier, datatype, encoding rule
 [TOOL] encode (enum|scale|bitfield)   ← PURE FUNCTION, deterministic bytes
      │
 [TOOL] assemble S/H/P block      ← ECU Release Mgmt (header + defaults)
      │   → draft .par
 [VALIDATOR] round-trip decode + range/enum check vs CDD
      │   → pass: version & publish   |   fail: flag for human review
      ▼
   .par file
```

**LLM does:** intent parsing, fuzzy-match truncated Nomenclatures to CDD entries, reconcile naming drift, flag unmapped params, explain gaps.
**LLM never does:** produce hex bytes. Encoding + validation are pure functions.

---

## 6. Validation gate (delivers consistency + traceability)

Generate `.par` → re-parse it back through the CDD into feature choices → **diff against the input config**. Zero diff = valid. Non-zero = block output, flag for review. This one mechanism catches both hallucination and CDD gaps.

---

## 7. Suggested stack & layout

- **Backend:** Python + FastAPI + LangGraph (nodes: parse → scope → lookup → encode → validate → publish, with a human-review branch).
- **LLM:** **Ollama (local).** The model only parses NL → structured config; it never encodes bytes, so a small local model is plenty and ECU/CDD IP never leaves the machine.
- **Encoders:** three pure functions — `encode_enum`, `encode_linear` (`raw=(physical-offset)/resolution`), `encode_bitfield`.
- **Chatbot UI:** chat in, generated `.par` rendered + downloadable, validation result shown inline.
- **Versioning:** tag each generated `.par` with config hash + CBF version; simple store for controlled distribution.

```
par-builder/
├── data/                # part_number.xlsx, cdd.xml, release_mgmt.*
├── core/
│   ├── cdd_loader.py    # parse CDD → {qualifier: {datatype, encoding, rule}}
│   ├── scope.py         # Excel → applicable params for variant
│   ├── encoders.py      # encode_enum / encode_linear / encode_bitfield
│   ├── assembler.py     # emit S/H/P lines
│   └── validator.py     # round-trip decode + diff
├── agent/
│   └── graph.py         # LangGraph orchestration
├── api/
│   └── main.py          # FastAPI: POST /generate, /validate
└── ui/                  # chatbot front-end
```

---

## 8. Ollama (local LLM) integration

The model does **one** job: turn free text into a validated JSON config. Everything downstream is deterministic, so the LLM is the lowest-risk part of the system.

**Model choice** — pick one that's strong at structured extraction:
- `qwen2.5:7b` or `qwen2.5:14b` — best JSON adherence in this size class.
- `llama3.1:8b` — solid all-rounder.
- Start at 7B; only go bigger if intent parsing misfires on messy requests.

```bash
ollama pull qwen2.5:7b
ollama serve            # exposes http://localhost:11434
```

**Constrain the output with a JSON schema** so the model can't free-wheel. Ollama supports `format` (JSON schema) and `temperature: 0` for deterministic extraction. Validate the result against a Pydantic model; on schema failure, **retry once then fall to human-review** — never pass an unvalidated config downstream.

```python
# agent/llm.py
from langchain_ollama import ChatOllama
from pydantic import BaseModel

class VehicleConfig(BaseModel):
    program: str
    variant: str                 # e.g. "CGW05T"
    features: dict[str, str]      # {"TIMER LIST": "DEFAULT", "BS": "EBS WITH ESP"}

llm = ChatOllama(model="qwen2.5:7b", temperature=0)
parser = llm.with_structured_output(VehicleConfig)   # enforces JSON schema

def parse_request(text: str) -> VehicleConfig:
    return parser.invoke(
        "Extract the vehicle config. Only use feature names and choices "
        "present in the request; do not invent values.\n\n" + text
    )
```

Guardrails specific to a local model:
- `temperature=0` for repeatable parsing.
- The model maps text → *feature choices only*. The CHOICE must still exist in the Excel/CDD; reject anything that doesn't resolve in the scope step.
- Keep the system prompt short and example-driven — small models follow few-shot better than long instructions.

---

## 9. 4-day timebox

- **Day 1 — Datasets → unified model.** Parse all three. CDD is CANdelaStudio XML; loader must extract per-qualifier datatype + encoding type + enum/scale/bitfield rule. *Make-or-break.* **Verify the CDD covers every qualifier in the target `.par`** (esp. the long `GVCData_1` blobs). Any CBF-only params → route to human-review, don't let them block the build.
- **Day 2 — Encoders + assembler.** Build the three encoders + S/H/P emitter. Validate against the known-good `.par` as ground truth. Pin down endianness here.
- **Day 3 — Agent + chatbot.** LangGraph nodes, NL intent parsing, human-review branch, version tagging, distribution store, chat UI.
- **Day 4 — Round-trip validator + edge cases** (blank Nomenclatures = defaults or skip?, multi-byte endianness) + deck (problem, architecture, demo).

---

## 10. Open questions to resolve Day 1

1. CDD join key — **Partnumber** (stable) vs a hidden qualifier column? Confirm.
2. Blank-Nomenclature rows (e.g. `A.031.447.13.27`) — carry a default value, or skipped entirely?
3. Multi-byte endianness — little vs big. Need one known Excel-value ↔ par-hex pair.
4. Does the CDD encode the long `GVCData_1` array blobs, or are those CBF-only?

---

## 11. Seed data (from reference files, for tests/mocks)

```
S,ECU,CGW05T
S,DIAGNOSISVARIANT,CGW05T_App_1014
H,APPNAME,Drumroll
H,APPVERSION,8.22.6146.14
H,SAPIVERSION,1.32.888.6
H,CBFVERSION,04.03.80
H,QUALIFIERFORMAT,MCD
P,VCD_CGW_BKAM_Timer.Timeout_Value,B,4B
P,VCD_CGW_BKAM_Timer.CreateReset,B,FF
P,VCD_CGW_Config_Adjustable_SelfHealing.SelfHealing_Category_2,B,...
P,VCD_CGW_Ecu_Timer_List.ECU_Monitoring_Start_delay_bus_wake_up,B,2
```

Known Excel parameter rows (all `ECU Qualifier = CGW05T`, `Type = PARAMETER`):
`A.034.447.29.27 TIMER LIST: DEFAULT` · `A.033.447.10.27 TRACKWIDTH: 2550` · `A.033.447.21.27 TPM TYPE: TPM2` · `A.033.447.68.27 STEERPARA-EVO-RF` · `A.034.447.41.27 BS: EBS WITH ESP` · `A.034.447.96.27 DRIVE: LEFT-HAND`
