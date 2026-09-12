from typing import Final

PROMPT_VERSION: Final = "v1"

CLASSIFICATION_SCHEMA: Final[dict] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "post_id": {"type": "integer"},
            "category": {"type": "string"},
            "topics": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["post_id", "category", "topics"],
    },
}

DIGEST_SCHEMA: Final[dict] = {
    "type": "object",
    "properties": {
        "bullets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["bullets"],
}

ANOMALY_SCHEMA: Final[dict] = {
    "type": "object",
    "properties": {
        "note": {"type": "string"},
    },
    "required": ["note"],
}


def build_classification_prompt(posts: list[tuple[int, str]]) -> str:
    lines = [f"- post_id={post_id}: {text}" for post_id, text in posts]
    joined = "\n".join(lines)
    return (
        "You are classifying Telegram channel posts. For each post below, pick one short "
        "category (a single word or short phrase, e.g. 'news', 'tech', 'humor') and 1-3 topic "
        "tags. Respond with a JSON array with one entry per post_id, preserving every post_id "
        "given.\n\nPosts:\n" + joined
    )


def build_digest_prompt(channel_title: str | None, posts: list[str]) -> str:
    joined = "\n".join(f"- {text}" for text in posts)
    title = channel_title or "this channel"
    return (
        f"Summarize what {title} posted about over this period into 3-5 short bullet points "
        "(plain sentences, no markdown formatting beyond the bullets themselves). Base the "
        "summary only on the posts below.\n\nPosts:\n" + joined
    )


def build_anomaly_prompt(text: str, direction: str, score: float) -> str:
    return (
        f"This post's metrics showed a statistical {direction} (z-score {score:.2f}) compared "
        "to the channel's recent posts. Write one short, human-readable sentence explaining a "
        "plausible reason, based only on the post text below.\n\nPost:\n" + text
    )
