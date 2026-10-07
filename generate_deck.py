"""NovaRetail QBR deck generator (CLI).

Usage:
    python generate_deck.py                                  # data-driven draft -> narrative.json -> deck
    python generate_deck.py --narrative-only                 # write output/narrative.json only (edit it, then...)
    python generate_deck.py --narrative output/narrative.json  # build the deck from the edited narrative
    python generate_deck.py --llm adk --instruction "Make it more executive-friendly"
                                                             # refine with the Google ADK agent, then build
    python generate_deck.py --inspect                        # list every shape name in the template
    streamlit run app.py                                     # interactive review studio (upload, review, chat, generate)
"""
from __future__ import annotations

import argparse
from pathlib import Path

from src.analysis import load_and_analyse
from src.deck_builder import inspect
from src.narrative import rule_based, to_json
from src.pipeline import default_paths, initial_state, load_narrative_file, render, save_narrative_file

D = default_paths()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template", default=D["template"], type=Path)
    ap.add_argument("--data", default=D["data"], type=Path)
    ap.add_argument("--icons", default=D["icons"], type=Path)
    ap.add_argument("--out", default=D["out"], type=Path)
    ap.add_argument("--shape-map", type=Path, default=D["shape_map"])
    ap.add_argument("--narrative", type=Path, help="build from an edited narrative.json instead of the auto draft")
    ap.add_argument("--narrative-only", action="store_true", help="write narrative.json and stop (no deck)")
    ap.add_argument("--llm", choices=["rules", "adk"], default="rules")
    ap.add_argument("--instruction", default="Polish the whole deck for a CEO audience: sharper insight-led "
                                             "headlines, concise bullets, no new numbers.")
    ap.add_argument("--model", default=None, help="Gemini model for the ADK agent (default gemini-2.5-flash)")
    ap.add_argument("--user", default="cli-user", help="ADK user id (preferences are remembered per user)")
    ap.add_argument("--no-strict", action="store_true", help="build even if the narrative has validation warnings")
    ap.add_argument("--inspect", action="store_true", help="print template shape names and exit")
    a = ap.parse_args()

    if a.inspect:
        inspect(a.template)
        return

    m = load_and_analyse(a.data)
    nar_path = a.out.parent / "narrative.json"

    if a.narrative:
        narrative = load_narrative_file(a.narrative)
        print(f"[info] Using edited narrative: {a.narrative}")
    elif a.llm == "adk":
        from src.agent import DEFAULT_MODEL, DeckStudio, adk_configured
        ok, how = adk_configured()
        if not ok:
            raise SystemExit(f"ADK not configured: {how}")
        studio = DeckStudio(model=a.model or DEFAULT_MODEL)
        sid = studio.start_session(a.user, initial_state(a.data))
        res = studio.chat(a.user, sid, a.instruction)
        print(f"[agent] tools used: {', '.join(res['tool_calls']) or 'none'}\n[agent] {res['reply']}")
        narrative = {**res["narrative"], "source": "adk-agent"}
    else:
        narrative = to_json(rule_based(m), m)

    if not a.narrative:
        save_narrative_file(narrative, nar_path)
        print(f"[info] Narrative saved to {nar_path}")
    if a.narrative_only:
        print("Edit the file, then run:  python generate_deck.py --narrative output/narrative.json")
        return

    log, issues = render(m, narrative, a.out, a.template, a.icons, a.shape_map, strict=not a.no_strict)
    for i in issues:
        print(f"[warn] {i}")
    print("\n".join(log))
    print(f"\nDeck written to {a.out}")


if __name__ == "__main__":
    main()
