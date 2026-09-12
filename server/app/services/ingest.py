"""Ingest: connector output -> normalised postings -> clusters -> gates.

Ordering matters and is deliberate:
  fetch -> persist raw -> normalise -> dedup into clusters -> hard gates
Dedup before gating and scoring means the same role on six boards costs one
evaluation, not six, and the clustering itself produces the crowding signal.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors import build as build_connector
from app.models import (
    Company, EligibilityFlag, JobPosting, Profile, ProfileVariant, RawDocument,
    Source, SourceRun,
)
from app.services import dedup, gates, geo, slugs
from app.services.text import canonical_url, company_key, sha256, title_key

logger = logging.getLogger(__name__)

RAW_TTL_DAYS = 30
MAX_FAILURES_BEFORE_DISABLE = 8


def due_sources(db: Session) -> list[Source]:
    now = datetime.now(timezone.utc)
    out = []
    for source in db.execute(select(Source).where(Source.enabled.is_(True))).scalars():
        if source.last_run_at is None:
            out.append(source)
            continue
        last = source.last_run_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last + timedelta(minutes=source.cadence_minutes) <= now:
            out.append(source)
    return out


def run_source(db: Session, source: Source) -> SourceRun:
    now = datetime.now(timezone.utc)
    run = SourceRun(source_id=source.id, started_at=now)
    db.add(run)
    db.flush()

    try:
        connector = build_connector(source)
        result = connector.fetch()
    except Exception as exc:  # noqa: BLE001 - one bad source must not stop the sweep
        source.consecutive_failures += 1
        source.last_run_at = now
        run.finished_at = datetime.now(timezone.utc)
        run.error = f"{type(exc).__name__}: {exc}"[:2000]
        logger.warning("source %s failed: %s", source.key, run.error)
        if source.consecutive_failures >= MAX_FAILURES_BEFORE_DISABLE:
            # Kill switch. A source that has failed this many times in a row is
            # either gone or is telling us to stop.
            source.enabled = False
            source.disabled_reason = f"auto-disabled after {source.consecutive_failures} failures"
            logger.error("source %s auto-disabled", source.key)
        return run

    run.items_seen = len(result.items)
    run.bytes_fetched = result.bytes_fetched
    run.http_status = result.http_status
    run.schema_drift_note = result.drift_note
    if result.drift_note:
        logger.warning("source %s schema drift: %s", source.key, result.drift_note)

    new_count = changed_count = 0
    for item in result.items:
        created, changed = _upsert_posting(db, source, item)
        new_count += int(created)
        changed_count += int(changed)

    source.consecutive_failures = 0
    source.last_run_at = now
    source.last_ok_at = datetime.now(timezone.utc)
    run.items_new = new_count
    run.items_changed = changed_count
    run.finished_at = datetime.now(timezone.utc)
    logger.info(
        "source %s: %d seen, %d new, %d changed",
        source.key, run.items_seen, new_count, changed_count,
    )
    return run


def _get_or_create_company(db: Session, name: str | None) -> Company | None:
    if not name:
        return None
    ckey = company_key(name)[:255]
    if not ckey:
        return None
    company = db.execute(
        select(Company).where(Company.company_key == ckey)
    ).scalar_one_or_none()
    if company is None:
        company = Company(name=name.strip()[:255], company_key=ckey)
        db.add(company)
        db.flush()
    return company


def _upsert_posting(db: Session, source: Source, item) -> tuple[bool, bool]:
    """Returns (created, changed)."""
    now = datetime.now(timezone.utc)

    existing = db.execute(
        select(JobPosting).where(
            JobPosting.source_id == source.id,
            JobPosting.external_id == str(item.external_id),
        )
    ).scalar_one_or_none()

    desc_hash = sha256(item.body) if item.body else None
    canon = canonical_url(item.apply_url or item.url)
    company = _get_or_create_company(db, item.company_name)

    # Sources express eligibility as country names, ISO codes or free text.
    # Normalise once here so the gates never compare "United States" to "US".
    countries, worldwide = geo.normalize_list(item.countries_allowed)
    remote_policy = "global" if worldwide else item.remote_policy

    if existing is not None:
        changed = existing.desc_hash != desc_hash
        existing.last_seen_at = now
        if changed:
            existing.body_text = item.body
            existing.desc_hash = desc_hash
            existing.updated_at_source = item.updated_at or now
        return False, changed

    posting = JobPosting(
        source_id=source.id,
        external_id=str(item.external_id),
        company_id=company.id if company else None,
        title=(item.title or "")[:400],
        title_key=title_key(item.title)[:400],
        company_name=item.company_name,
        body_text=item.body,
        desc_hash=desc_hash,
        apply_url=item.apply_url or item.url,
        apply_url_canonical=canon,
        apply_url_hash=sha256(canon) if canon else None,
        employment_type=item.employment_type,
        seniority=item.seniority,
        location_raw=item.location_raw,
        countries_allowed=countries,
        timezones_allowed=item.timezones_allowed,
        remote_policy=remote_policy,
        comp_min=item.comp_min,
        comp_max=item.comp_max,
        comp_currency=item.comp_currency,
        comp_period=item.comp_period,
        first_published_at=item.posted_at,
        updated_at_source=item.updated_at,
        expires_at=item.expires_at,
        tags=item.tags,
        form_questions=item.form_questions,
        raw_extra=item.raw_extra,
        ingested_at=now,
        last_seen_at=now,
    )
    db.add(posting)
    db.flush()

    # Raw payload retention is per-posting rather than per-response: it keeps the
    # replay unit the same as the parse unit.
    db.add(RawDocument(
        source_id=source.id,
        external_id=str(item.external_id),
        fetched_at=now,
        content_hash=desc_hash or sha256(str(item.external_id)),
        body=(item.body or "").encode("utf-8", "replace")[:1_000_000],
        expires_at=now + timedelta(days=RAW_TTL_DAYS),
    ))

    cluster = dedup.assign_cluster(db, posting)
    dedup.recurrence_bump(db, cluster, desc_hash)
    for track in source.tracks or []:
        if track not in (cluster.tracks or []):
            cluster.tracks = list(cluster.tracks or []) + [track]

    slugs.harvest(db, posting.apply_url, posting.company_id, source.key)
    return True, False


def apply_gates(db: Session, limit: int = 2000) -> dict[str, int]:
    """Run stage-0 gates over postings that have not been gated yet."""
    profile = db.execute(select(Profile).limit(1)).scalar_one_or_none()
    if profile is None:
        logger.warning("no profile configured; skipping gates")
        return {}

    variants = {
        v.track: v
        for v in db.execute(select(ProfileVariant).where(ProfileVariant.enabled.is_(True)))
        .scalars()
    }

    postings = db.execute(
        select(JobPosting)
        .outerjoin(EligibilityFlag, EligibilityFlag.job_posting_id == JobPosting.id)
        .where(EligibilityFlag.id.is_(None))
        .order_by(JobPosting.id.desc())
        .limit(limit)
    ).scalars().all()

    counts: dict[str, int] = {}
    for posting in postings:
        cluster = posting.cluster
        if cluster is None:
            continue
        track = (cluster.tracks or ["fte"])[0]
        variant = variants.get(track)

        if posting.company_id:
            company = db.get(Company, posting.company_id)
            if company is not None and company.blocklisted:
                db.add(EligibilityFlag(
                    job_posting_id=posting.id, code="BLOCKLIST",
                    matched_pattern="company.blocklisted", matched_span=company.name,
                ))
                counts["BLOCKLIST"] = counts.get("BLOCKLIST", 0) + 1
                cluster.status = "gated"
                continue

        hits = gates.evaluate(posting, profile, variant)
        for hit in hits:
            db.add(EligibilityFlag(
                job_posting_id=posting.id, code=hit.code,
                matched_pattern=hit.pattern, matched_span=(hit.span or "")[:1000],
            ))
            counts[hit.code] = counts.get(hit.code, 0) + 1
        if hits:
            # The cluster is only killed if EVERY posting in it is gated: one board
            # may carry a truncated description that trips a false positive.
            siblings = db.execute(
                select(JobPosting.id).where(JobPosting.cluster_id == cluster.id)
            ).scalars().all()
            gated = db.execute(
                select(EligibilityFlag.job_posting_id)
                .where(EligibilityFlag.job_posting_id.in_(siblings))
                .distinct()
            ).scalars().all()
            if set(siblings) <= set(gated) | {posting.id}:
                cluster.status = "gated"

    return counts


def purge_expired_raw(db: Session) -> int:
    now = datetime.now(timezone.utc)
    rows = db.execute(
        select(RawDocument.id).where(RawDocument.expires_at < now).limit(5000)
    ).scalars().all()
    for rid in rows:
        obj = db.get(RawDocument, rid)
        if obj is not None:
            db.delete(obj)
    return len(rows)
