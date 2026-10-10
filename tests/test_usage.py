from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import sqlite3

import pytest

from app.audit_log import AuditLog, AuditRecord
from app.usage import ApiKeyAuthenticator, QuotaExceeded, QuotaLedger, RateLimiter


def test_authenticator_uses_only_pseudonymous_identity() -> None:
    key = "secret-test-key"
    key_id = ApiKeyAuthenticator((key,)).authenticate(key)

    assert key_id is not None and key_id != key
    assert ApiKeyAuthenticator((key,)).authenticate("wrong-key") is None


def test_rate_limit_is_rolling_and_isolated_by_key() -> None:
    current = [0.0]
    limiter = RateLimiter(limit=2, window_seconds=60, clock=lambda: current[0])
    assert limiter.allow("key-a")
    assert limiter.allow("key-a")
    assert not limiter.allow("key-a")
    assert limiter.allow("key-b")
    current[0] = 60.1
    assert limiter.allow("key-a")


def test_quota_reservation_is_atomic_and_resets_on_utc_day(tmp_path) -> None:
    path = tmp_path / "usage.sqlite3"
    audit = AuditLog(path)
    current = [datetime(2026, 10, 10, 23, 59, tzinfo=timezone.utc)]
    ledger = QuotaLedger(path, audit, cost_limit_usd=1, large_tier_limit=2, clock=lambda: current[0])

    def reserve(index: int) -> bool:
        try:
            ledger.reserve(f"request-{index}", "key-id", 0.2, 1)
            return True
        except QuotaExceeded:
            return False

    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(reserve, range(10)))
    assert sum(results) == 2  # Large-tier cap is reached before cost cap.

    current[0] = datetime(2026, 10, 11, 0, 1, tzinfo=timezone.utc)
    reservation = ledger.reserve("next-day", "key-id", 0.2, 1)
    assert reservation.utc_day == "2026-10-11"


def test_reconcile_commits_actual_usage_and_audit_excludes_secrets(tmp_path) -> None:
    path = tmp_path / "audit.sqlite3"
    audit = AuditLog(path)
    ledger = QuotaLedger(path, audit)
    reservation = ledger.reserve("request-1", "pseudonymous-id", 0.5, 1)
    assert ledger.reconcile(reservation, 0.1, 1)
    audit.record(
        AuditRecord(
            request_id="request-1",
            api_key_id="pseudonymous-id",
            timestamp_utc="2026-10-10T00:00:00+00:00",
            task_type="classification",
            selected_model="mock-small",
            status=200,
            estimated_cost_usd=0.1,
        )
    )

    rows = audit.rows()
    assert len(rows) == 1
    assert rows[0]["selected_model"] == "mock-small"
    connection = sqlite3.connect(path)
    try:
        usage = connection.execute(
            "SELECT committed_cost, reserved_cost, committed_large, reserved_large FROM daily_usage"
        ).fetchone()
        schema = connection.execute("SELECT sql FROM sqlite_master WHERE name='request_audit'").fetchone()[0]
    finally:
        connection.close()
    assert usage == (0.1, 0.0, 1, 0)
    assert "prompt" not in schema.lower()
    assert "api_key TEXT" not in schema
    assert "provider_output" not in schema


def test_cost_quota_denial_is_not_reserved(tmp_path) -> None:
    path = tmp_path / "quota.sqlite3"
    audit = AuditLog(path)
    ledger = QuotaLedger(path, audit, cost_limit_usd=0.1)
    with pytest.raises(QuotaExceeded):
        ledger.reserve("too-expensive", "key-id", 0.11, 0)
