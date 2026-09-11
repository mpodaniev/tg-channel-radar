import re
from datetime import UTC, datetime

from selectolax.parser import HTMLParser, Node

from app.parser.errors import InvalidUsernameError
from app.parser.types import ParsedChannel, ParsedPost

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{4,32}$")
_USERNAME_STRIP_RE = re.compile(r"^(?:https?://)?(?:t\.me|telegram\.me)/(?:s/)?", re.IGNORECASE)
_COUNT_RE = re.compile(r"^([\d,.]+)\s*([KMB]?)$", re.IGNORECASE)
_COUNT_MULTIPLIERS = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}

_SEL_HEADER_TITLE = ".tgme_channel_info_header_title"
_SEL_DESCRIPTION = ".tgme_channel_info_description"
_SEL_AVATAR = ".tgme_page_photo_image img, .tgme_channel_info_header_photo img"
_SEL_COUNTERS = ".tgme_channel_info_counter"
_SEL_COUNTER_VALUE = ".counter_value"
_SEL_COUNTER_TYPE = ".counter_type"
_SEL_MESSAGE = ".tgme_widget_message[data-post]"
_SEL_DATE = ".tgme_widget_message_date time[datetime]"
_SEL_TEXT = ".tgme_widget_message_text"
_SEL_VIEWS = ".tgme_widget_message_views"
_SEL_LINK_PREVIEW = ".tgme_widget_message_link_preview"
_SEL_REACTIONS = ".tgme_widget_message_reactions"
_SEL_REACTION = ".tgme_reaction"
_SEL_LOAD_MORE = ".tme_messages_more[data-before]"

_MEDIA_SELECTORS = (
    ("photo", ".tgme_widget_message_photo_wrap"),
    ("video", ".tgme_widget_message_video_player, .tgme_widget_message_roundvideo_player"),
    ("document", ".tgme_widget_message_document"),
    ("voice", ".tgme_widget_message_voice_player"),
    ("sticker", ".tgme_widget_message_sticker"),
    ("poll", ".tgme_widget_message_poll"),
)


def normalize_username(raw: str) -> str:
    stripped = _USERNAME_STRIP_RE.sub("", raw.strip()).lstrip("@").rstrip("/")
    if not _USERNAME_RE.match(stripped):
        raise InvalidUsernameError(f"invalid channel username: {raw!r}")
    return stripped.lower()


def parse_count(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = raw.strip().replace(",", "")
    if not text:
        return None
    match = _COUNT_RE.match(text)
    if not match:
        return None
    number, suffix = match.groups()
    return int(float(number) * _COUNT_MULTIPLIERS[suffix.upper()])


def _parse_reactions(node: Node) -> dict[str, int] | None:
    block = node.css_first(_SEL_REACTIONS)
    if block is None:
        return None

    reactions: dict[str, int] = {}
    for reaction in block.css(_SEL_REACTION):
        raw_text = reaction.text(deep=True).strip()
        match = re.search(r"([\d.,]+[KMB]?)$", raw_text, re.IGNORECASE)
        if match is None:
            continue
        count = parse_count(match.group(1))
        if count is None:
            continue
        label = raw_text[: match.start()].strip()
        classes = reaction.attributes.get("class") or ""
        if "tgme_reaction_paid" in classes:
            key = "stars"
        elif label:
            key = label
        else:
            emoji_el = reaction.css_first("tg-emoji")
            emoji_id = emoji_el.attributes.get("emoji-id") if emoji_el is not None else None
            key = f"emoji:{emoji_id}" if emoji_id else "unknown"
        reactions[key] = reactions.get(key, 0) + count

    return reactions or None


def _detect_media(node: Node) -> tuple[bool, str | None]:
    for media_type, selector in _MEDIA_SELECTORS:
        if node.css_first(selector) is not None:
            return True, media_type
    return False, None


def _parse_post(node: Node) -> ParsedPost | None:
    data_post = node.attributes.get("data-post")
    if not data_post or "/" not in data_post:
        return None
    message_id = int(data_post.rsplit("/", 1)[1])

    time_el = node.css_first(_SEL_DATE)
    posted_at = None
    if time_el is not None:
        raw_datetime = time_el.attributes.get("datetime")
        if raw_datetime:
            posted_at = datetime.fromisoformat(raw_datetime).astimezone(UTC)

    text_el = node.css_first(_SEL_TEXT)
    text = text_el.text(deep=True, separator="\n").strip() if text_el is not None else None

    has_media, media_type = _detect_media(node)

    link_preview_el = node.css_first(_SEL_LINK_PREVIEW)
    link_preview_url = (
        link_preview_el.attributes.get("href") if link_preview_el is not None else None
    )

    views_el = node.css_first(_SEL_VIEWS)
    views = parse_count(views_el.text()) if views_el is not None else None

    # The anonymous web preview never exposes a forward count; distinct from `0`,
    # `None` here means "not measured", which matters for snapshot dedup in ingest.
    forwards = None

    reactions = _parse_reactions(node)
    reactions_total = sum(reactions.values()) if reactions else None

    return ParsedPost(
        message_id=message_id,
        posted_at=posted_at,
        text=text or None,
        has_media=has_media,
        media_type=media_type,
        link_preview_url=link_preview_url,
        views=views,
        forwards=forwards,
        reactions_total=reactions_total,
        reactions=reactions,
    )


def _parse_counters(tree: HTMLParser) -> dict[str, int]:
    counters: dict[str, int] = {}
    for counter in tree.css(_SEL_COUNTERS):
        value_el = counter.css_first(_SEL_COUNTER_VALUE)
        type_el = counter.css_first(_SEL_COUNTER_TYPE)
        if value_el is None or type_el is None:
            continue
        value = parse_count(value_el.text())
        if value is not None:
            counters[type_el.text().strip().lower()] = value
    return counters


def parse_channel_page(html: str, *, username: str) -> ParsedChannel:
    tree = HTMLParser(html)

    title_el = tree.css_first(_SEL_HEADER_TITLE)
    description_el = tree.css_first(_SEL_DESCRIPTION)
    avatar_el = tree.css_first(_SEL_AVATAR)

    counters = _parse_counters(tree)

    posts = [post for node in tree.css(_SEL_MESSAGE) if (post := _parse_post(node)) is not None]

    load_more_el = tree.css_first(_SEL_LOAD_MORE)
    next_before = None
    if load_more_el is not None:
        raw_before = load_more_el.attributes.get("data-before")
        next_before = int(raw_before) if raw_before else None

    return ParsedChannel(
        username=username,
        title=title_el.text().strip() if title_el is not None else None,
        description=description_el.text().strip() if description_el is not None else None,
        avatar_url=avatar_el.attributes.get("src") if avatar_el is not None else None,
        subscribers=counters.get("subscribers"),
        photos_count=counters.get("photos"),
        videos_count=counters.get("videos"),
        links_count=counters.get("links"),
        posts=posts,
        next_before=next_before,
    )
