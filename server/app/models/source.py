from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, SmallInteger, String, Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Source(Base, TimestampMixin):
    """One row per configured source. Mirrors a block of config/sources.yaml."""

    __tablename__ = "source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(80), unique=True, nullable=False, index=True)
    family: Mapped[str] = mapped_column(String(40), nullable=False)
    legal_tier: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    tracks: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    field_map: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    company_hint: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    cadence_minutes: Mapped[int] = mapped_column(Integer, default=180, nullable=False)
    rate_limit_rps: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Auto-discovered sources (harvested ATS slugs) are kept apart from the ones
    # the user declared, so a bad harvest never silently rewrites the registry.
    auto_discovered: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    disabled_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    runs: Mapped[list["SourceRun"]] = relationship(back_populates="source")


class SourceRun(Base):
    __tablename__ = "source_run"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    items_seen: Mapped[int] = mapped_column(Integer, default=0)
    items_new: Mapped[int] = mapped_column(Integer, default=0)
    items_changed: Mapped[int] = mapped_column(Integer, default=0)
    bytes_fetched: Mapped[int] = mapped_column(Integer, default=0)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    schema_drift_note: Mapped[str | None] = mapped_column(Text)

    source: Mapped[Source] = relationship(back_populates="runs")


class RawDocument(Base):
    """Verbatim payload, kept before parsing.

    Sources change schema without warning (Himalayas announces breaking changes
    inside an in-band ``comments`` field). Keeping the raw bytes means a schema
    surprise costs a reparse, not a lost month of postings.
    """

    __tablename__ = "raw_document"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    source_run_id: Mapped[int | None] = mapped_column(ForeignKey("source_run.id", ondelete="SET NULL"))
    external_id: Mapped[str | None] = mapped_column(String(255))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    body: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
