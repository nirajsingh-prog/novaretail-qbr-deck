"""Narrative layer: turns computed metrics into slide copy.

* rule_based()    - deterministic first draft; every region name and number is derived from the data.
* to_json()/from_json() - the editable intermediate artifact (narrative.json) that sits between
                    analysis and deck assembly. Users (or the ADK agent) edit THIS, then the deck is built from it.
* KPI values are ALWAYS recomputed in Python and locked - neither users nor the AI can change them.
"""
from __future__ import annotations

import re

from .analysis import Metrics, quarter_label

SCHEMA_VERSION = "2.0"

# --------------------------------------------------------------------------- icon selection
ICON_KEYWORDS = {
    "growth_chart": {"growth", "revenue", "performance", "sales", "trend", "increase"},
    "customer_star": {"customer", "nps", "csat", "satisfaction", "experience", "loyalty"},
    "risk_alert": {"risk", "alert", "warning", "stockout", "threat", "strain", "fraud"},
    "lightbulb_action": {"action", "recommendation", "next", "steps", "idea", "plan"},
    "delivery_truck": {"logistics", "delivery", "shipping", "transport", "fulfilment"},
    "globe_region": {"region", "regional", "global", "overview", "multi-region", "summary", "portfolio"},
    "return_arrow": {"returns", "refund", "reverse"},
    "shield_security": {"security", "fraud", "protection", "compliance"},
}
SLIDE_THEMES = {
    1: "overview summary of a multi-region global portfolio quarter",
    2: "regional revenue growth and sales performance trend",
    3: "customer experience nps csat satisfaction loyalty",
    4: "operational risk alert stockout strain warning",
    5: "recommended action next steps plan",
}


def choose_icon(theme: str, available: list[str]) -> str:
    """Score every icon by keyword overlap with the slide theme; best score wins."""
    words = set(re.findall(r"[a-z\-]+", theme.lower()))

    def score(stem: str) -> int:
        kw = ICON_KEYWORDS.get(stem, set(stem.split("_")))
        return len(words & kw)
    return sorted(available, key=lambda s: (-score(s), s))[0]


# --------------------------------------------------------------------------- helpers
def money(m: float) -> str:
    return f"${m / 1000:.2f}B" if m >= 1000 else f"${m:,.0f}M"


def signed(v: float, unit: str = "%", dp: int = 0) -> str:
    return f"{v:+.{dp}f}{unit}".replace("-", "−")


def kpi_values(m: Metrics) -> list[tuple[str, str]]:
    """Locked KPI values + default labels. Always computed from data."""
    k = m.kpis
    return [
        (money(k["total_revenue_m"]), f"Total Revenue, {quarter_label(m.latest_q)}"),
        (signed(k["revenue_growth_pct"], "%", 1), f"Revenue Growth vs {quarter_label(m.first_q)}"),
        (f"{k['blended_nps']:.0f}", "Blended NPS (revenue-weighted)"),
    ]


# --------------------------------------------------------------------------- rule-based engine
def rule_based(m: Metrics) -> dict:
    f, k = m.facts, m.kpis
    L, F = m.latest_q, m.first_q
    Ll, Fl = quarter_label(L), quarter_label(F)
    rev, units, ret, mkt = f["rev"], f["units"], f["ret"], f["mkt"]
    nps, csat, deliv, tick = f["nps"], f["csat"], f["deliv"], f["tick"]
    stock, whu, rproc, fraud = f["stock"], f["whu"], f["rproc"], f["fraud"]
    g = f["rev_growth"]
    top, bot, big = f["top_growth_region"], f["bottom_growth_region"], f["largest_region"]
    best_nps, worst_nps = f["best_nps_region"], f["worst_nps_region"]
    hr, hw = f["highest_risk_region"], f["highest_whu_region"]
    share_big = rev.loc[L, big] / rev.loc[L].sum() * 100
    stock_up = (stock.loc[L] - stock.loc[F]).sort_values(ascending=False)
    strained = list(stock_up.index[:2])
    fraud_g = (fraud.loc[L, hr] - fraud.loc[F, hr]) / fraud.loc[F, hr] * 100
    tick_g = (tick.loc[L, worst_nps] - tick.loc[F, worst_nps]) / tick.loc[F, worst_nps] * 100

    hist = whu.drop(index=L).mean(axis=1)
    peak_q = hist.idxmax()
    pi = m.quarters.index(peak_q)
    peak_stock_jump = stock.loc[peak_q].mean() - stock.loc[m.quarters[pi - 1]].mean() if pi > 0 else 0

    mt, rt = mkt.sum(axis=1), rev.sum(axis=1)
    mq = mt.idxmax()
    mprev = m.quarters[max(0, m.quarters.index(mq) - 1)]
    mkt_jump = (mt[mq] / mt[mprev] - 1) * 100
    rev_jump = (rt[mq] / rt[mprev] - 1) * 100

    yr, q = L.split("-Q")
    nxt = f"Q{int(q) % 4 + 1} {int(yr) + (1 if q == '4' else 0)}"

    return {
        "subtitle": f"{Ll} Quarterly Business Review  |  Executive Readout",
        "kpis": kpi_values(m),
        "summary": (
            f"NovaRetail closed {Ll} at {money(k['total_revenue_m'])} in revenue, up "
            f"{k['revenue_growth_pct']:.1f}% versus {Fl} and {k['revenue_qoq_pct']:.1f}% quarter-on-quarter. "
            f"{top} is the standout, growing revenue {g[top]:.0f}% while holding the highest NPS ({nps.loc[L, top]:.0f}). "
            f"{bot} is moving the other way: revenue is down {abs(g[bot]):.0f}%, NPS has fallen to "
            f"{nps.loc[L, bot]:.0f} and stockouts have reached {stock.loc[L, bot]:.1f}%. "
            f"The biggest risk this quarter is fulfilment: {hw} warehouses are running at {whu.loc[L, hw]:.0f}% "
            f"capacity just as demand there accelerates."
        ),
        2: {
            "headline": f"{top} Leads Growth ({signed(g[top])}) While {bot} Contracts ({signed(g[bot])})",
            "bullets": [
                f"{top} grew revenue from {money(rev.loc[F, top])} to {money(rev.loc[L, top])} ({signed(g[top])}), "
                f"the fastest of any region, on {signed((units.loc[L, top] / units.loc[F, top] - 1) * 100)} units.",
                f"{big} remains the largest market at {money(rev.loc[L, big])}, {share_big:.0f}% of group revenue, "
                f"growing a steady {g[big]:.0f}%.",
                f"{bot} is the only declining region ({signed(g[bot])}); units fell "
                f"{abs((units.loc[L, bot] / units.loc[F, bot] - 1) * 100):.0f}% "
                f"and returns rose to {ret.loc[L, bot]:.1f}%, roughly double other regions.",
                f"{quarter_label(mq)} marketing spend jumped {mkt_jump:.0f}% to {money(mt[mq])} for only a "
                f"{rev_jump:.0f}% revenue lift; spend has since reset to {money(mt[L])}.",
            ],
        },
        3: {
            "headline": f"{best_nps} Sets the Customer Benchmark as {worst_nps} NPS Falls "
                        f"{abs(nps.loc[L, worst_nps] - nps.loc[F, worst_nps]):.0f} Points",
            "bullets": [
                f"{best_nps} leads on every CX measure: NPS {nps.loc[L, best_nps]:.0f}, CSAT {csat.loc[L, best_nps]:.0f}% "
                f"and {deliv.loc[L, best_nps]:.1f}-day average delivery.",
                f"{f['most_improved_nps']} is the most improved region, with NPS up "
                f"{nps.loc[L, f['most_improved_nps']] - nps.loc[F, f['most_improved_nps']]:.0f} points as delivery fell to "
                f"{deliv.loc[L, f['most_improved_nps']]:.1f} days.",
                f"{worst_nps} is deteriorating: NPS {nps.loc[F, worst_nps]:.0f} → {nps.loc[L, worst_nps]:.0f}, "
                f"CSAT {csat.loc[F, worst_nps]:.0f}% → {csat.loc[L, worst_nps]:.0f}%.",
                f"Slower delivery ({deliv.loc[F, worst_nps]:.1f} → {deliv.loc[L, worst_nps]:.1f} days) is driving a "
                f"{tick_g:.0f}% rise in {worst_nps} support tickets.",
            ],
        },
        4: {
            "headline": f"Supply-Chain Strain Is Building in {strained[0]} and {strained[1]}",
            "bullets": [
                f"{strained[0]} stockouts climbed to {stock.loc[L, strained[0]]:.1f}% "
                f"({signed(stock_up[strained[0]], ' pts', 1)} since {Fl}), the highest in the group.",
                f"{hw} warehouses are at {whu.loc[L, hw]:.0f}% capacity with stockouts at "
                f"{stock.loc[L, hw]:.1f}%, leaving no headroom for continued growth.",
                f"{hr} fraud incidents rose {fraud_g:.0f}% to {fraud.loc[L, hr]:.0f} per quarter, "
                f"the highest of any region.",
                f"Return processing in {hr} now takes {rproc.loc[L, hr]:.0f} days vs "
                f"{rproc.loc[L].drop(hr).max():.0f} or fewer elsewhere, compounding the CX decline.",
            ],
        },
        "actions": [
            ("Expand", f"{hw} fulfilment capacity before peak season, targeting utilisation below 85% "
                       f"(now {whu.loc[L, hw]:.0f}%) to protect {signed(g[hw])} growth."),
            ("Launch", f"a {bot} recovery plan to cut delivery from {deliv.loc[L, bot]:.1f} to under 5 days "
                       f"and returns processing from {rproc.loc[L, bot]:.0f} days."),
            ("Tighten", f"fraud controls in {hr}, where incidents are up {fraud_g:.0f}% to "
                        f"{fraud.loc[L, hr]:.0f} per quarter."),
            ("Pre-position", f"inventory ahead of {quarter_label(peak_q)[:2]}: last {quarter_label(peak_q)[:2]} average "
                             f"stockouts rose {peak_stock_jump:.1f} pts as warehouses peaked."),
        ],
        "closing": f"Protect the growth engine in {top}, fix the fundamentals in {bot}, "
                   f"and review progress at the {nxt} QBR.",
    }


# --------------------------------------------------------------------------- editable JSON artifact
def to_json(story: dict, m: Metrics | None = None, source: str = "rules") -> dict:
    """Internal story -> editable, human-readable narrative.json."""
    return {
        "schema_version": SCHEMA_VERSION,
        "quarter": quarter_label(m.latest_q) if m else None,
        "source": source,
        "subtitle": story["subtitle"],
        "kpis": [{"value": v, "label": lab, "value_locked": True} for v, lab in story["kpis"]],
        "summary": story["summary"],
        **{f"slide_{n}": {"headline": story[n]["headline"], "bullets": list(story[n]["bullets"])} for n in (2, 3, 4)},
        "actions": [{"verb": v, "text": t} for v, t in story["actions"]],
        "closing": story["closing"],
    }


def from_json(data: dict, m: Metrics) -> dict:
    """Editable narrative.json -> internal story used by deck_builder.

    KPI values are re-derived from the data (locked); only the labels come from the JSON.
    Empty bullets / actions are dropped so a user can delete one by clearing it.
    """
    locked = kpi_values(m)
    labels = [k.get("label", "") for k in data.get("kpis", [])]
    story = {
        "subtitle": data["subtitle"].strip(),
        "kpis": [(v, (labels[i].strip() if i < len(labels) and labels[i].strip() else lab))
                 for i, (v, lab) in enumerate(locked)],
        "summary": data["summary"].strip(),
        "actions": [(a["verb"].strip(), a["text"].strip()) for a in data["actions"]
                    if a.get("verb", "").strip() and a.get("text", "").strip()],
        "closing": data["closing"].strip(),
    }
    for n in (2, 3, 4):
        s = data[f"slide_{n}"]
        story[n] = {"headline": s["headline"].strip(), "bullets": [b.strip() for b in s["bullets"] if b.strip()]}
    return story


def build_narrative(m: Metrics) -> dict:
    return rule_based(m)
