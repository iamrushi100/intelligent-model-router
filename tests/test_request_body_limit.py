import asyncio
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import RequestBodyLimitMiddleware, create_app
from app.providers import MockProvider


def payload_bytes() -> bytes:
    return json.dumps(
        {
            "prompt": "Classify this synthetic item.",
            "task_type": "classification",
            "sensitivity": "public",
        },
        separators=(",", ":"),
    ).encode("utf-8")


def make_app(tmp_path, limit: int):
    database = tmp_path / "body-limit.sqlite3"
    provider = MockProvider()
    settings = Settings(
        api_keys=("body-limit-test-key",),
        max_request_body_bytes=limit,
        database_path=str(database),
    )
    return create_app(settings=settings, provider=provider), provider, database


async def asgi_post(
    application,
    body: bytes,
    headers: list[tuple[bytes, bytes]],
    *,
    chunks: list[bytes] | None = None,
    disconnect_before_final: bool = False,
):
    messages = []
    chunks = chunks if chunks is not None else [
        body[index:index + 11] for index in range(0, len(body), 11)
    ] or [b""]
    incoming = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1 or disconnect_before_final,
        }
        for index, chunk in enumerate(chunks)
    ]
    if disconnect_before_final:
        incoming.append({"type": "http.disconnect"})

    async def receive():
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/route",
        "raw_path": b"/route",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    await application(scope, receive, send)
    status = next(message["status"] for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"") for message in messages if message["type"] == "http.response.body"
    )
    return status, response_body


def safe_database_counts(database):
    connection = sqlite3.connect(database)
    try:
        return (
            connection.execute("SELECT status FROM request_audit").fetchall(),
            connection.execute("SELECT COUNT(*) FROM daily_usage").fetchone()[0],
            connection.execute("SELECT COUNT(*) FROM quota_reservations").fetchone()[0],
        )
    finally:
        connection.close()


def test_body_one_byte_below_configured_limit_is_accepted(tmp_path):
    body = payload_bytes()
    application, provider, _database = make_app(tmp_path, len(body) + 1)
    client = TestClient(application)

    response = client.post(
        "/route",
        content=body,
        headers={
            "X-API-Key": "body-limit-test-key",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 200
    assert provider.calls == ["mock-small"]


def test_exact_byte_boundary_is_accepted_and_one_byte_over_is_rejected(tmp_path):
    body = payload_bytes()
    application, provider, database = make_app(tmp_path, len(body))
    client = TestClient(application)
    headers = {
        "X-API-Key": "body-limit-test-key",
        "Content-Type": "application/json",
    }

    exact = client.post("/route", content=body, headers=headers)
    over = client.post("/route", content=body + b" ", headers=headers)

    assert exact.status_code == 200
    assert over.status_code == 413
    assert over.json() == {"detail": "Request body exceeds the configured size limit."}
    assert provider.calls == ["mock-small"]
    audit_statuses, daily_rows, reservations = safe_database_counts(database)
    assert audit_statuses == [(200,), (413,)]
    assert daily_rows == 1  # Only the accepted request reserved quota.
    assert reservations == 0


@pytest.mark.parametrize("length_header", [None, b"not-a-number", b"2"])
def test_streamed_body_limit_catches_missing_malformed_or_understated_length(
    tmp_path, length_header
):
    application, provider, database = make_app(tmp_path, 64)
    body = b'{"prompt":"' + b"x" * 100 + b'","task_type":"classification","sensitivity":"public"}'
    headers = [
        (b"x-api-key", b"body-limit-test-key"),
        (b"content-type", b"application/json"),
    ]
    if length_header is not None:
        headers.append((b"content-length", length_header))

    status, response_body = asyncio.run(asgi_post(application, body, headers))

    assert status == 413, response_body
    assert json.loads(response_body) == {
        "detail": "Request body exceeds the configured size limit."
    }
    assert b"x" * 40 not in response_body
    assert provider.calls == []
    audit_statuses, daily_rows, reservations = safe_database_counts(database)
    assert audit_statuses == [(413,)]
    assert daily_rows == 0
    assert reservations == 0


def test_declared_oversize_is_rejected_without_provider_or_quota_use(tmp_path):
    application, provider, database = make_app(tmp_path, 32)
    client = TestClient(application)
    response = client.post(
        "/route",
        content=b"x" * 100,
        headers={
            "X-API-Key": "body-limit-test-key",
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 413
    assert provider.calls == []
    audit_statuses, daily_rows, reservations = safe_database_counts(database)
    assert audit_statuses == [(413,)]
    assert daily_rows == reservations == 0


def test_health_is_not_subject_to_route_body_limit(tmp_path):
    application, _provider, _database = make_app(tmp_path, 1)
    response = TestClient(application).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("length_values", [[b"20", b"20"], [b"-1"]])
def test_duplicate_or_negative_content_length_uses_actual_bytes(tmp_path, length_values):
    application, provider, database = make_app(tmp_path, 64)
    body = b'{"prompt":"' + b"z" * 100 + b'"}'
    headers = [
        (b"x-api-key", b"body-limit-test-key"),
        (b"content-type", b"application/json"),
        *((b"content-length", value) for value in length_values),
    ]

    status, response_body = asyncio.run(asgi_post(application, body, headers))

    assert status == 413
    assert json.loads(response_body) == {
        "detail": "Request body exceeds the configured size limit."
    }
    assert provider.calls == []
    audit_statuses, daily_rows, reservations = safe_database_counts(database)
    assert audit_statuses == [(413,)]
    assert daily_rows == reservations == 0


def test_declared_length_larger_than_actual_under_limit_body_is_accepted(tmp_path):
    body = payload_bytes()
    application, provider, _database = make_app(tmp_path, len(body) + 20)
    headers = [
        (b"x-api-key", b"body-limit-test-key"),
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body) + 10).encode("ascii")),
    ]

    status, _response_body = asyncio.run(asgi_post(application, body, headers))

    assert status == 200
    assert provider.calls == ["mock-small"]


def test_declared_oversize_is_rejected_when_actual_body_is_under_limit(tmp_path):
    limit = 16
    actual_body = b"{}"
    application, provider, _database = make_app(tmp_path, limit)
    headers = [
        (b"x-api-key", b"body-limit-test-key"),
        (b"content-type", b"application/json"),
        (b"content-length", str(limit + 1).encode("ascii")),
    ]

    # Direct ASGI invocation preserves the deliberately contradictory header/body
    # pair, which an HTTP client may normalize or reject before reaching the app.
    status, response_body = asyncio.run(asgi_post(application, actual_body, headers))

    assert len(actual_body) < limit
    assert status == 413
    assert json.loads(response_body) == {
        "detail": "Request body exceeds the configured size limit."
    }
    assert provider.calls == []


def test_empty_intermediate_chunks_do_not_change_body(tmp_path):
    body = payload_bytes()
    application, provider, _database = make_app(tmp_path, len(body))
    split = len(body) // 2
    headers = [
        (b"x-api-key", b"body-limit-test-key"),
        (b"content-type", b"application/json"),
    ]

    status, _response_body = asyncio.run(
        asgi_post(application, body, headers, chunks=[body[:split], b"", body[split:]])
    )

    assert status == 200
    assert provider.calls == ["mock-small"]


def test_disconnect_before_final_body_is_forwarded_as_disconnect(tmp_path):
    application, provider, database = make_app(tmp_path, 128)
    headers = [
        (b"x-api-key", b"body-limit-test-key"),
        (b"content-type", b"application/json"),
    ]

    status, _response_body = asyncio.run(
        asgi_post(application, b'{"prompt":"partial', headers,
                  chunks=[b'{"prompt":"partial'], disconnect_before_final=True)
    )

    assert status in {400, 422}
    assert provider.calls == []
    audit_statuses, daily_rows, reservations = safe_database_counts(database)
    assert audit_statuses == [(status,)]
    assert daily_rows == reservations == 0


def test_replayed_chunks_are_delivered_once_then_disconnect():
    received = []

    async def downstream(scope, receive, send):
        received.append(await receive())
        received.append(await receive())
        received.append(await receive())

    middleware = RequestBodyLimitMiddleware(downstream, max_bytes=20)
    incoming = [
        {"type": "http.request", "body": b"first", "more_body": True},
        {"type": "http.request", "body": b"second", "more_body": False},
    ]

    async def receive():
        return incoming.pop(0)

    async def send(_message):
        return None

    scope = {
        "type": "http", "method": "POST", "path": "/route", "headers": [],
    }
    asyncio.run(middleware(scope, receive, send))

    assert received == [
        {"type": "http.request", "body": b"first", "more_body": True},
        {"type": "http.request", "body": b"second", "more_body": False},
        {"type": "http.disconnect"},
    ]


@pytest.mark.parametrize("limit", [0, -1])
def test_settings_reject_zero_or_negative_request_body_limit(limit):
    with pytest.raises(ValueError, match="max_request_body_bytes must be positive"):
        Settings(max_request_body_bytes=limit)


def test_unauthenticated_oversized_request_still_returns_401_without_provider(tmp_path):
    application, provider, database = make_app(tmp_path, 16)
    response = TestClient(application).post("/route", content=b"x" * 100)

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key."}
    assert provider.calls == []
    audit_statuses, daily_rows, reservations = safe_database_counts(database)
    assert audit_statuses == [(401,)]
    assert daily_rows == reservations == 0
