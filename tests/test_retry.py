from app.retry import backoff_delay


def test_first_attempt_returns_base_delay() -> None:
    assert backoff_delay(0, 1.0) == 1.0


def test_delay_doubles_each_attempt() -> None:
    assert backoff_delay(1, 1.0) == 2.0
    assert backoff_delay(2, 1.0) == 4.0
    assert backoff_delay(3, 1.0) == 8.0


def test_base_seconds_scales_the_delay() -> None:
    assert backoff_delay(2, 0.5) == 2.0
