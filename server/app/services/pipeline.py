"""Scoring pipeline: gated corpus -> fast score -> LLM judge -> priority.

The tiering exists because cost is dominated by the screening stage. Running the
big model over everything would be several times the price for a decision of the
form "does this even mention my stack".
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    EligibilityFlag, JobCluster, JobPosting, Profile, ProfileVariant, Score, Source,
)
from app.services import judge as judge_service
from app.services import priors, scoring, search_filters
from app.services.gates import _seniority_rank

logger = logging.getLogger(__name__)

_ATS_FAMILIES = {"greenhouse_board", "lever_postings", "ashby_board", "workable_search"}


def _canonical_posting(db: Session, cluster: JobCluster) -> JobPosting | None:
    if cluster.canonical_posting_id:
        posting = db.get(JobPosting, cluster.canonical_posting_id)
        if posting is not None:
            return posting
    return db.execute(
        select(JobPosting).where(JobPosting.cluster_id == cluster.id).limit(1)
    ).scalar_one_or_none()


def scorable_clusters(db: Session, limit: int) -> list[JobCluster]:
    """Clusters that survived the gates and have no fresh score yet."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=3)
    stale_score = select(Score.cluster_id).where(Score.scored_at > cutoff)
    return db.execute(
        select(JobCluster)
        .where(
            JobCluster.status.in_(("new", "scored")),
            JobCluster.id.notin_(stale_score),
        )
        .order_by(JobCluster.last_seen_at.desc())
        .limit(limit)
    ).scalars().all()


def score_batch(db: Session, limit: int = 600) -> dict[str, int]:
    profile = db.execute(select(Profile).limit(1)).scalar_one_or_none()
    if profile is None:
        logger.warning("no profile; scoring skipped")
        return {"scored": 0}

    variants = {
        v.track: v
        for v in db.execute(
            select(ProfileVariant).where(ProfileVariant.enabled.is_(True))
        ).scalars()
    }
    if not variants:
        logger.warning("no enabled profile variants; scoring skipped")
        return {"scored": 0}

    clusters = scorable_clusters(db, limit)
    if not clusters:
        return {"scored": 0}

    postings: dict[int, JobPosting] = {}
    for cluster in clusters:
        posting = _canonical_posting(db, cluster)
        if posting is not None:
            postings[cluster.id] = posting

    # IDF over the batch plus the profile text, so rare-but-relevant terms
    # (bicep, pgvector) score as informative rather than as noise.
    corpus = [
        f"{p.title or ''}\n{p.body_text or ''}" for p in postings.values()
    ] + [v.headline or "" for v in variants.values()]
    idf = scoring.corpus_idf(corpus)

    source_families = dict(db.execute(select(Source.id, Source.family)).all())
    prior_cache = {track: priors.posterior_map(db, track) for track in variants}
    filters = search_filters.load(db)
    # Excluded sources are skipped rather than disabled: the user may want the
    # source collecting for another track while ignoring it in this ranking.
    excluded_source_ids = {
        sid for sid, key in db.execute(select(Source.id, Source.key)).all()
        if key.lower() in filters.exclude_sources
    }

    scored = 0
    for cluster in clusters:
        posting = postings.get(cluster.id)
        if posting is None or posting.source_id in excluded_source_ids:
            continue
        track = _pick_track(cluster, variants)
        variant = variants.get(track)
        if variant is None:
            continue

        on_own_ats = source_families.get(posting.source_id) in _ATS_FAMILIES
        prior = prior_cache.get(track, {}).get(posting.source_id, 0.4)

        fast = scoring.compute_fast(
            posting=posting, cluster=cluster, profile=profile, variant=variant,
            idf=idf, source_prior=prior, on_own_ats=on_own_ats, filters=filters,
        )
        seniority_fit = _seniority_fit(posting)
        track_weight = float((profile.track_weights or {}).get(track, 0.25))
        priority, parts = scoring.compute_priority(
            fast=fast, llm_fit=None, seniority_fit=seniority_fit,
            track_weight=track_weight,
            liveness_ok=posting.liveness_ok is not False,
        )

        _upsert_score(db, cluster, track, fast, parts, priority, posting)
        cluster.status = "scored"
        scored += 1

    return {"scored": scored}


def _pick_track(cluster: JobCluster, variants: dict) -> str:
    for track in cluster.tracks or []:
        if track in variants:
            return track
    return next(iter(variants))


def _seniority_fit(posting: JobPosting) -> float:
    rank = _seniority_rank(posting)
    if rank is None:
        return 0.5
    # Senior/staff/lead/principal is the target band.
    return {4: 1.0, 5: 1.0, 6: 0.9, 3: 0.6, 7: 0.5, 8: 0.4}.get(rank, 0.2)


def _upsert_score(db, cluster, track, fast, parts, priority, posting) -> Score:
    row = db.execute(
        select(Score).where(Score.cluster_id == cluster.id, Score.track == track)
    ).scalar_one_or_none()
    if row is None:
        row = Score(cluster_id=cluster.id, track=track)
        db.add(row)
    row.scored_at = datetime.now(timezone.utc)
    row.semantic = fast.semantic
    row.skill_coverage = fast.skill_coverage
    row.title_fit = fast.title_fit
    row.freshness = fast.freshness
    row.comp_fit = fast.comp_fit
    row.source_prior = fast.source_prior
    row.ghost_risk = fast.ghost_risk
    row.crowding = fast.crowding
    row.s_fast = fast.s_fast
    row.fit = parts["fit"]
    row.trust = parts["trust"]
    row.reach = parts["reach"]
    row.value = parts["value"]
    row.priority = priority
    row.features = {
        "matched_skills": fast.detail.get("matched_skills", []),
        "boost_hits": fast.detail.get("boost_hits", []),
        "keyword_boost": round(fast.keyword_boost, 3),
    }
    row.explain = scoring.explain(fast, parts, priority)
    db.flush()
    return row


def judge_top(db: Session, top_k: int | None = None) -> dict[str, int]:
    """Deep LLM pass over the best unjudged clusters.

    Capped hard: the local gateway is shared and runs on free provider tiers, and
    the whole premise of this tool is that a small, well-chosen set beats a large
    one anyway.
    """
    top_k = top_k or settings.JUDGE_TOP_K
    profile = db.execute(select(Profile).limit(1)).scalar_one_or_none()
    if profile is None:
        return {"judged": 0}
    variants = {
        v.track: v
        for v in db.execute(
            select(ProfileVariant).where(ProfileVariant.enabled.is_(True))
        ).scalars()
    }

    rows = db.execute(
        select(Score)
        .where(Score.llm_fit.is_(None), Score.priority > 0)
        .order_by(Score.s_fast.desc())
        .limit(top_k)
    ).scalars().all()

    judged = 0
    for row in rows:
        cluster = db.get(JobCluster, row.cluster_id)
        if cluster is None:
            continue
        posting = _canonical_posting(db, cluster)
        variant = variants.get(row.track)
        if posting is None or variant is None:
            continue
        source = db.get(Source, posting.source_id)
        verdict = judge_service.judge(posting, profile, variant, source.key if source else "?")
        if verdict is None:
            break  # gateway is down or capped; stop rather than hammer it

        row.llm_model = verdict.get("_model")
        row.llm_fit = float(verdict.get("fit_0_10") or 0)
        row.llm_verdict = {k: v for k, v in verdict.items() if not k.startswith("_")}
        row.llm_evidence = verdict.get("evidence")
        row.injection_suspected = bool(verdict.get("injection_suspected"))

        fast = scoring.FastScore(
            semantic=row.semantic, skill_coverage=row.skill_coverage,
            title_fit=row.title_fit, freshness=row.freshness, comp_fit=row.comp_fit,
            source_prior=row.source_prior, ghost_risk=row.ghost_risk,
            crowding=row.crowding, s_fast=row.s_fast,
            keyword_boost=float((row.features or {}).get("keyword_boost") or 0.0),
            detail=row.features or {},
        )
        priority, parts = scoring.compute_priority(
            fast=fast,
            llm_fit=row.llm_fit,
            # Model-supplied floats are clamped inside compute_priority: they
            # arrive on whatever scale the free model felt like using.
            seniority_fit=scoring.clamp01(
                verdict.get("seniority_match"), default=_seniority_fit(posting)
            ),
            track_weight=float((profile.track_weights or {}).get(row.track, 0.25)),
            strategic_fit=scoring.clamp01(verdict.get("strategic_fit"), default=0.5),
            liveness_ok=posting.liveness_ok is not False,
            injection_suspected=row.injection_suspected,
        )
        row.fit, row.trust, row.reach, row.value = (
            parts["fit"], parts["trust"], parts["reach"], parts["value"]
        )
        row.priority = priority
        row.explain = scoring.explain(fast, parts, priority)

        if verdict.get("disqualifiers"):
            db.add(EligibilityFlag(
                job_posting_id=posting.id,
                code="STACK_EXCLUDE",
                severity="soft",
                matched_pattern="llm_disqualifier",
                matched_span="; ".join(str(d) for d in verdict["disqualifiers"])[:1000],
            ))
        if row.injection_suspected:
            logger.warning(
                "prompt injection suspected in posting %s (%s)", posting.id, posting.apply_url
            )
        judged += 1

    return {"judged": judged}


def pipeline_stats(db: Session) -> dict:
    total_postings = db.execute(select(func.count(JobPosting.id))).scalar_one()
    total_clusters = db.execute(select(func.count(JobCluster.id))).scalar_one()
    gated = db.execute(
        select(func.count(func.distinct(EligibilityFlag.job_posting_id)))
    ).scalar_one()
    scored = db.execute(select(func.count(Score.id))).scalar_one()
    judged = db.execute(
        select(func.count(Score.id)).where(Score.llm_fit.isnot(None))
    ).scalar_one()
    return {
        "postings": total_postings,
        "clusters": total_clusters,
        "gated_postings": gated,
        "scored": scored,
        "judged": judged,
    }
