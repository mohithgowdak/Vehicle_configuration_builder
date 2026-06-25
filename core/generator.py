"""Top-level orchestration: NL → .par."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from . import data_loader as dl
from . import encoders
from .assembler import assemble_par, format_p_line
from .config import Settings
from .llm import ParseResult, VehicleConfig, parse_request


@dataclass
class GeneratedParam:
    feature: str
    choice: str
    qualifier: str
    datatype: str
    hex_value: str
    status: str           # ok | unmapped | encode_error
    detail: str = ""


@dataclass
class GenerationResult:
    config: VehicleConfig
    par_text: str
    par_path: Path
    parsed: list[GeneratedParam] = field(default_factory=list)
    review_needed: list[GeneratedParam] = field(default_factory=list)
    llm_source: str = "fallback"
    notes: list[str] = field(default_factory=list)


def generate_from_text(text: str, settings: Settings) -> GenerationResult:
    parse: ParseResult = parse_request(text, model=settings.ollama_model, host=settings.ollama_host)
    cfg = parse.config

    cdd = dl.load_cdd(settings.cdd_xlsx, settings.cdd_xml)
    header = dl.load_reference_par(settings.reference_par)

    parsed: list[GeneratedParam] = []
    review: list[GeneratedParam] = []
    p_lines: list[str] = []

    for feature, choice in cfg.features.items():
        qualifier = dl.NOMENCLATURE_TO_QUALIFIER.get(feature.upper())
        if not qualifier or qualifier not in cdd:
            item = GeneratedParam(
                feature=feature, choice=choice,
                qualifier=qualifier or "?", datatype="?", hex_value="",
                status="unmapped",
                detail="no CDD entry for this feature — flag for human review",
            )
            review.append(item)
            continue
        spec = cdd[qualifier]
        try:
            hex_value = encoders.encode(choice, spec.datatype, spec.encoding, spec.rule)
        except encoders.EncodingError as exc:
            item = GeneratedParam(
                feature=feature, choice=choice,
                qualifier=qualifier, datatype=spec.datatype, hex_value="",
                status="encode_error", detail=str(exc),
            )
            review.append(item)
            continue

        item = GeneratedParam(
            feature=feature, choice=choice,
            qualifier=qualifier, datatype=spec.datatype, hex_value=hex_value,
            status="ok", detail=spec.encoding,
        )
        parsed.append(item)
        p_lines.append(format_p_line(qualifier, spec.datatype, hex_value))

    par_text = assemble_par(header, p_lines, ecu_variant=cfg.variant)
    out_path = settings.output_dir / _filename(cfg)
    out_path.write_text(par_text, encoding="utf-8", newline="")

    return GenerationResult(
        config=cfg,
        par_text=par_text,
        par_path=out_path,
        parsed=parsed,
        review_needed=review,
        llm_source=parse.source,
        notes=parse.notes,
    )


def _filename(cfg: VehicleConfig) -> str:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    variant = (cfg.variant or "CONFIG").replace(" ", "_")
    return f"{variant}_{stamp}.par"
