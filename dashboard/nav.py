import streamlit as st


def open_incident(incident_id: int) -> None:
    """Remember the incident and jump to the detail page."""
    st.session_state["incident_id"] = int(incident_id)
    pages = st.session_state.get("pages")
    if pages:                       # absent when a view runs on its own (tests)
        st.switch_page(pages["incident"])


def show_api_error(e: Exception) -> None:
    st.error(f"{e}", icon=":material/error:")
    st.stop()
