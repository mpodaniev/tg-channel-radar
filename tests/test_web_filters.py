from datetime import UTC, datetime, timedelta, timezone

from app.services.analytics_types import Anomaly, AnomalyDirection, SourceHealth
from app.web import filters


class TestHumanize:
    def test_none_returns_dash(self) -> None:
        assert filters.humanize(None) == "—"

    def test_zero(self) -> None:
        assert filters.humanize(0) == "0"

    def test_small_number_grouped(self) -> None:
        assert filters.humanize(1234) == "1 234"

    def test_below_threshold_not_abbreviated(self) -> None:
        assert filters.humanize(999) == "999"

    def test_thousands_abbreviated(self) -> None:
        assert filters.humanize(12_500) == "12.5K"

    def test_millions_abbreviated(self) -> None:
        assert filters.humanize(3_400_000) == "3.4M"


class TestRatioAndPct:
    def test_ratio_pct_multiplies_by_100(self) -> None:
        assert filters.ratio_pct(0.0512) == "5.12%"

    def test_pct_does_not_multiply(self) -> None:
        assert filters.pct(5.12) == "5.1%"

    def test_ratio_pct_none(self) -> None:
        assert filters.ratio_pct(None) == "—"

    def test_pct_none(self) -> None:
        assert filters.pct(None) == "—"

    def test_same_input_diverges_between_ratio_pct_and_pct(self) -> None:
        value = 0.5
        assert filters.ratio_pct(value) != filters.pct(value)


class TestSigned:
    def test_positive(self) -> None:
        assert filters.signed(1234) == "+1 234"

    def test_negative(self) -> None:
        assert filters.signed(-12) == "−12"

    def test_zero(self) -> None:
        assert filters.signed(0) == "0"

    def test_none(self) -> None:
        assert filters.signed(None) == "—"


class TestDatetimeFilters:
    def test_dt_none(self) -> None:
        assert filters.dt(None) == "—"

    def test_dt_normalizes_non_utc_tz(self) -> None:
        eastern = timezone(timedelta(hours=-4))
        value = datetime(2026, 9, 11, 8, 0, tzinfo=eastern)
        assert filters.dt(value) == "2026-09-11 12:00 UTC"

    def test_day_none(self) -> None:
        assert filters.day(None) == "—"

    def test_day_formats_date(self) -> None:
        assert filters.day(datetime(2026, 9, 11, tzinfo=UTC).date()) == "2026-09-11"


class TestAgo:
    def test_none(self) -> None:
        assert filters.ago(None) == "—"

    def test_seconds(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        value = now - timedelta(seconds=30)
        assert filters.ago(value, now=now) == "just now"

    def test_minutes(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        value = now - timedelta(minutes=45)
        assert filters.ago(value, now=now) == "45m ago"

    def test_hours(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        value = now - timedelta(hours=5)
        assert filters.ago(value, now=now) == "5h ago"

    def test_days(self) -> None:
        now = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
        value = now - timedelta(days=3)
        assert filters.ago(value, now=now) == "3d ago"


class TestDash:
    def test_none(self) -> None:
        assert filters.dash(None) == "—"

    def test_empty_string(self) -> None:
        assert filters.dash("") == "—"

    def test_passthrough(self) -> None:
        assert filters.dash("hello") == "hello"

    def test_passthrough_number(self) -> None:
        assert filters.dash(0) == 0


class TestHealthClass:
    def test_green(self) -> None:
        assert filters.health_class(SourceHealth.GREEN) == "badge badge--green"

    def test_yellow(self) -> None:
        assert filters.health_class(SourceHealth.YELLOW) == "badge badge--yellow"

    def test_red(self) -> None:
        assert filters.health_class(SourceHealth.RED) == "badge badge--red"


class TestAnomalyClass:
    def test_none(self) -> None:
        assert filters.anomaly_class(None) == ""

    def test_spike(self) -> None:
        anomaly = Anomaly(index=0, score=4.0, direction=AnomalyDirection.SPIKE)
        assert filters.anomaly_class(anomaly) == "anomaly anomaly--spike"

    def test_drop(self) -> None:
        anomaly = Anomaly(index=0, score=-4.0, direction=AnomalyDirection.DROP)
        assert filters.anomaly_class(anomaly) == "anomaly anomaly--drop"


class TestTmePostUrl:
    def test_builds_url(self) -> None:
        assert filters.tme_post_url("durov", 123) == "https://t.me/durov/123"
