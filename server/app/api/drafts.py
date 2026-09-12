from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Draft, DraftVersion, JobCluster, JobPosting, Profile
from app.services import docx_export, queue as queue_service

router = APIRouter()

VALID_CHANNELS = {"ats_form", "email", "upwork", "referral", "platform_alert"}


class EditBody(BaseModel):
    body: str


class SentBody(BaseModel):
    channel: str = "ats_form"


@router.get("")
def list_drafts(status: str | None = None, db: Session = Depends(get_db)) -> list[dict]:
    stmt = select(Draft).order_by(Draft.created_at.desc()).limit(100)
    if status:
        stmt = stmt.where(Draft.status == status)
    out = []
    for draft in db.execute(stmt).scalars():
        version = queue_service.latest_version(db, draft)
        cluster = db.get(JobCluster, draft.cluster_id)
        posting = queue_service._canonical(db, cluster) if cluster else None
        out.append({
            "id": draft.id,
            "status": draft.status,
            "template": draft.template,
            "track": draft.track,
            "cluster_id": draft.cluster_id,
            "title": posting.title if posting else None,
            "company": posting.company_name if posting else None,
            "word_count": version.word_count if version else 0,
            "checks_passed": version.passed_all_checks if version else False,
            "created_at": draft.created_at,
        })
    return out


@router.get("/{draft_id}")
def draft_detail(draft_id: int, db: Session = Depends(get_db)) -> dict:
    draft = _get(db, draft_id)
    version = queue_service.latest_version(db, draft)
    if version is None:
        raise HTTPException(404, "draft has no version")
    cluster = db.get(JobCluster, draft.cluster_id)
    posting = queue_service._canonical(db, cluster) if cluster else None
    return {
        "id": draft.id,
        "status": draft.status,
        "template": draft.template,
        "track": draft.track,
        "cluster_id": draft.cluster_id,
        "title": posting.title if posting else None,
        "company": posting.company_name if posting else None,
        "apply_url": posting.apply_url if posting else None,
        "form_questions": posting.form_questions if posting else None,
        "version": {
            "id": version.id,
            "version": version.version,
            "subject": version.subject,
            "body": version.body,
            "word_count": version.word_count,
            "model": version.model,
            "fact_keys": version.fact_keys,
            "jd_hooks": version.jd_hooks,
            "open_questions": version.open_questions,
            "edited_by_human": version.edited_by_human,
            "passed_all_checks": version.passed_all_checks,
        },
        # Failed checks are shown, never hidden: a draft that did not verify is
        # the human's call to make, with the violations in front of them.
        "checks": [
            {"check": c.check, "passed": c.passed, "detail": c.detail}
            for c in version.checks
        ],
    }


@router.patch("/{draft_id}")
def edit_draft(draft_id: int, body: EditBody, db: Session = Depends(get_db)) -> dict:
    draft = _get(db, draft_id)
    if not body.body.strip():
        raise HTTPException(400, "body cannot be empty")
    version = queue_service.edit_draft(db, draft, body.body.strip())
    db.commit()
    # Editing invalidates any approval token by design: the token is bound to
    # the exact bytes that were approved.
    return {"ok": True, "version": version.version, "requires_reapproval": True}


@router.post("/{draft_id}/approve")
def approve_draft(draft_id: int, db: Session = Depends(get_db)) -> dict:
    draft = _get(db, draft_id)
    version = queue_service.approve(db, draft)
    db.commit()
    return {
        "ok": True,
        "version": version.version,
        "note": "approved. the bot does not send: open the apply URL and submit it yourself",
    }


@router.post("/{draft_id}/sent")
def mark_sent(draft_id: int, body: SentBody, db: Session = Depends(get_db)) -> dict:
    draft = _get(db, draft_id)
    if body.channel not in VALID_CHANNELS:
        raise HTTPException(400, f"channel must be one of {sorted(VALID_CHANNELS)}")
    try:
        application = queue_service.mark_sent(db, draft, body.channel)
    except PermissionError as exc:
        # The approval token is the structural control, so its failures are 403,
        # not 400: this is an authorisation outcome, not a validation one.
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    db.commit()
    return {"ok": True, "application_id": application.id}


@router.post("/{draft_id}/discard")
def discard_draft(draft_id: int, db: Session = Depends(get_db)) -> dict:
    from datetime import datetime, timezone

    draft = _get(db, draft_id)
    draft.status = "discarded"
    draft.discarded_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}


@router.get("/{draft_id}/docx")
def download_docx(draft_id: int, db: Session = Depends(get_db)) -> Response:
    draft = _get(db, draft_id)
    version = queue_service.latest_version(db, draft)
    if version is None:
        raise HTTPException(404, "draft has no version")
    cluster = db.get(JobCluster, draft.cluster_id)
    posting = queue_service._canonical(db, cluster) if cluster else None
    profile = db.execute(select(Profile).limit(1)).scalar_one_or_none()

    content = docx_export.build_letter_docx(
        title=posting.title if posting else "Application",
        company=posting.company_name if posting else "",
        body=version.body,
        display_name=profile.display_name if profile else "",
    )
    filename = _safe_filename(
        f"{(posting.company_name or 'application')}_{(posting.title or '')}"
    )
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{filename}.docx"'},
    )


def _safe_filename(raw: str) -> str:
    import re

    cleaned = re.sub(r"[^\w\s-]", "", raw).strip().replace(" ", "_")
    return (cleaned or "application")[:80]


def _get(db: Session, draft_id: int) -> Draft:
    draft = db.get(Draft, draft_id)
    if draft is None:
        raise HTTPException(404, "draft not found")
    return draft
