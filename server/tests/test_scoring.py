"""Ranking arithmetic. These caught three real bugs on the first live run."""
from app.services.scoring import FastScore, clamp01, compute_priority, freshness


def test_clamp_handles_zero_to_ten_confusion():
    # A free model answering a 0..1 field with "3.0" must not inflate the score.
    assert clamp01(3.0) == 0.3
    assert clamp01(0.8) == 0.8
    assert clamp01(11) == 1.0
    assert clamp01(-2) == 0.0
    assert clamp01("nonsense", default=0.5) == 0.5
    assert clamp01(None, default=0.25) == 0.25


def _fast(**kw) -> FastScore:
    base = dict(
        semantic=0.6, skill_coverage=0.7, title_fit=0.8, freshness=0.9,
        comp_fit=0.6, source_prior=0.5, ghost_risk=0.1, crowding=0.2,
    )
    base.update(kw)
    return FastScore(**base)


def test_components_never_exceed_one():
    priority, parts = compute_priority(
        fast=_fast(semantic=1.0, skill_coverage=1.0, comp_fit=1.0),
        llm_fit=10, seniority_fit=3.0, track_weight=5.0, strategic_fit=9.0,
    )
    for name, value in parts.items():
        assert 0.0 <= value <= 1.0, f"{name} out of range: {value}"


def test_low_judge_verdict_vetoes_high_keyword_overlap():
    """A 3/10 from the judge must not be outranked by keyword stuffing."""
    strong_keywords_weak_judge, _ = compute_priority(
        fast=_fast(semantic=1.0, skill_coverage=1.0), llm_fit=3,
        seniority_fit=1.0, track_weight=0.35,
    )
    modest_keywords_good_judge, _ = compute_priority(
        fast=_fast(semantic=0.5, skill_coverage=0.5), llm_fit=8,
        seniority_fit=0.9, track_weight=0.35,
    )
    assert modest_keywords_good_judge > strong_keywords_weak_judge


def test_prompt_injection_is_demoted_not_promoted():
    clean, _ = compute_priority(
        fast=_fast(), llm_fit=8, seniority_fit=1.0, track_weight=0.35,
    )
    hostile, _ = compute_priority(
        fast=_fast(), llm_fit=8, seniority_fit=1.0, track_weight=0.35,
        injection_suspected=True,
    )
    assert hostile < clean / 3


def test_ghost_risk_reduces_trust():
    clean, _ = compute_priority(fast=_fast(ghost_risk=0.0), llm_fit=8,
                                seniority_fit=1.0, track_weight=0.35)
    ghosty, _ = compute_priority(fast=_fast(ghost_risk=0.9), llm_fit=8,
                                 seniority_fit=1.0, track_weight=0.35)
    assert ghosty < clean


def test_dead_posting_scores_zero():
    priority, _ = compute_priority(
        fast=_fast(), llm_fit=9, seniority_fit=1.0, track_weight=0.35,
        liveness_ok=False,
    )
    assert priority == 0.0


def test_freshness_decays_over_a_week():
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    assert freshness(now, now) > 0.99
    assert 0.55 < freshness(now - timedelta(days=3.5), now) < 0.65
    assert freshness(now - timedelta(days=28), now) < 0.05
    # Unknown age is mildly negative, never neutral.
    assert freshness(None) < 0.5
