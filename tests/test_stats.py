from datetime import UTC, date, datetime, timedelta

import pytest

from app.services import stats
from app.services.analytics_types import AnomalyDirection, SourceHealth


class TestSafeRatio:
    @pytest.mark.parametrize(
        ("numerator", "denominator"),
        [(None, 100), (5, None), (5, 0), (None, None)],
    )
    def test_none_when_inputs_missing_or_zero_denominator(
        self, numerator: int | None, denominator: int | None
    ) -> None:
        assert stats.safe_ratio(numerator, denominator) is None

    def test_computes_ratio(self) -> None:
        assert stats.safe_ratio(5, 20) == 0.25


class TestGrowth:
    def test_increase(self) -> None:
        result = stats.growth(100, 150)
        assert result is not None
        assert result.absolute == 50
        assert result.percent == 50.0

    def test_decrease(self) -> None:
        result = stats.growth(150, 100)
        assert result is not None
        assert result.absolute == -50
        assert result.percent == pytest.approx(-33.333, rel=1e-3)

    def test_none_first(self) -> None:
        assert stats.growth(None, 100) is None

    def test_none_last(self) -> None:
        assert stats.growth(100, None) is None

    def test_zero_start_gives_no_percent(self) -> None:
        result = stats.growth(0, 50)
        assert result is not None
        assert result.absolute == 50
        assert result.percent is None


class TestDetectAnomalies:
    def test_one_spike_among_equal_values(self) -> None:
        values = [100, 100, 100, 100, 100, 100, 5000]
        anomalies = stats.detect_anomalies(values)
        assert len(anomalies) == 1
        assert anomalies[0].index == 6
        assert anomalies[0].direction == AnomalyDirection.SPIKE

    def test_one_drop_among_equal_values(self) -> None:
        values = [100, 100, 100, 100, 100, 100, 1]
        anomalies = stats.detect_anomalies(values)
        assert len(anomalies) == 1
        assert anomalies[0].index == 6
        assert anomalies[0].direction == AnomalyDirection.DROP

    def test_constant_series_has_no_anomalies(self) -> None:
        assert stats.detect_anomalies([100] * 10) == []

    def test_series_shorter_than_min_samples_is_empty(self) -> None:
        values = [100, 100, 100, 5000]
        assert len(values) < stats.ANOMALY_MIN_SAMPLES
        assert stats.detect_anomalies(values) == []

    def test_none_values_are_skipped_and_indices_preserved(self) -> None:
        values = [100, None, 100, 100, 100, 100, 5000]
        anomalies = stats.detect_anomalies(values)
        assert len(anomalies) == 1
        assert anomalies[0].index == 6

    def test_mad_zero_fallback_still_detects_outlier(self) -> None:
        values = [100] * 10 + [5000]
        anomalies = stats.detect_anomalies(values)
        assert len(anomalies) == 1
        assert anomalies[0].index == 10
        assert anomalies[0].direction == AnomalyDirection.SPIKE


class TestSourceHealth:
    NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    @pytest.mark.parametrize(
        ("status", "last_fetch_at", "consecutive_failures", "expected"),
        [
            ("error", NOW, 0, SourceHealth.RED),
            ("not_found", NOW, 0, SourceHealth.RED),
            ("private", NOW, 0, SourceHealth.RED),
            ("active", NOW, 3, SourceHealth.RED),
            ("active", NOW - timedelta(hours=13), 0, SourceHealth.RED),
            ("pending", NOW, 0, SourceHealth.YELLOW),
            ("active", NOW, 1, SourceHealth.YELLOW),
            ("active", NOW - timedelta(hours=3), 0, SourceHealth.YELLOW),
            ("active", None, 0, SourceHealth.YELLOW),
            ("active", NOW, 0, SourceHealth.GREEN),
        ],
    )
    def test_health_table(
        self,
        status: str,
        last_fetch_at: datetime | None,
        consecutive_failures: int,
        expected: SourceHealth,
    ) -> None:
        result = stats.source_health(
            status=status,
            last_fetch_at=last_fetch_at,
            consecutive_failures=consecutive_failures,
            now=self.NOW,
        )
        assert result == expected


class TestDailyPoints:
    def test_day_without_posts_present_with_zero(self) -> None:
        start = date(2026, 9, 1)
        end = date(2026, 9, 3)
        rows = [(datetime(2026, 9, 1, 10, tzinfo=UTC), 100)]

        points = stats.daily_points(rows, start=start, end=end)

        assert len(points) == 3
        assert points[0].day == date(2026, 9, 1)
        assert points[0].posts == 1
        assert points[0].views == 100
        assert points[1].day == date(2026, 9, 2)
        assert points[1].posts == 0
        assert points[1].views is None
        assert points[2].day == date(2026, 9, 3)
        assert points[2].posts == 0

    def test_period_bounds_are_inclusive(self) -> None:
        start = date(2026, 9, 1)
        end = date(2026, 9, 1)
        rows = [(datetime(2026, 9, 1, 23, 59, tzinfo=UTC), 5)]

        points = stats.daily_points(rows, start=start, end=end)

        assert len(points) == 1
        assert points[0].posts == 1

    def test_groups_in_utc(self) -> None:
        tz = UTC
        start = date(2026, 9, 1)
        end = date(2026, 9, 2)
        # 23:30 UTC stays on the 1st, not shifted to the 2nd.
        rows = [(datetime(2026, 9, 1, 23, 30, tzinfo=tz), 10)]

        points = stats.daily_points(rows, start=start, end=end)

        assert points[0].posts == 1
        assert points[1].posts == 0
