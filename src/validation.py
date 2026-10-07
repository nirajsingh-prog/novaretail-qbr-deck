"""Guard-rails for narrative text (used for manual edits, narrative.json files and every ADK agent edit).

1. Structure  - required sections, 3-4 bullets per slide, 3-4 actions.
2. Length     - limits sized to the fixed template boxes (no box resizing allowed).
3. Grounding  - every number in the text must exist in the dataset or in the computed facts,
                so neither a user nor the AI can slip an invented figure into the deck.
"""
from __future__ import annotations

import re

from .analysis import Metrics
from .narrative import rule_based

LIMITS = {"subtitle": 90, "summary": 520, "headline": 90, "bullet": 160, "action_verb": 20,
          "action_text": 150, "closing": 120, "kpi_label": 40}
NUM_RE = re.compile(r"(?<![A-Za-z])\d+(?:[.,]\d+)?")


def _numbers(text: str) -> list[float]:
    out = []
    for tok in NUM_RE.findall(text):
        try:
            out.append(float(tok.replace(",", "")))
        except ValueError:
            pass
    return out


def allowed_numbers(m: Metrics) -> list[float]:
    """Every number that is legitimately 'true' for this dataset."""
    vals: set[float] = set()
    for df in (m.perf, m.cx, m.risk):
        for v in df.select_dtypes("number").to_numpy().ravel():
            vals.add(round(float(v), 2))
    # every metric pivot: change and % change between any two quarters, per region and for the total
    for key in ("rev", "units", "ret", "mkt", "nps", "csat", "deliv", "tick", "stock", "whu", "rproc", "fraud"):
        p = m.facts[key]
        frames = [p, p.sum(axis=1).to_frame("total"), p.mean(axis=1).to_frame("avg")]
        for fr in frames:
            for col in fr.columns:
                s = [float(x) for x in fr[col].tolist()]
                vals.update(round(a, 2) for a in s)
                # first->latest, previous->latest and each quarter-on-quarter step
                pairs = {(0, len(s) - 1), (len(s) - 2, len(s) - 1)} | {(i, i + 1) for i in range(len(s) - 1)}
                for i, j in pairs:
                    a, b = s[i], s[j]
                    if a:
                        vals.add(round(abs((b - a) / a * 100), 1))
                    vals.add(round(abs(b - a), 2))
        # share of total in each quarter
        for q in p.index:
            tot = p.loc[q].sum()
            for v in p.loc[q]:
                if tot:
                    vals.add(round(float(v) / tot * 100, 1))
    for v in m.kpis.values():
        vals.add(round(abs(float(v)), 2))
    for q in m.quarters:                     # years / quarter numbers
        y, qq = q.split("-Q")
        vals.update({float(y), float(y[2:]), float(qq)})
    # all numbers already used in the data-driven draft
    draft = rule_based(m)
    for t in _flatten(draft):
        vals.update(round(x, 2) for x in _numbers(t))
    vals.update(float(i) for i in range(0, 11))   # small counts ("3 regions", "top 2 risks")
    return sorted(vals)


def _matches(n: float, allowed: list[float]) -> bool:
    for a in allowed:
        if abs(n - a) <= 0.051 or (n == int(n) and abs(a - n) < 0.5) or (n >= 100 and abs(n - a) / n < 0.006):
            return True
        if a >= 1000 and abs(n - a / 1000) <= 0.006:       # $1.28B vs 1280 (US$ M)
            return True
    return False


def _flatten(story_or_json) -> list[str]:
    s = story_or_json
    texts = [s["subtitle"], s["summary"], s["closing"]]
    for n in (2, 3, 4):
        sec = s.get(n) or s.get(f"slide_{n}")
        texts += [sec["headline"], *sec["bullets"]]
    for a in s["actions"]:
        texts.append(" ".join(a) if isinstance(a, (list, tuple)) else f"{a['verb']} {a['text']}")
    return texts


def ungrounded_numbers(text: str, allowed: list[float]) -> list[str]:
    return [tok for tok, n in zip(NUM_RE.findall(text), _numbers(text)) if not _matches(n, allowed)]


def check_text(kind: str, text: str, allowed: list[float]) -> list[str]:
    errs = []
    if not text or not text.strip():
        return [f"{kind} is empty"]
    lim = LIMITS.get(kind)
    if lim and len(text) > lim:
        errs.append(f"{kind} is {len(text)} chars (max {lim})")
    bad = ungrounded_numbers(text, allowed)
    if bad:
        errs.append(f"{kind} contains number(s) not found in the data: {', '.join(bad)}")
    return errs


def validate_narrative(data: dict, allowed: list[float]) -> list[str]:
    """Validate an editable narrative.json dict. Returns a list of human-readable issues (empty = OK)."""
    issues: list[str] = []
    for key in ("subtitle", "summary", "closing", "slide_2", "slide_3", "slide_4", "actions", "kpis"):
        if key not in data:
            issues.append(f"missing section '{key}'")
    if issues:
        return issues
    issues += check_text("subtitle", data["subtitle"], allowed)
    issues += check_text("summary", data["summary"], allowed)
    issues += check_text("closing", data["closing"], allowed)
    for i, k in enumerate(data["kpis"], 1):
        issues += [f"KPI {i}: {e}" for e in check_text("kpi_label", k.get("label", ""), allowed)]
    for n in (2, 3, 4):
        sec = data[f"slide_{n}"]
        issues += [f"Slide {n}: {e}" for e in check_text("headline", sec.get("headline", ""), allowed)]
        bullets = [b for b in sec.get("bullets", []) if b.strip()]
        if not 3 <= len(bullets) <= 4:
            issues.append(f"Slide {n}: needs 3-4 bullets (has {len(bullets)})")
        for j, b in enumerate(bullets, 1):
            issues += [f"Slide {n} bullet {j}: {e}" for e in check_text("bullet", b, allowed)]
    acts = [a for a in data["actions"] if a.get("verb", "").strip() and a.get("text", "").strip()]
    if not 3 <= len(acts) <= 4:
        issues.append(f"Slide 5: needs 3-4 actions (has {len(acts)})")
    for j, a in enumerate(acts, 1):
        issues += [f"Action {j}: {e}" for e in check_text("action_verb", a["verb"], allowed)]
        issues += [f"Action {j}: {e}" for e in check_text("action_text", a["text"], allowed)]
    return issues
