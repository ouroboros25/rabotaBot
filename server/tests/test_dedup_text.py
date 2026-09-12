"""Normalisation that the whole dedup cascade rests on."""
from app.services.text import (
    canonical_url, company_key, extract_ats_slug, is_public_http_url, title_key,
)


def test_company_key_collapses_legal_suffixes():
    assert company_key("Acme Inc.") == company_key("ACME, LLC") == "acme"
    assert company_key("Isar Aerospace GmbH") == "isar aerospace"


def test_title_key_strips_req_ids_and_locations():
    assert title_key("Senior Engineer II (Remote, EMEA) #40213") == \
           title_key("senior engineer")
    assert title_key("Backend Developer — EMEA") == "backend developer"


def test_canonical_url_strips_tracking_params():
    a = canonical_url("https://Boards.Greenhouse.io/acme/jobs/123?gh_src=x&utm_source=y")
    b = canonical_url("https://boards.greenhouse.io/acme/jobs/123/")
    assert a == b


def test_canonical_url_keeps_meaningful_params():
    url = canonical_url("https://example.com/jobs?id=7&utm_medium=email")
    assert "id=7" in url and "utm_medium" not in url


def test_ssrf_guard_blocks_private_and_metadata_targets():
    assert is_public_http_url("https://example.com") is True
    assert is_public_http_url("http://127.0.0.1/admin") is False
    assert is_public_http_url("http://169.254.169.254/latest/meta-data/") is False
    assert is_public_http_url("http://192.168.0.192:8181/v1") is False
    assert is_public_http_url("file:///etc/passwd") is False
    assert is_public_http_url("gopher://example.com") is False


def test_ats_slug_extraction_covers_the_boards_we_harvest():
    assert extract_ats_slug("https://boards.greenhouse.io/stripe/jobs/1") == ("greenhouse", "stripe")
    assert extract_ats_slug("https://jobs.lever.co/palantir/abc") == ("lever", "palantir")
    assert extract_ats_slug("https://jobs.ashbyhq.com/ramp/xyz") == ("ashby", "ramp")
    assert extract_ats_slug("https://apply.workable.com/hotjar/j/ABC/") == ("workable", "hotjar")
    assert extract_ats_slug("https://example.com/careers") is None
    # Generic path segments must never be mistaken for a company slug.
    assert extract_ats_slug("https://jobs.ashbyhq.com/jobs/123") is None
