"""The migration runner (app/core/db.py) against a fake psycopg connection.

The real SQL runs against Postgres in the end-to-end run. This checks the runner's own logic:
order, skipping applied versions, the advisory lock, and that its log lines don't crash.
"""

import contextlib
import json

import pytest

from app.core import db
from app.core.logging import setup_logging
from app.migrations import MIGRATIONS


class FakeConnection:
    def __init__(self, applied=()):
        self.applied = set(applied)
        self.statements: list[str] = []

    def execute(self, sql, params=None):
        self.statements.append(sql)
        if sql.startswith("SELECT version FROM schema_migrations"):
            return [(v,) for v in sorted(self.applied)]
        if sql.startswith("INSERT INTO schema_migrations"):
            self.applied.add(params[0])
        return []

    @contextlib.contextmanager
    def transaction(self):
        yield

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def conn(monkeypatch):
    fake = FakeConnection()
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/trips")
    monkeypatch.setattr(db.psycopg, "connect", lambda *a, **k: fake)
    return fake


def test_applies_every_migration_in_order_under_the_lock(conn, capsys):
    setup_logging("trip-service")  # the real JSON handler, on pytest's captured stdout
    assert db.migrate(MIGRATIONS) == len(MIGRATIONS)
    assert conn.applied == {v for v, _, _ in MIGRATIONS}
    assert conn.statements[0].startswith("SELECT pg_advisory_lock")
    assert conn.statements[-1].startswith("SELECT pg_advisory_unlock")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [e["migration"] for e in lines if e["msg"] == "migration applied"] == [
        name for _, name, _ in sorted(MIGRATIONS)
    ]


def test_a_second_run_applies_nothing(conn):
    db.migrate(MIGRATIONS)
    assert db.migrate(MIGRATIONS) == 0


def test_pool_sizes_come_from_the_environment(monkeypatch):
    monkeypatch.delenv("DB_POOL_MIN", raising=False)
    monkeypatch.delenv("DB_POOL_MAX", raising=False)
    assert db.pool_sizes() == {"min_size": 1, "max_size": 10}
    monkeypatch.setenv("DB_POOL_MIN", "4")
    monkeypatch.setenv("DB_POOL_MAX", "8")
    assert db.pool_sizes() == {"min_size": 4, "max_size": 8}
    monkeypatch.setenv("DB_POOL_MIN", "20")  # more than the max: clamped, the pool would refuse it
    assert db.pool_sizes() == {"min_size": 8, "max_size": 8}
