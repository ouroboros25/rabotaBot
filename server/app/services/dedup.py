"""Cross-board deduplication.

Runs before enrichment and scoring for two reasons. The cheap one: the same role
sits on the company's own board plus four aggregators, so scoring every copy
burns the LLM budget five times over. The valuable one: clustering is what
produces ``fan_out``, and fan-out is evidence of crowding, not of quality.

Cascade, cheapest level first:
  L0  (source_id, external_id)          idempotency, catches re-polls
  L1  canonical apply URL hash          ~40% of real duplicates
  L2  company_key + title_key + country
  L3  fuzzy title within an L2 candidate group  (rapidfuzz, no embeddings needed)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company, JobCluster, JobPosting, Source
from app.services.text import company_key, sha256, title_key

logger = logging.getLogger(__name__)

TITLE_FUZZ_THRESHOLD = 90


def _identity_hash(ckey: str, tkey: str, country: str | None) -> str:
    return sha256(f"{ckey}|{tkey}|{(country or '').lower()}")


def assign_cluster(db: Session, posting: JobPosting) -> JobCluster:
    """Attach a posting to its cluster, creating one if needed."""
    now = datetime.now(timezone.utc)
    # Both columns are bounded; an HN ad can carry a whole paragraph as its
    # first line, so truncate rather than let the insert blow up mid-sweep.
    ckey = company_key(posting.company_name)[:255]
    tkey = (posting.title_key or title_key(posting.title))[:255]
    country = (posting.countries_allowed or [None])[0] if posting.countries_allowed else None

    cluster: JobCluster | None = None

    # L1: another posting already resolved to the same canonical apply URL.
    if posting.apply_url_hash:
        twin = db.execute(
            select(JobPosting)
            .where(
                JobPosting.apply_url_hash == posting.apply_url_hash,
                JobPosting.id != posting.id,
                JobPosting.cluster_id.isnot(None),
            )
            .limit(1)
        ).scalar_one_or_none()
        if twin is not None:
            cluster = db.get(JobCluster, twin.cluster_id)

    # L2: normalised identity.
    ident = _identity_hash(ckey, tkey, country)
    if cluster is None:
        cluster = db.execute(
            select(JobCluster).where(JobCluster.identity_hash == ident)
        ).scalar_one_or_none()

    # L3: fuzzy title inside the same company, for titles L2 did not collapse.
    if cluster is None and ckey:
        candidates = db.execute(
            select(JobCluster)
            .join(Company, JobCluster.company_id == Company.id, isouter=True)
            .where(Company.company_key == ckey)
            .limit(50)
        ).scalars().all()
        for cand in candidates:
            if fuzz.token_set_ratio(tkey, cand.title_key or "") >= TITLE_FUZZ_THRESHOLD:
                cluster = cand
                break

    if cluster is None:
        cluster = JobCluster(
            identity_hash=ident,
            company_id=posting.company_id,
            title_key=tkey,
            country_code=country,
            fan_out=0,
            first_seen_at=now,
            last_seen_at=now,
            tracks=[],
            status="new",
        )
        db.add(cluster)
        db.flush()

    _attach(db, cluster, posting, now)
    return cluster


def _attach(db: Session, cluster: JobCluster, posting: JobPosting, now: datetime) -> None:
    was_new = posting.cluster_id != cluster.id
    posting.cluster_id = cluster.id
    cluster.last_seen_at = now
    if cluster.company_id is None and posting.company_id:
        cluster.company_id = posting.company_id

    if was_new:
        # fan_out counts distinct SOURCES, not postings: two pages of the same
        # feed must not look like two independent scanners finding the job.
        distinct_sources = db.execute(
            select(JobPosting.source_id)
            .where(JobPosting.cluster_id == cluster.id)
            .distinct()
        ).scalars().all()
        cluster.fan_out = max(1, len(set(distinct_sources) | {posting.source_id}))

    # Canonical target is always the company's own ATS URL when we have one:
    # aggregator redirects lose attribution, break forms and hide first_published.
    canonical = (
        db.get(JobPosting, cluster.canonical_posting_id)
        if cluster.canonical_posting_id
        else None
    )
    if canonical is None or _canonical_rank(db, posting) > _canonical_rank(db, canonical):
        cluster.canonical_posting_id = posting.id


# Sources whose apply URL is the employer's own board rather than a redirect.
_ATS_FAMILIES = {"greenhouse_board", "lever_postings", "ashby_board", "workable_search"}


def _canonical_rank(db: Session, posting: JobPosting) -> int:
    """Higher wins. Own-ATS beats aggregator; having a body beats not having one."""
    family = db.execute(
        select(Source.family).where(Source.id == posting.source_id)
    ).scalar_one_or_none()
    rank = 10 if family in _ATS_FAMILIES else 0
    if posting.body_text:
        rank += 3
    if posting.first_published_at:
        rank += 2
    if posting.comp_max:
        rank += 1
    return rank


def recurrence_bump(db: Session, cluster: JobCluster, desc_hash: str | None) -> None:
    """Count how many distinct months this exact description has appeared in.

    A body that reappears months later for the same company is the strongest
    cheap ghost-job signal we have.
    """
    if not desc_hash:
        return
    months = db.execute(
        select(JobPosting.first_published_at)
        .where(JobPosting.desc_hash == desc_hash)
    ).scalars().all()
    distinct = {d.strftime("%Y-%m") for d in months if d}
    cluster.recurrence_months = max(cluster.recurrence_months, len(distinct))
