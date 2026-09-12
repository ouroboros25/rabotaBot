"""Loaders for config/*.yaml with light schema validation.

A malformed source row must fail loudly at boot, not silently ingest nothing
for three weeks.
"""
from __future__ import annotations

import functools
from pathlib import Path
from typing import Any

import yaml

from app.config import CONFIG_DIR

KNOWN_FAMILIES = {
    "greenhouse_board",
    "lever_postings",
    "ashby_board",
    "workable_search",
    "json_api_generic",
    "rss_feed",
    "hn_algolia",
}


def _read(name: str) -> dict[str, Any]:
    path = Path(CONFIG_DIR) / name
    if not path.exists():
        raise FileNotFoundError(f"config file missing: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@functools.lru_cache
def load_rubric() -> dict[str, Any]:
    return _read("rubric.yaml")


@functools.lru_cache
def load_profile_seed() -> dict[str, Any]:
    return _read("profile.example.yaml")


def load_sources() -> list[dict[str, Any]]:
    raw = _read("sources.yaml")
    defaults = raw.get("defaults") or {}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw.get("sources") or []:
        key = row.get("key")
        family = row.get("family")
        if not key:
            raise ValueError(f"source without key: {row!r}")
        if key in seen:
            raise ValueError(f"duplicate source key: {key}")
        if family not in KNOWN_FAMILIES:
            raise ValueError(f"source {key}: unknown family {family!r}")
        tier = int(row.get("legal_tier", 0))
        if tier >= 4:
            raise ValueError(
                f"source {key}: legal_tier 4 is forbidden by design and must not be configured"
            )
        seen.add(key)
        merged = {**defaults, **row}
        merged["legal_tier"] = tier
        merged.setdefault("tracks", ["fte"])
        merged.setdefault("params", {})
        merged.setdefault("field_map", {})
        merged.setdefault("company", {})
        out.append(merged)
    return out


def reset_caches() -> None:
    load_rubric.cache_clear()
    load_profile_seed.cache_clear()
