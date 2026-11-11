import pandas as pd
import streamlit as st

from dashboard import client, theme
from dashboard.nav import open_incident, show_api_error

st.title("Incidents")

try:
    devices = client.devices()
except client.ApiError as e:
    show_api_error(e)

f1, f2, f3 = st.columns(3)
status = f1.selectbox("Status", ["All", "open", "investigating", "resolved", "closed"])
severity = f2.selectbox("Severity", ["All", *theme.SEVERITY_ORDER])
device_names = {"All": None, **{d["hostname"]: d["id"] for d in devices}}
device = f3.selectbox("Device", list(device_names))

try:
    page = client.incidents(status=status, severity=severity, device_id=device_names[device],
                            limit=200)
except client.ApiError as e:
    show_api_error(e)

st.caption(f"{page['total']:,} incident(s) match; showing the {len(page['items'])} most recent.")
if not page["items"]:
    st.info("Nothing matches these filters.")
    st.stop()

df = pd.DataFrame(page["items"])
df["opened"] = df["opened_at"].map(theme.fmt_time)
df["duration"] = [theme.duration(a, b) for a, b in zip(df["opened_at"], df["last_seen_at"])]
df["severity"] = df["severity"].map(theme.SEVERITY_LABEL)
event = st.dataframe(
    df[["code", "hostname", "status", "severity", "top_cause", "anomaly_count", "peak_score",
        "opened", "duration"]],
    hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
    column_config={
        "code": "Incident", "hostname": "Device", "top_cause": "Top cause",
        "anomaly_count": "Anomalies",
        "peak_score": st.column_config.ProgressColumn("Peak score", min_value=0, max_value=1,
                                                      format="%.2f"),
    },
    key="incident_table",
)
st.caption("Select a row to open the incident.")
if event.selection.rows:
    open_incident(df.iloc[event.selection.rows[0]]["id"])
