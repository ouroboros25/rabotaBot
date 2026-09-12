from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Profile(Base, TimestampMixin):
    """Single-row table. One user, by design (see README: GDPR household exemption)."""

    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(120), default="", nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), default="Europe/Warsaw", nullable=False)
    countries_eligible: Mapped[list] = mapped_column(ARRAY(String(8)), default=list, nullable=False)
    min_overlap_hours: Mapped[int] = mapped_column(Integer, default=4, nullable=False)
    entity_status: Mapped[str] = mapped_column(String(32), default="individual", nullable=False)
    eor_ready: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    rate_floor_hourly: Mapped[float | None] = mapped_column(Float)
    rate_target_hourly: Mapped[float | None] = mapped_column(Float)
    comp_floor_annual: Mapped[float | None] = mapped_column(Float)
    comp_currency: Mapped[str] = mapped_column(String(8), default="USD", nullable=False)

    track_weights: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    weekly_send_cap: Mapped[int] = mapped_column(Integer, default=18, nullable=False)

    variants: Mapped[list["ProfileVariant"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )
    facts: Mapped[list["ProfileFact"]] = relationship(
        back_populates="profile", cascade="all, delete-orphan"
    )


class ProfileVariant(Base):
    """Per-track positioning. A cover letter for an equity role and one for a
    6-month contract are different products, not the same text with a swapped noun.
    """

    __tablename__ = "profile_variant"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"))
    track: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    headline: Mapped[str] = mapped_column(Text, default="", nullable=False)
    target_titles: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    must_have_skills: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    nice_to_have_skills: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    exclude_skills: Mapped[list] = mapped_column(JSONB, default=list, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="variants")


class ProfileFact(Base):
    """The fact ledger.

    The drafting engine may only select, order and rephrase these. It may not
    introduce a claim, number, company, tool or date that is not here or quoted
    from the job description. Enforced deterministically after generation, not
    requested politely in the prompt.
    """

    __tablename__ = "profile_fact"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[int] = mapped_column(ForeignKey("profile.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    type: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    verifiable_by: Mapped[str | None] = mapped_column(Text)
    evidence_url: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    profile: Mapped[Profile] = relationship(back_populates="facts")


class Setting(Base, TimestampMixin):
    """Small key/value store for runtime counters and UI-editable knobs."""

    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
