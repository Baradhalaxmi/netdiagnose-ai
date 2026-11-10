from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.config import Settings
from backend.deps import get_detector, get_session, settings_dep
from backend.schemas.stats import Health

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
def health(
    session: Session = Depends(get_session),
    detector=Depends(get_detector),
    settings: Settings = Depends(settings_dep),
):
    try:
        session.execute(text("SELECT 1"))
        db = "up"
    except Exception:
        db = "down"
    ok = db == "up" and detector is not None
    return Health(
        status="ok" if ok else "degraded",
        database=db,
        model_loaded=detector is not None,
        model_version=detector.version if detector else None,
        agent_enabled=settings.agent_enabled,
    )
