from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum


class SourceHealth(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class AnomalyDirection(StrEnum):
    SPIKE = "spike"
    DROP = "drop"


@dataclass(frozen=True)
class Growth:
    absolute: int
    percent: float | None


@dataclass(frozen=True)
class TrendPoint:
    captured_at: datetime
    value: int


@dataclass(frozen=True)
class DailyPoint:
    day: date
    posts: int
    views: int | None


@dataclass(frozen=True)
class Anomaly:
    index: int
    score: float
    direction: AnomalyDirection


@dataclass(frozen=True)
class PostSummary:
    post_id: int
    message_id: int
    posted_at: datetime | None
    text_preview: str | None
    has_media: bool
    media_type: str | None
    views: int | None
    forwards: int | None
    reactions_total: int | None
    reaction_rate: float | None
    forward_rate: float | None
    anomaly: Anomaly | None


@dataclass(frozen=True)
class ChannelOverview:
    channel_id: int
    username: str
    title: str | None
    status: str
    health: SourceHealth
    subscribers: int | None
    subscribers_growth: Growth | None
    posts_total: int
    posts_in_period: int
    avg_views: float | None
    median_views: float | None
    avg_reaction_rate: float | None
    avg_forward_rate: float | None
    last_fetch_at: datetime | None
    consecutive_failures: int


@dataclass(frozen=True)
class ChannelAnalytics:
    overview: ChannelOverview
    period_days: int
    subscribers_trend: list[TrendPoint] = field(default_factory=list)
    daily: list[DailyPoint] = field(default_factory=list)
    posts: list[PostSummary] = field(default_factory=list)


@dataclass(frozen=True)
class PostAnalytics:
    post: PostSummary
    channel_username: str
    growth: list[TrendPoint] = field(default_factory=list)
