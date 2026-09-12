from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class JobCluster(Base, TimestampMixin):
    """One real-world opening, however many boards it appears on.

    ``fan_out`` is deliberately a NEGATIVE feature downstream. A posting that
    every scanner found is, by that fact, a more crowded auction: measured reply
    rate falls from 9.44% at one automated bidder to 2.11% at eleven or more.
    """

    __tablename__ = "job_cluster"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    canonical_posting_id: Mapped[int | None] = mapped_column(Integer)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("company.id", ondelete="SET NULL"))
    title_key: Mapped[str] = mapped_column(String(255), nullable=False)
    country_code: Mapped[str | None] = mapped_column(String(8))
    fan_out: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # A description hash that reappears for the same company months later is the
    # strongest cheap ghost-job tell available.
    recurrence_months: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tracks: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="new", nullable=False, index=True)
    # new | gated | scored | queued | drafted | actioned | archived

    postings: Mapped[list["JobPosting"]] = relationship(back_populates="cluster")


class JobPosting(Base, TimestampMixin):
    __tablename__ = "job_posting"
    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_source_external"),
        Index("ix_posting_apply_hash", "apply_url_hash"),
        Index("ix_posting_desc_hash", "desc_hash"),
        Index("ix_posting_first_published", "first_published_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    cluster_id: Mapped[int | None] = mapped_column(
        ForeignKey("job_cluster.id", ondelete="SET NULL"), index=True
    )
    company_id: Mapped[int | None] = mapped_column(ForeignKey("company.id", ondelete="SET NULL"))

    title: Mapped[str] = mapped_column(String(400), nullable=False)
    title_key: Mapped[str] = mapped_column(String(400), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(255))
    body_text: Mapped[str | None] = mapped_column(Text)
    desc_hash: Mapped[str | None] = mapped_column(String(64))

    apply_url: Mapped[str | None] = mapped_column(Text)
    apply_url_canonical: Mapped[str | None] = mapped_column(Text)
    apply_url_hash: Mapped[str | None] = mapped_column(String(64))

    employment_type: Mapped[str | None] = mapped_column(String(64))
    seniority: Mapped[str | None] = mapped_column(String(64))
    location_raw: Mapped[str | None] = mapped_column(Text)
    countries_allowed: Mapped[list | None] = mapped_column(ARRAY(String(64)))
    timezones_allowed: Mapped[list | None] = mapped_column(ARRAY(String(64)))
    remote_policy: Mapped[str] = mapped_column(String(24), default="unknown", nullable=False)
    # global | geo_fenced | hybrid | onsite | unknown

    comp_min: Mapped[float | None] = mapped_column(Float)
    comp_max: Mapped[float | None] = mapped_column(Float)
    comp_currency: Mapped[str | None] = mapped_column(String(8))
    comp_period: Mapped[str | None] = mapped_column(String(16))

    first_published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at_source: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    tags: Mapped[list | None] = mapped_column(JSONB)
    # Some ATSs hand us the application form's free-text questions in advance
    # (Recruitee open_questions, Greenhouse ?questions=true). Pre-answering them
    # is the highest-leverage friction we can absorb for the user.
    form_questions: Mapped[list | None] = mapped_column(JSONB)
    raw_extra: Mapped[dict | None] = mapped_column(JSONB)

    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    liveness_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    liveness_ok: Mapped[bool | None] = mapped_column(Boolean)

    cluster: Mapped[JobCluster | None] = relationship(back_populates="postings")
    flags: Mapped[list["EligibilityFlag"]] = relationship(
        back_populates="posting", cascade="all, delete-orphan"
    )


class EligibilityFlag(Base):
    """A hard-gate rejection, with the exact span that triggered it.

    Storing the matched span is what makes a rejection auditable and what lets the
    weekly retro propose a better rule instead of a vaguer one.
    """

    __tablename__ = "eligibility_flag"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_posting_id: Mapped[int] = mapped_column(
        ForeignKey("job_posting.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    severity: Mapped[str] = mapped_column(String(16), default="hard", nullable=False)
    matched_pattern: Mapped[str | None] = mapped_column(Text)
    matched_span: Mapped[str | None] = mapped_column(Text)

    posting: Mapped[JobPosting] = relationship(back_populates="flags")
