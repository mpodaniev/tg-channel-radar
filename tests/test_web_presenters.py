import json
from datetime import UTC, date, datetime

from app.services.analytics_types import DailyPoint, TrendPoint
from app.web import presenters


class TestSubscribersChart:
    def test_empty(self) -> None:
        result = presenters.subscribers_chart([])
        assert result == {"labels": [], "series": [{"label": "Subscribers", "data": []}]}
        json.dumps(result)

    def test_datetime_becomes_human_readable_label(self) -> None:
        point = TrendPoint(captured_at=datetime(2026, 9, 11, 12, 0, tzinfo=UTC), value=100)
        result = presenters.subscribers_chart([point])
        assert result["labels"] == ["2026-09-11 12:00 UTC"]
        assert result["series"][0]["data"] == [100]
        json.dumps(result)


class TestDailyChart:
    def test_empty(self) -> None:
        result = presenters.daily_chart([])
        assert result["labels"] == []
        assert result["series"][0]["label"] == "Posts"
        assert result["series"][1]["label"] == "Views"
        json.dumps(result)

    def test_date_becomes_iso_string_and_none_preserved(self) -> None:
        points = [
            DailyPoint(day=date(2026, 9, 11), posts=3, views=120),
            DailyPoint(day=date(2026, 9, 12), posts=0, views=None),
        ]
        result = presenters.daily_chart(points)
        assert result["labels"] == ["2026-09-11", "2026-09-12"]
        assert result["series"][0]["data"] == [3, 0]
        assert result["series"][1]["data"] == [120, None]
        serialized = json.dumps(result)
        assert "null" in serialized


class TestPostGrowthChart:
    def test_empty(self) -> None:
        result = presenters.post_growth_chart([])
        assert result == {"labels": [], "series": [{"label": "Views", "data": []}]}
        json.dumps(result)

    def test_survives_json_dumps(self) -> None:
        points = [TrendPoint(captured_at=datetime(2026, 9, 11, tzinfo=UTC), value=10)]
        result = presenters.post_growth_chart(points)
        json.dumps(result)
