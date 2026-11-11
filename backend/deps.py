"""FastAPI dependencies shared by the routers (overridable in tests)."""

from __future__ import annotations

from functools import lru_cache

from fastapi import Query, Request

from backend.config import Settings, get_settings
from backend.db.session import get_session  # noqa: F401  (re-exported for routers)
from backend.schemas.common import DEFAULT_LIMIT, MAX_LIMIT
from ml.predict import Detector


def get_detector(request: Request) -> Detector | None:
    """The model loaded once at startup (None if it failed to load)."""
    return getattr(request.app.state, "detector", None)


def settings_dep() -> Settings:
    return get_settings()


@lru_cache(maxsize=2)
def _attack_features(path: str) -> tuple[dict, ...]:
    from simulator.replay import load_attack_features

    return tuple(load_attack_features(path))


def get_attack_features() -> list[dict]:
    """Real attack-flow features for injected faults (read once, then cached)."""
    from backend.errors import AppError

    path = get_settings().dataset_path
    try:
        return list(_attack_features(path))
    except FileNotFoundError as exc:
        raise AppError(503, "DATASET_UNAVAILABLE",
                       f"{path} not found; the simulator needs the UNSW-NB15 testing CSV") from exc


class Pagination:
    def __init__(
        self,
        limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
        offset: int = Query(0, ge=0),
    ):
        self.limit = limit
        self.offset = offset
