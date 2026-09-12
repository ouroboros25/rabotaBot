from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
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
    db: Session = Depends(get_db),
) -> dict:
    stmt = (
        select(Score, JobCluster)
        .join(JobCluster, Score.cluster_id == JobCluster.id)
        .where(
            JobCluster.status.in_(("scored", "queued", "drafted")),
            Score.priority >= min_priority,
        )
        .order_by(Score.priority.desc())
    )
    if track:
        stmt = stmt.where(Score.track == track)

    rows = db.execute(stmt.offset(offset).limit(limit)).all()
    items = []
    for score, cluster in rows:
        posting = _canonical(db, cluster)
        if posting is None:
            continue
        source = db.get(Source, posting.source_id)
        items.append({
            "cluster_id": cluster.id,
            "title": posting.title,
            "company": posting.company_name,
            "track": score.track,
            "priority": round(score.priority, 1),
            "llm_fit": score.llm_fit,
            "source": source.key if source else None,
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
        })
    return {"items": items, "count": len(items), "offset": offset}


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
