"""NovaRetail Deck Studio - Streamlit front end.

    streamlit run app.py

1. Upload the Excel dataset (or use the bundled one).
2. Review the data-driven draft: KPIs, headlines, bullets, actions - edit anything directly.
3. Chat with the Google ADK agent to refine sections ("make this more executive-friendly").
   The agent edits the SAME draft held in its session state and remembers your preferences.
4. Inspect / download / upload the narrative JSON (the editable intermediate artifact).
5. Click Generate Deck -> download the PowerPoint.
"""
from __future__ import annotations

import getpass
import hashlib
import io
import json
import time

import streamlit as st

from src.analysis import load_and_analyse, quarter_label
from src.narrative import rule_based, to_json
from src.pipeline import default_paths, render
from src.validation import allowed_numbers, validate_narrative

st.set_page_config(page_title="NovaRetail Deck Studio", page_icon="📊", layout="wide")
D = default_paths()
ss = st.session_state
SLIDE_NAMES = {2: "Performance", 3: "Customer experience", 4: "Operational risk"}
QUICK_PROMPTS = [
    "Make the whole deck more executive-friendly",
    "Focus the summary more on customer experience",
    "Make slide 4 headline sharper and more urgent",
    "Shorten all bullets to one crisp line each",
]

st.markdown("""<style>
.block-container {padding-top: 1.6rem;}
div[data-testid="stMetricValue"] {color:#1E2761; font-weight:700;}
.small {color:#6b7280; font-size:0.85rem;}
</style>""", unsafe_allow_html=True)


# ---------------------------------------------------------------- widget <-> narrative sync
def load_widgets(n: dict) -> None:
    ss.w_subtitle, ss.w_summary, ss.w_closing = n["subtitle"], n["summary"], n["closing"]
    for i, k in enumerate(n["kpis"]):
        ss[f"w_kpi_{i}"] = k["label"]
    for s in (2, 3, 4):
        sec = n[f"slide_{s}"]
        ss[f"w_h_{s}"] = sec["headline"]
        for j in range(4):
            ss[f"w_b_{s}_{j}"] = sec["bullets"][j] if j < len(sec["bullets"]) else ""
    for j in range(4):
        a = n["actions"][j] if j < len(n["actions"]) else {"verb": "", "text": ""}
        ss[f"w_av_{j}"], ss[f"w_at_{j}"] = a["verb"], a["text"]


def narrative_from_widgets(base: dict) -> dict:
    n = json.loads(json.dumps(base))
    n["subtitle"], n["summary"], n["closing"] = ss.w_subtitle, ss.w_summary, ss.w_closing
    for i in range(len(n["kpis"])):
        n["kpis"][i]["label"] = ss[f"w_kpi_{i}"]
    for s in (2, 3, 4):
        n[f"slide_{s}"] = {"headline": ss[f"w_h_{s}"],
                           "bullets": [ss[f"w_b_{s}_{j}"] for j in range(4) if ss[f"w_b_{s}_{j}"].strip()]}
    n["actions"] = [{"verb": ss[f"w_av_{j}"], "text": ss[f"w_at_{j}"]} for j in range(4)
                    if ss[f"w_av_{j}"].strip() and ss[f"w_at_{j}"].strip()]
    return n


def set_narrative(n: dict, note: str, by: str = "user") -> None:
    """Queue a narrative to load into the widgets on the next rerun."""
    ss.narrative = n
    ss._pending = n
    ss.local_log.append({"time": time.strftime("%H:%M:%S"), "by": by, "section": "all", "note": note})


# ---------------------------------------------------------------- ADK
@st.cache_resource(show_spinner=False)
def get_studio(model: str):
    from src.agent import DeckStudio
    return DeckStudio(model=model)


def adk_status():
    try:
        from src.agent import adk_configured
        return adk_configured()
    except ImportError:
        return False, "google-adk not installed (pip install -r requirements.txt)"


# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("📊 Deck Studio")
    up = st.file_uploader("Excel dataset", type=["xlsx"], help="3 sheets: Regional_Performance, "
                          "Customer_Experience, Operational_Risk")
    with st.expander("Template & icons (optional)"):
        tpl_up = st.file_uploader("PowerPoint template", type=["pptx"])
        ico_up = st.file_uploader("Icon repo (.zip)", type=["zip"])
    st.divider()
    st.subheader("AI agent (Google ADK)")
    ok, how = adk_status()
    st.caption(("🟢 " if ok else "🔴 ") + how)
    model = st.selectbox("Model", ["gemini-3.8-flash", "gemini-2.5-flash"], disabled=not ok)
    user_id = st.text_input("User id", value=getpass.getuser(),
                            help="Your style preferences are remembered per user id")
    new_session = st.button("↺ Start new review session", width="stretch")

# ---------------------------------------------------------------- load data (on first run / new file)
data_bytes = up.getvalue() if up else D["data"].read_bytes()
data_hash = hashlib.md5(data_bytes).hexdigest()
if ss.get("data_hash") != data_hash or new_session:
    try:
        m = load_and_analyse(io.BytesIO(data_bytes))
    except Exception as exc:  # noqa: BLE001
        st.error(f"Could not read the workbook: {exc}")
        st.stop()
    ss.data_hash, ss.metrics, ss.allowed = data_hash, m, allowed_numbers(m)
    ss.local_log, ss.chat, ss.deck_bytes, ss.session_id, ss.agent_synced = [], [], None, None, None
    set_narrative(to_json(rule_based(m), m), f"data-driven draft from {up.name if up else 'bundled dataset'}",
                  by="system")
    if ok:
        try:
            from src.analysis import metrics_for_llm
            studio = get_studio(model)
            ss.session_id = studio.start_session(user_id, {"narrative": ss.narrative,
                                                            "facts": metrics_for_llm(m),
                                                            "allowed_numbers": ss.allowed})
            ss.agent_synced = json.dumps(ss.narrative, sort_keys=True)
        except Exception as exc:  # noqa: BLE001
            st.sidebar.error(f"Agent session failed: {exc}")

m = ss.metrics
if ss.get("_pending"):
    load_widgets(ss._pending)
    ss._pending = None

# ---------------------------------------------------------------- header + locked KPIs
st.title("NovaRetail QBR · Deck Studio")
st.caption(f"{quarter_label(m.latest_q)} · {len(m.regions)} regions × {len(m.quarters)} quarters · "
           f"review → refine with AI → generate")
k1, k2, k3, k4 = st.columns(4)
kp = ss.narrative["kpis"]
k1.metric(kp[0]["label"], kp[0]["value"])
k2.metric(kp[1]["label"], kp[1]["value"])
k3.metric(kp[2]["label"], kp[2]["value"])
k4.metric("Standout / at-risk", f"{m.facts['top_growth_region']} / {m.facts['bottom_growth_region']}")
st.caption("🔒 KPI values are computed from the data and locked; only their labels can be edited.")

tab_edit, tab_json, tab_hist, tab_data = st.tabs(
    ["① Review & refine", "② Narrative JSON", "③ Revision history", "Data"])

# ---------------------------------------------------------------- tab 1: editor + chat
with tab_edit:
    left, right = st.columns([3, 2], gap="large")
    with left:
        s1, s2, s3, s4, s5 = st.tabs(["Slide 1 · Summary", "Slide 2 · Performance", "Slide 3 · Customer",
                                      "Slide 4 · Risk", "Slide 5 · Actions"])
        with s1:
            st.text_input("Subtitle", key="w_subtitle")
            c = st.columns(3)
            for i in range(3):
                c[i].text_input(f"KPI {i + 1} label  ({kp[i]['value']})", key=f"w_kpi_{i}")
            st.text_area("Executive summary", key="w_summary", height=170)
        for tab, s in ((s2, 2), (s3, 3), (s4, 4)):
            with tab:
                st.text_input("Headline", key=f"w_h_{s}")
                for j in range(4):
                    st.text_input(f"Bullet {j + 1}" + (" (optional)" if j == 3 else ""), key=f"w_b_{s}_{j}")
        with s5:
            for j in range(4):
                a, b = st.columns([1, 4])
                a.text_input(f"Verb {j + 1}", key=f"w_av_{j}")
                b.text_input(f"Action {j + 1}" + (" (optional)" if j == 3 else ""), key=f"w_at_{j}")
            st.text_input("Closing statement", key="w_closing")

        # sync manual edits
        edited = narrative_from_widgets(ss.narrative)
        if json.dumps(edited, sort_keys=True) != json.dumps(ss.narrative, sort_keys=True):
            ss.narrative = edited
            ss.local_log.append({"time": time.strftime("%H:%M:%S"), "by": "user", "section": "editor",
                                 "note": "manual edit"})
        issues = validate_narrative(ss.narrative, ss.allowed)
        if issues:
            with st.expander(f"⚠️ {len(issues)} validation issue(s)", expanded=True):
                for i in issues:
                    st.write("• " + i)
        else:
            st.success("✓ Narrative passes all checks: structure, length and every number grounded in the data.")

    with right:
        st.subheader("💬 Refine with AI")
        if not ok or not ss.session_id:
            st.info("The AI agent is off. Add `GOOGLE_API_KEY=...` (or Vertex AI settings) to a `.env` file "
                    "and restart. You can still edit everything manually and generate the deck.")
        else:
            studio = get_studio(model)
            st.caption(f"ADK session `{ss.session_id}` · storage: {studio.storage}")
            box = st.container(height=380)
            with box:
                if not ss.chat:
                    st.markdown("<span class='small'>Ask for changes in plain English. The agent edits the draft "
                                "on the left and remembers your preferences.</span>", unsafe_allow_html=True)
                for role, text in ss.chat:
                    st.chat_message(role).write(text)
            qp = st.columns(2)
            clicked = None
            for i, p in enumerate(QUICK_PROMPTS):
                if qp[i % 2].button(p, key=f"qp_{i}", width="stretch"):
                    clicked = p
            msg = st.chat_input("e.g. make slide 3 bullets more customer-centric") or clicked
            if msg:
                cur = json.dumps(ss.narrative, sort_keys=True)
                if cur != ss.agent_synced:            # push manual edits into the ADK session first
                    studio.push_state(user_id, ss.session_id, {"narrative": ss.narrative}, "manual edits synced")
                with st.spinner("Agent is working…"):
                    try:
                        res = studio.chat(user_id, ss.session_id, msg)
                    except Exception as exc:  # noqa: BLE001
                        res = {"reply": f"⚠️ Agent error: {exc}", "narrative": None, "tool_calls": []}
                tools = [t for t in res["tool_calls"] if t != "get_deck_context"]
                reply = res["reply"] + (f"\n\n_🛠 {', '.join(tools)}_" if tools else "")
                ss.chat += [("user", msg), ("assistant", reply)]
                if res.get("narrative"):
                    ss.agent_synced = json.dumps(res["narrative"], sort_keys=True)
                    if ss.agent_synced != cur:
                        set_narrative(res["narrative"], f"agent: {msg[:60]}", by="agent")
                st.rerun()
            prefs = studio.get_state(user_id, ss.session_id).get("user:preferences", [])
            if prefs:
                with st.expander(f"🧠 Remembered preferences ({len(prefs)})"):
                    for p in prefs:
                        st.write("• " + p)
                    if st.button("Forget my preferences"):
                        studio.push_state(user_id, ss.session_id, {"user:preferences": []}, "preferences cleared")
                        st.rerun()

    st.divider()
    g1, g2, g3 = st.columns([2, 2, 3])
    force = g2.checkbox("Build even with warnings", value=False, disabled=not issues)
    if g1.button("🚀 Generate Deck", type="primary", width="stretch", disabled=bool(issues) and not force):
        buf = io.BytesIO()
        try:
            with st.spinner("Building deck…"):
                log, _ = render(m, ss.narrative, buf,
                                template=io.BytesIO(tpl_up.getvalue()) if tpl_up else None,
                                icons=ico_up.getvalue() if ico_up else None, strict=not force)
            ss.deck_bytes, ss.build_log = buf.getvalue(), log
            D["out"].parent.mkdir(parents=True, exist_ok=True)
            D["out"].write_bytes(ss.deck_bytes)
            (D["out"].parent / "narrative.json").write_text(json.dumps(ss.narrative, indent=2, ensure_ascii=False),
                                                           encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            st.error(f"Deck build failed: {exc}")
    if ss.get("deck_bytes"):
        g3.download_button("⬇️ Download PowerPoint", ss.deck_bytes, "NovaRetail_QBR_Executive_Deck.pptx",
                           mime="application/vnd.openxmlformats-officedocument.presentationml.presentation",
                           width="stretch")
        with st.expander("Build log"):
            st.code("\n".join(ss.build_log))

# ---------------------------------------------------------------- tab 2: narrative JSON
with tab_json:
    st.caption("This JSON is the editable intermediate artifact between analysis and deck assembly. "
               "The deck is built only from this file.")
    a, b = st.columns(2)
    a.download_button("⬇️ Download narrative.json", json.dumps(ss.narrative, indent=2, ensure_ascii=False),
                      "narrative.json", mime="application/json", width="stretch")
    nj = b.file_uploader("Load a narrative.json", type=["json"], label_visibility="collapsed")
    if nj and ss.get("loaded_json") != nj.file_id:
        try:
            data = json.loads(nj.getvalue().decode("utf-8"))
            bad = [i for i in validate_narrative(data, ss.allowed) if i.startswith("missing")]
            if bad:
                st.error("; ".join(bad))
            else:
                ss.loaded_json = nj.file_id
                set_narrative(data, f"loaded {nj.name}")
                st.rerun()
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")
    raw = st.text_area("Edit JSON directly", json.dumps(ss.narrative, indent=2, ensure_ascii=False), height=420)
    if st.button("Apply JSON"):
        try:
            data = json.loads(raw)
            set_narrative(data, "JSON edited directly")
            st.rerun()
        except json.JSONDecodeError as exc:
            st.error(f"Invalid JSON: {exc}")

# ---------------------------------------------------------------- tab 3: history
with tab_hist:
    log = ss.local_log
    if ok and ss.session_id:
        try:
            log = get_studio(model).get_state(user_id, ss.session_id).get("revision_log", log)
        except Exception:  # noqa: BLE001
            pass
    st.dataframe(list(reversed(log)), width="stretch", hide_index=True)

# ---------------------------------------------------------------- tab 4: data
with tab_data:
    for name, df in (("Regional_Performance", m.perf), ("Customer_Experience", m.cx), ("Operational_Risk", m.risk)):
        st.markdown(f"**{name}**")
        st.dataframe(df, width="stretch", hide_index=True)
