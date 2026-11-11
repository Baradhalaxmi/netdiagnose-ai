"""Thin client for the FastAPI backend. The dashboard never touches the DB.

Reads are cached briefly with st.cache_data; any write clears the cache so
the next render shows fresh data.
"""

from __future__ import annotations

import os

import requests
import streamlit as st

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = 60  # injecting a scenario writes ~70 rows and runs a diagnosis


class ApiError(Exception):
    """Carries the API's {"error": {"code", "message"}} body, or a connection problem."""

    def __init__(self, message: str, code: str = "API_ERROR"):
        super().__init__(message)
        self.code = code


def _request(method: str, path: str, **kwargs):
    try:
        r = requests.request(method, f"{BASE_URL}{path}", timeout=TIMEOUT, **kwargs)
    except requests.ConnectionError as exc:
        raise ApiError(f"Cannot reach the API at {BASE_URL}. Is uvicorn running?",
                       "UNREACHABLE") from exc
    if r.status_code >= 400:
        try:
            err = r.json()["error"]
            raise ApiError(err["message"], err["code"])
        except (ValueError, KeyError):
            raise ApiError(f"HTTP {r.status_code}: {r.text[:200]}") from None
    return r.json()


def _clean(params: dict) -> dict:
    return {k: v for k, v in params.items() if v not in (None, "", "All")}


# ---- reads (cached) --------------------------------------------------
@st.cache_data(ttl=10, show_spinner=False)
def health() -> dict:
    return _request("GET", "/health")


@st.cache_data(ttl=300, show_spinner=False)
def devices() -> list[dict]:
    return _request("GET", "/devices")["items"]


@st.cache_data(ttl=15, show_spinner=False)
def stats() -> dict:
    return _request("GET", "/stats/summary")


@st.cache_data(ttl=10, show_spinner=False)
def incidents(status=None, severity=None, device_id=None, limit=100) -> dict:
    return _request("GET", "/incidents", params=_clean(
        {"status": status, "severity": severity, "device_id": device_id, "limit": limit}))


@st.cache_data(ttl=10, show_spinner=False)
def incident(incident_id: int) -> dict:
    return _request("GET", f"/incidents/{incident_id}")


@st.cache_data(ttl=30, show_spinner=False)
def metrics(device_id: int, limit: int = 500) -> list[dict]:
    return _request("GET", "/metrics", params={"device_id": device_id, "limit": limit})["items"]


@st.cache_data(ttl=300, show_spinner=False)
def scenarios() -> list[dict]:
    return _request("GET", "/simulator/scenarios")


# ---- writes (clear the cache) ----------------------------------------
def diagnose(incident_id: int, force: bool = False) -> list[dict]:
    out = _request("POST", f"/incidents/{incident_id}/diagnose", params={"force": force})
    st.cache_data.clear()
    return out


def update_incident(incident_id: int, **fields) -> dict:
    out = _request("PATCH", f"/incidents/{incident_id}", json=fields)
    st.cache_data.clear()
    return out


def inject(name: str, device: str | None = None) -> dict:
    out = _request("POST", f"/simulator/scenarios/{name}", params=_clean({"device": device}))
    st.cache_data.clear()
    return out
