"""Connector plumbing shared by every family.

A family implements three things: ``fetch`` (get raw payloads), ``parse`` (turn
one payload into canonical item dicts), and a ``name``. Rate limiting, the
identified User-Agent, retries, size caps and SSRF guarding all live here, so a
new family is roughly forty lines.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

import httpx
from dateutil import parser as date_parser

from app.config import settings
from app.services.text import is_public_http_url

logger = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 24 * 1024 * 1024  # some ATS boards legitimately return ~5MB


@dataclass
class RawItem:
    """One posting in canonical shape, before it touches the database."""

    external_id: str
    title: str
    company_name: str | None = None
    company_slug: str | None = None
    apply_url: str | None = None
    url: str | None = None
    posted_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    comp_min: float | None = None
    comp_max: float | None = None
    comp_currency: str | None = None
    comp_period: str | None = None
    seniority: str | None = None
    employment_type: str | None = None
    location_raw: str | None = None
    countries_allowed: list[str] | None = None
    timezones_allowed: list[str] | None = None
    remote_policy: str = "unknown"
    body: str | None = None
    tags: list[str] | None = None
    form_questions: list[dict] | None = None
    raw_extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class FetchResult:
    items: list[RawItem]
    bytes_fetched: int = 0
    http_status: int | None = None
    drift_note: str | None = None


class RateLimiter:
    """Per-source politeness. Not a bypass mechanism, the opposite of one."""

    def __init__(self, rps: float) -> None:
        self.min_interval = 1.0 / max(0.05, rps)
        self._last = 0.0

    def wait(self) -> None:
        elapsed = time.monotonic() - self._last
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last = time.monotonic()


class Connector:
    family = "base"

    def __init__(self, source) -> None:
        self.source = source
        self.params: dict[str, Any] = source.params or {}
        self.field_map: dict[str, str] = source.field_map or {}
        self.limiter = RateLimiter(source.rate_limit_rps or 1.0)
        self._bytes = 0
        self._status: int | None = None

    # -- http --------------------------------------------------------------
    def get(self, url: str, **kwargs) -> httpx.Response:
        if not is_public_http_url(url):
            raise ValueError(f"refusing non-public URL: {url}")
        self.limiter.wait()
        headers = {
            "User-Agent": settings.HTTP_USER_AGENT,
            "Accept": kwargs.pop("accept", "application/json, text/xml;q=0.9, */*;q=0.8"),
        }
        headers.update(kwargs.pop("headers", {}) or {})
        with httpx.Client(
            timeout=settings.HTTP_TIMEOUT_S, follow_redirects=True, http2=False
        ) as client:
            resp = client.get(url, headers=headers, **kwargs)
        self._status = resp.status_code
        self._bytes += len(resp.content)
        if len(resp.content) > MAX_RESPONSE_BYTES:
            raise ValueError(f"response too large from {url}: {len(resp.content)} bytes")
        resp.raise_for_status()
        return resp

    # -- interface ---------------------------------------------------------
    def fetch(self) -> FetchResult:  # pragma: no cover - overridden
        raise NotImplementedError


def parse_date(value: Any) -> datetime | None:
    """Accept ISO strings, epoch seconds, epoch millis and RFC 2822."""
    if value in (None, "", 0):
        return None
    try:
        if isinstance(value, (int, float)):
            seconds = float(value)
            if seconds > 1e11:  # milliseconds
                seconds /= 1000.0
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        text = str(value).strip()
        if text.isdigit():
            return parse_date(int(text))
        dt = date_parser.parse(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None


def to_float(value: Any) -> float | None:
    if value in (None, "", 0):
        return None
    try:
        return float(str(value).replace(",", "").replace("$", "").strip())
    except (TypeError, ValueError):
        return None


def as_list(value: Any) -> list[str] | None:
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple, set)):
        out = [str(v).strip() for v in value if str(v).strip()]
        return out or None
    return [str(value).strip()]


_REMOTE_HINTS = ("remote", "anywhere", "worldwide", "distributed", "work from home")
_HYBRID_HINTS = ("hybrid",)
_ONSITE_HINTS = ("on-site", "onsite", "in office", "in-office")


def infer_remote_policy(*fragments: Any) -> str:
    text = " ".join(str(f) for f in fragments if f).lower()
    if any(h in text for h in _HYBRID_HINTS):
        return "hybrid"
    if any(h in text for h in _ONSITE_HINTS):
        return "onsite"
    if any(h in text for h in _REMOTE_HINTS):
        return "global"
    return "unknown"


def dig(obj: Any, path: str) -> Any:
    """Dotted path lookup used by the config-driven json_api_generic family."""
    if not path:
        return obj
    cur = obj
    for seg in path.split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            if not seg.isdigit():
                return None
            idx = int(seg)
            cur = cur[idx] if 0 <= idx < len(cur) else None
        elif isinstance(cur, dict):
            cur = cur.get(seg)
        else:
            return None
    return cur


def iter_chunks(items: Iterable, size: int):
    chunk: list = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
