"""Data analysis layer: loads the NovaRetail workbook and computes every metric the deck needs.

Nothing in the deck is hard-coded - every number and every "winner/loser" region is derived here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

SHEETS = ("Regional_Performance", "Customer_Experience", "Operational_Risk")


@dataclass
class Metrics:
    quarters: list[str]
    regions: list[str]
    latest_q: str
    first_q: str
    prev_q: str
    perf: pd.DataFrame
    cx: pd.DataFrame
    risk: pd.DataFrame
    kpis: dict = field(default_factory=dict)
    facts: dict = field(default_factory=dict)

    def pivot(self, sheet: str, col: str) -> pd.DataFrame:
        df = {"perf": self.perf, "cx": self.cx, "risk": self.risk}[sheet]
        return df.pivot(index="Quarter", columns="Region", values=col).loc[self.quarters, self.regions]


def _pct(new: float, old: float) -> float:
    return (new - old) / old * 100.0


def quarter_label(q: str, short: bool = False) -> str:
    """'2026-Q2' -> 'Q2 2026' (or "Q2 '26" when short=True)."""
    year, qq = q.split("-")
    return f"{qq} '{year[2:]}" if short else f"{qq} {year}"


def load_and_analyse(xlsx) -> Metrics:
    """xlsx can be a path or a file-like object (e.g. a Streamlit upload)."""
    book = pd.read_excel(xlsx, sheet_name=None)
    missing = [s for s in SHEETS if s not in book]
    if missing:
        raise ValueError(f"Dataset is missing sheet(s): {missing}")
    perf, cx, risk = (book[s].copy() for s in SHEETS)
    for df in (perf, cx, risk):
        df["Quarter"] = df["Quarter"].astype(str).str.strip()
        df["Region"] = df["Region"].astype(str).str.strip()

    quarters = sorted(perf["Quarter"].unique())
    regions = list(dict.fromkeys(perf["Region"]))  # keep workbook order
    m = Metrics(quarters, regions, quarters[-1], quarters[0], quarters[-2], perf, cx, risk)

    rev = m.pivot("perf", "Revenue_USD_M")
    units = m.pivot("perf", "Units_Sold_K")
    ret = m.pivot("perf", "Returns_Rate_pct")
    mkt = m.pivot("perf", "Marketing_Spend_USD_M")
    nps = m.pivot("cx", "NPS")
    csat = m.pivot("cx", "CSAT_pct")
    deliv = m.pivot("cx", "Avg_Delivery_Days")
    tick = m.pivot("cx", "Support_Tickets_K")
    stock = m.pivot("risk", "Stockout_Rate_pct")
    whu = m.pivot("risk", "Warehouse_Capacity_Util_pct")
    rproc = m.pivot("risk", "Return_Processing_Days")
    fraud = m.pivot("risk", "Fraud_Incidents")

    L, F, P = m.latest_q, m.first_q, m.prev_q
    total = rev.sum(axis=1)
    rev_growth = ((rev.loc[L] - rev.loc[F]) / rev.loc[F] * 100).sort_values()
    nps_change = (nps.loc[L] - nps.loc[F]).sort_values()
    blended_nps = float((nps.loc[L] * rev.loc[L]).sum() / rev.loc[L].sum())
    blended_nps_first = float((nps.loc[F] * rev.loc[F]).sum() / rev.loc[F].sum())

    m.kpis = {
        "total_revenue_m": float(total[L]),
        "revenue_growth_pct": _pct(total[L], total[F]),
        "revenue_qoq_pct": _pct(total[L], total[P]),
        "blended_nps": blended_nps,
        "blended_nps_change": blended_nps - blended_nps_first,
    }

    top = rev_growth.index[-1]
    bottom = rev_growth.index[0]
    largest = rev.loc[L].idxmax()
    risk_score = (stock.loc[L].rank() + whu.loc[L].rank() + fraud.loc[L].rank())  # simple composite
    m.facts = {
        "top_growth_region": top,
        "bottom_growth_region": bottom,
        "largest_region": largest,
        "rev": rev, "units": units, "ret": ret, "mkt": mkt,
        "nps": nps, "csat": csat, "deliv": deliv, "tick": tick,
        "stock": stock, "whu": whu, "rproc": rproc, "fraud": fraud,
        "rev_growth": rev_growth, "nps_change": nps_change,
        "best_nps_region": nps.loc[L].idxmax(),
        "worst_nps_region": nps.loc[L].idxmin(),
        "most_improved_nps": nps_change.index[-1],
        "most_declined_nps": nps_change.index[0],
        "highest_risk_region": risk_score.idxmax(),
        "highest_whu_region": whu.loc[L].idxmax(),
    }
    return m


def metrics_for_llm(m: Metrics) -> dict:
    """Compact, JSON-serialisable view of the data for the AI agent."""
    out = {"latest_quarter": m.latest_q, "first_quarter": m.first_q, "quarters": m.quarters,
           "kpis": {k: round(v, 2) for k, v in m.kpis.items()}, "regions": {}}
    f = m.facts
    for r in m.regions:
        out["regions"][r] = {
            k: [round(float(v), 2) for v in f[k][r].tolist()]
            for k in ("rev", "units", "ret", "mkt", "nps", "csat", "deliv", "tick", "stock", "whu", "rproc", "fraud")
        }
    out["metric_legend"] = {
        "rev": "Revenue US$ M", "units": "Units sold (K)", "ret": "Returns rate %", "mkt": "Marketing spend US$ M",
        "nps": "NPS", "csat": "CSAT %", "deliv": "Avg delivery days", "tick": "Support tickets (K)",
        "stock": "Stockout rate %", "whu": "Warehouse capacity utilisation %", "rproc": "Return processing days",
        "fraud": "Fraud incidents"}
    out["derived"] = {k: f[k] for k in ("top_growth_region", "bottom_growth_region", "largest_region",
                                        "best_nps_region", "worst_nps_region", "highest_risk_region",
                                        "highest_whu_region")}
    return out
