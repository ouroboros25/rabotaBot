from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin


class Company(Base, TimestampMixin):
    __tablename__ = "company"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Normalised join key: lowercase, legal suffix stripped, punctuation collapsed.
    company_key: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    domain: Mapped[str | None] = mapped_column(String(255))
    hq_country: Mapped[str | None] = mapped_column(String(2))
    headcount_band: Mapped[str | None] = mapped_column(String(32))
    blocklisted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)

    ats_slugs: Mapped[list["CompanyAtsSlug"]] = relationship(back_populates="company")


class CompanyAtsSlug(Base):
    """Harvested mapping of company -> ATS board.

    Slug discovery is the real engineering problem, not access: Ashby cannot be
    searched, Workable 404s identically on a bad slug and a disabled board. The
    fix is parasitic: every aggregator hands us apply URLs that point straight at
    boards.greenhouse.io/x, jobs.lever.co/y, jobs.ashbyhq.com/z. We regex those
    out of data we already fetched, for free.
    """

    __tablename__ = "company_ats_slug"
    __table_args__ = (UniqueConstraint("ats", "slug", name="uq_ats_slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    company_id: Mapped[int | None] = mapped_column(ForeignKey("company.id", ondelete="SET NULL"))
    ats: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(160), nullable=False)
    discovered_from: Mapped[str | None] = mapped_column(String(80))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    jobs_last_seen: Mapped[int | None] = mapped_column(Integer)
    promoted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    company: Mapped[Company | None] = relationship(back_populates="ats_slugs")
