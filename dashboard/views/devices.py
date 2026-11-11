import pandas as pd
import streamlit as st

from dashboard import client, theme
from dashboard.nav import show_api_error

st.title("Devices")

try:
    devices = client.devices()
except client.ApiError as e:
    show_api_error(e)

by_name = {d["hostname"]: d for d in devices}
col1, col2 = st.columns([2, 1])
hostname = col1.selectbox("Device", list(by_name), key="device_host")
limit = col2.select_slider("Recent samples", options=[100, 250, 500, 1000], value=500)
dev = by_name[hostname]
st.caption(f"{dev['device_type']} · {dev['ip_address'] or 'no IP'} · {dev['location'] or '–'} · "
           f"link {float(dev['link_capacity_mbps']):,.0f} Mbps")

try:
    rows = client.metrics(dev["id"], limit)
except client.ApiError as e:
    show_api_error(e)

if not rows:
    st.info("No metrics for this device yet.")
    st.stop()

df = pd.DataFrame(rows)
df["ts"] = pd.to_datetime(df["ts"])
scored = df["is_anomaly"].notna().sum()
flagged = (df["is_anomaly"] == True).sum()  # noqa: E712  (column holds True/False/None)
c1, c2, c3 = st.columns(3)
c1.metric("Samples shown", f"{len(df):,}")
c2.metric("Flagged by detector", f"{flagged:,}")
c3.metric("Flagged share", f"{flagged / scored:.0%}" if scored else "–")

# One chart per metric (small multiples): never two scales on one chart.
for pair in [("latency_ms", "packet_loss_pct"), ("throughput_mbps", "interface_utilization_pct"),
             ("cpu_usage_pct", "jitter_ms")]:
    cols = st.columns(2)
    for col, metric in zip(cols, pair):
        with col:
            if df[metric].notna().any():
                st.altair_chart(theme.metric_chart(df, metric), width="stretch")
            else:
                st.caption(f"{theme.METRIC_LABELS[metric]}: no data (e.g. latency is only "
                           "measured for TCP flows)")

with st.expander("Table view"):
    st.dataframe(df.drop(columns=["device_id"]), hide_index=True, width="stretch")
