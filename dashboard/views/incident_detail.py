import pandas as pd
import streamlit as st

from dashboard import client, theme
from dashboard.nav import show_api_error

st.title("Incident detail")

# Which incident: the one chosen elsewhere, or pick from the most recent.
try:
    recent = client.incidents(limit=50)["items"]
except client.ApiError as e:
    show_api_error(e)
if not recent and "incident_id" not in st.session_state:
    st.info("No incidents yet. Use the **Simulator** page to inject a fault.")
    st.stop()

options = {i["id"]: f"{i['code']} · {i['hostname']} · {i['status']}" for i in recent}
current = st.session_state.get("incident_id")
if current is not None and current not in options:
    options = {current: f"Incident {current}", **options}
ids = list(options)
chosen = st.selectbox("Incident", ids, index=ids.index(current) if current in ids else 0,
                      format_func=options.get)
st.session_state["incident_id"] = chosen

try:
    inc = client.incident(chosen)
except client.ApiError as e:
    show_api_error(e)

st.subheader(f"{inc['code']} on {inc['device']['hostname']}")
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Status", inc["status"])
c2.metric("Severity", theme.SEVERITY_LABEL[inc["severity"]])
c3.metric("Anomalies", inc["anomaly_count"])
c4.metric("Peak score", f"{inc['peak_score']:.2f}" if inc["peak_score"] is not None else "–")
c5.metric("Duration", theme.duration(inc["opened_at"], inc["last_seen_at"]))
st.caption(f"Opened {theme.fmt_time(inc['opened_at'])} · last anomaly "
           f"{theme.fmt_time(inc['last_seen_at'])}"
           + (f" · closed {theme.fmt_time(inc['closed_at'])}" if inc["closed_at"] else ""))

# ---- diagnosis ---------------------------------------------------------
FALLBACK_TEXT = {
    "disabled": "The AI agent is switched off (AGENT_ENABLED=false), so only the rules ran.",
    "auth": "The LLM API key is missing or invalid; showing the rules diagnosis.",
    "unreachable": "The LLM provider could not be reached; showing the rules diagnosis.",
    "rate_limited": "The LLM provider is rate-limiting requests; try again shortly.",
    "refusal": "The model declined this request; showing the rules diagnosis.",
    "invalid_output": "The AI answer failed validation twice (e.g. cited evidence that does "
                      "not exist), so it was discarded in favour of the rules.",
    "no_submission": "The AI did not produce a diagnosis; showing the rules diagnosis.",
}
AI_LABEL = {0.3: "low", 0.5: "medium", 0.7: "medium-high", 0.85: "high"}

try:
    agent_on = client.health()["agent_enabled"]
except client.ApiError:
    agent_on = False

b1, b2, b3, _ = st.columns([1, 1.2, 1, 2])
if b1.button("Run diagnosis", type="primary", icon=":material/troubleshoot:"):
    with st.spinner("Collecting evidence and evaluating rules…"):
        try:
            client.diagnose(chosen)
        except client.ApiError as e:
            show_api_error(e)
    st.rerun()
if b2.button("Run AI diagnosis", icon=":material/smart_toy:", disabled=not agent_on,
             help=None if agent_on else "Set AGENT_ENABLED=true and an API key to enable"):
    with st.spinner("The agent is reading the evidence and calling tools…"):
        try:
            out = client.diagnose(chosen, use_agent=True)
        except client.ApiError as e:
            show_api_error(e)
    st.session_state["agent_note"] = (chosen, out["fallback_reason"])
    st.rerun()
diagnoses = inc["diagnoses"]
if diagnoses and b3.button("Rebuild evidence", icon=":material/restart_alt:",
                           help="Recompute evidence against the current baseline"):
    client.diagnose(chosen, force=True)
    st.rerun()

note = st.session_state.get("agent_note")
if note and note[0] == chosen and note[1]:
    st.warning(FALLBACK_TEXT.get(note[1], f"AI fallback: {note[1]}"), icon=":material/info:")

rules = next((d for d in diagnoses if d["source"] == "rules"), None)
ai = next((d for d in diagnoses if d["source"] == "ai_agent"), None)

if rules is None:
    st.info("Not diagnosed yet. Click **Run diagnosis** to collect evidence and rank causes.")
else:
    st.markdown(f"### Probable cause: {rules['probable_cause']}")
    st.caption(f"Rule engine · confidence {rules['confidence']:.2f} · cites evidence "
               f"{', '.join(map(str, rules['cited_evidence'])) or '–'}")

    left, right = st.columns([1, 1])
    with left:
        st.markdown("**Ranked causes**")
        causes = [{"cause": rules["probable_cause"], "confidence": rules["confidence"]}] + [
            {"cause": a["cause"], "confidence": a["confidence"]} for a in rules["alternatives"]]
        st.altair_chart(theme.causes_chart(causes), width="stretch")
    with right:
        st.markdown("**Recommended investigation**")
        for i, step in enumerate(rules["recommendations"] or ["Review the flagged flows"], 1):
            st.markdown(f"{i}. {step}")

    if ai is not None:
        st.markdown("### Rules vs AI agent")
        c_rules, c_ai = st.columns(2)
        with c_rules.container(border=True):
            st.markdown("**Rule engine**")
            st.markdown(f"#### {rules['probable_cause']}")
            st.caption(f"confidence {rules['confidence']:.2f} · deterministic")
            for a in rules["alternatives"][:3]:
                st.markdown(f"- alternative: {a['cause']} ({a['confidence']:.2f})")
        with c_ai.container(border=True):
            st.markdown("**AI agent**")
            st.markdown(f"#### {ai['probable_cause']}")
            label = AI_LABEL.get(round(ai["confidence"] or 0, 2), f"{ai['confidence']:.2f}")
            st.caption(f"confidence {label} · cites {', '.join(map(str, ai['cited_evidence']))}")
            for a in ai["alternatives"][:3]:
                st.markdown(f"- alternative: **{a['cause']}**: {a['why_less_likely']}")
        agree = ai["probable_cause"].strip().lower() == rules["probable_cause"].strip().lower()
        st.caption("The AI agrees with the rule engine." if agree else
                   "The AI reached a different conclusion; compare the cited evidence below.")
        with st.expander("AI report (markdown)", expanded=True):
            st.markdown(ai["report_markdown"] or "_No report_")

    st.markdown("**Evidence: incident window vs device baseline**")
    ev = pd.DataFrame(inc["evidence"])
    if not ev.empty:
        by_rules = set(rules["cited_evidence"])
        by_ai = set(ai["cited_evidence"]) if ai else set()
        ev["metric"] = ev["metric_name"].map(theme.METRIC_LABELS).fillna(ev["metric_name"])
        ev["abnormal"] = ev["breached"].map({True: "⚠ yes", False: "no"})
        ev["cited"] = ev["id"].map(lambda i: ", ".join(
            [n for n, s in (("rules", by_rules), ("AI", by_ai)) if i in s]))
        st.dataframe(
            ev[["id", "metric", "observed_value", "baseline_value", "ratio", "abnormal", "cited"]],
            hide_index=True, width="stretch",
            column_config={
                "id": "ID", "metric": "Metric",
                "observed_value": st.column_config.NumberColumn("Observed", format="%.2f"),
                "baseline_value": st.column_config.NumberColumn("Baseline", format="%.2f"),
                "ratio": st.column_config.NumberColumn("× baseline", format="%.1f×"),
                "abnormal": "Abnormal", "cited": "Cited by",
            },
        )
    with st.expander("Rule engine report (markdown)"):
        st.markdown(rules["report_markdown"] or "_No report_")

# ---- status ------------------------------------------------------------
st.divider()
s1, s2, _ = st.columns([1, 1, 3])
new_status = s1.selectbox("Change status", ["investigating", "resolved", "closed"],
                          label_visibility="collapsed")
if s2.button("Update status"):
    try:
        client.update_incident(chosen, status=new_status)
    except client.ApiError as e:
        show_api_error(e)
    st.rerun()
