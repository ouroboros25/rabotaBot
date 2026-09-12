from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import llm

router = APIRouter()


@router.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> dict:
    """Liveness plus the two dependencies that actually break: DB and gateway."""
    try:
        db.execute(text("select 1"))
        db_ok = True
    except Exception:  # noqa: BLE001 - health endpoints never raise
        db_ok = False
    gateway = llm.health()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "llm_gateway": gateway,
    }
