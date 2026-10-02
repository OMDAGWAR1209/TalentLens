"""Smoke test for the PostgreSQL backend — run this before trusting it.

The PostgreSQL path (app/db_backend.py, app/schema_postgres.sql) was written carefully
but has never been run against a real PostgreSQL server in this project's build
environment, which has neither PostgreSQL nor network access to install the driver.
This script is the fast way to find out if it actually works, before running the full
application against it.

    docker compose --profile postgres up -d db
    pip install -r requirements.txt
    set DATABASE_URL=postgresql://talentlens:talentlens@localhost:5432/talentlens   (Windows)
    export DATABASE_URL=postgresql://talentlens:talentlens@localhost:5432/talentlens (Mac/Linux)
    python scripts/check_postgres.py

Exits 0 and prints "ALL CHECKS PASSED" if everything works. Any failure prints exactly
which step broke and the raw error, since that is what needs fixing.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

CHECKS_RUN, CHECKS_FAILED = [], []


def check(label):
    def deco(fn):
        def wrapped(*a, **k):
            try:
                fn(*a, **k)
                CHECKS_RUN.append(label)
                print(f"  OK   {label}")
            except Exception as exc:  # noqa: BLE001 — we want to see every failure, not stop at the first
                CHECKS_FAILED.append((label, exc))
                print(f"  FAIL {label}")
                print(f"       {type(exc).__name__}: {exc}")
        return wrapped
    return deco


def main() -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith(("postgres://", "postgresql://")):
        print("DATABASE_URL is not set to a postgres:// URL — nothing to check.")
        print("Example: export DATABASE_URL=postgresql://talentlens:talentlens@localhost:5432/talentlens")
        sys.exit(1)
    print(f"Checking PostgreSQL at: {url.split('@')[-1]}\n")

    from app.db import Database, DBError
    from app import services as S

    db = None

    @check("driver import (psycopg)")
    def t_driver():
        import psycopg  # noqa: F401

    @check("connect and run schema_postgres.sql")
    def t_connect():
        nonlocal db
        db = Database(url)

    @check("backend reports 'postgres'")
    def t_backend():
        assert db.backend == "postgres", db.backend

    @check("all 12 tables exist")
    def t_tables():
        stats = db.stats()
        assert len(stats["tables"]) == 12, stats["tables"]

    @check("insert + read a user (JSON, hashing, SERIAL id all round-trip)")
    def t_user():
        u = db.create_user(f"pgcheck+{os.getpid()}@example.com", "PG Check", "Welcome1A", "candidate")
        assert u["user_id"] and isinstance(u["user_id"], int)
        fetched = db.get_user(u["user_id"])
        assert fetched["email"] == u["email"]

    @check("duplicate email raises DBError, not a raw driver exception")
    def t_duplicate():
        email = f"pgdupe+{os.getpid()}@example.com"
        db.create_user(email, "First", "Welcome1A", "candidate")
        try:
            db.create_user(email, "Second", "Welcome1A", "candidate")
            raise AssertionError("duplicate email was accepted")
        except DBError:
            pass

    @check("JSON column round-trips as a Python list, not a string or an empty default")
    def t_json():
        job = db.create_job({"job_title": "PG Check Role", "must_have": ["Python", "SQL"],
                             "nice_to_have": ["Docker"]})
        fetched = db.get_job(job["job_id"])
        assert fetched["must_have"] == ["Python", "SQL"], fetched["must_have"]

    @check("booleans store and read back correctly (not 1/0 ints)")
    def t_boolean():
        job = db.get_job(db.list_jobs()[0]["job_id"])
        assert job["is_open"] is True, (job["is_open"], type(job["is_open"]))

    @check("ATS stage machine: illegal jump is rejected, legal one is logged")
    def t_ats():
        cand = db.upsert_candidate(
            {"identity_hash": f"pgcheck{os.getpid()}" * 2, "name": "PG Candidate", "email": "x@x.com",
             "provenance": {}, "skills": [], "education": {}, "experience": {}},
            {"field_recall": 90, "mean_confidence": 90, "source_format": "txt"}, "uploads/x.txt")[0]
        job = db.list_jobs()[0]
        from app.matching import match
        result = match(db.profile(cand), db.job_spec(job["job_id"]))
        db.save_match(cand, job["job_id"], result,
                      {"missing_skills": [], "recommendations": [], "coverage_by_category": {}, "summary": "ok"})
        try:
            db.set_stage(cand, job["job_id"], "hired")
            raise AssertionError("illegal stage jump was allowed")
        except DBError:
            pass
        moved = db.set_stage(cand, job["job_id"], "screening")
        assert moved["status"] == "screening"

    @check("table_columns() works (powers the admin database browser)")
    def t_columns():
        cols = db.conn.table_columns("candidates")
        assert "candidate_id" in cols and "identity_hash" in cols, cols

    @check("timestamps come back as strings, not datetime objects (session expiry check needs this)")
    def t_timestamps():
        row = db.one("SELECT created_at FROM users LIMIT 1")
        assert isinstance(row["created_at"], str), type(row["created_at"])

    for fn in (t_driver, t_connect, t_backend, t_tables, t_user, t_duplicate, t_json,
              t_boolean, t_ats, t_columns, t_timestamps):
        fn()

    print(f"\n{len(CHECKS_RUN)} passed, {len(CHECKS_FAILED)} failed")
    if CHECKS_FAILED:
        print("\nNot ready yet — fix the failures above before running the full application "
              "against PostgreSQL.")
        sys.exit(1)
    print("ALL CHECKS PASSED — the PostgreSQL backend is working.")


if __name__ == "__main__":
    main()
