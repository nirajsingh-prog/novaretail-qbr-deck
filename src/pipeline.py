"""Shared pipeline used by the CLI and the Streamlit app:
data -> metrics -> editable narrative.json -> validated story -> deck."""
from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

from .analysis import Metrics, load_and_analyse, metrics_for_llm
from .deck_builder import build_deck
from .narrative import from_json, rule_based, to_json
from .validation import allowed_numbers, validate_narrative

ROOT = Path(__file__).resolve().parent.parent


def default_paths() -> dict:
    return {
        "template": ROOT / "inputs" / "novaretail_template.pptx",
        "data": ROOT / "inputs" / "novaretail_dataset.xlsx",
        "icons": ROOT / "inputs" / "novaretail_icon_repo.zip",
        "shape_map": ROOT / "shape_map.json",
        "out": ROOT / "output" / "NovaRetail_QBR_Executive_Deck.pptx",
    }


def resolve_icons(path) -> Path:
    """Accept a folder of PNGs, the provided icon .zip path, or zip bytes."""
    if isinstance(path, (bytes, bytearray)):
        src = io.BytesIO(path)
    elif Path(path).suffix.lower() == ".zip":
        src = path
    else:
        return Path(path)
    target = ROOT / "build" / "icons"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(src) as z:
        for member in z.namelist():
            if member.lower().endswith(".png"):
                (target / Path(member).name).write_bytes(z.read(member))
    return target


def initial_state(data) -> dict:
    """Everything the agent needs in ADK session state (all JSON-serialisable)."""
    m = load_and_analyse(data)
    return {
        "narrative": to_json(rule_based(m), m),
        "facts": metrics_for_llm(m),
        "allowed_numbers": allowed_numbers(m),
    }


def load_narrative_file(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_narrative_file(data: dict, path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def render(m: Metrics, narrative_json: dict, out, template=None, icons=None, shape_map=None,
           strict: bool = True) -> tuple[list[str], list[str]]:
    """Validate the editable narrative and build the deck. Returns (build_log, validation_issues)."""
    d = default_paths()
    issues = validate_narrative(narrative_json, allowed_numbers(m))
    if strict and issues:
        raise ValueError("Narrative failed validation:\n  - " + "\n  - ".join(issues))
    story = from_json(narrative_json, m)
    Path(out).parent.mkdir(parents=True, exist_ok=True) if isinstance(out, (str, Path)) else None
    log = build_deck(template or d["template"], out, m, story, resolve_icons(icons or d["icons"]),
                     shape_map or d["shape_map"])
    return log, issues
