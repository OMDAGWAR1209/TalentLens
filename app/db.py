"""Data access layer — SQLite by default, PostgreSQL if `DATABASE_URL` asks for it.

No ORM: the schema is one readable file per backend (`schema.sql` / `schema_postgres.sql`)
that maps one-to-one onto the project's database diagram, every query is parameterised,
foreign keys are enforced, and writes run inside transactions. `db_backend.py` is the one
place that knows the difference between the two engines — read its docstring for the
honest status of the PostgreSQL path, which has not been run against a real server in
this build environment. Everything below this line is written once and runs on either.

Two rules are enforced here rather than trusted to callers:
  * ATS stage changes must follow the pipeline (applied -> screening -> ...). An
    illegal move raises, and every legal one appends to `ats_events`.
  * A candidate re-uploading a resume updates their existing record (matched on
    identity hash), so the same person never appears twice.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from .auth import AuthError, expiry, hash_password, is_expired, token_hash
from .db_backend import integrity_errors, open_connection
from .interview import STAGES, TERMINAL, TRANSITIONS

SCHEMA_VERSION = 1
DEFAULT_DB = Path(__file__).resolve().parents[1] / "talentlens.db"


class DBError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _j(value: Any) -> Optional[str]:
    return None if value is None else json.dumps(value, ensure_ascii=False)


def _stringify_dates(row: Dict[str, Any]) -> Dict[str, Any]:
    """Make every row look the same regardless of backend.

    SQLite stores timestamps as plain text, so a row read from it already matches the
    "%Y-%m-%d %H:%M:%S" format `auth.expiry()` writes. psycopg instead parses TIMESTAMPTZ
    and DATE columns into native datetime/date objects — correct PostgreSQL behaviour,
    but auth.is_expired() calls strptime() on the value and would raise TypeError on the
    very first login check if it ever received one. Converting here, in the one place
    every row from either backend passes through, means no caller needs to know which
    backend is live.
    """
    import datetime as _dt
    for key, value in row.items():
        if isinstance(value, _dt.datetime):
            row[key] = value.strftime("%Y-%m-%d %H:%M:%S")
        elif isinstance(value, _dt.date):
            row[key] = value.strftime("%Y-%m-%d")
    return row


def _uj(value: Any, default: Any = None) -> Any:
    """Decode a JSON column. SQLite hands this a string; psycopg auto-decodes JSONB
    columns into a dict/list already, so a non-string value is returned as-is rather
    than being passed to json.loads (which would raise, get swallowed, and silently
    return `default` — turning every stored JSON field into an empty value on Postgres)."""
    if value is None or value == "":
        return default
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class Database:
    """One database, SQLite or PostgreSQL. Thread-safe for a single-process web server.

    `path` is a filesystem path for SQLite (the default) or is ignored if the
    `DATABASE_URL` environment variable holds a `postgres://` URL instead — that
    variable always wins, so a deployment sets one thing to switch engines.
    """

    def __init__(self, path: str | Path = DEFAULT_DB) -> None:
        url = os.environ.get("DATABASE_URL") or str(path)
        self.path = url
        self._lock = threading.RLock()
        self.conn = open_connection(url)
        self.backend = self.conn.kind       # "sqlite" or "postgres"
        self.migrate()

    # ------------------------------------------------------------ plumbing --
    @contextmanager
    def tx(self) -> Iterator[Any]:
        with self._lock:
            try:
                yield self.conn
                self.conn.commit()
            except Exception:
                self.conn.rollback()
                raise

    def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self.conn.execute(sql, params).fetchone()
        return _stringify_dates(dict(row)) if row else None

    def all(self, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
        with self._lock:
            return [_stringify_dates(dict(r)) for r in self.conn.execute(sql, params).fetchall()]

    def migrate(self) -> None:
        with self._lock:
            self.conn.init_schema()
            if not self.conn.execute("SELECT 1 FROM schema_version WHERE version = ?",
                                     (SCHEMA_VERSION,)).fetchone():
                self.conn.execute("INSERT INTO schema_version(version) VALUES (?)",
                                  (SCHEMA_VERSION,))
            self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --------------------------------------------------------------- users --
    def create_user(self, email: str, full_name: str, password: str, role: str,
                    candidate_id: Optional[int] = None) -> Dict[str, Any]:
        if role not in ("candidate", "recruiter", "admin"):
            raise DBError(f"unknown role '{role}'", 422)
        try:
            with self.tx() as c:
                new_id = c.insert_returning(
                    "INSERT INTO users(email, full_name, password_hash, role, candidate_id) "
                    "VALUES (?,?,?,?,?)",
                    (email.strip().lower(), full_name.strip(), hash_password(password), role,
                     candidate_id), "user_id")
        except integrity_errors() as exc:
            raise DBError("an account with that email already exists", 409) from exc
        return self.get_user(new_id)

    def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        return self.one("SELECT user_id, email, full_name, role, candidate_id, is_active, "
                        "created_at, last_login_at FROM users WHERE user_id = ?", (user_id,))

    def user_with_hash(self, email: str) -> Optional[Dict[str, Any]]:
        return self.one("SELECT * FROM users WHERE email = ?", ((email or "").strip().lower(),))

    def list_users(self, role: Optional[str] = None) -> List[Dict[str, Any]]:
        sql = ("SELECT user_id, email, full_name, role, candidate_id, is_active, created_at, "
               "last_login_at FROM users")
        if role:
            return self.all(sql + " WHERE role = ? ORDER BY user_id", (role,))
        return self.all(sql + " ORDER BY role, user_id")

    def update_user(self, user_id: int, *, role: Optional[str] = None,
                    is_active: Optional[bool] = None) -> Dict[str, Any]:
        if self.get_user(user_id) is None:
            raise DBError(f"no user {user_id}", 404)
        with self.tx() as c:
            if role is not None:
                if role not in ("candidate", "recruiter", "admin"):
                    raise DBError(f"unknown role '{role}'", 422)
                c.execute("UPDATE users SET role = ? WHERE user_id = ?", (role, user_id))
            if is_active is not None:
                c.execute("UPDATE users SET is_active = ? WHERE user_id = ?",
                          (bool(is_active), user_id))
                if not is_active:
                    c.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
        return self.get_user(user_id)

    def link_candidate(self, user_id: int, candidate_id: int) -> None:
        with self.tx() as c:
            c.execute("UPDATE users SET candidate_id = ? WHERE user_id = ?", (candidate_id, user_id))

    # ------------------------------------------------------------ sessions --
    def create_session(self, user_id: int, token: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO auth_sessions(token_hash, user_id, expires_at) VALUES (?,?,?)",
                      (token_hash(token), user_id, expiry()))
            c.execute("UPDATE users SET last_login_at = datetime('now') WHERE user_id = ?",
                      (user_id,))

    def user_for_token(self, token: str) -> Dict[str, Any]:
        row = self.one("SELECT s.expires_at, u.user_id FROM auth_sessions s "
                       "JOIN users u ON u.user_id = s.user_id WHERE s.token_hash = ?",
                       (token_hash(token),))
        if row is None:
            raise AuthError("session not recognised — sign in again")
        if is_expired(row["expires_at"]):
            self.delete_session(token)
            raise AuthError("session expired — sign in again")
        user = self.get_user(row["user_id"])
        if not user or not user["is_active"]:
            raise AuthError("this account is disabled", 403)
        return user

    def delete_session(self, token: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash(token),))

    # ---------------------------------------------------------- candidates --
    def upsert_candidate(self, profile: Dict[str, Any], quality: Dict[str, Any],
                         resume_path: str) -> Tuple[int, bool]:
        """Insert, or update the same person in place. Returns (candidate_id, created)."""
        exp = profile.get("experience") or {}
        values = {
            "name": profile.get("name"), "email": profile.get("email"),
            "phone": profile.get("phone"), "location": profile.get("location"),
            "education": _j(profile.get("education")),
            "experience": _j(exp), "experience_years": exp.get("total_years"),
            "skills": _j(profile.get("skills") or []),
            "resume_path": resume_path,
            "source_format": quality.get("source_format"),
            "field_recall": quality.get("field_recall", 0),
            "mean_confidence": quality.get("mean_confidence", 0),
            "field_provenance": _j(profile.get("provenance")),
            "needs_review": bool(quality.get("needs_review")),  # real bool: sqlite3 and psycopg both adapt it correctly, an int would not
        }
        existing = self.one("SELECT candidate_id FROM candidates WHERE identity_hash = ?",
                            (profile["identity_hash"],))
        with self.tx() as c:
            if existing:
                sets = ", ".join(f"{k} = ?" for k in values)
                c.execute(f"UPDATE candidates SET {sets}, updated_at = datetime('now') "
                          f"WHERE candidate_id = ?", (*values.values(), existing["candidate_id"]))
                return existing["candidate_id"], False
            cols = ", ".join(values) + ", identity_hash"
            marks = ", ".join("?" * (len(values) + 1))
            new_id = c.insert_returning(f"INSERT INTO candidates({cols}) VALUES ({marks})",
                                        (*values.values(), profile["identity_hash"]),
                                        "candidate_id")
            return new_id, True

    def _candidate_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        for key, default in (("education", {}), ("experience", {}), ("skills", []),
                             ("field_provenance", {})):
            out[key] = _uj(out.get(key), default)
        out["needs_review"] = bool(out.get("needs_review"))
        return out

    def get_candidate(self, candidate_id: int) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT * FROM candidates WHERE candidate_id = ?", (candidate_id,))
        return self._candidate_row(row) if row else None

    def list_candidates(self, search: Optional[str] = None) -> List[Dict[str, Any]]:
        if search:
            like = f"%{search.lower()}%"
            rows = self.all("SELECT * FROM candidates WHERE lower(name) LIKE ? OR lower(email) "
                            "LIKE ? ORDER BY field_recall DESC, candidate_id", (like, like))
        else:
            rows = self.all("SELECT * FROM candidates ORDER BY candidate_id")
        return [self._candidate_row(r) for r in rows]

    def profile(self, candidate_id: int) -> Dict[str, Any]:
        """A stored candidate in the shape the matching and interview engines consume."""
        c = self.get_candidate(candidate_id)
        if c is None:
            raise DBError(f"no candidate {candidate_id}", 404)
        exp = c["experience"] or {}
        return {"candidate_id": c["candidate_id"], "name": c["name"], "email": c["email"],
                "skills": c["skills"], "experience_years": c["experience_years"],
                "experience": exp, "education": c["education"],
                "seniority": exp.get("seniority"), "current_title": exp.get("current_title")}

    # ---------------------------------------------------------------- jobs --
    def create_job(self, job: Dict[str, Any], created_by: Optional[int] = None) -> Dict[str, Any]:
        must = list(job.get("must_have") or [])
        nice = [s for s in (job.get("nice_to_have") or []) if s not in must]
        required = list(job.get("required_skills") or (must + nice))
        if not (job.get("job_title") or job.get("title")):
            raise DBError("a job needs a title", 422)
        with self.tx() as c:
            new_id = c.insert_returning(
                "INSERT INTO jobs(job_title, department, required_skills, experience_required, "
                "job_description, must_have, nice_to_have, education_required, location, "
                "created_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job.get("job_title") or job.get("title"), job.get("department"), _j(required),
                 None if job.get("experience_required") is None else str(job["experience_required"]),
                 job.get("job_description"), _j(must or None), _j(nice or None),
                 job.get("education_required"), job.get("location"), created_by), "job_id")
        return self.get_job(new_id)

    def _job_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        for key in ("required_skills", "must_have", "nice_to_have"):
            out[key] = _uj(out.get(key), [] if key == "required_skills" else None)
        out["is_open"] = bool(out["is_open"])
        return out

    def get_job(self, job_id: int) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        return self._job_row(row) if row else None

    def list_jobs(self, open_only: bool = False) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM jobs" + (" WHERE is_open = TRUE" if open_only else "") + " ORDER BY job_id"  # TRUE/FALSE literals work on both backends; bare 1 does not compare to a boolean column in Postgres
        return [self._job_row(r) for r in self.all(sql)]

    def job_spec(self, job_id: int) -> Dict[str, Any]:
        """A stored job in the shape the matching engine consumes."""
        j = self.get_job(job_id)
        if j is None:
            raise DBError(f"no job {job_id}", 404)
        years = j["experience_required"]
        spec = {"job_id": j["job_id"], "title": j["job_title"],
                "experience_required": float(years) if years not in (None, "") else None,
                "education_required": j["education_required"],
                "job_description": j["job_description"]}
        if j["must_have"] or j["nice_to_have"]:
            spec["must_have"], spec["nice_to_have"] = j["must_have"] or [], j["nice_to_have"] or []
        else:
            spec["skills_required"] = j["required_skills"]
        return spec

    # ------------------------------------------------- matches + gap reports --
    def save_match(self, candidate_id: int, job_id: int, result: Dict[str, Any],
                   gap: Dict[str, Any]) -> Dict[str, Any]:
        """Store a score and its gap report. Re-scoring never resets the ATS stage."""
        skills = result["score_breakdown"]["skills"]
        with self.tx() as c:
            c.execute(
                "INSERT INTO candidate_match(candidate_id, job_id, match_score, hiring_score, band, "
                "score_breakdown) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(candidate_id, job_id) DO UPDATE SET match_score = excluded.match_score, "
                "hiring_score = excluded.hiring_score, band = excluded.band, "
                "score_breakdown = excluded.score_breakdown, matched_at = datetime('now')",
                (candidate_id, job_id, round(skills["score"] * 100, 2), result["hiring_score"],
                 result["band"], _j(result["score_breakdown"])))
            c.execute(
                "INSERT INTO skill_gap_report(candidate_id, job_id, missing_skills, "
                "recommended_courses, coverage_by_category, summary) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(candidate_id, job_id) DO UPDATE SET missing_skills = excluded.missing_skills, "
                "recommended_courses = excluded.recommended_courses, "
                "coverage_by_category = excluded.coverage_by_category, summary = excluded.summary, "
                "generated_at = datetime('now')",
                (candidate_id, job_id, _j(gap["missing_skills"]), _j(gap["recommendations"]),
                 _j(gap["coverage_by_category"]), gap["summary"]))
        return self.get_match(candidate_id, job_id)

    def _match_row(self, row: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(row)
        out["score_breakdown"] = _uj(out.get("score_breakdown"), {})
        return out

    def get_match(self, candidate_id: int, job_id: int) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT m.*, c.name AS candidate_name, j.job_title FROM candidate_match m "
                       "JOIN candidates c USING(candidate_id) JOIN jobs j USING(job_id) "
                       "WHERE m.candidate_id = ? AND m.job_id = ?", (candidate_id, job_id))
        return self._match_row(row) if row else None

    def list_matches(self, job_id: Optional[int] = None,
                     candidate_id: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = ("SELECT m.*, c.name AS candidate_name, c.email AS candidate_email, j.job_title "
               "FROM candidate_match m JOIN candidates c USING(candidate_id) JOIN jobs j USING(job_id)")
        where, params = [], []
        if job_id is not None:
            where.append("m.job_id = ?"); params.append(job_id)
        if candidate_id is not None:
            where.append("m.candidate_id = ?"); params.append(candidate_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        return [self._match_row(r) for r in self.all(sql + " ORDER BY m.hiring_score DESC", params)]

    def get_gap(self, candidate_id: int, job_id: int) -> Optional[Dict[str, Any]]:
        row = self.one("SELECT * FROM skill_gap_report WHERE candidate_id = ? AND job_id = ?",
                       (candidate_id, job_id))
        if not row:
            return None
        for key, default in (("missing_skills", []), ("recommended_courses", []),
                             ("coverage_by_category", {})):
            row[key] = _uj(row.get(key), default)
        return row

    # ----------------------------------------------------------------- ATS --
    def set_stage(self, candidate_id: int, job_id: int, to_stage: str, note: str = "",
                  actor_id: Optional[int] = None) -> Dict[str, Any]:
        if to_stage not in STAGES:
            raise DBError(f"unknown stage '{to_stage}'", 422)
        match = self.get_match(candidate_id, job_id)
        if match is None:
            raise DBError("score this candidate against the job before moving them in the ATS", 409)
        current = match["status"]
        if to_stage == current:
            return match
        allowed = TRANSITIONS.get(current, [])
        if to_stage not in allowed:
            raise DBError(f"cannot move {match['candidate_name']} from '{current}' to '{to_stage}'. "
                          f"Allowed: {', '.join(allowed) or 'none — this stage is final'}", 409)
        with self.tx() as c:
            c.execute("UPDATE candidate_match SET status = ? WHERE candidate_id = ? AND job_id = ?",
                      (to_stage, candidate_id, job_id))
            c.execute("INSERT INTO ats_events(candidate_id, job_id, from_stage, to_stage, note, "
                      "actor_id) VALUES (?,?,?,?,?,?)",
                      (candidate_id, job_id, current, to_stage, note, actor_id))
        return self.get_match(candidate_id, job_id)

    def ats_events(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self.all("SELECT e.*, c.name AS candidate_name, j.job_title, u.full_name AS actor "
                        "FROM ats_events e JOIN candidates c USING(candidate_id) "
                        "JOIN jobs j USING(job_id) LEFT JOIN users u ON u.user_id = e.actor_id "
                        "ORDER BY e.event_id DESC LIMIT ?", (limit,))

    def funnel(self, job_id: Optional[int] = None) -> Dict[str, Any]:
        """Stage counts, plus how many pairs *reached* each stage (from the event history)."""
        where = " WHERE job_id = ?" if job_id is not None else ""
        params = (job_id,) if job_id is not None else ()
        current = {s: 0 for s in STAGES}
        for r in self.all(f"SELECT status, COUNT(*) n FROM candidate_match{where} GROUP BY status", params):
            current[r["status"]] = r["n"]
        total = sum(current.values())
        reached = {s: 0 for s in STAGES}
        reached["applied"] = total
        for r in self.all(f"SELECT to_stage, COUNT(DISTINCT candidate_id || '-' || job_id) n "
                          f"FROM ats_events{where} GROUP BY to_stage", params):
            if r["to_stage"] != "applied":
                reached[r["to_stage"]] = r["n"]
        return {"current": current, "reached": reached, "total": total,
                "active": sum(v for k, v in current.items() if k not in TERMINAL)}

    # ----------------------------------------------------------- interviews --
    def save_questions(self, job_id: int, questions: List[Dict[str, Any]],
                       candidate_id: Optional[int] = None) -> int:
        with self.tx() as c:
            c.execute("DELETE FROM interview_questions WHERE job_id = ? AND "
                      "candidate_id IS NOT DISTINCT FROM ?", (job_id, candidate_id))
            c.executemany(
                "INSERT INTO interview_questions(job_id, question_text, difficulty_level, category, "
                "skill, candidate_id, rationale) VALUES (?,?,?,?,?,?,?)",
                [(job_id, q["question_text"], q.get("difficulty_level"), q.get("category"),
                  q.get("skill"), candidate_id, q.get("rationale")) for q in questions])
        return len(questions)

    def list_questions(self, job_id: int, candidate_id: Optional[int] = None) -> List[Dict[str, Any]]:
        return self.all("SELECT * FROM interview_questions WHERE job_id = ? AND "
                        "candidate_id IS NOT DISTINCT FROM ? ORDER BY question_id",
                        (job_id, candidate_id))

    def save_result(self, candidate_id: int, job_id: Optional[int], report: Dict[str, Any],
                    mode: str = "text", transcript: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        score = report.get("score")
        feedback = report.get("feedback") or report.get("assessment") or ""
        if isinstance(feedback, list):
            feedback = "; ".join(str(f) for f in feedback)
        with self.tx() as c:
            new_id = c.insert_returning(
                "INSERT INTO interview_results(candidate_id, job_id, score, feedback, mode, "
                "recommendation, transcript) VALUES (?,?,?,?,?,?,?)",
                (candidate_id, job_id, None if score is None else int(round(score)), feedback, mode,
                 report.get("recommendation"), _j(transcript)), "result_id")
        return self.one("SELECT * FROM interview_results WHERE result_id = ?", (new_id,))

    def list_results(self, candidate_id: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = ("SELECT r.*, c.name AS candidate_name, j.job_title FROM interview_results r "
               "JOIN candidates c USING(candidate_id) LEFT JOIN jobs j ON j.job_id = r.job_id")
        if candidate_id is not None:
            return self.all(sql + " WHERE r.candidate_id = ? ORDER BY r.result_id DESC", (candidate_id,))
        return self.all(sql + " ORDER BY r.result_id DESC")

    # --------------------------------------------------------------- survey --
    def add_survey(self, csat: int, sus: Optional[List[int]], sus_score: Optional[float],
                   role: Optional[str], user_id: Optional[int]) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO survey_responses(user_id, role, csat, sus, sus_score) "
                      "VALUES (?,?,?,?,?)", (user_id, role, csat, _j(sus), sus_score))

    def survey_rows(self) -> List[Dict[str, Any]]:
        rows = self.all("SELECT * FROM survey_responses ORDER BY response_id")
        for r in rows:
            r["sus"] = _uj(r["sus"])
        return rows

    # ---------------------------------------------------------------- audit --
    def audit(self, user_id: Optional[int], action: str, detail: str = "") -> None:
        with self.tx() as c:
            c.execute("INSERT INTO audit_log(user_id, action, detail) VALUES (?,?,?)",
                      (user_id, action, detail[:500]))

    def audit_rows(self, limit: int = 100) -> List[Dict[str, Any]]:
        return self.all("SELECT a.*, u.email, u.role FROM audit_log a LEFT JOIN users u "
                        "USING(user_id) ORDER BY a.log_id DESC LIMIT ?", (limit,))

    # ---------------------------------------------------------------- admin --
    TABLES = ("users", "candidates", "jobs", "candidate_match", "skill_gap_report",
              "interview_questions", "interview_results", "ats_events", "survey_responses",
              "audit_log", "auth_sessions")

    def stats(self) -> Dict[str, Any]:
        counts = {t: self.one(f"SELECT COUNT(*) n FROM {t}")["n"] for t in self.TABLES}
        size_kb = None
        if self.backend == "sqlite" and self.path != ":memory:":
            size_kb = round(Path(self.path).stat().st_size / 1024, 1)
        return {"tables": counts, "backend": self.backend,
                "path": self.path if self.backend == "sqlite" else "(PostgreSQL — see DATABASE_URL)",
                "size_kb": size_kb,
                "schema_version": self.one("SELECT MAX(version) v FROM schema_version")["v"]}

    def table_preview(self, table: str, limit: int = 25) -> Dict[str, Any]:
        if table not in self.TABLES:
            raise DBError(f"unknown table '{table}'", 404)
        rows = self.all(f"SELECT * FROM {table} ORDER BY 1 DESC LIMIT ?", (limit,))
        if table == "users":
            for r in rows:
                r["password_hash"] = "pbkdf2_sha256$…(hidden)"
        if table == "auth_sessions":
            for r in rows:
                r["token_hash"] = r["token_hash"][:8] + "…"
        return {"table": table, "columns": self.conn.table_columns(table), "rows": rows}
