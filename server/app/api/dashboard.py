from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import (
    Application, Draft, EligibilityFlag, JobCluster, Profile, Score, Source, SourceRun,
)
from app.services import pipeline, queue as queue_service

router = APIRouter()


@router.get("")
def overview(db: Session = Depends(get_db)) -> dict:
    profile = db.execute(select(Profile).limit(1)).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    stats = pipeline.pipeline_stats(db)

    gate_counts = dict(
        db.execute(
            select(EligibilityFlag.code, func.count(EligibilityFlag.id))
            .group_by(EligibilityFlag.code)
            .order_by(func.count(EligibilityFlag.id).desc())
        ).all()
    )

    stage_counts = dict(
        db.execute(
            select(Application.stage, func.count(Application.id))
            .group_by(Application.stage)
        ).all()
    )

    sent_7d = db.execute(
        select(func.count(Application.id)).where(Application.sent_at >= week_ago)
    ).scalar_one()
    replied_7d = db.execute(
        select(func.count(Application.id)).where(
            Application.replied.is_(True), Application.replied_at >= week_ago
        )
    ).scalar_one()
    total_sent = db.execute(
        select(func.count(Application.id)).where(Application.sent_at.isnot(None))
    ).scalar_one()
    total_replied = db.execute(
        select(func.count(Application.id)).where(Application.replied.is_(True))
    ).scalar_one()

    pending_drafts = db.execute(
        select(func.count(Draft.id)).where(Draft.status.in_(("draft", "approved")))
    ).scalar_one()

    failing_sources = db.execute(
        select(func.count(Source.id)).where(Source.consecutive_failures > 0)
    ).scalar_one()

    return {
        "pipeline": stats,
        "queue_size": db.execute(
            select(func.count(JobCluster.id)).where(JobCluster.status == "scored")
        ).scalar_one(),
        "gate_counts": gate_counts,
        "stage_counts": stage_counts,
        "pending_drafts": pending_drafts,
        "sends": {
            "this_week": queue_service.sends_this_week(db),
            "cap": (profile.weekly_send_cap if profile else settings.WEEKLY_SEND_CAP),
            "remaining": queue_service.remaining_this_week(db, profile),
            "last_7d": sent_7d,
        },
        "replies": {
            "last_7d": replied_7d,
            "total_sent": total_sent,
            "total_replied": total_replied,
            # The headline metric. Market baseline is 2-3%; this design targets 8%.
            "reply_rate": round(total_replied / total_sent, 4) if total_sent else None,
        },
        "sources": {
            "total": db.execute(select(func.count(Source.id))).scalar_one(),
            "enabled": db.execute(
                select(func.count(Source.id)).where(Source.enabled.is_(True))
            ).scalar_one(),
            "failing": failing_sources,
        },
        "llm_calls_today": _llm_calls_today(),
    }


def _llm_calls_today() -> dict:
    from app.services.llm import calls_today

    return {"used": calls_today(), "cap": settings.LLM_DAILY_CALL_CAP}


@router.get("/activity")
def activity(limit: int = 30, db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(SourceRun, Source)
        .join(Source, SourceRun.source_id == Source.id)
        .order_by(SourceRun.started_at.desc())
        .limit(limit)
    ).all()
    return [
        {
            "source": source.key,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "items_seen": run.items_seen,
            "items_new": run.items_new,
            "error": run.error,
            "drift": run.schema_drift_note,
        }
        for run, source in rows
    ]
