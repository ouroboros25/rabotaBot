from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Application(Base, TimestampMixin):
    __tablename__ = "application"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(
        ForeignKey("job_cluster.id", ondelete="CASCADE"), index=True
    )
    draft_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("draft_version.id", ondelete="SET NULL")
    )
    track: Mapped[str] = mapped_column(String(16), nullable=False)
    channel: Mapped[str] = mapped_column(String(24), default="ats_form", nullable=False)
    # ats_form | email | upwork | referral | platform_alert

    stage: Mapped[str] = mapped_column(String(24), default="approved", nullable=False, index=True)
    stage_entered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    rate_quoted: Mapped[float | None] = mapped_column(Float)
    follow_up_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    follow_up_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    presumed_dead_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome: Mapped[str | None] = mapped_column(String(32))
    outcome_detail: Mapped[str | None] = mapped_column(Text)

    replied: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    hours_to_reply: Mapped[float | None] = mapped_column(Float)

    events: Mapped[list["ApplicationEvent"]] = relationship(
        back_populates="application", cascade="all, delete-orphan",
        order_by="ApplicationEvent.at",
    )


class ApplicationEvent(Base):
    """Immutable transition log.

    Nothing is mutated in place. This is the audit trail and the training set at
    the same time: every human Skip with a reason code and every instant knockout
    is a labelled example the rule miner can use.
    """

    __tablename__ = "application_event"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(
        ForeignKey("application.id", ondelete="CASCADE"), index=True
    )
    from_stage: Mapped[str | None] = mapped_column(String(24))
    to_stage: Mapped[str] = mapped_column(String(24), nullable=False)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)  # auto | human
    reason_code: Mapped[str | None] = mapped_column(String(32))
    note: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSONB)

    application: Mapped[Application] = relationship(back_populates="events")


class SkipFeedback(Base):
    """A human 'skip' on a surfaced card, with a one-tap reason.

    Three occurrences of the same reason with a common extractable pattern become
    a proposed hard gate in the Sunday retro. Most real improvement comes from
    here, because gate misses are systematic while score drift is noise.
    """

    __tablename__ = "skip_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cluster_id: Mapped[int] = mapped_column(ForeignKey("job_cluster.id", ondelete="CASCADE"))
    reason_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
