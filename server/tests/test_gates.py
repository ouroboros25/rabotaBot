"""Hard gates. Every pattern here corresponds to a posting seen in the wild."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services import gates


def _profile(**kw):
    base = dict(countries_eligible=["UA", "PL"], comp_floor_annual=90000,
                min_overlap_hours=4)
    base.update(kw)
    return SimpleNamespace(**base)


def _posting(**kw):
    base = dict(
        title="Senior Backend Engineer", body_text="", seniority=None,
        countries_allowed=None, timezones_allowed=None, remote_policy="global",
        comp_min=None, comp_max=None, comp_currency="USD", comp_period="year",
        first_published_at=datetime.now(timezone.utc), updated_at_source=None,
        expires_at=None, liveness_ok=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def codes(hits):
    return {h.code for h in hits}


def test_us_only_shorthand_is_gated():
    """The exact posting that topped the first live digest by mistake."""
    hits = gates.evaluate(_posting(title="Remote (US only) | Senior SWE"), _profile())
    assert "GEO_FENCED" in codes(hits)


def test_structured_geo_fence_is_gated():
    hits = gates.evaluate(_posting(countries_allowed=["US", "CA"]), _profile())
    assert "GEO_FENCED" in codes(hits)


def test_eligible_country_passes():
    hits = gates.evaluate(_posting(countries_allowed=["PL", "DE"]), _profile())
    assert "GEO_FENCED" not in codes(hits)


def test_unknown_geo_is_not_a_rejection():
    assert "GEO_FENCED" not in codes(gates.evaluate(_posting(), _profile()))


def test_clearance_and_hybrid_and_w2():
    assert "CLEARANCE" in codes(gates.evaluate(
        _posting(body_text="Active TS/SCI security clearance required"), _profile()))
    assert "HYBRID_ONSITE" in codes(gates.evaluate(
        _posting(body_text="Hybrid, 3 days a week in the office"), _profile()))
    assert "EMPLOYMENT_MISMATCH" in codes(gates.evaluate(
        _posting(body_text="W2 only, no C2C"), _profile()))


def test_evergreen_label_in_the_title_is_gated():
    assert "EVERGREEN" in codes(gates.evaluate(
        _posting(title="Software Engineer (All Levels)"), _profile()))
    assert "EVERGREEN" in codes(gates.evaluate(
        _posting(title="Backend Engineer - Talent Pool"), _profile()))


def test_eeo_and_footer_boilerplate_is_not_evergreen():
    """Regression from production: "talent pool" gated a batch of genuine Airbnb
    roles because it sits in their standard EEO paragraph, and "join our talent
    community" is ATS footer text on plenty of real openings."""
    assert "EVERGREEN" not in codes(gates.evaluate(
        _posting(
            title="Senior Platform Engineer",
            body_text=(
                "Build our payments platform.\n" + "Details. " * 200 +
                "Airbnb is committed to working with the broadest possible talent pool. "
                "Join our talent community for future opportunities."
            ),
        ),
        _profile(),
    ))


def test_pipeline_requisition_stated_upfront_is_gated():
    assert "EVERGREEN" in codes(gates.evaluate(
        _posting(
            title="Backend Engineer",
            body_text="This is a general application for future roles on our team.",
        ),
        _profile(),
    ))


def test_junior_roles_are_gated_out():
    assert "SENIORITY_OUT" in codes(gates.evaluate(
        _posting(title="Junior Developer"), _profile()))
    assert "SENIORITY_OUT" not in codes(gates.evaluate(
        _posting(title="Staff Engineer"), _profile()))


def test_stale_posting_with_no_update_is_gated():
    old = datetime.now(timezone.utc) - timedelta(days=60)
    assert "STALE" in codes(gates.evaluate(_posting(first_published_at=old), _profile()))
    # An old posting that was actually refreshed is not stale.
    fresh_update = datetime.now(timezone.utc) - timedelta(days=2)
    assert "STALE" not in codes(gates.evaluate(
        _posting(first_published_at=old, updated_at_source=fresh_update), _profile()))


def test_comp_floor_has_tolerance_but_bites():
    assert "COMP_FLOOR" in codes(gates.evaluate(_posting(comp_max=50000), _profile()))
    # 15% tolerance for FX and banding.
    assert "COMP_FLOOR" not in codes(gates.evaluate(_posting(comp_max=85000), _profile()))
    # Unstated compensation is unknown, not disqualifying.
    assert "COMP_FLOOR" not in codes(gates.evaluate(_posting(), _profile()))


def test_hourly_rate_is_annualised_before_comparison():
    assert "COMP_FLOOR" not in codes(gates.evaluate(
        _posting(comp_max=80, comp_period="hour"), _profile()))
    assert "COMP_FLOOR" in codes(gates.evaluate(
        _posting(comp_max=20, comp_period="hour"), _profile()))


def test_region_suffix_in_title_is_gated():
    hits = gates.evaluate(
        _posting(title="Site Reliability Engineer, Infrastructure Platforms - AMER"),
        _profile(),
    )
    assert "GEO_FENCED" in codes(hits)


def test_emea_suffix_is_not_gated_for_a_polish_candidate():
    hits = gates.evaluate(
        _posting(title="Senior Backend Engineer - EMEA"), _profile()
    )
    assert "GEO_FENCED" not in codes(hits)


def test_structured_field_still_wins_over_the_title():
    """An explicit allow-list beats a title token: a title saying "US" while the
    feed says the role is open to Poland should not be gated."""
    hits = gates.evaluate(
        _posting(title="Backend Engineer (US)", countries_allowed=["PL", "US"]),
        _profile(),
    )
    assert "GEO_FENCED" not in codes(hits)


def test_evergreen_requisition_in_the_title_is_gated():
    """Seen live: "Forward Deployed Engineer, CE (Evergreen)". An evergreen
    requisition is a pipeline, not an opening."""
    assert "EVERGREEN" in codes(gates.evaluate(
        _posting(title="Forward Deployed Engineer, CE (Evergreen)"), _profile()))
    assert "EVERGREEN" in codes(gates.evaluate(
        _posting(title="Backend Engineer - Talent Pipeline"), _profile()))


def test_pipeline_in_the_body_is_not_evergreen():
    """Regression guard: matching "pipeline" anywhere would gate most data and
    platform roles, since CI/CD and data pipelines are in every description."""
    hits = gates.evaluate(
        _posting(
            title="Senior Data Engineer",
            body_text="You will own our CI/CD pipeline and the ingestion pipeline.",
        ),
        _profile(),
    )
    assert "EVERGREEN" not in codes(hits)


def test_evergreen_word_in_the_body_alone_is_not_gated():
    hits = gates.evaluate(
        _posting(body_text="We maintain an evergreen list of internal tools."),
        _profile(),
    )
    assert "EVERGREEN" not in codes(hits)
