"""Config-driven aggregator connectors: JSON APIs, RSS feeds, HN threads."""
from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

import feedparser

from app.connectors.base import (
    Connector, FetchResult, RawItem, as_list, dig, infer_remote_policy, parse_date, to_float,
)
from app.services.text import html_to_text

logger = logging.getLogger(__name__)


class JsonApiConnector(Connector):
    """Generic JSON feed driven entirely by ``params`` + ``field_map`` in YAML.

    Adding a new JSON aggregator is a config row, not a class.
    """

    family = "json_api_generic"

    def fetch(self) -> FetchResult:
        base = self.params["url"]
        query = dict(self.params.get("query") or {})
        items_path = self.params.get("items_path", "")
        max_pages = int(self.params.get("max_pages", 1))
        cursor_param = self.params.get("cursor_param")
        cursor_path = self.params.get("cursor_path")
        page_param = self.params.get("page_param")
        skip_first = bool(self.params.get("skip_first_item"))
        url_template = self.params.get("url_template")

        items: list[RawItem] = []
        drift = None
        cursor = None

        for page in range(max_pages):
            q = dict(query)
            if cursor_param and cursor:
                q[cursor_param] = cursor
            elif page_param and page:
                q[page_param] = page + 1
            url = f"{base}?{urlencode(q)}" if q else base
            payload = self.get(url).json()

            # Some feeds ship an in-band changelog. Surface it instead of silently
            # ingesting a schema that moved under us.
            for watch in self.params.get("watch_fields") or []:
                note = dig(payload, watch)
                if note:
                    drift = f"{watch}: {str(note)[:400]}"

            raw_items = dig(payload, items_path) if items_path else payload
            if not isinstance(raw_items, list):
                logger.warning("source %s: items_path %r did not yield a list",
                               self.source.key, items_path)
                break
            if skip_first and page == 0:
                raw_items = raw_items[1:]

            for entry in raw_items:
                if not isinstance(entry, dict):
                    continue
                item = self._map(entry, url_template)
                if item:
                    items.append(item)

            if cursor_path:
                cursor = dig(payload, cursor_path)
                if not cursor:
                    break
            elif not page_param:
                break

        return FetchResult(items, self._bytes, self._status, drift)

    def _map(self, entry: dict, url_template: str | None) -> RawItem | None:
        fm = self.field_map
        get = lambda key: dig(entry, fm[key]) if key in fm else None  # noqa: E731

        external_id = get("external_id")
        title = get("title")
        if not external_id or not title:
            return None

        if get("expired_flag"):
            return None

        url = get("url") or get("apply_url")
        if not url and url_template and get("slug"):
            url = url_template.format(slug=get("slug"))

        body_raw = get("body")
        location = get("location_raw")
        remote_flag = get("remote_flag")

        return RawItem(
            external_id=str(external_id),
            title=str(title),
            company_name=(str(get("company_name")) if get("company_name") else None),
            company_slug=(str(get("company_slug")) if get("company_slug") else None),
            apply_url=get("apply_url") or url,
            url=url,
            posted_at=parse_date(get("posted_at")),
            updated_at=parse_date(get("updated_at")),
            expires_at=parse_date(get("expires_at")),
            comp_min=to_float(get("comp_min")),
            comp_max=to_float(get("comp_max")),
            comp_currency=(str(get("comp_currency")) if get("comp_currency") else None),
            comp_period=(str(get("comp_period")) if get("comp_period") else None),
            seniority=(str(get("seniority")) if get("seniority") else None),
            employment_type=_first_str(get("employment_type")),
            location_raw=_first_str(location),
            countries_allowed=as_list(get("countries_allowed")),
            timezones_allowed=as_list(get("timezones_allowed")),
            remote_policy=infer_remote_policy(remote_flag, location, title),
            body=html_to_text(body_raw) if isinstance(body_raw, str) else None,
            tags=as_list(get("tags")),
            raw_extra={"source_family": self.family},
        )


def _first_str(value) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value if v) or None
    return str(value)


class RssConnector(Connector):
    """RSS / Atom feeds. Used for We Work Remotely category feeds."""

    family = "rss_feed"

    def fetch(self) -> FetchResult:
        url = self.params["url"]
        resp = self.get(url, accept="application/rss+xml, application/xml, text/xml")
        parsed = feedparser.parse(resp.content)
        items = []
        for entry in parsed.entries:
            link = entry.get("link")
            if not link:
                continue
            title_raw = entry.get("title") or ""
            company, title = _split_wwr_title(title_raw)
            body = html_to_text(
                entry.get("summary") or (entry.get("content") or [{}])[0].get("value")
            )
            items.append(RawItem(
                external_id=str(entry.get("id") or link),
                title=title,
                company_name=company,
                apply_url=link,
                url=link,
                posted_at=parse_date(entry.get("published") or entry.get("updated")),
                location_raw=entry.get("region") or entry.get("location"),
                remote_policy=infer_remote_policy("remote", title_raw, body[:400]),
                body=body,
                tags=as_list([t.get("term") for t in entry.get("tags") or []]),
                raw_extra={"feed": parsed.feed.get("title")},
            ))
        return FetchResult(items, self._bytes, self._status)


_WWR_TITLE = re.compile(r"^(?P<company>.+?)\s*:\s*(?P<title>.+)$")


def _split_wwr_title(raw: str) -> tuple[str | None, str]:
    """We Work Remotely encodes "Company: Role" in the RSS title."""
    m = _WWR_TITLE.match(raw.strip())
    if m:
        return m.group("company").strip(), m.group("title").strip()
    return None, raw.strip()


class HnAlgoliaConnector(Connector):
    """Hacker News 'Who is hiring?' threads via the free Algolia API.

    Roughly 260 real ads a month, written by the hiring engineers themselves.
    The thread rules ban recruiters and job boards, which removes the structural
    incentive to post a job that does not exist.

    Each top-level comment is one ad. They are free-form prose, so the title is
    parsed heuristically here and the LLM judge does the real reading later.
    """

    family = "hn_algolia"

    SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
    ITEM = "https://hn.algolia.com/api/v1/items/{id}"

    def fetch(self) -> FetchResult:
        author = self.params.get("author", "whoishiring")
        needle = (self.params.get("thread_query") or "who is hiring").lower()
        max_threads = int(self.params.get("max_threads", 2))
        max_comments = int(self.params.get("max_comments", 400))

        search_url = f"{self.SEARCH}?tags=story,author_{author}&hitsPerPage=10"
        hits = self.get(search_url).json().get("hits", [])
        threads = [h for h in hits if needle in (h.get("title") or "").lower()][:max_threads]

        items: list[RawItem] = []
        for thread in threads:
            thread_id = thread.get("objectID")
            if not thread_id:
                continue
            data = self.get(self.ITEM.format(id=thread_id)).json()
            posted_default = parse_date(data.get("created_at"))
            for child in (data.get("children") or [])[:max_comments]:
                item = self._parse_comment(child, thread_id, posted_default)
                if item:
                    items.append(item)
        return FetchResult(items, self._bytes, self._status)

    def _parse_comment(self, child: dict, thread_id: str, fallback_dt) -> RawItem | None:
        if child.get("type") != "comment" or not child.get("text"):
            return None
        text = html_to_text(child["text"])
        if len(text) < 120:
            return None  # replies and chatter, not ads
        company, title = _split_hn_headline(text)
        url = _first_url(text)
        return RawItem(
            external_id=f"hn:{child.get('id')}",
            title=title[:300] or "HN posting",
            company_name=company,
            apply_url=url or f"https://news.ycombinator.com/item?id={child.get('id')}",
            url=f"https://news.ycombinator.com/item?id={child.get('id')}",
            posted_at=parse_date(child.get("created_at")) or fallback_dt,
            remote_policy=infer_remote_policy(text[:600]),
            body=text,
            raw_extra={"thread_id": thread_id, "channel": "hn_whoishiring"},
        )


_HN_SEP = re.compile(r"\s*[\|–—]\s*|\s+-\s+")
_URL = re.compile(r"https?://[^\s<>\"')]+")


def _split_hn_headline(text: str) -> tuple[str | None, str]:
    """HN ads conventionally start "Company | Role | Location | Remote".

    Not all of them do. A first line that is a paragraph of prose is a company
    blurb, not a headline, so fall back to the first few words rather than
    storing an essay as the job title.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines[:3]:
        parts = [p.strip() for p in _HN_SEP.split(line) if p.strip()]
        if len(parts) >= 2 and len(parts[0]) <= 80:
            return parts[0][:200], " | ".join(parts[1:3])[:200]
    first = lines[0] if lines else ""
    if len(first) > 120:
        first = " ".join(first.split()[:14])
    return None, first[:200]


def _first_url(text: str) -> str | None:
    m = _URL.search(text)
    return m.group(0).rstrip(".,);") if m else None
