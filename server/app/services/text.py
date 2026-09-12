"""Text normalisation, hashing and URL canonicalisation.

Deliberately dependency-light. The similarity function here is honest TF-IDF
cosine, not an embedding: the local AI gateway exposes chat only, and pulling
numpy/scikit in for one score would triple the image for a marginal gain at
this corpus size. ``semantic_similarity`` is the documented swap-in point if a
real embedding endpoint ever lands on the gateway.
"""
from __future__ import annotations

import hashlib
import ipaddress
import math
import re
import socket
from collections import Counter
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from bs4 import BeautifulSoup

# Tracking parameters that differ between two links to the same posting.
_TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "gh_src", "gh_jid", "source", "src", "ref", "referrer", "lever-origin",
    "lever-source", "_gl", "fbclid", "gclid", "mc_cid", "mc_eid", "trk", "trackingId",
}

_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|inc\.|llc|l\.l\.c\.|ltd|ltd\.|limited|gmbh|b\.v\.|bv|oy|ab|a/s|s\.a\.|sa|"
    r"plc|pty|sarl|s\.r\.o\.|sp\. z o\.o\.|co|corp|corporation|company|holdings|group)\b",
    re.IGNORECASE,
)

# Leading "#" is consumed too: matching only the digits leaves a stray hash
# that survives every later substitution and breaks the identity hash.
_REQ_ID = re.compile(r"[#]?\b(?:req|r|job|jr|id)?[-#_]?\d{4,}\b", re.IGNORECASE)
_BRACKETED = re.compile(r"[\(\[\{][^\)\]\}]*[\)\]\}]")
_ROMAN = re.compile(r"\b(?:i{1,3}|iv|v|vi{1,3}|ix|x)\b", re.IGNORECASE)
_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️]")
_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9+#./_-]{1,}")

# Words that carry no discriminating signal in a job posting.
_STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for with
from by as is are was were be been being do does did doing have has had having
we you they he she it our your their its will would can could should may might
must shall about into over under again further once here there when where why how
all any both each few more most other some such no nor not only own same so too
very just also team role position job work working experience years year new
company please apply applicant candidate candidates opportunity opportunities
looking join help make build using use used across within including etc
""".split())


def html_to_text(html: str | None) -> str:
    """Strip markup, keep readable structure. Never trust this content."""
    if not html:
        return ""
    if "<" not in html:
        return _WS.sub(" ", html).strip()
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [ln.strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", "replace")).hexdigest()


def company_key(name: str | None) -> str:
    """Join key for 'is this the same employer'."""
    if not name:
        return ""
    key = _LEGAL_SUFFIXES.sub(" ", name.lower())
    key = re.sub(r"[^\w\s]", " ", key)
    return _WS.sub(" ", key).strip()


def title_key(title: str | None) -> str:
    """Join key for 'is this the same opening'.

    Strips requisition ids, bracketed locations, roman numerals and emoji so that
    "Senior Engineer II (Remote, EMEA) #40213" and "Senior Engineer" collapse.
    """
    if not title:
        return ""
    key = title.lower()
    key = _BRACKETED.sub(" ", key)
    key = _REQ_ID.sub(" ", key)
    key = _EMOJI.sub(" ", key)
    key = re.sub(r"[|,–—-]+\s*(remote|emea|apac|us|uk|eu|global).*$", " ", key)
    key = re.sub(r"[^\w\s+#./]", " ", key)
    key = _ROMAN.sub(" ", key)
    return _WS.sub(" ", key).strip()


def tokenize(text: str | None) -> list[str]:
    if not text:
        return []
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


def tf_idf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    if not tokens:
        return {}
    counts = Counter(tokens)
    total = sum(counts.values())
    vec: dict[str, float] = {}
    for term, n in counts.items():
        # Smoothed sublinear TF keeps long postings from dominating on repetition.
        tf = 1.0 + math.log(n)
        vec[term] = (tf / total) * idf.get(term, _DEFAULT_IDF)
    norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
    return {k: v / norm for k, v in vec.items()}


_DEFAULT_IDF = 6.0  # unseen term: treat as rare, i.e. informative


def cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return max(0.0, min(1.0, sum(v * b.get(k, 0.0) for k, v in a.items())))


def semantic_similarity(
    posting_text: str, profile_text: str, idf: dict[str, float]
) -> float:
    """Swap-in point for real embeddings if the gateway ever exposes them."""
    return cosine(
        tf_idf_vector(tokenize(posting_text), idf),
        tf_idf_vector(tokenize(profile_text), idf),
    )


def build_idf(documents: list[str]) -> dict[str, float]:
    """Corpus IDF. Cheap to recompute; we do it once per scoring run."""
    n_docs = max(1, len(documents))
    df: Counter[str] = Counter()
    for doc in documents:
        df.update(set(tokenize(doc)))
    return {term: math.log((n_docs + 1) / (count + 1)) + 1.0 for term, count in df.items()}


# --------------------------------------------------------------------------
# URL handling
# --------------------------------------------------------------------------

_PRIVATE_HOST_CACHE: dict[str, bool] = {}


def is_public_http_url(url: str) -> bool:
    """SSRF guard.

    The redirect resolver and every outbound fetch go through this. Blocks
    non-http schemes, RFC1918, loopback, link-local and the cloud metadata
    endpoint. A job board that redirects to 169.254.169.254 is not a scenario we
    need to be clever about; we just never follow it.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    if host in _PRIVATE_HOST_CACHE:
        return _PRIVATE_HOST_CACHE[host]

    ok = True
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        ok = False
        infos = []
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            ok = False
            break
        if (
            ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_multicast or ip.is_reserved or ip.is_unspecified
        ):
            ok = False
            break
    _PRIVATE_HOST_CACHE[host] = ok
    return ok


def canonical_url(url: str | None) -> str | None:
    """Strip tracking params, normalise host and path.

    Catches roughly 40% of cross-board duplicates on its own: an aggregator
    listing usually links straight at the company's own ATS URL.
    """
    if not url:
        return None
    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https"):
        return None
    query = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
             if k not in _TRACKING_PARAMS]
    query.sort()
    path = parsed.path.rstrip("/") or "/"
    return urlunparse((
        parsed.scheme.lower(),
        (parsed.hostname or "").lower() + (f":{parsed.port}" if parsed.port else ""),
        path,
        "",
        urlencode(query),
        "",
    ))


# ATS apply-URL patterns. Harvesting these from aggregator payloads is how the
# company board registry gets built for free, as a side effect of ingestion.
ATS_URL_PATTERNS = [
    ("greenhouse", re.compile(r"(?:boards|job-boards)\.greenhouse\.io/(?:embed/job_app\?for=)?([a-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([a-z0-9_-]+)", re.I)),
    ("lever", re.compile(r"jobs\.(?:eu\.)?lever\.co/([a-z0-9_-]+)", re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([a-z0-9_.-]+)", re.I)),
    ("workable", re.compile(r"apply\.workable\.com/([a-z0-9_-]+)", re.I)),
    ("smartrecruiters", re.compile(r"jobs\.smartrecruiters\.com/([a-z0-9_-]+)", re.I)),
    ("recruitee", re.compile(r"([a-z0-9_-]+)\.recruitee\.com", re.I)),
    ("personio", re.compile(r"([a-z0-9_-]+)\.jobs\.personio\.(?:de|com)", re.I)),
    ("teamtailor", re.compile(r"([a-z0-9_-]+)\.teamtailor\.com", re.I)),
]

_SLUG_BLOCKLIST = {"www", "jobs", "careers", "api", "boards", "embed", "apply", "job"}


def extract_ats_slug(url: str | None) -> tuple[str, str] | None:
    """Return (ats, slug) for a known ATS apply URL, else None."""
    if not url:
        return None
    for ats, pattern in ATS_URL_PATTERNS:
        m = pattern.search(url)
        if m:
            slug = m.group(1).lower()
            if slug and slug not in _SLUG_BLOCKLIST:
                return ats, slug
    return None
