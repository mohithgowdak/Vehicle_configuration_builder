from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    part_number_xlsx: Path | None
    cdd_xlsx: Path | None
    cdd_xml: Path | None
    reference_par: Path | None
    output_dir: Path
    ollama_model: str
    ollama_host: str | None
    background_image: Path | None
    background_overlay: float

    @classmethod
    def load(cls) -> "Settings":
        def _path(key: str) -> Path | None:
            v = os.getenv(key, "").strip()
            return Path(v) if v else None

        out = _path("OUTPUT_DIR") or Path("out")
        out.mkdir(parents=True, exist_ok=True)
        try:
            overlay = float(os.getenv("BACKGROUND_OVERLAY", "0.55"))
        except ValueError:
            overlay = 0.55
        return cls(
            part_number_xlsx=_path("PART_NUMBER_XLSX"),
            cdd_xlsx=_path("CDD_XLSX"),
            cdd_xml=_path("CDD_XML"),
            reference_par=_path("REFERENCE_PAR"),
            output_dir=out,
            ollama_model=os.getenv("OLLAMA_MODEL", "qwen2.5:7b").strip(),
            ollama_host=os.getenv("OLLAMA_HOST", "").strip() or None,
            background_image=_path("BACKGROUND_IMAGE"),
            background_overlay=max(0.0, min(0.95, overlay)),
        )

    def status(self) -> dict[str, tuple[bool, str]]:
        def check(p: Path | None) -> tuple[bool, str]:
            if p is None:
                return False, "not set"
            return (p.exists(), str(p))

        return {
            "Part Number xlsx": check(self.part_number_xlsx),
            "CDD xlsx": check(self.cdd_xlsx),
            "CDD xml": check(self.cdd_xml),
            "Reference .par": check(self.reference_par),
        }
