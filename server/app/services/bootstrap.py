"""Seed the database from config/*.yaml.

Idempotent. Runs on every prod start, so a new source added to sources.yaml
appears after a redeploy without a manual step. It never overwrites a source the
user has edited in the UI: config is the seed, the DB is the live state.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Profile, ProfileFact, ProfileVariant, Source
from app.services import priors
from app.services.configload import load_profile_seed, load_sources

logger = logging.getLogger(__name__)


def seed_sources(db: Session) -> dict[str, int]:
    created = updated = 0
    for row in load_sources():
        existing = db.execute(
            select(Source).where(Source.key == row["key"])
        ).scalar_one_or_none()
        if existing is None:
            db.add(Source(
                key=row["key"],
                family=row["family"],
                legal_tier=row["legal_tier"],
                tracks=row["tracks"],
                params=row["params"],
                field_map=row["field_map"],
                company_hint=row.get("company") or {},
                cadence_minutes=int(row.get("cadence_minutes", 180)),
                rate_limit_rps=float(row.get("rate_limit_rps", 1.0)),
                enabled=bool(row.get("enabled", True)),
            ))
            created += 1
        else:
            # Only refresh the mechanical parts. enabled/cadence stay whatever the
            # user last chose in the UI, otherwise a redeploy would silently
            # re-enable a source they turned off on purpose.
            existing.family = row["family"]
            existing.params = row["params"]
            existing.field_map = row["field_map"]
            existing.company_hint = row.get("company") or {}
            existing.legal_tier = row["legal_tier"]
            existing.tracks = row["tracks"]
            updated += 1
    db.flush()

    for source in db.execute(select(Source)).scalars():
        for track in source.tracks or ["fte"]:
            priors.ensure_prior(db, source, track)

    logger.info("sources seeded: %d created, %d refreshed", created, updated)
    return {"created": created, "updated": updated}


def seed_profile(db: Session) -> bool:
    """Create the profile once. After that the UI owns it."""
    existing = db.execute(select(Profile).limit(1)).scalar_one_or_none()
    if existing is not None:
        return False

    cfg = load_profile_seed()
    profile = Profile(
        display_name=cfg.get("display_name", ""),
        timezone=cfg.get("timezone", "Europe/Warsaw"),
        countries_eligible=cfg.get("countries_eligible", []),
        min_overlap_hours=int(cfg.get("min_overlap_hours", 4)),
        entity_status=cfg.get("entity_status", "individual"),
        eor_ready=bool(cfg.get("eor_ready", False)),
        rate_floor_hourly=cfg.get("rate_floor_hourly"),
        rate_target_hourly=cfg.get("rate_target_hourly"),
        comp_floor_annual=cfg.get("comp_floor_annual"),
        comp_currency=cfg.get("comp_currency", "USD"),
        track_weights=cfg.get("track_weights", {}),
        weekly_send_cap=int(cfg.get("weekly_send_cap", 18)),
    )
    db.add(profile)
    db.flush()

    for variant in cfg.get("variants", []):
        db.add(ProfileVariant(
            profile_id=profile.id,
            track=variant["track"],
            headline=variant.get("headline", ""),
            target_titles=variant.get("target_titles", []),
            must_have_skills=variant.get("must_have_skills", []),
            nice_to_have_skills=variant.get("nice_to_have_skills", []),
            exclude_skills=variant.get("exclude_skills", []),
        ))

    for fact in cfg.get("facts", []):
        db.add(ProfileFact(
            profile_id=profile.id,
            key=fact["key"],
            type=fact.get("type", "skill"),
            payload=fact.get("payload", {}),
            verifiable_by=fact.get("verifiable_by"),
            evidence_url=fact.get("evidence_url"),
        ))

    logger.info("profile seeded from config/profile.example.yaml")
    return True


def run(db: Session) -> dict:
    profile_created = seed_profile(db)
    source_stats = seed_sources(db)
    return {"profile_created": profile_created, **source_stats}
