"""Optional PostgreSQL verification — skips itself unless a real server is available.

The rest of this project's test suite (test_acceptance, test_matching, test_interview,
test_milestone4, test_database — 130 tests) runs against SQLite and needs nothing extra.
This file mirrors the important checks from test_database.py against PostgreSQL instead,
because that backend could not be run even once while this project was built: there was
no PostgreSQL server and no network access to install the `psycopg` driver in the build
environment. Every test here is something the SQLite suite already proved works — this
file exists only to prove the *same* behaviour holds once translated to PostgreSQL's
SQL dialect.

Run it once you have PostgreSQL available:

    docker compose --profile postgres up -d db
    pip install -r requirements.txt
    set DATABASE_URL=postgresql://talentlens:talentlens@localhost:5432/talentlens  (Windows)
    pytest tests/test_postgres.py -v

With no DATABASE_URL pointing at postgres://, every test below is skipped — this file
makes no claim about the PostgreSQL path except what these tests, once actually run,
report.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PG_URL = os.environ.get("DATABASE_URL", "")
pytestmark = pytest.mark.skipif(
    not PG_URL.startswith(("postgres://", "postgresql://")),
    reason="set DATABASE_URL to a postgres:// URL to run this suite")

FIXTURES = Path(__file__).resolve().parents[1] / "sample_resumes"


@pytest.fixture(scope="module")
def db():
    from app.db import Database
    database = Database(PG_URL)
    yield database
    # leave no trace in a shared database between runs
    for table in reversed(database.TABLES):
        if table != "schema_version":
            database.conn.execute(f"DELETE FROM {table}")
    database.conn.commit()


@pytest.fixture(scope="module")
def seeded(db):
    from app import services as S
    return S.seed_demo(db, FIXTURES)


def test_connects_and_reports_postgres(db):
    assert db.backend == "postgres"


def test_schema_has_every_table(db):
    assert set(db.stats()["tables"]) == set(db.TABLES)


def test_seed_runs_against_postgres(seeded):
    assert seeded["accepted"] >= 8


def test_json_columns_round_trip_as_python_objects(db, seeded):
    """The bug this guards against: psycopg auto-decodes JSON columns, so the app's
    own decode step must not double-decode (or silently swallow) an already-parsed value."""
    jobs = db.list_jobs()
    job = next(j for j in jobs if j["must_have"])
    assert isinstance(job["must_have"], list)
    assert all(isinstance(s, str) for s in job["must_have"])


def test_booleans_are_real_booleans_not_integers(db, seeded):
    job = db.list_jobs()[0]
    assert job["is_open"] is True


def test_timestamps_are_plain_strings(db, seeded):
    """The bug this guards against: psycopg returns TIMESTAMPTZ as a datetime object,
    which would crash auth.is_expired() on the very first login."""
    row = db.one("SELECT created_at FROM candidates LIMIT 1")
    assert isinstance(row["created_at"], str)


def test_login_and_session_work_end_to_end(db, seeded):
    from app import services as S
    out = S.login(db, "recruiter@talentlens.dev", "Recruit@2026")
    assert db.user_for_token(out["token"])["role"] == "recruiter"
    S.logout(db, out["token"])


def test_ats_stage_machine_is_enforced(db, seeded):
    from app.auth import AuthError
    from app.db import DBError
    m = next(m for m in db.list_matches() if m["status"] == "applied")
    with pytest.raises(DBError):
        db.set_stage(m["candidate_id"], m["job_id"], "hired")
    moved = db.set_stage(m["candidate_id"], m["job_id"], "screening")
    assert moved["status"] == "screening"


def test_duplicate_email_is_a_clean_db_error(db):
    from app.db import DBError
    db.create_user("pg.dup@example.com", "First", "Welcome1A", "candidate")
    with pytest.raises(DBError):
        db.create_user("pg.dup@example.com", "Second", "Welcome1A", "candidate")


def test_matching_and_gap_reports_persist(db, seeded):
    from app import services as S
    c = db.list_candidates()[0]
    S.score_candidate(db, c["candidate_id"])
    matches = db.list_matches(candidate_id=c["candidate_id"])
    assert matches
    gap = db.get_gap(c["candidate_id"], matches[0]["job_id"])
    assert gap is not None and isinstance(gap["missing_skills"], list)


def test_admin_table_browser_masks_password_hash(db, seeded):
    rows = db.table_preview("users")
    assert all("hidden" in r["password_hash"] for r in rows["rows"])
