def backoff_delay(attempt: int, base_seconds: float) -> float:
    return base_seconds * (2**attempt)
