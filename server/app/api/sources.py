from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import JobPosting, Source, SourcePrior, SourceRun

router = APIRouter()

TIER_LABELS = {
    0: "publisher-intended API, no auth, no ToS friction",
    1: "the user's own inbox / own session via the platform's own channel",
    2: "public HTML permitted by robots.txt, never authenticated",
    3: "needs a browser engine; liveness checks only",
}


class SourcePatch(BaseModel):
    enabled: bool | None = None
    cadence_minutes: int | None = None
    notes: str | None = None


@router.get("")
def list_sources(db: Session = Depends(get_db)) -> list[dict]:
    counts = dict(
        db.execute(
            select(JobPosting.source_id, func.count(JobPosting.id))
            .group_by(JobPosting.source_id)
        ).all()
    )
    priors = {}
    for prior in db.execute(select(SourcePrior)).scalars():
        priors.setdefault(prior.source_id, []).append(
            {"track": prior.track,
             "reply_rate": round(prior.alpha / (prior.alpha + prior.beta), 4),
             "sends": prior.sends, "replies": prior.replies}
        )

    out = []
    for source in db.execute(select(Source).order_by(Source.key)).scalars():
        last_run = db.execute(
            select(SourceRun).where(SourceRun.source_id == source.id)
            .order_by(SourceRun.id.desc()).limit(1)
        ).scalar_one_or_none()
        out.append({
            "id": source.id,
            "key": source.key,
            "family": source.family,
            "legal_tier": source.legal_tier,
            "legal_tier_label": TIER_LABELS.get(source.legal_tier, "unknown"),
            "tracks": source.tracks,
            "enabled": source.enabled,
            "auto_discovered": source.auto_discovered,
            "cadence_minutes": source.cadence_minutes,
            "consecutive_failures": source.consecutive_failures,
            "disabled_reason": source.disabled_reason,
            "last_ok_at": source.last_ok_at,
            "postings": counts.get(source.id, 0),
            "priors": priors.get(source.id, []),
            "last_run": {
                "started_at": last_run.started_at,
                "items_seen": last_run.items_seen,
                "items_new": last_run.items_new,
                "error": last_run.error,
                "drift": last_run.schema_drift_note,
            } if last_run else None,
            "notes": source.notes,
        })
    return out


@router.patch("/{source_id}")
def patch_source(
    source_id: int, body: SourcePatch, db: Session = Depends(get_db)
) -> dict:
    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "source not found")
    data = body.model_dump(exclude_unset=True)
    if data.get("enabled") is True:
        # Re-enabling clears the auto-disable state, otherwise the source would
        # trip the kill switch again on its next failure.
        source.consecutive_failures = 0
        source.disabled_reason = None
    for field, value in data.items():
        if value is not None:
            setattr(source, field, value)
    db.commit()
    return {"ok": True}


@router.post("/{source_id}/run")
def run_source_now(source_id: int, db: Session = Depends(get_db)) -> dict:
    """Synchronous single-source fetch. Useful when debugging a connector."""
    from app.services.ingest import run_source

    source = db.get(Source, source_id)
    if source is None:
        raise HTTPException(404, "source not found")
    run = run_source(db, source)
    db.commit()
    return {
        "ok": run.error is None,
        "items_seen": run.items_seen,
        "items_new": run.items_new,
        "error": run.error,
        "drift": run.schema_drift_note,
    }
