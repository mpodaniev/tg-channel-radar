import statistics
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, timedelta

from app.services.analytics_types import Anomaly, AnomalyDirection, DailyPoint, Growth, SourceHealth

ANOMALY_THRESHOLD = 3.5
ANOMALY_WINDOW = 20
ANOMALY_MIN_SAMPLES = 5
MAD_SCALE = 0.6745
MEAN_AD_SCALE = 1.253314
HEALTH_STALE_AFTER = timedelta(hours=2)
HEALTH_RED_AFTER = timedelta(hours=12)
HEALTH_RED_FAILURES = 3

_RED_STATUSES = {"error", "not_found", "private"}


def safe_ratio(numerator: int | None, denominator: int | None) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def mean_or_none(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return statistics.mean(present)


def median_or_none(values: Iterable[float | None]) -> float | None:
    present = [v for v in values if v is not None]
    if not present:
        return None
    return statistics.median(present)


def growth(first: int | None, last: int | None) -> Growth | None:
    if first is None or last is None:
        return None
    absolute = last - first
    percent = None if first == 0 else (absolute / first) * 100
    return Growth(absolute=absolute, percent=percent)


def detect_anomalies(
    values: Sequence[int | None], *, threshold: float = ANOMALY_THRESHOLD
) -> list[Anomaly]:
    indexed = [(index, value) for index, value in enumerate(values) if value is not None]
    if len(indexed) < ANOMALY_MIN_SAMPLES:
        return []

    numbers = [value for _, value in indexed]
    median = statistics.median(numbers)
    abs_deviations = [abs(value - median) for value in numbers]
    mad = statistics.median(abs_deviations)

    if mad != 0:
        scale = MAD_SCALE / mad
    else:
        mean_abs_deviation = statistics.mean(abs_deviations)
        if mean_abs_deviation == 0:
            return []
        scale = MEAN_AD_SCALE / mean_abs_deviation

    anomalies = []
    for index, value in indexed:
        score = (value - median) * scale
        if abs(score) >= threshold:
            direction = AnomalyDirection.SPIKE if score > 0 else AnomalyDirection.DROP
            anomalies.append(Anomaly(index=index, score=score, direction=direction))
    return anomalies


def source_health(
    *,
    status: str,
    last_fetch_at: datetime | None,
    consecutive_failures: int,
    now: datetime,
) -> SourceHealth:
    stale = last_fetch_at is None or (now - last_fetch_at) > HEALTH_STALE_AFTER
    very_stale = last_fetch_at is not None and (now - last_fetch_at) > HEALTH_RED_AFTER

    if status in _RED_STATUSES or consecutive_failures >= HEALTH_RED_FAILURES or very_stale:
        return SourceHealth.RED
    if status == "pending" or consecutive_failures >= 1 or stale:
        return SourceHealth.YELLOW
    return SourceHealth.GREEN


def daily_points(
    rows: Sequence[tuple[datetime, int | None]], *, start: date, end: date
) -> list[DailyPoint]:
    buckets: dict[date, list[int | None]] = {
        start + timedelta(days=offset): [] for offset in range((end - start).days + 1)
    }

    for posted_at, views in rows:
        day = posted_at.astimezone(UTC).date()
        if day in buckets:
            buckets[day].append(views)

    result = []
    for day in sorted(buckets):
        day_views = [v for v in buckets[day] if v is not None]
        result.append(
            DailyPoint(
                day=day,
                posts=len(buckets[day]),
                views=sum(day_views) if day_views else None,
            )
        )
    return result
