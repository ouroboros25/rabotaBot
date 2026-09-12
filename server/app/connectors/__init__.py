"""Connector registry.

Seven families cover every source in v1. Adding a source is a YAML row; adding a
protocol is one class implementing ``fetch``.
"""
from __future__ import annotations

from app.connectors.aggregators import (
    HnAlgoliaConnector, JsonApiConnector, RssConnector,
)
from app.connectors.ats import (
    AshbyConnector, GreenhouseConnector, LeverConnector, WorkableSearchConnector,
)
from app.connectors.base import Connector, FetchResult, RawItem

REGISTRY: dict[str, type[Connector]] = {
    GreenhouseConnector.family: GreenhouseConnector,
    LeverConnector.family: LeverConnector,
    AshbyConnector.family: AshbyConnector,
    WorkableSearchConnector.family: WorkableSearchConnector,
    JsonApiConnector.family: JsonApiConnector,
    RssConnector.family: RssConnector,
    HnAlgoliaConnector.family: HnAlgoliaConnector,
}


def build(source) -> Connector:
    cls = REGISTRY.get(source.family)
    if cls is None:
        raise ValueError(f"unknown connector family: {source.family}")
    return cls(source)


__all__ = ["REGISTRY", "build", "Connector", "FetchResult", "RawItem"]
