"""SQLite persistence for minimal, privacy-safe request metadata."""

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from contextlib import contextmanager
from typing import Iterator


@dataclass(frozen=True)
class AuditRecord:
    request_id: str
    api_key_id: str | None
    timestamp_utc: str
    task_type: str | None = None
    effective_sensitivity: str | None = None
    selected_model: str | None = None
    selected_tier: str | None = None
    status: int = 500
    retry_count: int = 0
    attempt_count: int = 0
    fallback_used: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    estimated_cost_usd: float | None = None
    latency_ms: float | None = None
    failure_category: str | None = None


class AuditLog:
    """Creates a WAL database and inserts one sanitized row per route request."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        if self.database_path != ":memory:":
            Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=15)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout = 15000")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS request_audit (
                    request_id TEXT PRIMARY KEY,
                    api_key_id TEXT,
                    timestamp_utc TEXT NOT NULL,
                    task_type TEXT,
                    effective_sensitivity TEXT,
                    selected_model TEXT,
                    selected_tier TEXT,
                    status INTEGER NOT NULL,
                    retry_count INTEGER NOT NULL,
                    attempt_count INTEGER NOT NULL,
                    fallback_used INTEGER NOT NULL,
                    input_tokens INTEGER,
                    output_tokens INTEGER,
                    estimated_cost_usd REAL,
                    latency_ms REAL,
                    failure_category TEXT
                );
                CREATE TABLE IF NOT EXISTS daily_usage (
                    api_key_id TEXT NOT NULL,
                    utc_day TEXT NOT NULL,
                    committed_cost REAL NOT NULL DEFAULT 0,
                    reserved_cost REAL NOT NULL DEFAULT 0,
                    committed_large INTEGER NOT NULL DEFAULT 0,
                    reserved_large INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (api_key_id, utc_day)
                );
                CREATE TABLE IF NOT EXISTS quota_reservations (
                    request_id TEXT PRIMARY KEY,
                    api_key_id TEXT NOT NULL,
                    utc_day TEXT NOT NULL,
                    cost_reserved REAL NOT NULL,
                    large_reserved INTEGER NOT NULL
                );
                """
            )

    def record(self, record: AuditRecord) -> None:
        values = (
            record.request_id,
            record.api_key_id,
            record.timestamp_utc,
            record.task_type,
            record.effective_sensitivity,
            record.selected_model,
            record.selected_tier,
            record.status,
            record.retry_count,
            record.attempt_count,
            int(record.fallback_used),
            record.input_tokens,
            record.output_tokens,
            record.estimated_cost_usd,
            record.latency_ms,
            record.failure_category,
        )
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO request_audit (
                    request_id, api_key_id, timestamp_utc, task_type,
                    effective_sensitivity, selected_model, selected_tier, status,
                    retry_count, attempt_count, fallback_used, input_tokens,
                    output_tokens, estimated_cost_usd, latency_ms, failure_category
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                values,
            )
            connection.commit()

    def rows(self) -> list[sqlite3.Row]:
        with self._connection() as connection:
            return list(connection.execute("SELECT * FROM request_audit ORDER BY timestamp_utc, request_id"))
