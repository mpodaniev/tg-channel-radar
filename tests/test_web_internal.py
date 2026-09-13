import httpx
import pytest

from app.config import get_settings
from app.services.refresh import RefreshBatchResult
from app.web import routes_internal


@pytest.mark.parametrize(
    "headers",
    [
        None,
        {"X-Refresh-Token": "wrong-token"},
        # Raw non-ASCII bytes: Starlette decodes headers as latin-1, so this
        # reaches the dependency as a str that secrets.compare_digest refuses.
        {"X-Refresh-Token": b"\xc3\xa9\xff"},
    ],
)
async def test_refresh_without_valid_token_is_unauthorized(
    app_client: httpx.AsyncClient, headers: dict[str, str | bytes] | None
) -> None:
    response = await app_client.post("/internal/refresh", headers=headers)

    assert response.status_code == 401


async def test_refresh_with_correct_token_runs_batch(
    app_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stub_result = RefreshBatchResult(
        succeeded=1,
        time_budget_exceeded=False,
        elapsed_seconds=1.5,
        usernames_failed=["broken"],
    )

    async def fake_refresh_due_channels(session_factory):
        return stub_result

    monkeypatch.setattr(routes_internal, "refresh_due_channels", fake_refresh_due_channels)

    token = get_settings().refresh_token
    response = await app_client.post("/internal/refresh", headers={"X-Refresh-Token": token})

    assert response.status_code == 200
    body = response.json()
    assert body["processed"] == 2
    assert body["succeeded"] == 1
    assert body["failed"] == 1
    assert body["usernames_failed"] == ["broken"]
    assert body["time_budget_exceeded"] is False
    assert body["elapsed_seconds"] == 1.5
