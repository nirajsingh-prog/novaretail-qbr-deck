"""NovaRetail QBR deck generator.

Usage:
    python generate_deck.py                       # rule-based narrative
    python generate_deck.py --llm gemini          # Gemini-written narrative (validated, with fallback)
    python generate_deck.py --inspect             # list every shape name in the template
"""
from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path

from src.analysis import load_and_analyse
from src.deck_builder import build_deck, inspect
from src.narrative import build_narrative

ROOT = Path(__file__).parent


def resolve_icons(path: Path) -> Path:
    """Accept either a folder of PNGs or the provided novaretail_icon_repo.zip."""
    if path.suffix.lower() == ".zip":
        target = ROOT / "build" / "icons"
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path) as z:
            for member in z.namelist():
                if member.lower().endswith(".png"):
                    (target / Path(member).name).write_bytes(z.read(member))
        return target
    return path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", default=ROOT / "inputs" / "novaretail_template.pptx", type=Path)
    ap.add_argument("--data", default=ROOT / "inputs" / "novaretail_dataset.xlsx", type=Path)
    ap.add_argument("--icons", default=ROOT / "inputs" / "novaretail_icon_repo.zip", type=Path)
    ap.add_argument("--out", default=ROOT / "output" / "NovaRetail_QBR_Executive_Deck.pptx", type=Path)
    ap.add_argument("--llm", choices=["rules", "gemini"], default="rules")
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--shape-map", type=Path, default=ROOT / "shape_map.json", help="optional JSON of exact shape names per slide")
    ap.add_argument("--inspect", action="store_true", help="print template shape names and exit")
    a = ap.parse_args()

    if a.inspect:
        inspect(a.template)
        return

    metrics = load_and_analyse(a.data)
    story = build_narrative(metrics, a.llm, a.model)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    (a.out.parent / "narrative.json").write_text(json.dumps(
        {k if isinstance(k, str) else f"slide_{k}": v for k, v in story.items()}, indent=2, ensure_ascii=False),
         encoding="utf-8")
    log = build_deck(a.template, a.out, metrics, story, resolve_icons(a.icons), a.shape_map)
    print("\n".join(log))
    print(f"\nDeck written to {a.out}")


if __name__ == "__main__":
    main()
