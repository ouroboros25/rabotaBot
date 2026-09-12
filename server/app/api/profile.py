from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Profile, ProfileFact, ProfileVariant
from app.models.enums import TRACKS
from app.services.factguard import _number_tokens, _ledger_corpus

router = APIRouter()


class ProfileBody(BaseModel):
    display_name: str | None = None
    timezone: str | None = None
    countries_eligible: list[str] | None = None
    min_overlap_hours: int | None = None
    entity_status: str | None = None
    eor_ready: bool | None = None
    rate_floor_hourly: float | None = None
    rate_target_hourly: float | None = None
    comp_floor_annual: float | None = None
    comp_currency: str | None = None
    track_weights: dict | None = None
    weekly_send_cap: int | None = Field(None, ge=1, le=60)


class VariantBody(BaseModel):
    track: str
    headline: str = ""
    target_titles: list[str] = []
    must_have_skills: list[str] = []
    nice_to_have_skills: list[str] = []
    exclude_skills: list[str] = []
    enabled: bool = True


class FactBody(BaseModel):
    key: str
    type: str = "metric"
    payload: dict = {}
    verifiable_by: str | None = None
    evidence_url: str | None = None
    active: bool = True


@router.get("")
def get_profile(db: Session = Depends(get_db)) -> dict:
    profile = _get(db)
    facts = db.execute(
        select(ProfileFact).where(ProfileFact.profile_id == profile.id)
        .order_by(ProfileFact.key)
    ).scalars().all()
    ledger_numbers = _number_tokens(_ledger_corpus([f for f in facts if f.active]))
    return {
        "display_name": profile.display_name,
        "timezone": profile.timezone,
        "countries_eligible": profile.countries_eligible,
        "min_overlap_hours": profile.min_overlap_hours,
        "entity_status": profile.entity_status,
        "eor_ready": profile.eor_ready,
        "rate_floor_hourly": profile.rate_floor_hourly,
        "rate_target_hourly": profile.rate_target_hourly,
        "comp_floor_annual": profile.comp_floor_annual,
        "comp_currency": profile.comp_currency,
        "track_weights": profile.track_weights,
        "weekly_send_cap": profile.weekly_send_cap,
        "variants": [
            {
                "id": v.id, "track": v.track, "headline": v.headline,
                "target_titles": v.target_titles,
                "must_have_skills": v.must_have_skills,
                "nice_to_have_skills": v.nice_to_have_skills,
                "exclude_skills": v.exclude_skills,
                "enabled": v.enabled,
            }
            for v in profile.variants
        ],
        "facts": [
            {
                "id": f.id, "key": f.key, "type": f.type, "payload": f.payload,
                "verifiable_by": f.verifiable_by, "evidence_url": f.evidence_url,
                "active": f.active,
            }
            for f in facts
        ],
        # Surfaced because it silently caps draft quality: with no measurable
        # outcome in the ledger, every letter is forced to stay vague, since a
        # number the ledger cannot source is rejected as a fabrication.
        "ledger_health": {
            "fact_count": len([f for f in facts if f.active]),
            "has_metrics": bool(ledger_numbers),
            "metric_count": len([f for f in facts if f.active and f.type == "metric"]),
        },
    }


@router.put("")
def update_profile(body: ProfileBody, db: Session = Depends(get_db)) -> dict:
    profile = _get(db)
    for field, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(profile, field, value)
    db.commit()
    return {"ok": True}


@router.put("/variants/{variant_id}")
def update_variant(
    variant_id: int, body: VariantBody, db: Session = Depends(get_db)
) -> dict:
    variant = db.get(ProfileVariant, variant_id)
    if variant is None:
        raise HTTPException(404, "variant not found")
    if body.track not in TRACKS:
        raise HTTPException(400, f"track must be one of {list(TRACKS)}")
    for field, value in body.model_dump().items():
        setattr(variant, field, value)
    db.commit()
    return {"ok": True}


@router.post("/variants")
def create_variant(body: VariantBody, db: Session = Depends(get_db)) -> dict:
    profile = _get(db)
    if body.track not in TRACKS:
        raise HTTPException(400, f"track must be one of {list(TRACKS)}")
    variant = ProfileVariant(profile_id=profile.id, **body.model_dump())
    db.add(variant)
    db.commit()
    return {"ok": True, "id": variant.id}


@router.post("/facts")
def create_fact(body: FactBody, db: Session = Depends(get_db)) -> dict:
    profile = _get(db)
    existing = db.execute(
        select(ProfileFact).where(
            ProfileFact.profile_id == profile.id, ProfileFact.key == body.key
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(409, f"fact {body.key} already exists")
    fact = ProfileFact(profile_id=profile.id, **body.model_dump())
    db.add(fact)
    db.commit()
    return {"ok": True, "id": fact.id}


@router.put("/facts/{fact_id}")
def update_fact(fact_id: int, body: FactBody, db: Session = Depends(get_db)) -> dict:
    fact = db.get(ProfileFact, fact_id)
    if fact is None:
        raise HTTPException(404, "fact not found")
    for field, value in body.model_dump().items():
        setattr(fact, field, value)
    db.commit()
    return {"ok": True}


@router.delete("/facts/{fact_id}")
def delete_fact(fact_id: int, db: Session = Depends(get_db)) -> dict:
    fact = db.get(ProfileFact, fact_id)
    if fact is None:
        raise HTTPException(404, "fact not found")
    db.delete(fact)
    db.commit()
    return {"ok": True}


def _get(db: Session) -> Profile:
    profile = db.execute(select(Profile).limit(1)).scalar_one_or_none()
    if profile is None:
        raise HTTPException(503, "profile not seeded; run `python -m app.cli seed`")
    return profile
