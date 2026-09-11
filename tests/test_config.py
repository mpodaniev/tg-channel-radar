from app.config import normalize_database_url


def test_strips_sslmode_and_sets_ssl_flag() -> None:
    result = normalize_database_url(
        "postgresql+asyncpg://user:pass@host/db?sslmode=require&channel_binding=require"
    )

    assert result.url == "postgresql+asyncpg://user:pass@host/db"
    assert result.ssl is True


def test_leaves_plain_url_untouched() -> None:
    result = normalize_database_url("postgresql+asyncpg://radar:radar@localhost:5433/radar")

    assert result.url == "postgresql+asyncpg://radar:radar@localhost:5433/radar"
    assert result.ssl is False


def test_sslmode_verify_ca_sets_ssl_flag() -> None:
    result = normalize_database_url("postgresql+asyncpg://user:pass@host/db?sslmode=verify-ca")

    assert result.url == "postgresql+asyncpg://user:pass@host/db"
    assert result.ssl is True


def test_sslmode_verify_full_sets_ssl_flag() -> None:
    result = normalize_database_url("postgresql+asyncpg://user:pass@host/db?sslmode=verify-full")

    assert result.url == "postgresql+asyncpg://user:pass@host/db"
    assert result.ssl is True


def test_sslmode_disable_is_stripped_without_ssl_flag() -> None:
    result = normalize_database_url("postgresql+asyncpg://user:pass@host/db?sslmode=disable")

    assert result.url == "postgresql+asyncpg://user:pass@host/db"
    assert result.ssl is False


def test_sslmode_prefer_is_stripped_without_ssl_flag() -> None:
    result = normalize_database_url("postgresql+asyncpg://user:pass@host/db?sslmode=prefer")

    assert result.url == "postgresql+asyncpg://user:pass@host/db"
    assert result.ssl is False


def test_channel_binding_alone_is_stripped_without_ssl_flag() -> None:
    result = normalize_database_url(
        "postgresql+asyncpg://user:pass@host/db?channel_binding=require"
    )

    assert result.url == "postgresql+asyncpg://user:pass@host/db"
    assert result.ssl is False


def test_sslmode_is_case_insensitive() -> None:
    result = normalize_database_url("postgresql+asyncpg://user:pass@host/db?sslmode=REQUIRE")

    assert result.url == "postgresql+asyncpg://user:pass@host/db"
    assert result.ssl is True


def test_preserves_non_ssl_query_params() -> None:
    result = normalize_database_url(
        "postgresql+asyncpg://user:pass@host/db?sslmode=require&application_name=radar"
    )

    assert result.url == "postgresql+asyncpg://user:pass@host/db?application_name=radar"
    assert result.ssl is True
