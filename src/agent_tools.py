"""Function tools for the ADK narrative-editor agent.

The agent never writes text into the deck directly. It calls these tools, which:
  * read/write the editable narrative held in ADK *session state* (state["narrative"]),
  * validate every edit (structure, length, numbers grounded in the data) and REJECT bad edits
    so the model has to correct itself,
  * keep a revision log and the user's lasting style preferences (state["user:preferences"],
    a user-scoped key that ADK carries across every session of the same user).

NOTE: no `from __future__ import annotations` here - ADK builds the tool schema from real type hints.
"""
import copy
import time

try:
    from google.adk.tools import ToolContext
except ImportError:  # lets unit tests run without ADK installed
    ToolContext = object  # type: ignore

from .validation import check_text

TEXT_SECTIONS = {
    "subtitle": ("subtitle", None),
    "summary": ("summary", None),
    "closing": ("closing", None),
    "slide_2_headline": ("headline", ("slide_2", "headline")),
    "slide_3_headline": ("headline", ("slide_3", "headline")),
    "slide_4_headline": ("headline", ("slide_4", "headline")),
    "kpi_label_1": ("kpi_label", ("kpis", 0)),
    "kpi_label_2": ("kpi_label", ("kpis", 1)),
    "kpi_label_3": ("kpi_label", ("kpis", 2)),
}


def _ensure_context(state) -> None:
    """When the agent runs stand-alone (e.g. `adk web`), load the default dataset into state."""
    if "narrative" in state:
        return
    from .pipeline import default_paths, initial_state
    state.update(initial_state(default_paths()["data"]))


def _log(state, section: str, note: str, by: str = "agent") -> None:
    log = list(state.get("revision_log", []))
    log.append({"time": time.strftime("%H:%M:%S"), "by": by, "section": section, "note": note})
    state["revision_log"] = log[-50:]


def get_deck_context(tool_context: ToolContext) -> dict:
    """Returns the dataset facts, the CURRENT narrative draft and the user's saved style preferences.

    Always call this first, before proposing or making any edit.
    """
    st = tool_context.state
    _ensure_context(st)
    return {
        "facts": st.get("facts"),
        "narrative": st.get("narrative"),
        "user_preferences": st.get("user:preferences", []),
        "editable_text_sections": list(TEXT_SECTIONS),
        "rules": "KPI values are locked. Use only numbers present in facts or the current narrative.",
    }


def update_text_section(section: str, new_text: str, tool_context: ToolContext) -> dict:
    """Replaces one single-text section of the narrative.

    Args:
        section: one of subtitle, summary, closing, slide_2_headline, slide_3_headline,
                 slide_4_headline, kpi_label_1, kpi_label_2, kpi_label_3.
        new_text: the complete replacement text.
    """
    st = tool_context.state
    _ensure_context(st)
    if section not in TEXT_SECTIONS:
        return {"status": "rejected", "issues": [f"unknown section '{section}'. Use one of {list(TEXT_SECTIONS)}"]}
    kind, path = TEXT_SECTIONS[section]
    issues = check_text(kind, new_text.strip(), st["allowed_numbers"])
    if issues:
        return {"status": "rejected", "issues": issues, "hint": "Fix the issues and call the tool again."}
    nar = copy.deepcopy(st["narrative"])
    if path is None:
        old = nar[section]; nar[section] = new_text.strip()
    elif path[0] == "kpis":
        old = nar["kpis"][path[1]]["label"]; nar["kpis"][path[1]]["label"] = new_text.strip()
    else:
        old = nar[path[0]][path[1]]; nar[path[0]][path[1]] = new_text.strip()
    st["narrative"] = nar
    _log(st, section, f"'{old[:40]}…' -> '{new_text.strip()[:40]}…'")
    return {"status": "ok", "section": section}


def update_bullets(slide: int, bullets: list[str], tool_context: ToolContext) -> dict:
    """Replaces ALL insight bullets on slide 2, 3 or 4.

    Args:
        slide: 2 (performance), 3 (customer experience) or 4 (operational risk).
        bullets: 3 or 4 complete bullet strings, each max 160 characters, each insight-led with numbers.
    """
    st = tool_context.state
    _ensure_context(st)
    if slide not in (2, 3, 4):
        return {"status": "rejected", "issues": ["slide must be 2, 3 or 4"]}
    clean = [b.strip() for b in bullets if b and b.strip()]
    issues = [] if 3 <= len(clean) <= 4 else [f"need 3-4 bullets, got {len(clean)}"]
    for i, b in enumerate(clean, 1):
        issues += [f"bullet {i}: {e}" for e in check_text("bullet", b, st["allowed_numbers"])]
    if issues:
        return {"status": "rejected", "issues": issues, "hint": "Fix the issues and call the tool again."}
    nar = copy.deepcopy(st["narrative"])
    nar[f"slide_{slide}"]["bullets"] = clean
    st["narrative"] = nar
    _log(st, f"slide_{slide}_bullets", f"{len(clean)} bullets rewritten")
    return {"status": "ok", "slide": slide}


def update_actions(verbs: list[str], texts: list[str], tool_context: ToolContext) -> dict:
    """Replaces the recommended actions on slide 5.

    Args:
        verbs: 3-4 bold lead action verbs, e.g. ["Expand", "Launch", "Tighten"].
        texts: matching action texts (same length as verbs), each tied to an insight from slides 2-4.
    """
    st = tool_context.state
    _ensure_context(st)
    pairs = [(v.strip(), t.strip()) for v, t in zip(verbs, texts) if v.strip() and t.strip()]
    issues = []
    if len(verbs) != len(texts):
        issues.append("verbs and texts must have the same length")
    if not 3 <= len(pairs) <= 4:
        issues.append(f"need 3-4 actions, got {len(pairs)}")
    for i, (v, t) in enumerate(pairs, 1):
        issues += [f"action {i}: {e}" for e in check_text("action_verb", v, st["allowed_numbers"])]
        issues += [f"action {i}: {e}" for e in check_text("action_text", t, st["allowed_numbers"])]
    if issues:
        return {"status": "rejected", "issues": issues, "hint": "Fix the issues and call the tool again."}
    nar = copy.deepcopy(st["narrative"])
    nar["actions"] = [{"verb": v, "text": t} for v, t in pairs]
    st["narrative"] = nar
    _log(st, "actions", f"{len(pairs)} actions rewritten")
    return {"status": "ok"}


def remember_preference(preference: str, tool_context: ToolContext) -> dict:
    """Saves a LASTING style preference of the user (e.g. 'keep bullets under 20 words',
    'always lead with customer impact'). Saved preferences are applied to every future edit
    and are remembered across sessions.
    """
    st = tool_context.state
    prefs = list(st.get("user:preferences", []))
    p = preference.strip()
    if p and p.lower() not in [x.lower() for x in prefs]:
        prefs.append(p)
    st["user:preferences"] = prefs[-15:]
    return {"status": "ok", "preferences": st["user:preferences"]}


def forget_preferences(tool_context: ToolContext) -> dict:
    """Clears all saved style preferences (only when the user explicitly asks)."""
    tool_context.state["user:preferences"] = []
    return {"status": "ok"}


ALL_TOOLS = [get_deck_context, update_text_section, update_bullets, update_actions,
             remember_preference, forget_preferences]
