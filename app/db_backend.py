"""Picks the database backend and normalises it to one interface.

TalentLens runs on SQLite by default — no install, no server, the whole database is one
file. Setting the `DATABASE_URL` environment variable to a `postgres://` URL switches to
PostgreSQL instead, for a deployment with multiple API instances behind a load balancer,
where a single file does not work.

Everything in `db.py` is written once, against this one interface:

    conn.execute(sql, params)       -> a cursor with .fetchone() / .fetchall()
    conn.executemany(sql, seq)
    conn.commit() / conn.rollback()
    conn.insert_returning(sql, params, pk_column)   -> the new row's id

`sql` is always written with `?` placeholders, in SQLite's style, because that is what
the whole codebase already uses. The PostgreSQL wrapper rewrites `?` to `%s` before
sending the query — safe here because every SQL string in this project is a fixed
literal written by a developer, never built from table data, so there is no `?`
character to confuse with a placeholder by accident.

--------------------------------------------------------------------------------------
HONEST STATUS: the PostgreSQL path is implemented and reviewed carefully, but it has
*not* been run against a real PostgreSQL server — this project's build environment has
no PostgreSQL and no network access to install the `psycopg` driver, so there was no way
to test it the way SQLite was tested (130 passing tests). Before trusting it:

    1. docker compose --profile postgres up -d db
    2. pip install -r requirements.txt            # now includes psycopg[binary]
    3. set DATABASE_URL=postgresql://talentlens:talentlens@localhost:5432/talentlens
    4. python scripts/check_postgres.py            # a smoke test written for this purpose
    5. pytest tests/test_postgres.py                # runs the same checks as the SQLite
       suite, but only if DATABASE_URL points at Postgres — it skips itself otherwise

If step 4 or 5 finds a problem, that is expected for unverified code, not a sign
something is secretly wrong with the rest of the project — report it and it gets fixed.
--------------------------------------------------------------------------------------
"""

from __future__ import annotations

import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional, Sequence

SCHEMA_SQLITE = Path(__file__).with_name("schema.sql")
SCHEMA_POSTGRES = Path(__file__).with_name("schema_postgres.sql")


class BackendError(Exception):
    """A backend could not be reached or initialised."""


# --------------------------------------------------------------------------- #
# SQLite — the default
# --------------------------------------------------------------------------- #
class SQLiteConn:
    kind = "sqlite"

    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.RLock()
        self._c = sqlite3.connect(path, check_same_thread=False)
        self._c.row_factory = sqlite3.Row
        self._c.execute("PRAGMA foreign_keys = ON")
        if path != ":memory:":
            self._c.execute("PRAGMA journal_mode = WAL")

    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self._c.execute(sql, params)

    def executemany(self, sql: str, seq) -> None:
        self._c.executemany(sql, seq)

    def insert_returning(self, sql: str, params: Sequence[Any], pk_column: str) -> int:
        return self._c.execute(sql, params).lastrowid

    def table_columns(self, table: str) -> list:
        return [row[1] for row in self._c.execute(f"PRAGMA table_info({table})").fetchall()]

    def init_schema(self) -> None:
        self._c.executescript(SCHEMA_SQLITE.read_text(encoding="utf-8"))

    def commit(self) -> None:
        self._c.commit()

    def rollback(self) -> None:
        self._c.rollback()

    def close(self) -> None:
        self._c.close()


# --------------------------------------------------------------------------- #
# PostgreSQL — opt in via DATABASE_URL, UNTESTED in this build (see module docstring)
# --------------------------------------------------------------------------- #
class PostgresConn:
    kind = "postgres"
    _PLACEHOLDER = re.compile(r"\?")

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg
            from psycopg.rows import dict_row
        except ImportError as exc:
            raise BackendError(
                "DATABASE_URL is a PostgreSQL URL but the 'psycopg' driver is not "
                "installed. Run: pip install \"psycopg[binary]\""
            ) from exc
        self.lock = threading.RLock()
        self._psycopg = psycopg
        self._c = psycopg.connect(dsn, row_factory=dict_row, autocommit=False)

    # a handful of queries call SQLite's datetime()/date() functions inline (e.g.
    # "SET updated_at = datetime('now')"); these are literal SQL syntax, never data,
    # so a fixed-string translation here is safe and keeps db.py's queries portable
    _FUNCS = (
        (re.compile(r"datetime\(\s*'now'\s*\)"), "now()"),
        (re.compile(r"date\(\s*'now'\s*\)"), "CURRENT_DATE"),
    )

    def _pg(self, sql: str) -> str:
        for pattern, replacement in self._FUNCS:
            sql = pattern.sub(replacement, sql)
        return self._PLACEHOLDER.sub("%s", sql)

    def execute(self, sql: str, params: Sequence[Any] = ()):
        cur = self._c.cursor()
        cur.execute(self._pg(sql), tuple(params))
        return cur

    def executemany(self, sql: str, seq) -> None:
        cur = self._c.cursor()
        cur.executemany(self._pg(sql), [tuple(p) for p in seq])

    def insert_returning(self, sql: str, params: Sequence[Any], pk_column: str) -> int:
        sql = sql.rstrip().rstrip(";")
        if "returning" not in sql.lower():
            sql += f" RETURNING {pk_column}"
        cur = self._c.cursor()
        cur.execute(self._pg(sql), tuple(params))
        return cur.fetchone()[pk_column]

    def table_columns(self, table: str) -> list:
        cur = self._c.cursor()
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position", (table,))
        return [row["column_name"] for row in cur.fetchall()]

    def init_schema(self) -> None:
        cur = self._c.cursor()
        cur.execute(SCHEMA_POSTGRES.read_text(encoding="utf-8"))
        self._c.commit()

    def commit(self) -> None:
        self._c.commit()

    def rollback(self) -> None:
        self._c.rollback()

    def close(self) -> None:
        self._c.close()


def open_connection(url: str):
    """`url` is either a filesystem path (SQLite) or a postgres://… URL."""
    if url.startswith(("postgres://", "postgresql://")):
        return PostgresConn(url)
    return SQLiteConn(url)


def integrity_errors() -> tuple:
    """Exception types that mean 'a constraint was violated' on whichever backend is in use.

    Caught as `except integrity_errors():`, since the SQLite and psycopg exception
    classes are unrelated to each other.
    """
    import sqlite3
    errors = [sqlite3.IntegrityError]
    try:
        import psycopg
        errors.append(psycopg.IntegrityError)
    except ImportError:
        pass
    return tuple(errors)
