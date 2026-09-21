"""Web Admin brute-force lockout (2026-09-21, Dale: 5 failed logins).

Five different wrong credentials from one address lock that address out for
15 minutes - on the login form and on every token-protected route, so the
token cannot be guessed through /api/status instead.
"""

import pytest
from aiohttp.test_utils import TestClient, TestServer

from wolfrat.web_server import WolfWebServer

from tests.test_web_http_integration import FakeServerManager

GOOD = {"username": "operator", "token": "web-access-token"}


@pytest.fixture
async def service_and_client():
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    service.set_auth("operator", "web-access-token")
    client = TestClient(TestServer(service.application))
    await client.start_server()
    try:
        yield service, client
    finally:
        await client.close()


async def _bad_login(client, n):
    return await client.post(
        "/api/auth", json={"username": "operator", "token": f"guess-{n}"}
    )


@pytest.mark.asyncio
async def test_fifth_failed_login_locks_the_address_out(service_and_client):
    service, client = service_and_client
    for n in range(4):
        assert (await _bad_login(client, n)).status == 401

    fifth = await _bad_login(client, 4)
    assert fifth.status == 429
    assert "Too many failed logins" in (await fifth.json())["error"]
    assert int(fifth.headers["Retry-After"]) == service.LOCKOUT_SECONDS

    # Even the right credentials are refused while locked.
    locked = await client.post("/api/auth", json=GOOD)
    assert locked.status == 429
    # ...and so is a valid bearer token on a protected route.
    status = await client.get(
        "/api/status", headers={"Authorization": "Bearer web-access-token"}
    )
    assert status.status == 401


@pytest.mark.asyncio
async def test_lockout_expires(service_and_client):
    service, client = service_and_client
    for n in range(5):
        await _bad_login(client, n)
    assert (await client.post("/api/auth", json=GOOD)).status == 429

    with service._auth_lock:
        for record in service._failed_logins.values():
            record["until"] = 1  # long past
    assert (await client.post("/api/auth", json=GOOD)).status == 200


@pytest.mark.asyncio
async def test_successful_login_resets_the_count(service_and_client):
    service, client = service_and_client
    for n in range(4):
        await _bad_login(client, n)
    assert (await client.post("/api/auth", json=GOOD)).status == 200
    for n in range(10, 14):
        assert (await _bad_login(client, n)).status == 401


@pytest.mark.asyncio
async def test_bearer_token_guessing_is_locked_out_too(service_and_client):
    service, client = service_and_client
    for n in range(5):
        response = await client.get(
            "/api/status", headers={"Authorization": f"Bearer guess-{n}"}
        )
        assert response.status == 401
    assert (await client.post("/api/auth", json=GOOD)).status == 429


@pytest.mark.asyncio
async def test_repeating_one_stale_token_never_locks_out(service_and_client):
    """A phone still holding the old token after a token change retries it."""
    service, client = service_and_client
    for _ in range(20):
        await client.get(
            "/api/status", headers={"Authorization": "Bearer old-stale-token"}
        )
    assert (await client.post("/api/auth", json=GOOD)).status == 200


@pytest.mark.asyncio
async def test_requests_without_credentials_are_not_counted(service_and_client):
    service, client = service_and_client
    for _ in range(20):
        assert (await client.get("/api/status")).status == 401
    assert (await client.post("/api/auth", json=GOOD)).status == 200


def test_guesses_are_not_stored_in_clear():
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    service._record_failed_login("1.2.3.4", "secret-guess")
    assert "secret-guess" not in repr(service._failed_logins)


def test_old_failures_age_out_of_the_window():
    service = WolfWebServer(FakeServerManager(), host="127.0.0.1", port=0)
    for n in range(4):
        service._record_failed_login("1.2.3.4", f"g{n}", now=1000.0)
    later = 1000.0 + service.LOCKOUT_WINDOW + 1
    assert service._record_failed_login("1.2.3.4", "g4", now=later) is False
    assert service._lockout_remaining("1.2.3.4", now=later) == 0
