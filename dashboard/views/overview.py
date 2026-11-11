import pandas as pd
import streamlit as st

from dashboard import client, theme
from dashboard.nav import open_incident, show_api_error

st.title("Network overview")

try:
    s = client.stats()
    recent = client.incidents(limit=10)
except client.ApiError as e:
    show_api_error(e)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Devices", f"{s['devices']:,}")
c2.metric("Metrics (24h)", f"{s['metrics_24h']:,}")
c3.metric("Anomalies (24h)", f"{s['anomalies_24h']:,}")
c4.metric("Open incidents", f"{s['open_incidents']:,}")

left, right = st.columns([2, 1])
with left:
    st.subheader("Anomalies per hour, last 24h")
    if s["anomalies_per_hour"]:
        st.altair_chart(theme.anomalies_per_hour_chart(s["anomalies_per_hour"]),
                        width="stretch")
    else:
        st.info("No anomalies in the last 24 hours.")
with right:
    st.subheader("Open incidents by severity")
    if s["open_incidents"]:
        st.altair_chart(theme.severity_chart(s["incidents_by_severity"]), width="stretch")
    else:
        st.success("No open incidents.", icon=":material/check_circle:")

st.subheader("Latest incidents")
if not recent["items"]:
    st.info("No incidents yet. Use the **Simulator** page to inject a fault.")
else:
    df = pd.DataFrame(recent["items"])
    df["last_seen"] = df["last_seen_at"].map(theme.fmt_time)
    df["severity"] = df["severity"].map(theme.SEVERITY_LABEL)
    event = st.dataframe(
        df[["code", "hostname", "status", "severity", "top_cause", "anomaly_count", "last_seen"]],
        hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        column_config={"code": "Incident", "hostname": "Device", "top_cause": "Top cause",
                       "anomaly_count": "Anomalies", "last_seen": "Last seen"},
        key="overview_incidents",
    )
    st.caption("Select a row to open the incident.")
    if event.selection.rows:
        open_incident(df.iloc[event.selection.rows[0]]["id"])
