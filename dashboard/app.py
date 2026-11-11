"""NetDiagnose AI dashboard.

    streamlit run dashboard/app.py          # http://localhost:8501
    (the API must be running: uvicorn backend.main:app)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))   # so "dashboard" / "backend" import

import streamlit as st  # noqa: E402

from dashboard import client  # noqa: E402

st.set_page_config(page_title="NetDiagnose AI", page_icon=":material/lan:", layout="wide")

VIEWS = Path(__file__).parent / "views"
PAGES = {
    "overview": st.Page(VIEWS / "overview.py", title="Overview", icon=":material/dashboard:",
                        default=True),
    "devices": st.Page(VIEWS / "devices.py", title="Devices", icon=":material/router:"),
    "incidents": st.Page(VIEWS / "incidents.py", title="Incidents", icon=":material/warning:"),
    "incident": st.Page(VIEWS / "incident_detail.py", title="Incident detail",
                        icon=":material/troubleshoot:"),
    "simulator": st.Page(VIEWS / "simulator.py", title="Simulator", icon=":material/science:"),
}
st.session_state["pages"] = PAGES        # views use this to jump to another page

nav = st.navigation(list(PAGES.values()))

with st.sidebar:
    st.markdown("**NetDiagnose AI**  \nML detects → rules gather evidence → diagnosis")
    try:
        h = client.health()
        model = h["model_version"] or "not loaded"
        st.caption(f"API ✅ {h['status']} · DB {h['database']}  \nModel: `{model}`")
    except client.ApiError as e:
        st.error(str(e), icon=":material/cloud_off:")
    if st.button("Refresh data", icon=":material/refresh:", width="stretch"):
        st.cache_data.clear()
        st.rerun()

nav.run()
