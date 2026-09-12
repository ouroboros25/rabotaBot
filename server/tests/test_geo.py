"""Geo normalisation.

This is the highest-consequence filter in the product: a false GEO_FENCED hides
a job the user could have had, silently, with no error anywhere.
"""
from app.services.geo import eligible, normalize_list, normalize_one


def test_country_names_map_to_iso():
    assert normalize_one("United States") == {"US"}
    assert normalize_one("Ukraine") == {"UA"}
    assert normalize_one("  poland ") == {"PL"}


def test_worldwide_is_an_empty_set_not_none():
    # Empty set means "explicitly unrestricted", None means "unknown".
    assert normalize_one("Worldwide") == set()
    assert normalize_one("Anywhere") == set()
    assert normalize_one("Zzyzx") is None


def test_regions_expand():
    assert "PL" in normalize_one("EMEA")
    assert "UA" in normalize_one("CEE")
    assert normalize_one("UK") == {"GB"}


def test_normalize_list_reports_worldwide_separately():
    codes, worldwide = normalize_list(["United States", "Canada"])
    assert codes == ["CA", "US"] and worldwide is False

    codes, worldwide = normalize_list(["Worldwide", "United States"])
    assert worldwide is True and codes is None

    codes, worldwide = normalize_list([])
    assert codes is None and worldwide is False


def test_himalayas_style_payload():
    """The exact shape that broke the first live run."""
    codes, worldwide = normalize_list(
        ["Australia", "Canada", "Ireland", "New Zealand", "United Kingdom", "United States"]
    )
    assert codes == ["AU", "CA", "GB", "IE", "NZ", "US"]
    assert eligible(codes, ["UA", "PL"]) is False
    assert eligible(codes, ["CA"]) is True


def test_unknown_eligibility_is_never_a_rejection():
    assert eligible(None, ["UA"]) is None
    assert eligible(["US"], None) is None


def test_region_suffix_in_title_is_resolved():
    """Regression: "Senior SRE - AMER" and "Staff Engineer - CAN" led the first
    production digest because the fence was in the title, not in a field."""
    from app.services.geo import infer_from_title

    assert infer_from_title("Site Reliability Engineer, Infrastructure - AMER") == \
        sorted({"US", "CA", "BR", "MX", "AR", "CO", "CL"})
    assert infer_from_title("Staff Software Engineer, Data Platform - CAN") == ["CA"]
    assert infer_from_title("Senior Backend Engineer (US)") == ["US"]
    assert infer_from_title("Senior Backend Engineer") is None


def test_emea_in_a_title_is_not_a_fence_for_this_profile():
    """The point of resolving through the region map instead of blanket-matching:
    AMER excludes a Poland-based candidate, EMEA does not."""
    from app.services.geo import eligible, infer_from_title

    emea = infer_from_title("Senior Platform Engineer - EMEA")
    assert eligible(emea, ["PL", "UA"]) is True

    amer = infer_from_title("Senior Platform Engineer - AMER")
    assert eligible(amer, ["PL", "UA"]) is False
