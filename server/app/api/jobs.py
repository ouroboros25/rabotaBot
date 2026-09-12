from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (
    Company, EligibilityFlag, JobCluster, JobPosting, Score, Source,
)
from app.services import queue as queue_service

router = APIRouter()


class SkipBody(BaseModel):
    reason_code: str = Field(
        ...,
        description="wrong_stack | geo | comp | too_junior | too_senior | company | gut",
    )
    note: str | None = None


@router.get("")
def list_jobs(
    limit: int = Query(50, le=200),
    offset: int = 0,
    track: str | None = None,
    min_priority: float = 0.0,
    q: str | None = Query(None, description="substring of the title or company"),
    source: str | None = Query(None, description="source key"),
    max_age_days: int | None = Query(None, ge=0, le=365),
    has_comp: bool | None = Query(None, description="only postings that state pay"),
    remote_only: bool | None = Query(None),
    db: Session = Depends(get_db),
) -> dict:
    """The queue, filtered.

    Filtering happens in SQL rather than in the page so paging stays correct:
    filtering after LIMIT would silently return fewer rows than asked for and
    make "next page" meaningless.
    """
    # The canonical posting carries the fields we filter on, so join it directly
    # instead of resolving per row afterwards.
    stmt = (
        select(Score, JobCluster, JobPosting)
        .join(JobCluster, Score.cluster_id == JobCluster.id)
        .join(JobPosting, JobPosting.id == JobCluster.canonical_posting_id)
        .where(
            JobCluster.status.in_(("scored", "queued", "drafted")),
            Score.priority >= min_priority,
        )
        .order_by(Score.priority.desc())
    )
    if track:
        stmt = stmt.where(Score.track == track)
    if q:
        needle = f"%{q.strip().lower()}%"
        stmt = stmt.where(or_(
            func.lower(JobPosting.title).like(needle),
            func.lower(func.coalesce(JobPosting.company_name, "")).like(needle),
        ))
    if source:
        stmt = stmt.join(Source, Source.id == JobPosting.source_id).where(
            Source.key == source
        )
    if max_age_days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        stmt = stmt.where(JobPosting.first_published_at >= cutoff)
    if has_comp:
        stmt = stmt.where(JobPosting.comp_max.isnot(None))
    if remote_only:
        stmt = stmt.where(JobPosting.remote_policy.in_(("global", "unknown")))

    total = db.execute(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    ).scalar_one()
    rows = db.execute(stmt.offset(offset).limit(limit)).all()
    items = []
    for score, cluster, posting in rows:
        source_row = db.get(Source, posting.source_id)
        items.append({
            "cluster_id": cluster.id,
            "title": posting.title,
            "company": posting.company_name,
            "track": score.track,
            "priority": round(score.priority, 1),
            "llm_fit": score.llm_fit,
            "source": source_row.key if source_row else None,
            "apply_url": posting.apply_url,
            "posted_at": posting.first_published_at,
            "comp_min": posting.comp_min,
            "comp_max": posting.comp_max,
            "comp_currency": posting.comp_currency,
            "comp_period": posting.comp_period,
            "remote_policy": posting.remote_policy,
            "countries_allowed": posting.countries_allowed,
            "fan_out": cluster.fan_out,
            "ghost_risk": round(score.ghost_risk, 2),
            "injection_suspected": score.injection_suspected,
            "status": cluster.status,
            "keyword_boost": (score.features or {}).get("keyword_boost", 0),
            "boost_hits": (score.features or {}).get("boost_hits", []),
        })
    return {"items": items, "count": len(items), "total": total, "offset": offset}


@router.post("/rescan")
def rescan(db: Session = Depends(get_db)) -> dict:
    """Re-apply every hard gate to the whole collected corpus.

    Needed because gates are evaluated once per posting behind a watermark, so a
    rule added today would otherwise only affect postings collected from now on.
    Synchronous on purpose: it takes seconds, and a background job would make the
    UI lie about when the new rules are actually in force.
    """
    from app.services import gates
    from app.services.configload import reset_caches
    from app.services.ingest import apply_gates

    # Re-read rubric.yaml first: otherwise a long-running worker re-applies the
    # rules it compiled at startup and the rescan looks like it did nothing.
    reset_caches()
    gates.reset_rule_cache()

    totals: dict[str, int] = {}
    batch = apply_gates(db, rescan=True)
    db.commit()
    while True:
        for code, count in batch.items():
            totals[code] = totals.get(code, 0) + count
        if not batch.get("_processed"):
            break
        batch = apply_gates(db)
        db.commit()

    processed = totals.pop("_processed", 0)
    return {"ok": True, "processed": processed, "gate_counts": totals}


@router.get("/facets")
def facets(db: Session = Depends(get_db)) -> dict:
    """What is actually in the queue right now, for populating filter controls."""
    rows = db.execute(
        select(Source.key, func.count(JobCluster.id))
        .join(JobPosting, JobPosting.source_id == Source.id)
        .join(JobCluster, JobCluster.canonical_posting_id == JobPosting.id)
        .join(Score, Score.cluster_id == JobCluster.id)
        .where(JobCluster.status.in_(("scored", "queued", "drafted")))
        .group_by(Source.key)
        .order_by(func.count(JobCluster.id).desc())
    ).all()
    tracks = db.execute(
        select(Score.track, func.count(Score.id))
        .join(JobCluster, Score.cluster_id == JobCluster.id)
        .where(JobCluster.status.in_(("scored", "queued", "drafted")))
        .group_by(Score.track)
    ).all()
    return {
        "sources": [{"key": k, "count": c} for k, c in rows],
        "tracks": [{"track": t, "count": c} for t, c in tracks],
    }


@router.get("/{cluster_id}")
def job_detail(cluster_id: int, db: Session = Depends(get_db)) -> dict:
    cluster = db.get(JobCluster, cluster_id)
    if cluster is None:
        raise HTTPException(404, "cluster not found")
    posting = _canonical(db, cluster)
    if posting is None:
        raise HTTPException(404, "no posting in cluster")

    score = db.execute(
        select(Score).where(Score.cluster_id == cluster_id)
        .order_by(Score.priority.desc()).limit(1)
    ).scalar_one_or_none()

    siblings = db.execute(
        select(JobPosting, Source)
        .join(Source, JobPosting.source_id == Source.id)
        .where(JobPosting.cluster_id == cluster_id)
    ).all()

    flags = db.execute(
        select(EligibilityFlag).where(
            EligibilityFlag.job_posting_id.in_([p.id for p, _ in siblings])
        )
    ).scalars().all()

    company = db.get(Company, cluster.company_id) if cluster.company_id else None

    return {
        "cluster_id": cluster.id,
        "status": cluster.status,
        "fan_out": cluster.fan_out,
        "recurrence_months": cluster.recurrence_months,
        "tracks": cluster.tracks,
        "company": {
            "id": company.id, "name": company.name, "blocklisted": company.blocklisted,
        } if company else None,
        "posting": {
            "id": posting.id,
            "title": posting.title,
            "company_name": posting.company_name,
            # Returned as plain text. The SPA must render it as text, never as
            # HTML: a job description is attacker-controllable content.
            "body_text": posting.body_text,
            "apply_url": posting.apply_url,
            "location_raw": posting.location_raw,
            "countries_allowed": posting.countries_allowed,
            "timezones_allowed": posting.timezones_allowed,
            "remote_policy": posting.remote_policy,
            "employment_type": posting.employment_type,
            "seniority": posting.seniority,
            "comp_min": posting.comp_min,
            "comp_max": posting.comp_max,
            "comp_currency": posting.comp_currency,
            "comp_period": posting.comp_period,
            "first_published_at": posting.first_published_at,
            "updated_at_source": posting.updated_at_source,
            "form_questions": posting.form_questions,
        },
        "score": {
            "track": score.track,
            "priority": round(score.priority, 1),
            "s_fast": round(score.s_fast, 1),
            "fit": score.fit, "trust": score.trust,
            "reach": score.reach, "value": score.value,
            "semantic": score.semantic, "skill_coverage": score.skill_coverage,
            "title_fit": score.title_fit, "freshness": score.freshness,
            "comp_fit": score.comp_fit, "source_prior": score.source_prior,
            "ghost_risk": score.ghost_risk, "crowding": score.crowding,
            "llm_fit": score.llm_fit,
            "llm_model": score.llm_model,
            "evidence": score.llm_evidence,
            "verdict": score.llm_verdict,
            "injection_suspected": score.injection_suspected,
            "explain": score.explain,
            "matched_skills": (score.features or {}).get("matched_skills", []),
        } if score else None,
        "sources": [
            {"source": src.key, "url": p.apply_url, "posted_at": p.first_published_at}
            for p, src in siblings
        ],
        "gates": [
            {"code": f.code, "pattern": f.matched_pattern, "span": f.matched_span}
            for f in flags
        ],
    }


@router.post("/{cluster_id}/skip")
def skip_job(cluster_id: int, body: SkipBody, db: Session = Depends(get_db)) -> dict:
    if db.get(JobCluster, cluster_id) is None:
        raise HTTPException(404, "cluster not found")
    queue_service.skip(db, cluster_id, body.reason_code, body.note)
    db.commit()
    return {"ok": True}


@router.post("/{cluster_id}/draft")
def draft_job(
    cluster_id: int, template: str | None = None, db: Session = Depends(get_db)
) -> dict:
    if db.get(JobCluster, cluster_id) is None:
        raise HTTPException(404, "cluster not found")
    draft = queue_service.create_draft(db, cluster_id, template)
    if draft is None:
        raise HTTPException(
            503, "draft generation failed: the AI gateway is unavailable or capped"
        )
    db.commit()
    return {"ok": True, "draft_id": draft.id}


def _canonical(db: Session, cluster: JobCluster) -> JobPosting | None:
    if cluster.canonical_posting_id:
        posting = db.get(JobPosting, cluster.canonical_posting_id)
        if posting is not None:
            return posting
    return db.execute(
        select(JobPosting).where(JobPosting.cluster_id == cluster.id).limit(1)
    ).scalar_one_or_none()
