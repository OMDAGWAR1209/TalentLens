"""Database, authentication and role-based access tests.

These run against a real SQLite file in a temporary directory — not mocks — so the
schema, constraints, foreign keys and queries are all exercised exactly as in production.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import services as S  # noqa: E402
from app.auth import AuthError, can, hash_password, verify_password  # noqa: E402
from app.db import Database, DBError  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "sample_resumes"


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    database = Database(tmp_path_factory.mktemp("db") / "talentlens.db")
    S.seed_demo(database, FIXTURES)
    return database


@pytest.fixture(scope="module")
def admin(db):
    return S.login(db, "admin@talentlens.dev", "Admin@2026")["user"]


@pytest.fixture(scope="module")
def recruiter(db):
    return S.login(db, "recruiter@talentlens.dev", "Recruit@2026")["user"]


@pytest.fixture(scope="module")
def candidate(db):
    return S.login(db, "candidate@talentlens.dev", "Candidate@2026")["user"]


# --------------------------------------------------------------------------- #
# schema matches the project diagram
# --------------------------------------------------------------------------- #
DIAGRAM = {
    "candidates": {"candidate_id", "name", "email", "phone", "education", "experience",
                   "resume_path", "created_at"},
    "candidate_match": {"match_id", "candidate_id", "job_id", "match_score", "hiring_score", "status"},
    "jobs": {"job_id", "job_title", "department", "required_skills", "experience_required",
             "job_description", "created_at"},
    "skill_gap_report": {"report_id", "candidate_id", "job_id", "missing_skills",
                         "recommended_courses", "generated_at"},
    "interview_results": {"result_id", "candidate_id", "job_id", "score", "feedback", "interview_date"},
    "interview_questions": {"question_id", "job_id", "question_text", "difficulty_level", "category"},
}


def test_every_column_in_the_diagram_exists(db):
    for table, columns in DIAGRAM.items():
        actual = {r[1] for r in db.conn.execute(f"PRAGMA table_info({table})")}
        missing = columns - actual
        assert not missing, f"{table} is missing {missing} from the schema diagram"


def test_foreign_keys_are_enforced(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO candidate_match(candidate_id, job_id, match_score, hiring_score) "
                        "VALUES (99999, 99999, 1, 1)")
    db.conn.rollback()


def test_check_constraints_reject_bad_values(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("UPDATE candidate_match SET status = 'promoted' WHERE match_id = 1")
    db.conn.rollback()
    with pytest.raises(sqlite3.IntegrityError):
        db.conn.execute("INSERT INTO users(email, full_name, password_hash, role) "
                        "VALUES ('x@y.z', 'X', 'h', 'superuser')")
    db.conn.rollback()


def test_seed_populates_every_core_table(db):
    t = db.stats()["tables"]
    assert t["users"] == 3 and t["jobs"] == 4 and t["candidates"] == 8
    assert t["candidate_match"] == 32 and t["skill_gap_report"] == 32
    assert t["ats_events"] > 0


def test_seed_is_idempotent(db):
    S.seed_demo(db, FIXTURES)
    t = db.stats()["tables"]
    assert t["users"] == 3 and t["candidates"] == 8 and t["jobs"] == 4


def test_data_survives_reopening_the_file(db):
    again = Database(db.path)
    assert again.stats()["tables"]["candidates"] == 8
    again.close()


# --------------------------------------------------------------------------- #
# duplicates — regression for a real bug
# --------------------------------------------------------------------------- #
def test_thin_duplicate_does_not_overwrite_a_complete_profile(db):
    """The sample set uploads Ananya twice; the second copy has far fewer skills. It used
    to overwrite her full profile and drop her out of the platform-engineer shortlist."""
    ananya = next(c for c in db.list_candidates() if c["name"] == "Ananya Krishnan")
    assert len(ananya["skills"]) > 15
    strong = {m["candidate_name"] for m in db.list_matches() if m["band"] == "strong"}
    assert strong == {"Ananya Krishnan", "Michael Chen", "Nisha Menon"}
    assert any("duplicate" in a["action"] for a in db.audit_rows())


def test_database_results_match_the_in_memory_pipeline(db, recruiter):
    k = S.dashboard(db, recruiter)["kpis"]
    assert k["shortlist_rate"] == 37.5
    assert k["average_best_match"] == 76.5


# --------------------------------------------------------------------------- #
# authentication
# --------------------------------------------------------------------------- #
def test_passwords_are_hashed_with_salt():
    a, b = hash_password("Secret123", 1000), hash_password("Secret123", 1000)
    assert a != b and a.startswith("pbkdf2_sha256$")
    assert verify_password("Secret123", a)
    assert not verify_password("secret123", a)


def test_no_plaintext_password_is_stored(db):
    for row in db.all("SELECT password_hash FROM users"):
        assert row["password_hash"].startswith("pbkdf2_sha256$")
        assert "2026" not in row["password_hash"]


def test_login_with_wrong_password_fails_without_revealing_which_part(db):
    with pytest.raises(AuthError) as bad_pw:
        S.login(db, "admin@talentlens.dev", "Wrong@2026")
    with pytest.raises(AuthError) as bad_user:
        S.login(db, "nobody@talentlens.dev", "Wrong@2026")
    assert str(bad_pw.value) == str(bad_user.value)


def test_login_on_the_wrong_portal_is_refused(db):
    """A candidate cannot sign in through the recruiter or admin login page."""
    with pytest.raises(AuthError) as err:
        S.login(db, "candidate@talentlens.dev", "Candidate@2026", role="admin")
    assert err.value.status == 403


def test_session_token_resolves_and_logout_revokes_it(db):
    out = S.login(db, "recruiter@talentlens.dev", "Recruit@2026")
    assert db.user_for_token(out["token"])["role"] == "recruiter"
    S.logout(db, out["token"])
    with pytest.raises(AuthError):
        db.user_for_token(out["token"])


def test_only_a_hash_of_the_token_is_stored(db):
    out = S.login(db, "recruiter@talentlens.dev", "Recruit@2026")
    assert db.one("SELECT 1 FROM auth_sessions WHERE token_hash = ?", (out["token"],)) is None


def test_public_registration_creates_candidates_only(db):
    u = S.register(db, "new.person@example.com", "New Person", "Welcome1A")
    assert u["role"] == "candidate"


def test_weak_passwords_are_rejected(db):
    for bad in ("short1A", "alllowercase1", "NoDigitsHere"):
        with pytest.raises(AuthError):
            S.register(db, f"{bad}@example.com", "X", bad)


def test_duplicate_email_is_rejected(db):
    with pytest.raises(DBError) as err:
        S.register(db, "ADMIN@talentlens.dev", "Copycat", "Welcome1A")
    assert err.value.status == 409


def test_disabled_account_cannot_sign_in(db, admin):
    u = S.register(db, "temp.user@example.com", "Temp", "Welcome1A")
    S.update_user(db, admin, u["user_id"], is_active=False)
    with pytest.raises(AuthError):
        S.login(db, "temp.user@example.com", "Welcome1A")


# --------------------------------------------------------------------------- #
# role-based access
# --------------------------------------------------------------------------- #
def test_permission_matrix():
    assert can("admin", "users.write") and not can("recruiter", "users.write")
    assert can("recruiter", "ats.write") and not can("candidate", "ats.write")
    assert can("candidate", "gap.read_own") and not can("candidate", "candidates.read")


def test_candidate_sees_only_their_own_matches(db, candidate):
    rows = S.my_matches(db, candidate)
    assert rows and {r["candidate_id"] for r in rows} == {candidate["candidate_id"]}


def test_candidate_cannot_read_another_candidates_gap_report(db, candidate):
    other = next(c for c in db.list_candidates() if c["candidate_id"] != candidate["candidate_id"])
    with pytest.raises(AuthError) as err:
        S.gap_report(db, candidate, other["candidate_id"], 1)
    assert err.value.status == 403
    assert S.gap_report(db, candidate, candidate["candidate_id"], 1)["missing_skills"] is not None


def test_candidate_cannot_list_candidates_or_move_ats(db, candidate):
    with pytest.raises(AuthError):
        S.list_candidates(db, candidate)
    with pytest.raises(AuthError):
        S.move_stage(db, candidate, candidate["candidate_id"], 1, "hired")


def test_recruiter_cannot_manage_users_or_open_the_db_console(db, recruiter):
    with pytest.raises(AuthError):
        S.create_staff(db, recruiter, "x@talentlens.dev", "X", "Welcome1A", "admin")
    with pytest.raises(AuthError):
        S.admin_overview(db, recruiter)


def test_admin_can_create_a_recruiter(db, admin):
    u = S.create_staff(db, admin, "second.recruiter@talentlens.dev", "Second", "Welcome1A", "recruiter")
    assert u["role"] == "recruiter"


def test_admin_cannot_lock_themselves_out(db, admin):
    with pytest.raises(AuthError):
        S.update_user(db, admin, admin["user_id"], is_active=False)


# --------------------------------------------------------------------------- #
# ATS + interviews persist
# --------------------------------------------------------------------------- #
def test_ats_transitions_are_enforced_in_the_database(db, recruiter):
    m = next(m for m in db.list_matches() if m["status"] == "applied")
    with pytest.raises(DBError):
        S.move_stage(db, recruiter, m["candidate_id"], m["job_id"], "hired")
    moved = S.move_stage(db, recruiter, m["candidate_id"], m["job_id"], "screening")
    assert moved["status"] == "screening"


def test_every_stage_change_is_logged(db):
    for e in db.ats_events(200):
        assert e["to_stage"] and e["candidate_name"]


def test_recruiter_interview_is_stored_and_advances_the_ats(db, recruiter):
    m = next(m for m in db.list_matches() if m["status"] == "screening")
    started = S.start_interview(db, recruiter, m["candidate_id"], m["job_id"], total=4)
    out = None
    for _ in range(4):
        out = S.answer_interview(db, recruiter, started["session_id"],
                                 "I profiled the slow stage, rewrote it and cut latency by forty percent.")
    assert out["finished"] and out["report"]["score"] is not None
    assert db.get_match(m["candidate_id"], m["job_id"])["status"] == "interview_completed"
    assert db.list_results(m["candidate_id"])[0]["score"] is not None


def test_candidate_practice_interview_does_not_touch_the_ats(db, candidate):
    before = db.get_match(candidate["candidate_id"], 2)["status"]
    s = S.start_interview(db, candidate, candidate["candidate_id"], 2, total=3)
    for _ in range(3):
        S.answer_interview(db, candidate, s["session_id"], "A detailed answer about deploying ML models.")
    assert db.get_match(candidate["candidate_id"], 2)["status"] == before


def test_candidate_cannot_open_someone_elses_interview(db, candidate):
    other = next(c for c in db.list_candidates() if c["candidate_id"] != candidate["candidate_id"])
    with pytest.raises(AuthError):
        S.start_interview(db, candidate, other["candidate_id"], 1)


def test_voice_screen_is_stored(db, recruiter):
    summary = S.voice_assess(db, recruiter, 3, 2, [
        {"transcript": "I built a forecasting model in PyTorch and cut stockouts by eighteen percent.",
         "duration_seconds": 30}])
    assert summary["score"] is not None
    assert any(r["mode"] == "voice" for r in db.list_results(3))


def test_survey_responses_persist_and_summarise(db, candidate):
    for _ in range(8):
        S.submit_survey(db, candidate, 5, [5, 1, 5, 1, 5, 1, 5, 1, 5, 1])
    s = S.survey_summary(db)
    assert s["responses"] >= 8 and s["sample_sufficient"]


def test_admin_db_console_hides_secrets(db, admin):
    users = db.table_preview("users")
    assert all("hidden" in r["password_hash"] for r in users["rows"])
    with pytest.raises(DBError):
        db.table_preview("sqlite_master")
