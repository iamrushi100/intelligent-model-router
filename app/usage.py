"""API-key authentication, in-memory rolling rate limits, and atomic UTC quotas."""

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
import hashlib
import hmac
import math
from pathlib import Path
import sqlite3
from threading import Lock
import time
from typing import Callable

from app.audit_log import AuditLog


class ApiKeyAuthenticator:
    def __init__(self, api_keys: tuple[str, ...]) -> None:
        self._keys = tuple(key.encode("utf-8") for key in api_keys)

    def authenticate(self, supplied_key: str | None) -> str | None:
        if supplied_key is None:
            return None
        candidate = supplied_key.encode("utf-8")
        matched = False
        # Compare with every configured key; never reveal which key/prefix matched.
        for configured_key in self._keys:
            matched = hmac.compare_digest(candidate, configured_key) or matched
        if not matched:
            return None
        return hashlib.sha256(candidate).hexdigest()


class RateLimiter:
    """Per-process rolling window; rejected calls do not consume a slot."""

    def __init__(
        self,
        limit: int = 30,
        window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._lock = Lock()
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, api_key_id: str) -> bool:
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            events = self._events[api_key_id]
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= self.limit:
                return False
            events.append(now)
            return True


class QuotaExceeded(Exception):
    """Daily cost or large-tier quota could not reserve this request."""

    def __init__(self, quota: str) -> None:
        super().__init__(quota)
        self.quota = quota


@dataclass(frozen=True)
class QuotaReservation:
    request_id: str
    api_key_id: str
    utc_day: str
    cost_reserved: float
    large_reserved: int


class QuotaLedger:
    """Transaction-safe UTC quota reservations backed by the audit SQLite file."""

    def __init__(
        self,
        database_path: str | Path,
        audit_log: AuditLog,
        cost_limit_usd: float = 1.0,
        large_tier_limit: int = 10,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self.database_path = str(database_path)
        self.audit_log = audit_log
        self.cost_limit_usd = cost_limit_usd
        self.large_tier_limit = large_tier_limit
        self._clock = clock

    def _today(self) -> date:
        current = self._clock()
        if current.tzinfo is None:
            raise ValueError("Quota clock must return a timezone-aware datetime")
        return current.astimezone(timezone.utc).date()

    def reserve(
        self,
        request_id: str,
        api_key_id: str,
        cost: float,
        large_requests: int,
    ) -> QuotaReservation:
        if not math.isfinite(cost) or cost < 0 or large_requests not in {0, 1}:
            raise ValueError("Quota reservation values are invalid")
        utc_day = self._today().isoformat()
        connection = sqlite3.connect(self.database_path, timeout=15)
        try:
            connection.execute("PRAGMA busy_timeout = 15000")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "INSERT OR IGNORE INTO daily_usage (api_key_id, utc_day) VALUES (?, ?)",
                (api_key_id, utc_day),
            )
            usage = connection.execute(
                "SELECT committed_cost, reserved_cost, committed_large, reserved_large "
                "FROM daily_usage WHERE api_key_id = ? AND utc_day = ?",
                (api_key_id, utc_day),
            ).fetchone()
            if usage[0] + usage[1] + cost > self.cost_limit_usd + 1e-12:
                connection.rollback()
                raise QuotaExceeded("daily_cost")
            if usage[2] + usage[3] + large_requests > self.large_tier_limit:
                connection.rollback()
                raise QuotaExceeded("daily_large_tier")
            connection.execute(
                "UPDATE daily_usage SET reserved_cost = reserved_cost + ?, "
                "reserved_large = reserved_large + ? WHERE api_key_id = ? AND utc_day = ?",
                (cost, large_requests, api_key_id, utc_day),
            )
            connection.execute(
                "INSERT INTO quota_reservations "
                "(request_id, api_key_id, utc_day, cost_reserved, large_reserved) "
                "VALUES (?, ?, ?, ?, ?)",
                (request_id, api_key_id, utc_day, cost, large_requests),
            )
            connection.commit()
            return QuotaReservation(request_id, api_key_id, utc_day, cost, large_requests)
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def reconcile(self, reservation: QuotaReservation, actual_cost: float, actual_large: int) -> bool:
        if (
            not math.isfinite(actual_cost)
            or actual_cost < 0
            or actual_cost > reservation.cost_reserved + 1e-12
            or actual_large not in {0, 1}
            or actual_large > reservation.large_reserved
        ):
            raise ValueError("Final quota usage exceeds its reservation")
        connection = sqlite3.connect(self.database_path, timeout=15)
        try:
            connection.execute("PRAGMA busy_timeout = 15000")
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT api_key_id, utc_day, cost_reserved, large_reserved "
                "FROM quota_reservations WHERE request_id = ?",
                (reservation.request_id,),
            ).fetchone()
            if row is None:
                connection.rollback()
                return False
            connection.execute(
                "UPDATE daily_usage SET reserved_cost = reserved_cost - ?, "
                "committed_cost = committed_cost + ?, reserved_large = reserved_large - ?, "
                "committed_large = committed_large + ? WHERE api_key_id = ? AND utc_day = ?",
                (row[2], actual_cost, row[3], actual_large, row[0], row[1]),
            )
            connection.execute(
                "DELETE FROM quota_reservations WHERE request_id = ?", (reservation.request_id,)
            )
            connection.commit()
            return True
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
