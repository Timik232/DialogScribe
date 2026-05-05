import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, Column, DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gigaam_transcriber.database import Base


def _utcnow():
    return datetime.utcnow()


def _uuid():
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    username: Mapped[str] = mapped_column(String(30), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(10), nullable=False, default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    approved_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, default=None)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    reset_token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    reset_token_expires: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    limits = relationship("UserLimit", back_populates="user", lazy="selectin")
    approver = relationship("User", remote_side=lambda: [User.id], foreign_keys=[approved_by])


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    emoji: Mapped[str | None] = mapped_column(String(10), nullable=True, default="")
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    user_prompt_template: Mapped[str | None] = mapped_column(Text, nullable=True, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_templates_user_key"),
    )

    user = relationship("User", backref="templates")


class UsageEvent(Base):
    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    value: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, nullable=True, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, index=True)


class UserLimit(Base):
    __tablename__ = "user_limits"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    limit_type: Mapped[str] = mapped_column(String(50), nullable=False)
    max_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    period: Mapped[str] = mapped_column(String(20), nullable=False, default="monthly")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    user = relationship("User", back_populates="limits")


class SavedTranscription(Base):
    __tablename__ = "saved_transcriptions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    full_text: Mapped[str] = mapped_column(Text, nullable=False)
    analysis_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    segments_json: Mapped[dict] = mapped_column("segments_json", JSON, nullable=True, default=dict)
    speaker_names: Mapped[dict] = mapped_column("speaker_names", JSON, nullable=True, default=dict)
    duration: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    language: Mapped[str] = mapped_column(String(10), nullable=False, default="ru")
    share_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True, default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)

    user = relationship("User", backref="saved_transcriptions")
