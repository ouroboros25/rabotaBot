"""User-editable search filters.

These run inside stage 0, so a mistake here either hides jobs the user wanted
(silent, expensive) or lets through everything they asked to exclude (noisy).
Both failure modes get tests.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services import gates
from app.services.search_filters import (
    BOOST_CAP, SearchFilters, boost_score, first_match, matches, normalize,
)


# ---------------------------------------------------------------- matching

def test_word_boundary_does_not_match_inside_a_word():
    assert matches("go", "we write Go services") is True
    assert matches("go", "a good algorithm") is False
    assert matches("java", "Java backend") is True
    assert matches("java", "JavaScript frontend") is False


def test_terms_with_punctuation_still_match():
    """A plain \\b fails on .NET, C# and CI/CD, which are exactly the terms a
    developer will type first."""
    assert matches(".net", "We use .NET 8 on Azure") is True
    assert matches("c#", "Strong C# skills") is True
    assert matches("ci/cd", "own the CI/CD pipeline") is True
    assert matches("node.js", "Node.js and Deno") is True


def test_matching_is_case_insensitive():
    assert matches("azure", "AZURE cloud") is True


def test_first_match_returns_the_offending_term():
    assert first_match(["wordpress", "drupal"], "a Drupal migration") == "drupal"
    assert first_match(["wordpress"], "a Django app") is None


# ---------------------------------------------------------------- boost

def test_boost_is_bounded():
    filters = SearchFilters(boost=[f"term{i}" for i in range(20)])
    text = " ".join(f"term{i}" for i in range(20))
    score, hits = boost_score(filters, text)
    assert len(hits) == 20
    assert score == BOOST_CAP, "a long boost list must not outweigh actual fit"


def test_boost_counts_only_terms_present():
    filters = SearchFilters(boost=["bicep", "pgvector", "kafka"])
    score, hits = boost_score(filters, "We deploy with Bicep on Azure")
    assert hits == ["bicep"]
    assert 0 < score < BOOST_CAP


def test_no_boost_terms_means_no_bonus():
    assert boost_score(SearchFilters(), "anything") == (0.0, [])


# ---------------------------------------------------------------- normalise

def test_normalize_trims_lowercases_and_dedupes():
    f = normalize(SearchFilters(exclude=["  WordPress ", "wordpress", "Drupal", ""]))
    assert f.exclude == ["wordpress", "drupal"]


def test_normalize_clamps_numbers():
    f = normalize(SearchFilters(max_age_days=-5, min_priority=-3))
    assert f.max_age_days == 0
    assert f.min_priority == 0.0


# ---------------------------------------------------------------- gates

def _profile():
    return SimpleNamespace(
        countries_eligible=["UA", "PL"], comp_floor_annual=None, min_overlap_hours=4,
    )


def _posting(**kw):
    base = dict(
        title="Senior Backend Engineer", body_text="", seniority=None,
        countries_allowed=None, timezones_allowed=None, remote_policy="unknown",
        comp_min=None, comp_max=None, comp_currency="USD", comp_period="year",
        first_published_at=datetime.now(timezone.utc), updated_at_source=None,
        expires_at=None, liveness_ok=None, company_name=None, location_raw=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def codes(hits):
    return {h.code for h in hits}


def test_exclude_keyword_rejects():
    f = SearchFilters(exclude=["wordpress"], require_full_remote=False)
    hits = gates.evaluate(
        _posting(body_text="Maintain a WordPress site"), _profile(), None, f
    )
    assert "KEYWORD_EXCLUDE" in codes(hits)


def test_exclude_title_only_looks_at_the_title():
    """"manager" in a body is normal; in a title it is disqualifying."""
    f = SearchFilters(exclude_title=["manager"], require_full_remote=False)
    assert "TITLE_EXCLUDE" in codes(
        gates.evaluate(_posting(title="Engineering Manager"), _profile(), None, f)
    )
    assert "TITLE_EXCLUDE" not in codes(
        gates.evaluate(
            _posting(body_text="You report to the engineering manager"),
            _profile(), None, f,
        )
    )


def test_require_any_rejects_only_when_nothing_matches():
    f = SearchFilters(require_any=["azure", "aws"], require_full_remote=False)
    assert "KEYWORD_MISSING" in codes(
        gates.evaluate(_posting(body_text="On-prem VMware shop"), _profile(), None, f)
    )
    assert "KEYWORD_MISSING" not in codes(
        gates.evaluate(_posting(body_text="Runs on AWS"), _profile(), None, f)
    )


def test_empty_require_any_is_not_a_requirement():
    """An empty list means "no requirement", not "require nothing"."""
    hits = gates.evaluate(_posting(body_text="anything"), _profile(), None,
                           SearchFilters(require_full_remote=False))
    assert "KEYWORD_MISSING" not in codes(hits)


def test_company_exclusion_is_substring_and_case_insensitive():
    f = SearchFilters(exclude_companies=["andela"], require_full_remote=False)
    assert "COMPANY_EXCLUDE" in codes(
        gates.evaluate(_posting(company_name="Andela Talent Cloud"), _profile(), None, f)
    )
    assert "COMPANY_EXCLUDE" not in codes(
        gates.evaluate(_posting(company_name="Stripe"), _profile(), None, f)
    )


def test_max_age_filter():
    old = datetime.now(timezone.utc) - timedelta(days=40)
    f = SearchFilters(max_age_days=14, require_full_remote=False)
    assert "TOO_OLD" in codes(
        gates.evaluate(_posting(first_published_at=old), _profile(), None, f)
    )
    assert "TOO_OLD" not in codes(gates.evaluate(_posting(), _profile(), None, f))
    # 0 disables the filter entirely.
    assert "TOO_OLD" not in codes(
        gates.evaluate(_posting(first_published_at=old), _profile(), None,
                       SearchFilters(max_age_days=0, require_full_remote=False))
    )


def test_no_filters_object_means_every_switch_off():
    """Passing None must behave like a filter set with nothing enabled.

    Not like a default SearchFilters(): those default to strict (full remote
    required, keywords-only on), which is right for the product and wrong as the
    meaning of "no filters were supplied".
    """
    posting = _posting(body_text="WordPress everywhere", title="Manager")
    permissive = SearchFilters(require_full_remote=False, keywords_only=False)
    assert codes(gates.evaluate(posting, _profile(), None, None)) == \
           codes(gates.evaluate(posting, _profile(), None, permissive))


def test_search_tags_feed_the_ranking_not_only_the_filter():
    """The behaviour the user actually expects from "search by these tags".

    Without this, editing tags shrinks the corpus but leaves the ORDER driven by
    whatever skills were configured months ago, which reads as "it ignored me".
    """
    from app.services.scoring import skill_coverage

    variant = SimpleNamespace(
        must_have_skills=["dotnet"], nice_to_have_skills=[], exclude_skills=[],
        target_titles=[], headline="",
    )
    posting_text = "We are building a Django platform in Python."

    without_tags, _ = skill_coverage(posting_text, variant)
    with_tags, matched = skill_coverage(
        posting_text, variant, SearchFilters(require_any=["python", "django"]),
    )
    assert with_tags > without_tags
    assert "python" in matched and "django" in matched


def test_boost_tags_count_as_nice_to_have_in_coverage():
    from app.services.scoring import skill_coverage

    variant = SimpleNamespace(
        must_have_skills=["dotnet"], nice_to_have_skills=[], exclude_skills=[],
        target_titles=[], headline="",
    )
    _, matched = skill_coverage(
        "Deploys with Bicep on Azure.", variant, SearchFilters(boost=["bicep"]),
    )
    assert "bicep" in matched


def test_a_tag_already_in_the_profile_is_not_counted_twice():
    from app.services.scoring import skill_coverage

    variant = SimpleNamespace(
        must_have_skills=["azure"], nice_to_have_skills=[], exclude_skills=[],
        target_titles=[], headline="",
    )
    score, matched = skill_coverage(
        "Runs on Azure.", variant, SearchFilters(require_any=["azure"]),
    )
    assert matched.count("azure") == 1
    assert score <= 1.0


# ---------------------------------------------------------------- history

class _FakeSetting:
    def __init__(self, value):
        self.value = value


class _FakeDB:
    """Enough of a Session for the settings round-trip."""

    def __init__(self, row=None):
        self.row = row
        self.added = []

    def get(self, model, key):
        return self.row

    def add(self, obj):
        self.added.append(obj)
        self.row = obj

    def flush(self):
        pass


def test_save_keeps_the_value_it_replaced():
    """Motivated by a real loss: a hand-typed filter set was overwritten during
    testing and there was no way to recover it."""
    from app.services import search_filters as sf

    db = _FakeDB(_FakeSetting(SearchFilters(require_any=["dotnet"]).as_dict()))
    sf.save(db, SearchFilters(require_any=["python"]))

    assert db.row.value["require_any"] == ["python"]
    assert db.row.value["_previous"]["require_any"] == ["dotnet"]
    assert sf.previous(db)["require_any"] == ["dotnet"]


def test_load_ignores_the_history_key():
    from app.services import search_filters as sf

    db = _FakeDB(_FakeSetting({
        **SearchFilters(require_any=["python"]).as_dict(),
        "_previous": {"require_any": ["dotnet"]},
    }))
    loaded = sf.load(db)
    assert loaded.require_any == ["python"]
    assert not hasattr(loaded, "_previous")


def test_saving_the_same_value_does_not_shift_history():
    from app.services import search_filters as sf

    db = _FakeDB(_FakeSetting(SearchFilters(require_any=["dotnet"]).as_dict()))
    sf.save(db, SearchFilters(require_any=["dotnet"]))
    assert "_previous" not in db.row.value, "an unchanged save must not lose history"


# ---------------------------------------------------------------- modes

def test_full_remote_gate_rejects_silence_and_offices():
    f = SearchFilters(require_full_remote=True)
    assert "NOT_FULL_REMOTE" in codes(gates.evaluate(
        _posting(title="Senior Backend Engineer",
                 body_text="Join our payments team in New York."),
        _profile(), None, f,
    ))
    assert "HYBRID_ONSITE" in codes(gates.evaluate(
        _posting(body_text="Hybrid, 3 days a week in the office"), _profile(), None, f,
    ))
    assert "NOT_FULL_REMOTE" not in codes(gates.evaluate(
        _posting(body_text="This is a 100% remote role."), _profile(), None, f,
    ))


def test_full_remote_gate_can_be_turned_off():
    f = SearchFilters(require_full_remote=False)
    assert "NOT_FULL_REMOTE" not in codes(gates.evaluate(
        _posting(body_text="Join our payments team in New York."), _profile(), None, f,
    ))


def test_keywords_only_disables_profile_derived_gates():
    """Seniority, salary floor and the profile's excluded stack all come from the
    profile, not from the keywords. Applying them silently on top of a keyword
    search is the opposite of what "search only by these words" asks for."""
    variant = SimpleNamespace(exclude_skills=["php"])
    posting = _posting(
        title="Junior PHP Developer",
        body_text="100% remote. We use PHP.",
        comp_max=20000,
    )
    profile = SimpleNamespace(
        countries_eligible=["UA", "PL"], comp_floor_annual=90000, min_overlap_hours=4,
    )

    strict = codes(gates.evaluate(
        posting, profile, variant, SearchFilters(keywords_only=False)))
    assert {"SENIORITY_OUT", "COMP_FLOOR", "STACK_EXCLUDE"} <= strict

    kw = codes(gates.evaluate(
        posting, profile, variant, SearchFilters(keywords_only=True)))
    assert not ({"SENIORITY_OUT", "COMP_FLOOR", "STACK_EXCLUDE"} & kw)


def test_keyword_coverage_rewards_matching_more_of_the_set():
    from app.services.scoring import keyword_coverage

    f = SearchFilters(require_any=["python", "django", "fastapi"])
    few, _ = keyword_coverage("We use Python here.", f)
    many, hits = keyword_coverage("Python, Django and FastAPI.", f)
    assert many > few
    assert set(hits) == {"python", "django", "fastapi"}


def test_keyword_coverage_without_keywords_is_neutral():
    from app.services.scoring import keyword_coverage

    score, hits = keyword_coverage("anything at all", SearchFilters())
    assert score == 0.5 and hits == []


def test_keywords_only_fit_ignores_the_profile():
    """Two postings with identical keyword coverage must rank the same, however
    differently they match the profile's own skills."""
    from app.services.scoring import FastScore, compute_priority

    def priority(semantic, skill_cov):
        fast = FastScore(
            semantic=semantic, skill_coverage=skill_cov, title_fit=0.9,
            freshness=0.8, comp_fit=0.9, source_prior=0.5, ghost_risk=0.0,
            crowding=0.0, keyword_coverage=1.0,
        )
        return compute_priority(
            fast=fast, llm_fit=8, seniority_fit=1.0, track_weight=0.35,
            keywords_only=True,
        )[0]

    assert priority(1.0, 1.0) == priority(0.0, 0.0)
