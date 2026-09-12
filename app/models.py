from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    desc,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ChannelStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    ERROR = "error"
    NOT_FOUND = "not_found"
    PRIVATE = "private"


class Channel(Base):
    __tablename__ = "channels"
    __table_args__ = (
        CheckConstraint(
            f"status IN {tuple(s.value for s in ChannelStatus)}",
            name="ck_channels_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)
    avatar_url: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ChannelStatus.PENDING.value
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_fetch_status: Mapped[str | None] = mapped_column(String(16))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    posts: Mapped[list["Post"]] = relationship(
        back_populates="channel", lazy="raise", cascade="all, delete-orphan"
    )


class ChannelSnapshot(Base):
    __tablename__ = "channel_snapshots"
    __table_args__ = (
        Index("ix_channel_snapshots_channel_captured", "channel_id", desc("captured_at")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    subscribers: Mapped[int | None] = mapped_column(Integer)
    photos_count: Mapped[int | None] = mapped_column(Integer)
    videos_count: Mapped[int | None] = mapped_column(Integer)
    links_count: Mapped[int | None] = mapped_column(Integer)


class Post(Base):
    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint("channel_id", "message_id", name="uq_posts_channel_message"),
        Index("ix_posts_channel_posted", "channel_id", desc("posted_at")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    text: Mapped[str | None] = mapped_column(Text)
    has_media: Mapped[bool] = mapped_column(nullable=False, default=False)
    media_type: Mapped[str | None] = mapped_column(String(32))
    link_preview_url: Mapped[str | None] = mapped_column(Text)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    content_hash: Mapped[str | None] = mapped_column(String(64))

    channel: Mapped[Channel] = relationship(back_populates="posts", lazy="raise")
    metric_snapshots: Mapped[list["PostMetricSnapshot"]] = relationship(
        back_populates="post", lazy="raise", cascade="all, delete-orphan"
    )


class PostMetricSnapshot(Base):
    __tablename__ = "post_metric_snapshots"
    __table_args__ = (
        Index("ix_post_metric_snapshots_post_captured", "post_id", desc("captured_at")),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    views: Mapped[int | None] = mapped_column(Integer)
    forwards: Mapped[int | None] = mapped_column(Integer)
    reactions_total: Mapped[int | None] = mapped_column(Integer)
    reactions_json: Mapped[dict | None] = mapped_column(JSONB)

    post: Mapped[Post] = relationship(back_populates="metric_snapshots", lazy="raise")


class PostAiAnnotation(Base):
    __tablename__ = "post_ai_annotations"

    id: Mapped[int] = mapped_column(primary_key=True)
    post_id: Mapped[int] = mapped_column(
        ForeignKey("posts.id", ondelete="CASCADE"), unique=True, nullable=False
    )
    category: Mapped[str | None] = mapped_column(String(64))
    topics_json: Mapped[list | None] = mapped_column(JSONB)
    summary: Mapped[str | None] = mapped_column(Text)
    anomaly_note: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ChannelDigest(Base):
    __tablename__ = "channel_digests"
    __table_args__ = (
        UniqueConstraint(
            "channel_id",
            "period_start",
            "period_end",
            "prompt_version",
            name="uq_channel_digests_period",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), nullable=False
    )
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    digest_md: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
