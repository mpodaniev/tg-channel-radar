from datetime import UTC, date, datetime

from app.services.analytics_types import Anomaly, SourceHealth

_HUMANIZE_THRESHOLD = 10_000
_HUMANIZE_STEPS: tuple[tuple[float, str], ...] = (
    (1_000_000_000, "B"),
    (1_000_000, "M"),
    (1_000, "K"),
)


def humanize(value: int | float | None) -> str:
    if value is None:
        return "—"
    if abs(value) < _HUMANIZE_THRESHOLD:
        return f"{value:,}".replace(",", " ")
    for divisor, suffix in _HUMANIZE_STEPS:
        if abs(value) >= divisor:
            return f"{value / divisor:.1f}{suffix}"
    return f"{value:,}".replace(",", " ")


def ratio_pct(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value * 100:.{digits}f}%"


def pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}%"


def signed(value: int | None) -> str:
    if value is None:
        return "—"
    if value > 0:
        return f"+{humanize(value)}"
    if value < 0:
        return f"−{humanize(-value)}"
    return humanize(value)


def dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def day(value: date | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d")


def ago(value: datetime | None, now: datetime | None = None) -> str:
    if value is None:
        return "—"
    now = now or datetime.now(UTC)
    delta = now.astimezone(UTC) - value.astimezone(UTC)
    seconds = delta.total_seconds()
    if seconds < 60:
        return "just now"
    minutes = seconds / 60
    if minutes < 60:
        return f"{int(minutes)}m ago"
    hours = minutes / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    return f"{int(days)}d ago"


def dash(value: object) -> object:
    if value is None or value == "":
        return "—"
    return value


def health_class(health: SourceHealth) -> str:
    return f"badge badge--{health.value}"


def anomaly_class(anomaly: Anomaly | None) -> str:
    if anomaly is None:
        return ""
    return f"anomaly anomaly--{anomaly.direction.value}"


def tme_post_url(username: str, message_id: int) -> str:
    return f"https://t.me/{username}/{message_id}"
