"""Stage 1 (deterministic) and stage 3 (final ranking).

Nothing here calls an LLM. That is the point: the expensive model only ever sees
the small slice that survived free arithmetic, and every number below can be
explained to the user in the "why" view without a model round-trip.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from rapidfuzz import fuzz

from app.models import JobCluster, JobPosting, Profile, ProfileVariant
from app.services.configload import load_rubric
from app.services.gates import _to_annual
from app.services.text import build_idf, semantic_similarity, tokenize


@dataclass
class FastScore:
    semantic: float = 0.0
    skill_coverage: float = 0.0
    title_fit: float = 0.0
    freshness: float = 0.0
    comp_fit: float = 0.0
    source_prior: float = 0.0
    ghost_risk: float = 0.0
    crowding: float = 0.0
    keyword_boost: float = 0.0
    s_fast: float = 0.0
    detail: dict = field(default_factory=dict)


# Calibration constant. A TF-IDF cosine of this value or better between the
# profile and a posting is treated as a full match. Measured against a 5,349
# posting corpus on 2026-09-12: the distribution runs 0.02-0.38, so 0.30 puts the
# genuinely strong matches at 1.0 without saturating the middle of the field.
# Re-derive this if the similarity function is ever swapped for embeddings.
SEMANTIC_FULL_MATCH = 0.30


def _rescale_semantic(raw: float) -> float:
    return max(0.0, min(1.0, raw / SEMANTIC_FULL_MATCH))


_EVERGREEN = re.compile(
    r"\(all levels\)|general application|talent (pool|community|network)|future opportunities",
    re.IGNORECASE,
)


def freshness(published: datetime | None, now: datetime | None = None) -> float:
    """exp(-age_hours / 168): 1.00 at 0h, 0.61 at 3.5d, 0.37 at 7d, 0.02 at 28d."""
    if published is None:
        return 0.35  # unknown age is mildly negative, not neutral
    now = now or datetime.now(timezone.utc)
    if published.tzinfo is None:
        published = published.replace(tzinfo=timezone.utc)
    age_hours = max(0.0, (now - published).total_seconds() / 3600.0)
    return math.exp(-age_hours / 168.0)


def skill_coverage(
    text: str, variant: ProfileVariant, filters=None,
) -> tuple[float, list[str]]:
    """Share of the wanted skills the posting actually asks for.

    Extracted by dictionary match rather than by an LLM: deterministic, free and
    auditable, and the user can see exactly which term fired.

    The user's own search tags count here too. Without that, editing the tags on
    the Search page shrinks the corpus but leaves the ORDER driven by whatever
    skills were configured months ago, which reads as "it ignored my tags".
    Required tags carry must-have weight, boost tags carry nice-to-have weight.
    """
    must = [str(s).lower() for s in (variant.must_have_skills or [])]
    nice = [str(s).lower() for s in (variant.nice_to_have_skills or [])]
    if filters is not None:
        must += [t for t in (filters.require_any or []) if t not in must]
        nice += [t for t in (filters.boost or []) if t not in nice and t not in must]
    if not must and not nice:
        return 0.5, []
    haystack = text.lower()
    matched = [s for s in must + nice if _mentions(haystack, s)]
    weighted = sum(1.0 for s in must if _mentions(haystack, s))
    weighted += sum(0.4 for s in nice if _mentions(haystack, s))
    denom = len(must) + 0.4 * len(nice)
    return (min(1.0, weighted / denom) if denom else 0.5), matched


def _mentions(haystack: str, skill: str) -> bool:
    # ".NET" and "dotnet", "CI/CD" and "cicd" are the same requirement.
    variants = {skill, skill.replace(".", ""), skill.replace("/", ""), skill.replace("-", "")}
    if skill == "dotnet":
        variants |= {".net", "asp.net", "c#"}
    if skill == "csharp":
        variants |= {"c#", ".net"}
    return any(re.search(rf"(?<![\w]){re.escape(v)}(?![\w])", haystack) for v in variants if v)


def title_fit(title: str, variant: ProfileVariant) -> float:
    targets = [str(t) for t in (variant.target_titles or [])]
    if not targets:
        return 0.5
    return max(fuzz.token_set_ratio(title or "", t) for t in targets) / 100.0


def comp_fit(posting: JobPosting, profile: Profile) -> float:
    floor = profile.comp_floor_annual
    target = profile.rate_target_hourly and profile.rate_target_hourly * 1800
    if not floor:
        return 0.5
    annual = _to_annual(posting.comp_max, posting.comp_period)
    if annual is None:
        return 0.5  # unstated compensation is genuinely unknown, not bad
    ceiling = target or floor * 1.6
    if ceiling <= floor:
        return 1.0 if annual >= floor else 0.0
    return max(0.0, min(1.0, (annual - floor) / (ceiling - floor)))


def ghost_risk(posting: JobPosting, cluster: JobCluster, on_own_ats: bool) -> float:
    """18-22% of postings are ghosts. These are the signals available for free.

    Deliberately NOT used: Greenhouse's "Verified" badge. It certifies candidate
    treatment practices, not that the job exists, so wiring it in would buy false
    confidence.
    """
    cfg = load_rubric().get("ghost_risk", {})
    now = datetime.now(timezone.utc)
    risk = 0.0

    pub = posting.first_published_at
    if pub:
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age_days = (now - pub).days
        unchanged = not posting.updated_at_source or posting.updated_at_source <= pub
        if age_days > 30 and unchanged:
            risk += float(cfg.get("stale_unchanged_30d", 0.35))

    if cluster.recurrence_months > 1:
        risk += float(cfg.get("reposted_same_body", 0.25))
    if _EVERGREEN.search(posting.title or ""):
        risk += float(cfg.get("evergreen_title", 0.10))
    if not on_own_ats:
        risk += float(cfg.get("aggregator_only", 0.10))
    if not posting.apply_url:
        risk += float(cfg.get("no_apply_url", 0.20))

    return max(0.0, min(1.0, risk))


def crowding(cluster: JobCluster, applicant_count: int | None = None) -> float:
    """Fan-out is a negative signal.

    Reply rate falls from 9.44% with one automated bidder to 2.11% at eleven or
    more, and two thirds of proposals land on a job another bot also found. A
    posting that scores highly on every scanner is, by that fact, worse.
    """
    cfg = load_rubric().get("crowding", {})
    fan_div = float(cfg.get("fan_out_divisor", 5))
    app_div = float(cfg.get("applicant_divisor", 200))
    fan_term = max(0.0, min(1.0, (cluster.fan_out - 1) / fan_div))
    if applicant_count is None:
        return fan_term
    app_term = max(0.0, min(1.0, applicant_count / app_div))
    return 0.5 * fan_term + 0.5 * app_term


def compute_fast(
    *,
    posting: JobPosting,
    cluster: JobCluster,
    profile: Profile,
    variant: ProfileVariant,
    idf: dict[str, float],
    source_prior: float,
    on_own_ats: bool,
    applicant_count: int | None = None,
    filters=None,
) -> FastScore:
    w = load_rubric().get("weights", {})
    text = f"{posting.title or ''}\n{posting.body_text or ''}"
    # Search tags are repeated so they weigh as much as configured must-haves:
    # a tag the user typed today is a stronger statement of intent than a skill
    # list they filled in once.
    tag_terms: list[str] = []
    if filters is not None:
        tag_terms = [*(filters.require_any or []), *(filters.boost or [])]

    profile_text = " ".join(
        [variant.headline or ""]
        + [str(t) for t in (variant.target_titles or [])]
        + [str(s) for s in (variant.must_have_skills or [])] * 3
        + [str(s) for s in (variant.nice_to_have_skills or [])]
        + tag_terms * 3
    )

    sem = _rescale_semantic(semantic_similarity(text, profile_text, idf))
    cov, matched = skill_coverage(text, variant, filters)
    tfit = title_fit(posting.title or "", variant)
    fresh = freshness(posting.first_published_at)
    cfit = comp_fit(posting, profile)
    ghost = ghost_risk(posting, cluster, on_own_ats)
    crowd = crowding(cluster, applicant_count)

    # User boost terms are a bounded nudge on top of the weighted sum, never a
    # replacement for fit: a long boost list must not outrank a genuine match.
    boost, boost_hits = (0.0, [])
    if filters is not None:
        from app.services.search_filters import boost_score

        boost, boost_hits = boost_score(filters, text)

    raw = (
        float(w.get("semantic", 0.30)) * sem
        + float(w.get("skill_coverage", 0.20)) * cov
        + float(w.get("title_fit", 0.10)) * tfit
        + float(w.get("freshness", 0.15)) * fresh
        + float(w.get("comp_fit", 0.10)) * cfit
        + float(w.get("source_prior", 0.15)) * source_prior
    )
    s_fast = 100.0 * min(1.0, raw + boost) * (1.0 - 0.5 * ghost)

    return FastScore(
        semantic=sem, skill_coverage=cov, title_fit=tfit, freshness=fresh,
        comp_fit=cfit, source_prior=source_prior, ghost_risk=ghost, crowding=crowd,
        keyword_boost=boost,
        s_fast=round(s_fast, 2),
        detail={"matched_skills": matched, "boost_hits": boost_hits},
    )


def clamp01(value, default: float = 0.5) -> float:
    """Coerce a model-supplied number into [0, 1].

    Free models routinely answer a "0.0-1.0" field with 3.0 because the rest of
    the schema is on a 0-10 scale. Left unclamped that silently inflates the
    weighted sum past 1.0 and reorders the whole digest, which is exactly the bug
    this function exists to prevent. A value in (1, 10] is read as a 0-10 score.
    """
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    if num != num:  # NaN
        return default
    if num > 10.0:
        return 1.0
    if num > 1.0:
        return num / 10.0
    return max(0.0, num)


def compute_priority(
    *,
    fast: FastScore,
    llm_fit: float | None,
    seniority_fit: float,
    track_weight: float,
    warm_path: float = 0.0,
    strategic_fit: float = 0.5,
    liveness_ok: bool = True,
    injection_suspected: bool = False,
) -> tuple[float, dict[str, float]]:
    """Priority = 1000 * Fit^1.4 * Trust * (0.30 + 0.70*Reach) * (0.40 + 0.60*Value)

    The exponent on Fit is convex on purpose. With a hard weekly send cap the
    ranking should be aggressive about the top of the distribution rather than
    fair to the middle: a 0.9 fit is worth 2.6x a 0.6 fit, not 1.5x.
    """
    r = load_rubric().get("ranking", {})
    fw, rw, vw = r.get("fit", {}), r.get("reach", {}), r.get("value", {})
    exponent = float(r.get("fit_exponent", 1.4))

    # Before the judge has run, fall back to the deterministic signal rather than
    # pretending a missing verdict is a neutral one.
    llm_component = (
        max(0.0, min(1.0, llm_fit / 10.0)) if llm_fit is not None else fast.semantic
    )
    seniority_fit = clamp01(seniority_fit)
    strategic_fit = clamp01(strategic_fit)
    track_weight = clamp01(track_weight, default=0.25)

    fit = clamp01(
        float(fw.get("llm", 0.35)) * llm_component
        + float(fw.get("semantic", 0.25)) * fast.semantic
        + float(fw.get("skill_coverage", 0.25)) * fast.skill_coverage
        + float(fw.get("seniority", 0.15)) * seniority_fit,
        default=0.0,
    )

    # A judge verdict of 3/10 is a strong signal and must not be outvoted by a
    # high keyword overlap. Below the "all must-haves present" anchor (7) the
    # whole priority is scaled down proportionally.
    judge_veto = 1.0
    if llm_fit is not None and llm_fit < 7:
        judge_veto = max(0.15, llm_fit / 7.0)

    # A posting containing text aimed at the scorer is not a posting to apply to.
    # It stays visible and flagged rather than being deleted, but it never leads.
    injection_penalty = 0.25 if injection_suspected else 1.0

    trust = (
        (1.0 - fast.ghost_risk)
        * (1.0 if liveness_ok else 0.0)
        * judge_veto
        * injection_penalty
    )
    reach = (
        float(rw.get("source_prior", 0.40)) * fast.source_prior
        + float(rw.get("freshness", 0.25)) * fast.freshness
        + float(rw.get("warm_path", 0.25)) * warm_path
        + float(rw.get("uncrowded", 0.10)) * (1.0 - fast.crowding)
    )
    value = clamp01(
        float(vw.get("comp_fit", 0.55)) * fast.comp_fit
        + float(vw.get("track_weight", 0.25)) * track_weight
        + float(vw.get("strategic_fit", 0.20)) * strategic_fit,
        default=0.0,
    )
    reach = clamp01(reach, default=0.0)

    priority = (
        1000.0
        * (max(0.0, fit) ** exponent)
        * trust
        * (0.30 + 0.70 * reach)
        * (0.40 + 0.60 * value)
    )
    return round(priority, 1), {
        "fit": round(fit, 4), "trust": round(trust, 4),
        "reach": round(reach, 4), "value": round(value, 4),
    }


def corpus_idf(texts: list[str]) -> dict[str, float]:
    return build_idf(texts)


def explain(fast: FastScore, parts: dict[str, float], priority: float) -> str:
    """Human-readable score breakdown for the "why" view.

    Score transparency is what makes the human keep tapping, and their
    disagreements are only usable as signal if they can see what they disagree with.
    """
    lines = [
        f"priority {priority:.0f}",
        f"  fit {parts.get('fit', 0):.2f}  trust {parts.get('trust', 0):.2f}  "
        f"reach {parts.get('reach', 0):.2f}  value {parts.get('value', 0):.2f}",
        f"  semantic {fast.semantic:.2f} | skills {fast.skill_coverage:.2f} "
        f"| title {fast.title_fit:.2f} | fresh {fast.freshness:.2f} | comp {fast.comp_fit:.2f}",
        f"  source prior {fast.source_prior:.2f} | ghost risk {fast.ghost_risk:.2f} "
        f"| crowding {fast.crowding:.2f}",
    ]
    matched = fast.detail.get("matched_skills") or []
    if matched:
        lines.append("  matched: " + ", ".join(matched[:12]))
    boost_hits = fast.detail.get("boost_hits") or []
    if boost_hits:
        lines.append(
            f"  boost +{fast.keyword_boost:.2f}: " + ", ".join(boost_hits[:10])
        )
    return "\n".join(lines)
