from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class PostAiSummary:
    post_id: int
    category: str | None
    topics: list[str] = field(default_factory=list)
    summary: str | None = None
    anomaly_note: str | None = None


@dataclass(frozen=True)
class DigestView:
    channel_id: int
    period_start: datetime
    period_end: datetime
    bullets: list[str] = field(default_factory=list)
