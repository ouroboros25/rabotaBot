"""Beta-Binomial reply-rate priors per (source, track).

Seeded from measured per-board interview rates so the very first digest already
ranks sensibly. Source choice alone swings conversion by up to 50x, which is a
larger effect than any other single factor we model, so this is the cheapest
real signal in the system.

Seeds are expressed as a prior mean plus a strength. Strength 30 means "this is
worth about 30 observations": strong enough to matter on day one, weak enough
that twenty of your own outcomes start to move it.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Source, SourcePrior

PRIOR_STRENGTH = 30.0

# Measured application-to-interview rates by board (Huntr, 1.24M applications).
# Anything not listed falls back to the market baseline.
SEED_RATES: dict[str, float] = {
    "hn_whoishiring": 0.060,     # written by hiring engineers, recruiters banned in-thread
    "ashby_openai": 0.045,
    "ashby_ramp": 0.045,
    "ashby_linear": 0.045,
    "ashby_vanta": 0.045,
    "ashby_posthog": 0.045,
    "gh_stripe": 0.040,
    "gh_databricks": 0.040,
    "gh_gitlab": 0.040,
    "gh_cloudflare": 0.040,
    "gh_airbnb": 0.040,
    "gh_dropbox": 0.040,
    "lever_palantir": 0.040,
    "workable_remote_eng": 0.030,
    "workable_remote_dotnet": 0.030,
    "himalayas": 0.030,
    "wwr_programming": 0.028,
    "wwr_devops": 0.028,
    "fourdayweek": 0.025,
    "arbeitnow": 0.022,
    "jobicy": 0.018,
    "remoteok": 0.015,
}
BASELINE_RATE = 0.025


def seed_rate_for(source_key: str, family: str) -> float:
    if source_key in SEED_RATES:
        return SEED_RATES[source_key]
    # Company-owned ATS boards behave like the seeded ones above.
    if family in ("greenhouse_board", "lever_postings", "ashby_board"):
        return 0.040
    return BASELINE_RATE


def ensure_prior(db: Session, source: Source, track: str) -> SourcePrior:
    prior = db.execute(
        select(SourcePrior).where(
            SourcePrior.source_id == source.id, SourcePrior.track == track
        )
    ).scalar_one_or_none()
    if prior is not None:
        return prior
    rate = seed_rate_for(source.key, source.family)
    prior = SourcePrior(
        source_id=source.id,
        track=track,
        alpha=max(0.5, rate * PRIOR_STRENGTH),
        beta=max(0.5, (1.0 - rate) * PRIOR_STRENGTH),
        sends=0,
        replies=0,
        updated_at=datetime.now(timezone.utc),
    )
    db.add(prior)
    db.flush()
    return prior


def record_send(db: Session, source_id: int, track: str) -> None:
    prior = db.execute(
        select(SourcePrior).where(
            SourcePrior.source_id == source_id, SourcePrior.track == track
        )
    ).scalar_one_or_none()
    if prior is None:
        return
    prior.sends += 1
    prior.beta += 1.0
    prior.updated_at = datetime.now(timezone.utc)


def record_reply(db: Session, source_id: int, track: str) -> None:
    """A reply moves one observation from the failure bucket to the success one."""
    prior = db.execute(
        select(SourcePrior).where(
            SourcePrior.source_id == source_id, SourcePrior.track == track
        )
    ).scalar_one_or_none()
    if prior is None:
        return
    prior.replies += 1
    prior.alpha += 1.0
    prior.beta = max(0.5, prior.beta - 1.0)
    prior.updated_at = datetime.now(timezone.utc)


def posterior_map(db: Session, track: str) -> dict[int, float]:
    """source_id -> posterior mean, normalised to the best source in this track."""
    rows = db.execute(
        select(SourcePrior).where(SourcePrior.track == track)
    ).scalars().all()
    if not rows:
        return {}
    means = {r.source_id: r.alpha / (r.alpha + r.beta) for r in rows}
    top = max(means.values()) or 1.0
    return {k: v / top for k, v in means.items()}
