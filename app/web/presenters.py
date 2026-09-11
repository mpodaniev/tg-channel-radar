from collections.abc import Sequence
from typing import Any

from app.services.analytics_types import DailyPoint, TrendPoint


def subscribers_chart(trend: Sequence[TrendPoint]) -> dict[str, Any]:
    return {
        "labels": [point.captured_at.isoformat() for point in trend],
        "series": [
            {"label": "Subscribers", "data": [point.value for point in trend]},
        ],
    }


def daily_chart(daily: Sequence[DailyPoint]) -> dict[str, Any]:
    return {
        "labels": [point.day.isoformat() for point in daily],
        "series": [
            {"label": "Posts", "data": [point.posts for point in daily]},
            {"label": "Views", "data": [point.views for point in daily]},
        ],
    }


def post_growth_chart(growth: Sequence[TrendPoint]) -> dict[str, Any]:
    return {
        "labels": [point.captured_at.isoformat() for point in growth],
        "series": [
            {"label": "Views", "data": [point.value for point in growth]},
        ],
    }
