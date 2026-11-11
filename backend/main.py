"""NetDiagnose AI API.

    uvicorn backend.main:app --reload      # http://localhost:8000/docs
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from backend import errors
from backend.api import health, incidents, metrics, simulator, stats
from backend.config import get_settings
from ml.predict import Detector

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("netdiagnose.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Load the model once at startup, not per request. If it is missing the
    API still starts; /health reports it and /detect returns 503."""
    settings = get_settings()
    try:
        app.state.detector = Detector.load(settings.model_path, settings.anomaly_threshold)
        log.info("model loaded: %s (threshold %.4f)",
                 app.state.detector.version, app.state.detector.threshold)
    except Exception as exc:
        app.state.detector = None
        log.warning("model not loaded: %s", exc)
    yield


app = FastAPI(
    title="NetDiagnose AI",
    version="0.4.0",
    description="Evidence-based network fault diagnosis: detect, correlate, diagnose.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8501", "http://127.0.0.1:8501"],  # Streamlit dashboard
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_log(request: Request, call_next):
    """One log line per request, with an id echoed back as X-Request-ID."""
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    response.headers["X-Request-ID"] = rid
    log.info("rid=%s %s %s -> %s %.1fms", rid, request.method, request.url.path,
             response.status_code, ms)
    return response


errors.register(app)
for r in (health.router, metrics.router, incidents.router, stats.router, simulator.router):
    app.include_router(r)
