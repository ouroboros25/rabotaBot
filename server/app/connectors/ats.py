"""Company-owned ATS boards.

These are publisher-intended machine interfaces: no auth, no ToS friction, no
ban risk, and stable precisely because companies WANT them read. Every endpoint
here was probed live on 2026-09-12.
"""
from __future__ import annotations

import logging

from app.connectors.base import (
    Connector, FetchResult, RawItem, as_list, infer_remote_policy, parse_date, to_float,
)
from app.services.text import html_to_text

logger = logging.getLogger(__name__)


class GreenhouseConnector(Connector):
    """boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

    first_published + updated_at together are the ghost-job filter: a posting
    older than 30 days whose updated_at never moved is the cheapest reliable tell.
    """

    family = "greenhouse_board"

    def fetch(self) -> FetchResult:
        token = self.params["board_token"]
        url = (
            f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
            "?content=true&pay_transparency=true"
        )
        data = self.get(url).json()
        company = (self.source.company_hint or {}).get("name") or token
        items = []
        for job in data.get("jobs", []):
            pay = job.get("pay_input_ranges") or []
            comp_min = to_float(pay[0].get("min_cents")) if pay else None
            comp_max = to_float(pay[0].get("max_cents")) if pay else None
            location = (job.get("location") or {}).get("name")
            items.append(RawItem(
                external_id=str(job.get("id")),
                title=job.get("title") or "",
                company_name=job.get("company_name") or company,
                company_slug=token,
                apply_url=job.get("absolute_url"),
                url=job.get("absolute_url"),
                posted_at=parse_date(job.get("first_published")),
                updated_at=parse_date(job.get("updated_at")),
                location_raw=location,
                remote_policy=infer_remote_policy(location, job.get("title")),
                comp_min=comp_min / 100 if comp_min else None,
                comp_max=comp_max / 100 if comp_max else None,
                comp_currency=(pay[0].get("currency_type") if pay else None),
                comp_period="year" if pay else None,
                body=html_to_text(job.get("content")),
                raw_extra={"departments": [d.get("name") for d in job.get("departments") or []],
                           "ats": "greenhouse"},
            ))
        return FetchResult(items, self._bytes, self._status)


class LeverConnector(Connector):
    """api.lever.co/v0/postings/{site}?mode=json (EU tenants on api.eu.lever.co)."""

    family = "lever_postings"

    def fetch(self) -> FetchResult:
        site = self.params["site"]
        host = "api.eu.lever.co" if self.params.get("eu") else "api.lever.co"
        data = self.get(f"https://{host}/v0/postings/{site}?mode=json").json()
        company = (self.source.company_hint or {}).get("name") or site
        items = []
        for job in data if isinstance(data, list) else []:
            cat = job.get("categories") or {}
            salary = job.get("salaryRange") or {}
            body = job.get("descriptionPlain") or html_to_text(job.get("description"))
            lists = job.get("lists") or []
            if lists:
                body += "\n\n" + "\n".join(
                    f"{lst.get('text', '')}: {html_to_text(lst.get('content'))}"
                    for lst in lists
                )
            items.append(RawItem(
                external_id=str(job.get("id")),
                title=job.get("text") or "",
                company_name=company,
                company_slug=site,
                apply_url=job.get("applyUrl") or job.get("hostedUrl"),
                url=job.get("hostedUrl"),
                posted_at=parse_date(job.get("createdAt")),
                location_raw=cat.get("location"),
                employment_type=cat.get("commitment"),
                seniority=cat.get("level"),
                remote_policy=infer_remote_policy(
                    job.get("workplaceType"), cat.get("location"), job.get("text")
                ),
                comp_min=to_float(salary.get("min")),
                comp_max=to_float(salary.get("max")),
                comp_currency=salary.get("currency"),
                comp_period=(salary.get("interval") or "").replace("per-", "") or None,
                countries_allowed=as_list(job.get("country")),
                body=body,
                raw_extra={"team": cat.get("team"), "ats": "lever"},
            ))
        return FetchResult(items, self._bytes, self._status)


class AshbyConnector(Connector):
    """api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true

    Over-indexes on well-funded startups and ships free salary bands. Cannot be
    filtered server-side, so we pull the whole board and let the gates do the work.
    """

    family = "ashby_board"

    def fetch(self) -> FetchResult:
        board = self.params["board"]
        url = (
            f"https://api.ashbyhq.com/posting-api/job-board/{board}"
            "?includeCompensation=true"
        )
        data = self.get(url).json()
        company = (self.source.company_hint or {}).get("name") or board
        items = []
        for job in data.get("jobs", []):
            if job.get("isListed") is False:
                continue
            comp = job.get("compensation") or {}
            summary = (comp.get("summaryComponents") or [{}])[0] if comp else {}
            secondary = job.get("secondaryLocations") or []
            countries = [
                loc.get("location") for loc in secondary if isinstance(loc, dict)
            ] or None
            items.append(RawItem(
                external_id=str(job.get("id")),
                title=job.get("title") or "",
                company_name=company,
                company_slug=board,
                apply_url=job.get("applyUrl") or job.get("jobUrl"),
                url=job.get("jobUrl"),
                posted_at=parse_date(job.get("publishedAt")),
                location_raw=job.get("location"),
                employment_type=job.get("employmentType"),
                remote_policy=(
                    "global" if job.get("isRemote")
                    else infer_remote_policy(job.get("workplaceType"), job.get("location"))
                ),
                comp_min=to_float(summary.get("minValue")),
                comp_max=to_float(summary.get("maxValue")),
                comp_currency=summary.get("currencyCode"),
                comp_period=(summary.get("interval") or "").replace("PER_", "").lower() or None,
                countries_allowed=as_list(countries),
                body=job.get("descriptionPlain") or html_to_text(job.get("descriptionHtml")),
                raw_extra={"team": job.get("team"), "department": job.get("department"),
                           "ats": "ashby"},
            ))
        return FetchResult(items, self._bytes, self._status)


class WorkableSearchConnector(Connector):
    """jobs.workable.com/api/v1/jobs - cross-tenant search, no auth, ~170k jobs.

    Gotcha worth keeping: unsupported filter parameters do not error, they
    silently zero totalSize. Do not add &remote=true or similar.
    """

    family = "workable_search"

    def fetch(self) -> FetchResult:
        # Queries come from the user's own keywords when they have any. A search
        # source whose query is hardcoded in YAML collects for someone else's
        # search: the tags have to reach the fetch, not only the filter.
        queries = self.params.get("_queries") or [
            self.params.get("query", "software engineer")
        ]
        max_pages = int(self.params.get("max_pages", 3))
        items: list[RawItem] = []
        drift = None

        for query in queries[:6]:
            items.extend(self._search(query, max_pages))
        return FetchResult(items, self._bytes, self._status, drift)

    def _search(self, query: str, max_pages: int) -> list[RawItem]:
        items: list[RawItem] = []
        token: str | None = None

        for page in range(max_pages):
            url = f"https://jobs.workable.com/api/v1/jobs?query={httpx_quote(query)}"
            if token:
                url += f"&pageToken={httpx_quote(token)}"
            data = self.get(url).json()
            for job in data.get("jobs", []):
                company = job.get("company") or {}
                loc = job.get("location") or {}
                items.append(RawItem(
                    external_id=str(job.get("id")),
                    title=job.get("title") or "",
                    company_name=company.get("name") if isinstance(company, dict) else str(company),
                    apply_url=job.get("url"),
                    url=job.get("url"),
                    posted_at=parse_date(job.get("created")),
                    updated_at=parse_date(job.get("updated")),
                    location_raw=", ".join(
                        str(v) for v in [loc.get("city"), loc.get("country")] if v
                    ) or None,
                    employment_type=job.get("employmentType"),
                    remote_policy=infer_remote_policy(job.get("workplace"), loc.get("city")),
                    body=html_to_text(job.get("description")),
                    raw_extra={"ats": "workable", "state": job.get("state")},
                ))
            token = data.get("nextPageToken")
            if not token:
                break
        return items


def httpx_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(str(value), safe="")
