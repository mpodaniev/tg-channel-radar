from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class ParsedPost:
    message_id: int
    posted_at: datetime | None
    text: str | None
    has_media: bool
    media_type: str | None
    link_preview_url: str | None
    views: int | None
    forwards: int | None
    reactions_total: int | None
    reactions: dict[str, int] | None


@dataclass(frozen=True)
class ParsedChannel:
    username: str
    title: str | None
    description: str | None
    avatar_url: str | None
    subscribers: int | None
    photos_count: int | None
    videos_count: int | None
    links_count: int | None
    posts: list[ParsedPost] = field(default_factory=list)
    next_before: int | None = None
