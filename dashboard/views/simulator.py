import streamlit as st

from dashboard import client
from dashboard.nav import open_incident, show_api_error

st.title("Fault simulator")
st.caption("Each button writes an hour of normal telemetry for the device, then a 5-minute fault "
           "carrying real attack-flow features. The ML detector should open an incident and the "
           "rule engine should rank the true cause first.")

try:
    scenarios = client.scenarios()
    hostnames = [d["hostname"] for d in client.devices()]
except client.ApiError as e:
    show_api_error(e)

cols = st.columns(2)
for i, s in enumerate(scenarios):
    with cols[i % 2].container(border=True):
        st.markdown(f"**{s['name'].replace('_', ' ').title()}**")
        st.caption(s["description"])
        device = st.selectbox("Device", hostnames, index=hostnames.index(s["default_device"]),
                              key=f"dev_{s['name']}")
        st.caption(f"Expected cause: *{s['expected_cause']}*")
        if st.button(f"Inject {s['name'].replace('_', ' ')}", key=f"inject_{s['name']}",
                     type="primary", width="stretch"):
            with st.spinner("Injecting and diagnosing…"):
                try:
                    st.session_state["last_injection"] = client.inject(s["name"], device)
                except client.ApiError as e:
                    show_api_error(e)

r = st.session_state.get("last_injection")
if r:
    st.divider()
    st.subheader("Last injection")
    if r["incident"] is None:
        st.warning(f"The detector flagged {r['flagged']}/{r['fault_rows']} fault rows, so no "
                   "incident was opened.", icon=":material/report:")
    else:
        d = r["diagnosis"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Incident", r["incident"]["code"],
                  "new" if r["incident"]["created"] else "joined open incident",
                  delta_color="off")
        c2.metric("Flagged by detector", f"{r['flagged']}/{r['fault_rows']}")
        c3.metric("Top cause confidence", f"{d['confidence']:.2f}" if d else "–")
        if r["correct"]:
            st.success(f"Top cause **{d['probable_cause']}** matches the injected fault.",
                       icon=":material/check_circle:")
        elif d:
            st.error(f"Top cause **{d['probable_cause']}**, expected **{r['expected_cause']}**.",
                     icon=":material/cancel:")
        if st.button("Open incident", icon=":material/arrow_forward:"):
            open_incident(r["incident"]["id"])
