from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Application, JobCluster
from app.models.enums import ALL_STAGES
from app.services import queue as queue_service

router = APIRouter()


class AdvanceBody(BaseModel):
    stage: str
    note: str | None = None


@router.get("")
def list_applications(
    stage: str | None = None, limit: int = 100, db: Session = Depends(get_db)
) -> list[dict]:
    stmt = select(Application).order_by(Application.stage_entered_at.desc()).limit(limit)
    if stage:
        stmt = stmt.where(Application.stage == stage)
    out = []
    for app_row in db.execute(stmt).scalars():
        cluster = db.get(JobCluster, app_row.cluster_id)
        posting = queue_service._canonical(db, cluster) if cluster else None
        out.append({
            "id": app_row.id,
            "stage": app_row.stage,
            "track": app_row.track,
            "channel": app_row.channel,
            "title": posting.title if posting else None,
            "company": posting.company_name if posting else None,
            "apply_url": posting.apply_url if posting else None,
            "sent_at": app_row.sent_at,
            "replied": app_row.replied,
            "replied_at": app_row.replied_at,
            "hours_to_reply": app_row.hours_to_reply,
            "follow_up_due_at": app_row.follow_up_due_at,
            "presumed_dead_at": app_row.presumed_dead_at,
            "outcome": app_row.outcome,
        })
    return out


@router.get("/{application_id}")
def application_detail(application_id: int, db: Session = Depends(get_db)) -> dict:
    app_row = _get(db, application_id)
    return {
        "id": app_row.id,
        "stage": app_row.stage,
        "track": app_row.track,
        "channel": app_row.channel,
        "sent_at": app_row.sent_at,
        "replied": app_row.replied,
        "outcome": app_row.outcome,
        "events": [
            {"from": e.from_stage, "to": e.to_stage, "at": e.at,
             "actor": e.actor, "note": e.note}
            for e in app_row.events
        ],
    }


@router.post("/{application_id}/advance")
def advance(
    application_id: int, body: AdvanceBody, db: Session = Depends(get_db)
) -> dict:
    app_row = _get(db, application_id)
    if body.stage not in ALL_STAGES:
        raise HTTPException(400, f"unknown stage: {body.stage}")
    queue_service.advance(db, app_row, body.stage, actor="human", note=body.note)
    db.commit()
    return {"ok": True, "stage": app_row.stage}


def _get(db: Session, application_id: int) -> Application:
    app_row = db.get(Application, application_id)
    if app_row is None:
        raise HTTPException(404, "application not found")
    return app_row
