"""Service layer: the operations each role can perform.

Every function takes the signed-in user first and checks two things before touching data:

    1. permission — does this role have the right to do this at all (auth.PERMISSIONS)?
    2. ownership  — a candidate may only read or change their own record.

Putting those checks here, rather than in the HTTP routes, means they hold no matter how
the function is reached: the API, the Streamlit app, a script, or a test. The routes in
main.py stay thin and cannot accidentally skip a check.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any, Dict, List, Optional

from .auth import AuthError, can, new_token, require, validate_credentials, verify_password
from .db import Database, DBError
from .evaluation import EVAL_JOBS
from .interview import InterviewSession, STAGES, generate_interview_kit, generate_role_question_bank
from .matching import match, skill_gap_report
from .parser import parse_file
from .satisfaction import SurveyError, summarise, sus_score
from .voice import VoiceScreen, assess_transcript, summarise_screen

# interview sessions in progress live in memory; a finished session is written to
# interview_results, so nothing that matters is lost on restart
_SESSIONS: Dict[str, Dict[str, Any]] = {}

DEMO_ACCOUNTS = [
    ("admin@talentlens.dev", "Asha Admin", "Admin@2026", "admin"),
    ("recruiter@talentlens.dev", "Rohan Recruiter", "Recruit@2026", "recruiter"),
    ("candidate@talentlens.dev", "Nisha Menon", "Candidate@2026", "candidate"),
]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _own(user: Dict[str, Any], candidate_id: int) -> None:
    """A candidate may only touch their own record. Staff may touch any."""
    if user["role"] == "candidate" and user.get("candidate_id") != candidate_id:
        raise AuthError("candidates can only see their own records", 403)


def _check(user: Dict[str, Any], staff_perm: str, own_perm: Optional[str] = None,
           candidate_id: Optional[int] = None) -> None:
    if user["role"] == "candidate" and own_perm:
        require(user["role"], own_perm)
        if candidate_id is not None:
            _own(user, candidate_id)
        return
    require(user["role"], staff_perm)


# --------------------------------------------------------------------------- #
# accounts
# --------------------------------------------------------------------------- #
def register(db: Database, email: str, full_name: str, password: str) -> Dict[str, Any]:
    """Public sign-up. Always creates a candidate — staff accounts are made by an admin."""
    validate_credentials(email, password)
    if not (full_name or "").strip():
        raise AuthError("enter your name", 422)
    user = db.create_user(email, full_name, password, "candidate")
    db.audit(user["user_id"], "user.register", email)
    return user


def login(db: Database, email: str, password: str, role: Optional[str] = None) -> Dict[str, Any]:
    row = db.user_with_hash(email)
    # one message for both cases, so the login form does not reveal which emails exist
    if row is None or not verify_password(password, row["password_hash"]):
        db.audit(None, "auth.failed", (email or "")[:80])
        raise AuthError("email or password is incorrect")
    if not row["is_active"]:
        raise AuthError("this account is disabled — contact an administrator", 403)
    if role and role != row["role"]:
        raise AuthError(f"this account is a {row['role']} account — use the {row['role']} sign-in", 403)
    token = new_token()
    db.create_session(row["user_id"], token)
    db.audit(row["user_id"], "auth.login", row["role"])
    return {"token": token, "user": db.get_user(row["user_id"])}


def logout(db: Database, token: str) -> None:
    db.delete_session(token)


def create_staff(db: Database, admin: Dict[str, Any], email: str, full_name: str,
                 password: str, role: str) -> Dict[str, Any]:
    require(admin["role"], "users.write")
    validate_credentials(email, password)
    user = db.create_user(email, full_name, password, role)
    db.audit(admin["user_id"], "user.create", f"{email} as {role}")
    return user


def update_user(db: Database, admin: Dict[str, Any], user_id: int, role: Optional[str] = None,
                is_active: Optional[bool] = None) -> Dict[str, Any]:
    require(admin["role"], "users.write")
    if user_id == admin["user_id"] and (is_active is False or (role and role != "admin")):
        raise AuthError("you cannot disable or demote your own admin account", 409)
    user = db.update_user(user_id, role=role, is_active=is_active)
    db.audit(admin["user_id"], "user.update", f"user {user_id}: role={role} active={is_active}")
    return user


# --------------------------------------------------------------------------- #
# Milestone 1 — resumes
# --------------------------------------------------------------------------- #
def ingest_resume(db: Database, user: Dict[str, Any], path: str | Path,
                  original_name: Optional[str] = None) -> Dict[str, Any]:
    """Parse, store, and score against every open job in one step."""
    if user["role"] == "candidate":
        require("candidate", "resume.upload_own")
    else:
        require(user["role"], "resume.upload")
    parsed = parse_file(path)
    name = original_name or Path(path).name
    parsed.source_file = name
    if not parsed.is_resume:
        db.audit(user["user_id"], "resume.rejected", name)
        return {"accepted": False, "file": name,
                "reason": "does not look like a resume — too few fields and no recognisable sections"}
    profile = parsed.to_dict()
    quality = {**profile["quality"], "needs_review": parsed.fields_captured < parsed.fields_required}
    if user["role"] == "candidate" and user.get("candidate_id"):
        existing = db.get_candidate(user["candidate_id"])
        if existing and existing["identity_hash"] != profile["identity_hash"]:
            # a candidate re-uploading under a new email is still the same account
            profile["identity_hash"] = existing["identity_hash"]
    # Duplicate policy. A candidate re-uploading their own resume is updating it, so the
    # new version always wins. A recruiter uploading the same person twice (bulk import,
    # a forwarded copy) must not replace a complete profile with a thinner one — the
    # sample cohort contains exactly this case, and overwriting dropped a shortlisted
    # candidate out of the shortlist. There the more complete version is kept.
    existing = db.one("SELECT candidate_id, field_recall, skills FROM candidates WHERE identity_hash = ?",
                      (profile["identity_hash"],))
    if existing and user["role"] != "candidate":
        import json as _json
        old_skills = len(_json.loads(existing["skills"] or "[]"))
        new_skills = len(profile.get("skills") or [])
        if (existing["field_recall"], old_skills) > (parsed.field_recall, new_skills):
            db.audit(user["user_id"], "resume.duplicate_kept_existing",
                     f"{name}: kept candidate {existing['candidate_id']} "
                     f"({old_skills} skills) over incoming copy ({new_skills} skills)")
            return {"accepted": True, "file": name, "candidate_id": existing["candidate_id"],
                    "created": False, "duplicate": True, "kept": "existing",
                    "reason": "same person already on file with a more complete profile",
                    "field_recall": parsed.field_recall, "jobs_scored": 0}
    candidate_id, created = db.upsert_candidate(profile, quality, f"uploads/{name}")
    if user["role"] == "candidate" and not user.get("candidate_id"):
        db.link_candidate(user["user_id"], candidate_id)
        user["candidate_id"] = candidate_id
    scored = score_candidate(db, candidate_id)
    db.audit(user["user_id"], "resume.upload", f"{name} -> candidate {candidate_id}")
    return {"accepted": True, "file": name, "candidate_id": candidate_id, "created": created,
            "field_recall": parsed.field_recall, "mean_confidence": parsed.mean_confidence,
            "fields_captured": f"{parsed.fields_captured}/{parsed.fields_required}",
            "jobs_scored": scored}


def get_candidate(db: Database, user: Dict[str, Any], candidate_id: int) -> Dict[str, Any]:
    _check(user, "candidates.read", "profile.read_own", candidate_id)
    c = db.get_candidate(candidate_id)
    if c is None:
        raise DBError(f"no candidate {candidate_id}", 404)
    return c


def list_candidates(db: Database, user: Dict[str, Any], search: Optional[str] = None):
    require(user["role"], "candidates.read")
    return db.list_candidates(search)


# --------------------------------------------------------------------------- #
# Milestone 2 — matching + skill gaps
# --------------------------------------------------------------------------- #
def score_candidate(db: Database, candidate_id: int, job_ids: Optional[List[int]] = None) -> int:
    """Score one candidate against jobs and persist match + gap rows. Internal."""
    profile = db.profile(candidate_id)
    jobs = [db.get_job(j) for j in job_ids] if job_ids else db.list_jobs(open_only=True)
    for job in jobs:
        spec = db.job_spec(job["job_id"])
        result = match(profile, spec)
        db.save_match(candidate_id, job["job_id"], result, skill_gap_report(profile, spec, result))
    return len(jobs)


def score_all(db: Database, user: Dict[str, Any]) -> Dict[str, Any]:
    require(user["role"], "match.run")
    candidates = db.list_candidates()
    for c in candidates:
        score_candidate(db, c["candidate_id"])
    pairs = len(candidates) * len(db.list_jobs(open_only=True))
    db.audit(user["user_id"], "match.run_all", f"{pairs} pairs")
    return {"candidates": len(candidates), "pairs": pairs}


def job_ranking(db: Database, user: Dict[str, Any], job_id: int) -> List[Dict[str, Any]]:
    require(user["role"], "match.read")
    return db.list_matches(job_id=job_id)


def my_matches(db: Database, user: Dict[str, Any]) -> List[Dict[str, Any]]:
    """A candidate's own scores across every open role."""
    require(user["role"], "match.read_own")
    if not user.get("candidate_id"):
        return []
    return db.list_matches(candidate_id=user["candidate_id"])


def match_detail(db: Database, user: Dict[str, Any], candidate_id: int, job_id: int) -> Dict[str, Any]:
    _check(user, "match.read", "match.read_own", candidate_id)
    profile, spec = db.profile(candidate_id), db.job_spec(job_id)
    result = match(profile, spec)
    return {"match": result, "skill_gap_report": skill_gap_report(profile, spec, result),
            "stored": db.get_match(candidate_id, job_id)}


def gap_report(db: Database, user: Dict[str, Any], candidate_id: int, job_id: int) -> Dict[str, Any]:
    _check(user, "gap.read", "gap.read_own", candidate_id)
    report = db.get_gap(candidate_id, job_id)
    if report is None:
        score_candidate(db, candidate_id, [job_id])
        report = db.get_gap(candidate_id, job_id)
    return report


# --------------------------------------------------------------------------- #
# Milestone 3 — interviews + ATS
# --------------------------------------------------------------------------- #
def role_questions(db: Database, user: Dict[str, Any], job_id: int) -> Dict[str, Any]:
    if user["role"] != "candidate":
        require(user["role"], "interview.read")
    bank = generate_role_question_bank(db.job_spec(job_id))
    flat = [q for cat in ("technical", "behavioural", "scenario") for q in bank.get(cat, [])]
    db.save_questions(job_id, flat)
    return bank


def start_interview(db: Database, user: Dict[str, Any], candidate_id: int, job_id: int,
                    total: int = 8) -> Dict[str, Any]:
    """Recruiters run interviews; a candidate may start a practice session for themselves."""
    _check(user, "interview.run", "interview.practice", candidate_id)
    profile, spec = db.profile(candidate_id), db.job_spec(job_id)
    kit = generate_interview_kit(profile, spec, total=total)
    db.save_questions(job_id, kit["questions"], candidate_id)
    sid = secrets.token_urlsafe(9)
    _SESSIONS[sid] = {
        "owner": user["user_id"], "candidate_id": candidate_id, "job_id": job_id,
        "practice": user["role"] == "candidate", "kit": kit,
        "session": InterviewSession(candidate=kit["candidate"] or "Candidate",
                                    job_title=kit["job_title"] or "Role", questions=kit["questions"],
                                    candidate_id=candidate_id, job_id=job_id)}
    return {"session_id": sid, "kit": kit, "question": kit["questions"][0] if kit["questions"] else None}


def _session(user: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    s = _SESSIONS.get(session_id)
    if s is None:
        raise DBError("interview session not found — it may have finished or the server restarted", 404)
    if s["owner"] != user["user_id"] and user["role"] == "candidate":
        raise AuthError("this is not your interview session", 403)
    return s


def answer_interview(db: Database, user: Dict[str, Any], session_id: str, answer: str) -> Dict[str, Any]:
    s = _session(user, session_id)
    scored = s["session"].submit(answer)
    out = {"scored": scored, "finished": s["session"].finished,
           "next_question": s["session"].current()}
    if s["session"].finished:
        report = s["session"].report()
        transcript = [{"question": a.get("question"), "answer": a.get("answer"),
                       "score": a.get("score")} for a in s["session"].answers]
        db.save_result(s["candidate_id"], s["job_id"], report, "text", transcript)
        if not s["practice"]:
            _advance_after_interview(db, user, s["candidate_id"], s["job_id"], report)
        db.audit(user["user_id"], "interview.finished",
                 f"candidate {s['candidate_id']} job {s['job_id']} score {report.get('score')}")
        out["report"] = report
        _SESSIONS.pop(session_id, None)
    return out


def _advance_after_interview(db, user, candidate_id, job_id, report):
    """A recruiter-run interview moves the ATS record along the legal path."""
    m = db.get_match(candidate_id, job_id)
    if m is None:
        return
    path = {"applied": ["screening", "interview_scheduled", "interview_completed"],
            "screening": ["interview_scheduled", "interview_completed"],
            "interview_scheduled": ["interview_completed"]}.get(m["status"], [])
    for stage in path:
        db.set_stage(candidate_id, job_id, stage, f"AI interview: {report.get('recommendation')}",
                     user["user_id"])


def move_stage(db: Database, user: Dict[str, Any], candidate_id: int, job_id: int,
               stage: str, note: str = "") -> Dict[str, Any]:
    require(user["role"], "ats.write")
    m = db.set_stage(candidate_id, job_id, stage, note, user["user_id"])
    db.audit(user["user_id"], "ats.stage", f"candidate {candidate_id} job {job_id} -> {stage}")
    return m


def withdraw(db: Database, user: Dict[str, Any], job_id: int) -> Dict[str, Any]:
    """The one ATS move a candidate makes themselves."""
    if user["role"] != "candidate" or not user.get("candidate_id"):
        raise AuthError("only a candidate can withdraw their own application", 403)
    return db.set_stage(user["candidate_id"], job_id, "withdrawn", "withdrawn by candidate",
                        user["user_id"])


# --------------------------------------------------------------------------- #
# Milestone 4 — voice, dashboard, satisfaction
# --------------------------------------------------------------------------- #
def voice_prompts(db: Database, user: Dict[str, Any], candidate_id: int, job_id: int):
    _check(user, "voice.run", "voice.practice", candidate_id)
    return VoiceScreen(db.profile(candidate_id), db.job_spec(job_id)).prompts()


def voice_assess(db: Database, user: Dict[str, Any], candidate_id: int, job_id: int,
                 answers: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Score a whole voice screen and store it. `answers` = [{transcript, duration_seconds}]."""
    _check(user, "voice.run", "voice.practice", candidate_id)
    prompts = voice_prompts(db, user, candidate_id, job_id)
    results = [assess_transcript(a.get("transcript", ""), prompts[i], a.get("duration_seconds"))
               for i, a in enumerate(answers[:len(prompts)])]
    profile = db.profile(candidate_id)
    summary = summarise_screen(profile["name"], db.get_job(job_id)["job_title"], results)
    if results:
        db.save_result(candidate_id, job_id, summary, "voice",
                       [{"question": r["question_text"], "answer": r["transcript"], "score": r["score"]}
                        for r in results])
    return summary


def dashboard(db: Database, user: Dict[str, Any]) -> Dict[str, Any]:
    require(user["role"], "dashboard.read")
    stats = db.stats()["tables"]
    funnel = db.funnel()
    best = {}
    for m in db.list_matches():
        if m["candidate_id"] not in best or m["hiring_score"] > best[m["candidate_id"]]["hiring_score"]:
            best[m["candidate_id"]] = m
    results = [r for r in db.list_results() if r["score"] is not None]
    reached = funnel["reached"]
    offers = reached["offer"]
    return {
        "kpis": {
            "candidates": stats["candidates"],
            "open_jobs": len(db.list_jobs(open_only=True)),
            "interviews": reached["interview_scheduled"],
            "shortlist_rate": (round(sum(1 for b in best.values() if b["band"] == "strong") / len(best) * 100, 1)
                               if best else None),
            "average_best_match": (round(sum(b["hiring_score"] for b in best.values()) / len(best), 1)
                                   if best else None),
            "average_interview_score": (round(sum(r["score"] for r in results) / len(results), 1)
                                        if results else None),
            "offer_rate": (round(offers / reached["interview_completed"] * 100, 1)
                           if reached["interview_completed"] and offers else None),
        },
        "funnel": funnel,
        "bands": {b: sum(1 for m in db.list_matches() if m["band"] == b) for b in ("strong", "possible", "weak")},
        "recent_events": db.ats_events(10),
    }


def submit_survey(db: Database, user: Optional[Dict[str, Any]], csat: int,
                  sus: Optional[List[int]] = None, role: Optional[str] = None) -> Dict[str, Any]:
    try:
        summarise([{"csat": csat, "sus": sus}])     # validates
    except SurveyError as exc:
        raise DBError(str(exc), 422) from exc
    db.add_survey(csat, sus, sus_score(sus) if sus else None,
                  role or (user["role"] if user else None), user["user_id"] if user else None)
    return survey_summary(db)


def survey_summary(db: Database) -> Dict[str, Any]:
    return summarise([{"csat": r["csat"], "sus": r["sus"], "role": r["role"]} for r in db.survey_rows()])


# --------------------------------------------------------------------------- #
# admin
# --------------------------------------------------------------------------- #
def admin_overview(db: Database, user: Dict[str, Any]) -> Dict[str, Any]:
    require(user["role"], "db.read")
    users = db.list_users()
    return {"database": db.stats(),
            "users_by_role": {r: sum(1 for u in users if u["role"] == r) for r in ("candidate", "recruiter", "admin")},
            "active_sessions": db.stats()["tables"]["auth_sessions"],
            "survey": survey_summary(db)}


# --------------------------------------------------------------------------- #
# demo data
# --------------------------------------------------------------------------- #
def seed_demo(db: Database, resume_dir: str | Path) -> Dict[str, Any]:
    """Create demo accounts, the four evaluation roles, and the sample cohort. Idempotent."""
    created = []
    for email, name, password, role in DEMO_ACCOUNTS:
        if db.user_with_hash(email) is None:
            db.create_user(email, name, password, role)
            created.append(email)
    admin = db.user_with_hash(DEMO_ACCOUNTS[0][0])
    admin = db.get_user(admin["user_id"])
    if not db.list_jobs():
        for j in EVAL_JOBS:
            db.create_job({"job_title": j["title"], "must_have": j["must_have"],
                           "nice_to_have": j["nice_to_have"],
                           "experience_required": j["experience_required"],
                           "education_required": j["education_required"],
                           "department": {1: "Engineering", 2: "Data Science", 3: "Engineering",
                                          4: "Business Intelligence"}.get(j["job_id"])}, admin["user_id"])
    files = sorted((p for p in Path(resume_dir).iterdir() if p.suffix.lower() in {".pdf", ".docx", ".txt"}),
                   key=lambda p: p.name)
    ingested = [ingest_resume(db, admin, p) for p in files]
    # link the demo candidate login to their own resume
    cand_user = db.user_with_hash(DEMO_ACCOUNTS[2][0])
    nisha = next((c for c in db.list_candidates() if c["name"] == "Nisha Menon"), None)
    if nisha and cand_user and not cand_user["candidate_id"]:
        db.link_candidate(cand_user["user_id"], nisha["candidate_id"])
    # spread the pipeline so the funnel is not all "applied"
    for m in db.list_matches():
        if m["status"] != "applied":
            continue
        if m["band"] == "strong":
            db.set_stage(m["candidate_id"], m["job_id"], "screening", "strong match", admin["user_id"])
        elif m["band"] == "weak":
            db.set_stage(m["candidate_id"], m["job_id"], "rejected", "below the mandatory-skill gate",
                         admin["user_id"])
    return {"accounts_created": created, "resumes": len(ingested),
            "accepted": sum(1 for i in ingested if i["accepted"]), "stats": db.stats()["tables"]}
