"""What gets surfaced, what gets drafted, and what happens when a human taps.

The weekly send cap is enforced here, in code, not merely suggested in config.
That is the whole product thesis: measured conversion collapses with volume, so
a tool that quietly lets the cap slip is a tool that has stopped working.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    Application, ApplicationEvent, Draft, DraftCheck, DraftVersion, JobCluster,
    JobPosting, Profile, ProfileFact, ProfileVariant, Score, SkipFeedback, Source,
)
from app.services import approval, drafting, priors
from app.services.configload import load_rubric

logger = logging.getLogger(__name__)

TEMPLATE_BY_TRACK = {
    "fte": "ats_cover",
    "outstaff": "cold_email",
    "freelance": "upwork_proposal",
    "equity": "cofounder_pitch",
}


def sends_this_week(db: Session) -> int:
    monday = datetime.now(timezone.utc) - timedelta(days=datetime.now(timezone.utc).weekday())
    monday = monday.replace(hour=0, minute=0, second=0, microsecond=0)
    return db.execute(
        select(func.count(Application.id)).where(Application.sent_at >= monday)
    ).scalar_one()


def remaining_this_week(db: Session, profile: Profile | None = None) -> int:
    cap = (profile.weekly_send_cap if profile else None) or settings.WEEKLY_SEND_CAP
    return max(0, cap - sends_this_week(db))


def digest_candidates(db: Session, limit: int | None = None) -> list[dict]:
    """Top-ranked clusters that have not been actioned or skipped."""
    limit = limit or settings.DAILY_DIGEST_SIZE
    skipped = select(SkipFeedback.cluster_id)
    actioned = select(Application.cluster_id)

    rows = db.execute(
        select(Score, JobCluster)
        .join(JobCluster, Score.cluster_id == JobCluster.id)
        .where(
            JobCluster.status.in_(("scored", "queued")),
            JobCluster.id.notin_(skipped),
            JobCluster.id.notin_(actioned),
        )
        .order_by(Score.priority.desc())
        .limit(limit)
    ).all()

    out = []
    for score, cluster in rows:
        posting = _canonical(db, cluster)
        if posting is None:
            continue
        source = db.get(Source, posting.source_id)
        out.append({
            "cluster_id": cluster.id,
            "score_id": score.id,
            "track": score.track,
            "priority": score.priority,
            "title": posting.title,
            "company": posting.company_name,
            "apply_url": posting.apply_url,
            "source_key": source.key if source else None,
            "source_prior": score.source_prior,
            "posted_at": posting.first_published_at,
            "comp_min": posting.comp_min,
            "comp_max": posting.comp_max,
            "comp_currency": posting.comp_currency,
            "remote_policy": posting.remote_policy,
            "fan_out": cluster.fan_out,
            "ghost_risk": score.ghost_risk,
            "llm_fit": score.llm_fit,
            "evidence": score.llm_evidence or [],
            "risks": (score.llm_verdict or {}).get("risks") or [],
            "explain": score.explain,
            "injection_suspected": score.injection_suspected,
        })
    return out


def _canonical(db: Session, cluster: JobCluster) -> JobPosting | None:
    if cluster.canonical_posting_id:
        posting = db.get(JobPosting, cluster.canonical_posting_id)
        if posting is not None:
            return posting
    return db.execute(
        select(JobPosting).where(JobPosting.cluster_id == cluster.id).limit(1)
    ).scalar_one_or_none()


def recent_approved_bodies(db: Session, limit: int = 20) -> list[str]:
    return db.execute(
        select(DraftVersion.body)
        .join(Draft, DraftVersion.draft_id == Draft.id)
        .where(Draft.status.in_(("approved", "sent")))
        .order_by(DraftVersion.created_at.desc())
        .limit(limit)
    ).scalars().all()


def create_draft(db: Session, cluster_id: int, template: str | None = None) -> Draft | None:
    """Generate a draft for one cluster. Returns None if the gateway is unavailable."""
    cluster = db.get(JobCluster, cluster_id)
    if cluster is None:
        return None
    posting = _canonical(db, cluster)
    if posting is None:
        return None

    profile = db.execute(select(Profile).limit(1)).scalar_one_or_none()
    if profile is None:
        return None

    score = db.execute(
        select(Score).where(Score.cluster_id == cluster.id)
        .order_by(Score.priority.desc()).limit(1)
    ).scalar_one_or_none()
    track = score.track if score else (cluster.tracks or ["fte"])[0]
    variant = db.execute(
        select(ProfileVariant).where(ProfileVariant.track == track)
    ).scalar_one_or_none()
    if variant is None:
        return None

    facts = db.execute(
        select(ProfileFact).where(
            ProfileFact.profile_id == profile.id, ProfileFact.active.is_(True)
        )
    ).scalars().all()

    template = template or TEMPLATE_BY_TRACK.get(track, "ats_cover")
    result = drafting.generate(
        template=template, posting=posting, profile=profile, variant=variant,
        facts=facts, judge_verdict=score.llm_verdict if score else None,
        previous_bodies=recent_approved_bodies(db),
    )
    if result is None:
        return None

    draft = Draft(
        cluster_id=cluster.id,
        score_id=score.id if score else None,
        template=template,
        track=track,
        status="draft",
    )
    db.add(draft)
    db.flush()

    version = DraftVersion(
        draft_id=draft.id,
        version=1,
        subject=result.subject,
        body=result.body,
        paragraphs=result.paragraphs,
        fact_keys=result.fact_keys,
        jd_hooks=result.jd_hooks,
        open_questions=result.open_questions,
        word_count=len(result.body.split()),
        model=result.model,
        passed_all_checks=result.passed,
        created_at=datetime.now(timezone.utc),
    )
    db.add(version)
    db.flush()

    for check in result.checks:
        db.add(DraftCheck(
            draft_version_id=version.id, check=check.name,
            passed=check.passed, detail=check.detail,
        ))

    cluster.status = "drafted"
    logger.info(
        "draft %s created for cluster %s (template=%s, checks_passed=%s)",
        draft.id, cluster.id, template, result.passed,
    )
    return draft


def latest_version(db: Session, draft: Draft) -> DraftVersion | None:
    return db.execute(
        select(DraftVersion)
        .where(DraftVersion.draft_id == draft.id)
        .order_by(DraftVersion.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def edit_draft(db: Session, draft: Draft, new_body: str) -> DraftVersion:
    """A human edit creates a new version, which invalidates any approval token."""
    current = latest_version(db, draft)
    version = DraftVersion(
        draft_id=draft.id,
        version=(current.version + 1) if current else 1,
        subject=current.subject if current else None,
        body=new_body,
        paragraphs=current.paragraphs if current else None,
        fact_keys=current.fact_keys if current else None,
        jd_hooks=current.jd_hooks if current else None,
        open_questions=current.open_questions if current else None,
        word_count=len(new_body.split()),
        model=None,
        passed_all_checks=True,  # the human owns every word they typed
        edited_by_human=True,
        created_at=datetime.now(timezone.utc),
    )
    db.add(version)
    draft.status = "draft"
    db.flush()
    return version


def approve(db: Session, draft: Draft) -> DraftVersion:
    """Mint an approval token bound to the exact current bytes."""
    version = latest_version(db, draft)
    if version is None:
        raise ValueError("draft has no version to approve")
    approval.issue(db, version)
    draft.status = "approved"
    draft.approved_at = datetime.now(timezone.utc)
    return version


def mark_sent(db: Session, draft: Draft, channel: str = "ats_form") -> Application:
    """The human has actually sent it. This is the only path that creates an
    Application, and it requires a live approval token.
    """
    version = latest_version(db, draft)
    if version is None:
        raise ValueError("draft has no version")
    approval.verify_and_consume(db, version)

    cap_left = remaining_this_week(db)
    if cap_left <= 0:
        logger.warning("weekly send cap reached; recording send anyway but flagging it")

    now = datetime.now(timezone.utc)
    gates_cfg = load_rubric().get("gates", {})
    follow_up_days = int(gates_cfg.get("follow_up_after_days", 4))
    dead_days = int(gates_cfg.get("presumed_dead_after_days", 10))

    application = Application(
        cluster_id=draft.cluster_id,
        draft_version_id=version.id,
        track=draft.track,
        channel=channel,
        stage="sent",
        stage_entered_at=now,
        sent_at=now,
        follow_up_due_at=now + timedelta(days=follow_up_days),
        presumed_dead_at=now + timedelta(days=dead_days),
    )
    db.add(application)
    db.flush()
    db.add(ApplicationEvent(
        application_id=application.id, from_stage="approved", to_stage="sent",
        at=now, actor="human",
    ))

    draft.status = "sent"
    draft.sent_at = now
    cluster = db.get(JobCluster, draft.cluster_id)
    if cluster is not None:
        cluster.status = "actioned"
        posting = _canonical(db, cluster)
        if posting is not None:
            priors.record_send(db, posting.source_id, draft.track)
    return application


def skip(db: Session, cluster_id: int, reason_code: str, note: str | None = None) -> None:
    """A one-tap skip with a reason. Three of the same reason with a common
    extractable pattern become a proposed hard gate in the Sunday retro.
    """
    db.add(SkipFeedback(
        cluster_id=cluster_id, reason_code=reason_code,
        at=datetime.now(timezone.utc), note=note,
    ))
    cluster = db.get(JobCluster, cluster_id)
    if cluster is not None:
        cluster.status = "archived"


def advance(
    db: Session, application: Application, to_stage: str,
    actor: str = "human", note: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    db.add(ApplicationEvent(
        application_id=application.id, from_stage=application.stage,
        to_stage=to_stage, at=now, actor=actor, note=note,
    ))
    application.stage = to_stage
    application.stage_entered_at = now

    if to_stage in ("acknowledged", "screen", "tech", "final", "offer", "accepted"):
        if not application.replied:
            application.replied = True
            application.replied_at = now
            if application.sent_at:
                sent = application.sent_at
                if sent.tzinfo is None:
                    sent = sent.replace(tzinfo=timezone.utc)
                application.hours_to_reply = (now - sent).total_seconds() / 3600.0
            cluster = db.get(JobCluster, application.cluster_id)
            posting = _canonical(db, cluster) if cluster else None
            if posting is not None:
                priors.record_reply(db, posting.source_id, application.track)

    if to_stage in ("rejected", "ghosted", "withdrawn", "knocked_out", "declined", "accepted"):
        application.closed_at = now
        application.outcome = to_stage
