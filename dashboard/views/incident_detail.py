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
b1, b2, _ = st.columns([1, 1, 3])
if b1.button("Run diagnosis", type="primary", icon=":material/troubleshoot:"):
    with st.spinner("Collecting evidence and evaluating rules…"):
        try:
            client.diagnose(chosen)
        except client.ApiError as e:
            show_api_error(e)
    st.rerun()
if inc["diagnoses"] and b2.button("Rebuild evidence", icon=":material/restart_alt:",
                                  help="Recompute evidence against the current baseline"):
    client.diagnose(chosen, force=True)
    st.rerun()

if not inc["diagnoses"]:
    st.info("Not diagnosed yet. Click **Run diagnosis** to collect evidence and rank causes.")
else:
    d = inc["diagnoses"][0]
    st.markdown(f"### Probable cause: {d['probable_cause']}")
    st.caption(f"Source: {d['source']} · confidence {d['confidence']:.2f} · "
               f"cites evidence {', '.join(map(str, d['cited_evidence'])) or '–'}")

    left, right = st.columns([1, 1])
    with left:
        st.markdown("**Ranked causes**")
        causes = [{"cause": d["probable_cause"], "confidence": d["confidence"]}] + [
            {"cause": a["cause"], "confidence": a["confidence"]} for a in d["alternatives"]]
        st.altair_chart(theme.causes_chart(causes), width="stretch")
    with right:
        st.markdown("**Recommended investigation**")
        for i, step in enumerate(d["recommendations"] or ["Review the flagged flows"], 1):
            st.markdown(f"{i}. {step}")

    st.markdown("**Evidence: incident window vs device baseline**")
    ev = pd.DataFrame(inc["evidence"])
    if not ev.empty:
        cited = set(d["cited_evidence"])
        ev["metric"] = ev["metric_name"].map(theme.METRIC_LABELS).fillna(ev["metric_name"])
        ev["abnormal"] = ev["breached"].map({True: "⚠ yes", False: "no"})
        ev["cited"] = ev["id"].map(lambda i: "✔" if i in cited else "")
        st.dataframe(
            ev[["id", "metric", "observed_value", "baseline_value", "ratio", "abnormal", "cited"]],
            hide_index=True, width="stretch",
            column_config={
                "id": "ID", "metric": "Metric",
                "observed_value": st.column_config.NumberColumn("Observed", format="%.2f"),
                "baseline_value": st.column_config.NumberColumn("Baseline", format="%.2f"),
                "ratio": st.column_config.NumberColumn("× baseline", format="%.1f×"),
                "abnormal": "Abnormal", "cited": "Cited",
            },
        )
    with st.expander("Full report (markdown)"):
        st.markdown(d["report_markdown"] or "_No report_")

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
