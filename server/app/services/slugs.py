"""ATS slug harvesting.

Access to company boards is free; knowing WHICH board a company uses is the
actual problem. Ashby cannot be searched, Workable 404s identically on a bad
slug and a disabled board, SmartRecruiters returns totalFound=0 for both.

The fix is parasitic: every aggregator hands us apply URLs that point straight at
boards.greenhouse.io/x, jobs.lever.co/y, jobs.ashbyhq.com/z. A regex over URLs we
already fetched builds the board registry for free, as a side effect of ingestion.
Expect a few thousand companies, not two hundred thousand. That is plenty: you
can only apply to a dozen or two a week.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CompanyAtsSlug, Source
from app.services.text import extract_ats_slug

logger = logging.getLogger(__name__)

# A harvested slug is promoted to a real source only after this many sightings,
# so one malformed URL never adds a dead board to the ingest schedule.
PROMOTE_AFTER_SIGHTINGS = 3

_FAMILY_BY_ATS = {
    "greenhouse": ("greenhouse_board", "board_token"),
    "lever": ("lever_postings", "site"),
    "ashby": ("ashby_board", "board"),
}


def harvest(db: Session, url: str | None, company_id: int | None, source_key: str) -> None:
    found = extract_ats_slug(url)
    if not found:
        return
    ats, slug = found
    row = db.execute(
        select(CompanyAtsSlug).where(
            CompanyAtsSlug.ats == ats, CompanyAtsSlug.slug == slug
        )
    ).scalar_one_or_none()
    if row is None:
        row = CompanyAtsSlug(
            ats=ats, slug=slug, company_id=company_id,
            discovered_from=source_key, jobs_last_seen=1,
        )
        db.add(row)
        db.flush()
        return
    row.jobs_last_seen = (row.jobs_last_seen or 0) + 1
    if row.company_id is None and company_id:
        row.company_id = company_id


def promote_ready(db: Session, limit: int = 20) -> list[str]:
    """Turn well-attested harvested slugs into real, enabled sources.

    Auto-discovered sources are flagged as such so a bad harvest can be undone
    without touching the registry the user actually wrote.
    """
    created: list[str] = []
    rows = db.execute(
        select(CompanyAtsSlug)
        .where(
            CompanyAtsSlug.promoted.is_(False),
            CompanyAtsSlug.jobs_last_seen >= PROMOTE_AFTER_SIGHTINGS,
            CompanyAtsSlug.ats.in_(list(_FAMILY_BY_ATS)),
        )
        .limit(limit)
    ).scalars().all()

    for row in rows:
        family, param_name = _FAMILY_BY_ATS[row.ats]
        key = f"auto_{row.ats}_{row.slug}"[:80]

        # A board that is already configured by hand must not be promoted again:
        # two sources pointing at the same endpoint double the outbound traffic
        # to a publisher that is doing us a favour by exposing it at all.
        if _already_covered(db, family, param_name, row.slug):
            row.promoted = True
            row.verified_at = datetime.now(timezone.utc)
            continue

        exists = db.execute(select(Source).where(Source.key == key)).scalar_one_or_none()
        if exists is None:
            db.add(Source(
                key=key,
                family=family,
                legal_tier=0,
                tracks=["fte", "outstaff"],
                params={param_name: row.slug},
                field_map={},
                company_hint={},
                cadence_minutes=720,
                rate_limit_rps=0.5,
                enabled=True,
                auto_discovered=True,
                notes=f"auto-harvested from {row.discovered_from}",
            ))
            created.append(key)
        row.promoted = True
        row.verified_at = datetime.now(timezone.utc)

    if created:
        logger.info("promoted %d harvested ATS boards: %s", len(created), created)
    return created


def _already_covered(db: Session, family: str, param_name: str, slug: str) -> bool:
    """True when some existing source already polls this exact board."""
    for source in db.execute(
        select(Source).where(Source.family == family)
    ).scalars():
        if str((source.params or {}).get(param_name, "")).lower() == slug.lower():
            return True
    return False
