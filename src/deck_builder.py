"""Presentation layer: locates named placeholder shapes in the fixed template and replaces them
with text, native charts and icons using python-pptx.

Lookup strategy (per the brief - use shape names, never guessed coordinates):
  1. shape.name matched against role patterns (e.g. chart_*, icon_*, insights_*, title_N);
  2. fallback for text roles: the template's bracketed instruction text (e.g. "[VALUE]").
  3. optional shape_map.json overrides (exact shape names), see README.
"""
from __future__ import annotations

import copy
import json
import math
import re
from pathlib import Path

from lxml import etree
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.oxml.ns import qn
from pptx.util import Emu, Pt

from .analysis import Metrics, quarter_label

# Region colours stay identical on every chart so leadership can track a region across slides.
REGION_COLOURS = ["1E2761", "7A8FA6", "0E9F8B", "D9534F", "F0AD4E", "6F42C1"]
TEXT_DARK = RGBColor(0x1E, 0x27, 0x61)
PLACEHOLDER_HINTS = re.compile(r"\[FILL|\[VALUE\]|Insert your chart|Pick the icon|^\s*(CHART|ICON)\s*$", re.I | re.M)

ROLE_PATTERNS = {
    "subtitle": r"sub.?title|date|period",
    "kpi_value": r"kpi.*(val|num|figure)|^value",
    "kpi_label": r"kpi.*(label|name|desc)|^label_?kpi",
    "summary": r"summary|exec",
    "title": r"^(title|headline)",
    "insights": r"insight|bullet|body",
    "actions": r"action|recommend",
    "closing": r"closing|cta|call|statement|forward|tagline",
    "chart": r"chart",
    "icon": r"icon",
}
TEXT_MARKERS = {
    "subtitle": "[FILL: Quarter",
    "kpi_value": "[VALUE]",
    "kpi_label": "[FILL: KPI label",
    "summary": "executive summary",
    "title": "insight-led headline",
    "insights": "bullet insights",
    "actions": "recommended actions",
    "closing": "forward-looking",
    "chart": "Insert your chart",
    "icon": "Pick the icon",
}


# --------------------------------------------------------------------------- discovery
def inspect(template: str | Path) -> None:
    prs = Presentation(template)
    for i, slide in enumerate(prs.slides, 1):
        print(f"--- Slide {i}")
        for sh in slide.shapes:
            txt = sh.text_frame.text.replace("\n", " ")[:70] if sh.has_text_frame else ""
            print(f"  {sh.shape_id:>3}  {sh.name:<28} {sh.shape_type!s:<22} "
                  f"x={Emu(sh.left).inches:.2f} y={Emu(sh.top).inches:.2f} "
                  f"w={Emu(sh.width).inches:.2f} h={Emu(sh.height).inches:.2f}  {txt}")


def _text(sh) -> str:
    return sh.text_frame.text if sh.has_text_frame else ""


def _has_placeholder_text(sh) -> bool:
    return bool(PLACEHOLDER_HINTS.search(_text(sh)))


def find(slide, role: str, overrides: dict | None = None, many: bool = False):
    """Return shape(s) for a role on a slide, by name first, then by instruction-text marker."""
    shapes = list(slide.shapes)
    if overrides and role in overrides:
        names = overrides[role] if isinstance(overrides[role], list) else [overrides[role]]
        hits = [s for n in names for s in shapes if s.name == n]
        if hits:
            return hits if many else hits[0]
    pat = re.compile(ROLE_PATTERNS[role], re.I)
    by_name = [s for s in shapes if pat.search(s.name)]
    if role in ("chart", "icon"):
        # the dashed box is the biggest shape carrying the name; helper captions are smaller
        hits = sorted(by_name, key=lambda s: -(s.width * s.height))[:1] if not many else by_name
    else:
        hits = [s for s in by_name if _has_placeholder_text(s)]
    if not hits:  # fallback: bracketed instruction text
        marker = TEXT_MARKERS[role].lower()
        hits = [s for s in shapes if marker in _text(s).lower()]
        if role in ("chart", "icon") and hits:
            # caption found -> use the largest shape whose box contains it
            cap = hits[0]
            boxes = [s for s in shapes if _contains(s, cap) and s is not cap]
            hits = [max(boxes, key=lambda s: s.width * s.height)] if boxes else hits
    hits.sort(key=lambda s: (s.left, s.top))
    if not hits:
        raise LookupError(f"Could not locate '{role}' on slide {slide.slide_id}. Run with --inspect.")
    return hits if many else hits[0]


def _contains(outer, inner, tol=Emu(45720)) -> bool:
    cx, cy = inner.left + inner.width // 2, inner.top + inner.height // 2
    return (outer.left - tol <= cx <= outer.left + outer.width + tol and
            outer.top - tol <= cy <= outer.top + outer.height + tol)


def _remove(shape) -> None:
    el = shape._element
    el.getparent().remove(el)


def clear_region(slide, box, keep=()) -> tuple[int, int, int, int]:
    """Delete the dashed placeholder box and every instruction caption that sits inside it."""
    geom = (box.left, box.top, box.width, box.height)
    keep_ids = {k.shape_id for k in keep}
    for sh in list(slide.shapes):
        if sh.shape_id in keep_ids:
            continue
        if sh is box or sh.shape_id == box.shape_id or (_contains(box, sh) and sh.width * sh.height <= box.width * box.height):
            _remove(sh)
    return geom


# --------------------------------------------------------------------------- text
def _font_snapshot(shape):
    for p in shape.text_frame.paragraphs:
        for r in p.runs:
            f = r.font
            color = None
            try:
                color = f.color.rgb if f.color and f.color.type is not None else None
            except AttributeError:
                color = None
            return {"size": f.size, "bold": f.bold, "name": f.name, "color": color}
    return {"size": None, "bold": None, "name": None, "color": None}


def _fit_size(shape, paragraphs: list[str], start: float, minimum: float = 10.0, spacing: float = 1.25) -> float:
    """Estimate the largest font size (pt) at which the text fits the fixed box (no resizing allowed)."""
    tf = shape.text_frame
    w = Emu(shape.width - (tf.margin_left or 91440) - (tf.margin_right or 91440)).pt
    h = Emu(shape.height - (tf.margin_top or 45720) - (tf.margin_bottom or 45720)).pt
    size = start
    while size > minimum:
        cpl = max(1, int(w / (size * 0.52)))
        lines = sum(max(1, math.ceil(len(p) / cpl)) for p in paragraphs)
        need = lines * size * spacing + (len(paragraphs) - 1) * size * 0.45
        if need <= h:
            return size
        size -= 0.5
    return minimum


def _set_bullet(paragraph, char="•", indent_emu=228600):
    pPr = paragraph._p.get_or_add_pPr()
    pPr.set("marL", str(indent_emu))
    pPr.set("indent", str(-indent_emu))
    for tag in ("a:buNone", "a:buChar", "a:buAutoNum"):
        for el in pPr.findall(qn(tag)):
            pPr.remove(el)
    bu = etree.SubElement(pPr, qn("a:buChar"))
    bu.set("char", char)


def write_text(shape, paragraphs, *, bullets=False, bold_lead=False, colour=None,
               default_size=14, min_size=10, max_size=None, bold=None):
    """Replace placeholder text, inheriting the template's font but dropping the instruction italics.

    paragraphs: list[str] or, when bold_lead=True, list[(lead, rest)].
    """
    snap = _font_snapshot(shape)
    plain = [p if isinstance(p, str) else f"{p[0]} {p[1]}" for p in paragraphs]
    start = (snap["size"].pt if snap["size"] else default_size)
    if max_size:
        start = min(start, max_size)
    size = _fit_size(shape, plain, start, min_size, spacing=1.2)
    tf = shape.text_frame
    tf.word_wrap = True
    # wipe existing paragraphs except the first (keeps paragraph-level props like alignment)
    for p in tf.paragraphs[1:]:
        p._p.getparent().remove(p._p)
    first = tf.paragraphs[0]
    for r in list(first.runs):
        r._r.getparent().remove(r._r)
    for i, item in enumerate(paragraphs):
        para = first if i == 0 else tf.add_paragraph()
        if i > 0:
            pPr = first._p.find(qn("a:pPr"))
            if pPr is not None:
                para._p.insert(0, copy.deepcopy(pPr))
        para.space_after = Pt(size * 0.45) if len(paragraphs) > 1 else None
        segs = [(item[0], True), (" " + item[1], False)] if bold_lead else [(item, None)]
        for txt, is_bold in segs:
            run = para.add_run()
            run.text = txt
            f = run.font
            f.size = Pt(size)
            f.italic = False
            f.bold = is_bold if is_bold is not None else (bold if bold is not None else snap["bold"])
            if snap["name"]:
                f.name = snap["name"]
            c = colour or snap["color"]
            if c is not None:
                f.color.rgb = c
        if bullets:
            _set_bullet(para)
    return size


# --------------------------------------------------------------------------- charts
def _style_chart(chart, title: str, number_format: str):
    chart.has_title = True
    chart.chart_title.text_frame.text = title
    tp = chart.chart_title.text_frame.paragraphs[0]
    tp.runs[0].font.size = Pt(12)
    tp.runs[0].font.bold = True
    tp.runs[0].font.color.rgb = TEXT_DARK
    chart.has_legend = True
    chart.legend.position = XL_LEGEND_POSITION.BOTTOM
    chart.legend.include_in_layout = False
    chart.legend.font.size = Pt(10)
    chart.font.size = Pt(10)
    chart.font.color.rgb = RGBColor(0x44, 0x4B, 0x5A)
    va = chart.value_axis
    va.has_major_gridlines = True
    va.major_gridlines.format.line.color.rgb = RGBColor(0xE3, 0xE6, 0xEB)
    va.format.line.fill.background()
    va.tick_labels.number_format = number_format
    va.tick_labels.number_format_is_linked = False
    chart.category_axis.format.line.color.rgb = RGBColor(0xBF, 0xC5, 0xCE)
    chart.category_axis.tick_labels.font.size = Pt(10)


def add_chart(slide, geom, m: Metrics, sheet: str, col: str, kind: str, title: str, fmt: str):
    left, top, width, height = geom
    piv = m.pivot(sheet, col)
    data = CategoryChartData()
    data.categories = [quarter_label(q, short=True) for q in m.quarters]
    for region in m.regions:
        data.add_series(region, [float(v) for v in piv[region]])
    ctype = {"column": XL_CHART_TYPE.COLUMN_CLUSTERED, "line": XL_CHART_TYPE.LINE_MARKERS}[kind]
    gf = slide.shapes.add_chart(ctype, left, top, width, height, data)
    gf.name = f"{col}_chart"
    chart = gf.chart
    _style_chart(chart, title, fmt)
    for i, s in enumerate(chart.plots[0].series):
        rgb = RGBColor.from_string(REGION_COLOURS[i % len(REGION_COLOURS)])
        if kind == "column":
            s.format.fill.solid()
            s.format.fill.fore_color.rgb = rgb
        else:
            s.format.line.color.rgb = rgb
            s.format.line.width = Pt(2.75)
            s.smooth = False
            s.marker.style = XL_MARKER_STYLE.CIRCLE
            s.marker.size = 7
            s.marker.format.fill.solid()
            s.marker.format.fill.fore_color.rgb = rgb
            s.marker.format.line.color.rgb = rgb
    if kind == "column":
        chart.plots[0].gap_width = 70
        chart.plots[0].overlap = -10
    return gf


# --------------------------------------------------------------------------- icons
def add_icon(slide, geom, icon_path: Path, padding=0.06, align="center"):
    left, top, width, height = geom
    from PIL import Image
    with Image.open(icon_path) as im:
        iw, ih = im.size
    pad_w, pad_h = int(width * padding), int(height * padding)
    bw, bh = width - 2 * pad_w, height - 2 * pad_h
    scale = min(bw / iw, bh / ih)
    w, h = int(iw * scale), int(ih * scale)
    x = left if align == "left" else left + (width - w) // 2
    pic = slide.shapes.add_picture(str(icon_path), x, top + (height - h) // 2, w, h)
    pic.name = f"icon_{icon_path.stem}"
    return pic


# --------------------------------------------------------------------------- orchestration
CHART_SPECS = {
    2: ("perf", "Revenue_USD_M", "column", "Revenue by Region (US$ M)", '#,##0'),
    3: ("cx", "NPS", "line", "Net Promoter Score by Region", '0'),
    4: ("risk", "Stockout_Rate_pct", "line", "Stockout Rate by Region (%)", '0.0"%"'),
}


def build_deck(template, out, m: Metrics, story: dict, icon_dir: Path, overrides_path=None) -> list[str]:
    prs = Presentation(template)
    if len(prs.slides) != 5:
        raise ValueError(f"Template must have exactly 5 slides, found {len(prs.slides)}")
    ov_all = json.loads(Path(overrides_path).read_text(encoding="utf-8")) if overrides_path else {}
    icons = {p.stem: p for p in sorted(icon_dir.glob("*.png"))}
    if not icons:
        raise FileNotFoundError(f"No .png icons found in {icon_dir}")
    from .narrative import SLIDE_THEMES, choose_icon
    log = []

    for idx, slide in enumerate(prs.slides, 1):
        ov = ov_all.get(str(idx), {})
        if idx == 1:
            write_text(find(slide, "subtitle", ov), [story["subtitle"]])
            vals = find(slide, "kpi_value", ov, many=True)
            labs = find(slide, "kpi_label", ov, many=True)
            for shp, (v, _) in zip(vals, story["kpis"]):
                write_text(shp, [v], bold=True, min_size=20)
            for shp, (_, lab) in zip(labs, story["kpis"]):
                write_text(shp, [lab], min_size=9)
            write_text(find(slide, "summary", ov), [story["summary"]], min_size=11)
        elif idx in (2, 3, 4):
            write_text(find(slide, "title", ov), [story[idx]["headline"]], min_size=18)
            write_text(find(slide, "insights", ov), story[idx]["bullets"], bullets=True, min_size=10)
            box = find(slide, "chart", ov)
            geom = clear_region(slide, box)
            sheet, col, kind, title, fmt = CHART_SPECS[idx]
            add_chart(slide, geom, m, sheet, col, kind, title, fmt)
            log.append(f"Slide {idx}: {kind} chart of {col} at '{box.name}'")
        else:
            write_text(find(slide, "actions", ov), story["actions"], bullets=True, bold_lead=True, min_size=11)
            write_text(find(slide, "closing", ov), [story["closing"]], bold=True, min_size=11)

        icon_box = find(slide, "icon", ov)
        stem = choose_icon(SLIDE_THEMES[idx], list(icons))
        geom = clear_region(slide, icon_box)
        # slides 2-4: icon sits under the bullet column, so left-align it to that column
        add_icon(slide, geom, icons[stem], align="left" if idx in (2, 3, 4) else "center")
        log.append(f"Slide {idx}: icon '{stem}' at '{icon_box.name}'")

        # footer: drop the word "Template" so the deck reads as a finished document
        for sh in slide.shapes:
            if sh.has_text_frame and "Capstone Template" in sh.text_frame.text:
                for p in sh.text_frame.paragraphs:
                    for r in p.runs:
                        r.text = r.text.replace("Capstone Template", quarter_label(m.latest_q))

    leftovers = [(i, s.name, _text(s)[:50]) for i, sl in enumerate(prs.slides, 1)
                 for s in sl.shapes if _has_placeholder_text(s)]
    if leftovers:
        raise RuntimeError(f"Unfilled placeholders remain: {leftovers}")
    prs.save(out)
    return log
