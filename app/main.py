"""TalentLens API — FastAPI over the SQLite data layer.

    uvicorn app.main:app --reload        # http://localhost:8000  (console)
                                         # http://localhost:8000/docs  (API)

Routes are deliberately thin: each one resolves the signed-in user, then calls a
function in `services.py`, which is where permission and ownership checks live. A route
cannot skip a check because it never touches the database itself.

Authentication is a bearer token from `POST /api/auth/login`. There are three sign-in
portals in the console — candidate, recruiter, admin — and each passes its role, so a
candidate account is refused on the admin portal rather than quietly let in.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from . import services as S
from .auth import AuthError, ROLES, bearer
from .db import Database, DBError
from .satisfaction import CSAT_QUESTION, SUS_ITEMS

ROOT = Path(__file__).resolve().parents[1]
CONSOLE = ROOT / "TalentLens_Console.html"
ALLOWED = {".pdf", ".docx", ".doc", ".txt"}
MAX_BYTES = 8 * 1024 * 1024

DB = Database(os.environ.get("TALENTLENS_DB", ROOT / "talentlens.db"))
if os.environ.get("TALENTLENS_SEED", "1") == "1" and not DB.list_users():
    S.seed_demo(DB, ROOT / "sample_resumes")

app = FastAPI(title="TalentLens — AI Recruitment Copilot API", version="2.0.0",
              description="Resume parsing, matching, interviews, ATS and dashboard, "
                          "with candidate / recruiter / admin roles over SQLite.")
# bearer tokens, not cookies, so a wildcard origin does not expose sessions; it lets
# the console work when opened straight from disk as well as when served from here
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"],
                   allow_headers=["*"])


@app.exception_handler(AuthError)
def _auth_error(_: Request, exc: AuthError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


@app.exception_handler(DBError)
def _db_error(_: Request, exc: DBError):
    return JSONResponse({"detail": str(exc)}, status_code=exc.status)


def current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    return DB.user_for_token(bearer(authorization))


def optional_user(authorization: Optional[str] = Header(None)) -> Optional[Dict[str, Any]]:
    try:
        return DB.user_for_token(bearer(authorization))
    except AuthError:
        return None


# --------------------------------------------------------------------------- #
# request bodies
# --------------------------------------------------------------------------- #
class LoginIn(BaseModel):
    email: str
    password: str
    role: Optional[str] = Field(None, description="the portal used: candidate, recruiter or admin")


class RegisterIn(BaseModel):
    email: str
    full_name: str
    password: str


class StaffIn(RegisterIn):
    role: str


class UserPatch(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None


class JobIn(BaseModel):
    job_title: str = Field(min_length=2, max_length=140)
    department: Optional[str] = None
    must_have: List[str] = Field(default_factory=list)
    nice_to_have: List[str] = Field(default_factory=list)
    experience_required: Optional[float] = None
    education_required: Optional[str] = None
    job_description: Optional[str] = None
    location: Optional[str] = None


class StageIn(BaseModel):
    stage: str
    note: str = ""


class AnswerIn(BaseModel):
    answer: str = Field(default="", max_length=8000)


class VoiceIn(BaseModel):
    answers: List[Dict[str, Any]]


class SurveyIn(BaseModel):
    csat: int = Field(ge=1, le=5)
    sus: Optional[List[int]] = None
    role: Optional[str] = None


# --------------------------------------------------------------------------- #
# frontend + health
# --------------------------------------------------------------------------- #
@app.get("/", include_in_schema=False)
def console():
    if not CONSOLE.exists():
        raise HTTPException(404, "TalentLens_Console.html not found next to the backend folder")
    return FileResponse(CONSOLE)


@app.get("/api/health")
def health():
    return {"status": "ok", "milestones_shipped": 4, "database": DB.stats()["tables"]}


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@app.post("/api/auth/login")
def login(body: LoginIn):
    if body.role and body.role not in ROLES:
        raise HTTPException(422, f"role must be one of {ROLES}")
    return S.login(DB, body.email, body.password, body.role)


@app.post("/api/auth/register", status_code=201)
def register(body: RegisterIn):
    user = S.register(DB, body.email, body.full_name, body.password)
    return S.login(DB, body.email, body.password, "candidate") | {"registered": user}


@app.post("/api/auth/logout")
def logout(authorization: Optional[str] = Header(None)):
    S.logout(DB, bearer(authorization))
    return {"signed_out": True}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    out = dict(user)
    if user.get("candidate_id"):
        out["candidate"] = DB.get_candidate(user["candidate_id"])
    return out


# --------------------------------------------------------------------------- #
# Milestone 1 — resumes and candidates
# --------------------------------------------------------------------------- #
@app.post("/api/resumes", status_code=201)
async def upload_resumes(files: List[UploadFile] = File(...), user=Depends(current_user)):
    if user["role"] == "candidate" and len(files) > 1:
        raise HTTPException(422, "upload one resume — your own")
    out = []
    for f in files:
        suffix = Path(f.filename or "").suffix.lower()
        if suffix not in ALLOWED:
            out.append({"accepted": False, "file": f.filename, "reason": "only PDF, DOCX and TXT"})
            continue
        data = await f.read(MAX_BYTES + 1)            # a real cap, not a buffer size
        if len(data) > MAX_BYTES:
            out.append({"accepted": False, "file": f.filename, "reason": "larger than 8 MB"})
            continue
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(data)
        try:
            out.append(S.ingest_resume(DB, user, tmp.name, f.filename))
        finally:
            Path(tmp.name).unlink(missing_ok=True)
    return {"results": out}


@app.get("/api/candidates")
def candidates(search: Optional[str] = None, user=Depends(current_user)):
    return S.list_candidates(DB, user, search)


@app.get("/api/candidates/{candidate_id}")
def candidate(candidate_id: int, user=Depends(current_user)):
    return S.get_candidate(DB, user, candidate_id)


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
@app.get("/api/jobs")
def jobs(user=Depends(current_user)):
    return DB.list_jobs(open_only=user["role"] == "candidate")


@app.post("/api/jobs", status_code=201)
def create_job(body: JobIn, user=Depends(current_user)):
    S.require(user["role"], "jobs.write")
    job = DB.create_job(body.model_dump(), user["user_id"])
    for c in DB.list_candidates():
        S.score_candidate(DB, c["candidate_id"], [job["job_id"]])
    DB.audit(user["user_id"], "job.create", job["job_title"])
    return job


# --------------------------------------------------------------------------- #
# Milestone 2 — matching
# --------------------------------------------------------------------------- #
@app.post("/api/match/run")
def run_matching(user=Depends(current_user)):
    return S.score_all(DB, user)


@app.get("/api/jobs/{job_id}/ranking")
def ranking(job_id: int, user=Depends(current_user)):
    return S.job_ranking(DB, user, job_id)


@app.get("/api/me/matches")
def my_matches(user=Depends(current_user)):
    return S.my_matches(DB, user)


@app.get("/api/match/{candidate_id}/{job_id}")
def match_detail(candidate_id: int, job_id: int, user=Depends(current_user)):
    return S.match_detail(DB, user, candidate_id, job_id)


@app.get("/api/gap-report/{candidate_id}/{job_id}")
def gap(candidate_id: int, job_id: int, user=Depends(current_user)):
    return S.gap_report(DB, user, candidate_id, job_id)


# --------------------------------------------------------------------------- #
# Milestone 3 — interviews and ATS
# --------------------------------------------------------------------------- #
@app.get("/api/jobs/{job_id}/questions")
def questions(job_id: int, user=Depends(current_user)):
    return S.role_questions(DB, user, job_id)


@app.post("/api/interview/start/{candidate_id}/{job_id}", status_code=201)
def interview_start(candidate_id: int, job_id: int, total: int = Query(8, ge=3, le=15),
                    user=Depends(current_user)):
    return S.start_interview(DB, user, candidate_id, job_id, total)


@app.post("/api/interview/session/{session_id}/answer")
def interview_answer(session_id: str, body: AnswerIn, user=Depends(current_user)):
    return S.answer_interview(DB, user, session_id, body.answer)


@app.get("/api/interview/results")
def interview_results(user=Depends(current_user)):
    if user["role"] == "candidate":
        return DB.list_results(user.get("candidate_id") or -1)
    S.require(user["role"], "interview.read")
    return DB.list_results()


@app.get("/api/ats/pipeline")
def ats_pipeline(job_id: Optional[int] = None, user=Depends(current_user)):
    S.require(user["role"], "ats.read")
    return {"matches": DB.list_matches(job_id=job_id), "funnel": DB.funnel(job_id)}


@app.put("/api/ats/{candidate_id}/{job_id}/stage")
def ats_move(candidate_id: int, job_id: int, body: StageIn, user=Depends(current_user)):
    return S.move_stage(DB, user, candidate_id, job_id, body.stage, body.note)


@app.post("/api/me/withdraw/{job_id}")
def withdraw(job_id: int, user=Depends(current_user)):
    return S.withdraw(DB, user, job_id)


@app.get("/api/ats/events")
def ats_events(limit: int = Query(50, le=500), user=Depends(current_user)):
    S.require(user["role"], "ats.read")
    return DB.ats_events(limit)


# --------------------------------------------------------------------------- #
# Milestone 4 — dashboard, voice, satisfaction
# --------------------------------------------------------------------------- #
@app.get("/api/dashboard")
def dash(user=Depends(current_user)):
    return S.dashboard(DB, user)


@app.get("/api/voice/prompts/{candidate_id}/{job_id}")
def voice_prompts(candidate_id: int, job_id: int, user=Depends(current_user)):
    return S.voice_prompts(DB, user, candidate_id, job_id)


@app.post("/api/voice/assess/{candidate_id}/{job_id}")
def voice_assess(candidate_id: int, job_id: int, body: VoiceIn, user=Depends(current_user)):
    return S.voice_assess(DB, user, candidate_id, job_id, body.answers)


@app.get("/api/feedback/questions")
def feedback_questions():
    return {"csat": CSAT_QUESTION, "sus": SUS_ITEMS}


@app.post("/api/feedback", status_code=201)
def feedback(body: SurveyIn, user=Depends(optional_user)):
    return S.submit_survey(DB, user, body.csat, body.sus, body.role)


@app.get("/api/feedback/summary")
def feedback_summary():
    return S.survey_summary(DB)


# --------------------------------------------------------------------------- #
# admin
# --------------------------------------------------------------------------- #
@app.get("/api/admin/overview")
def admin_overview(user=Depends(current_user)):
    return S.admin_overview(DB, user)


@app.get("/api/admin/users")
def admin_users(user=Depends(current_user)):
    S.require(user["role"], "users.read")
    return DB.list_users()


@app.post("/api/admin/users", status_code=201)
def admin_create_user(body: StaffIn, user=Depends(current_user)):
    return S.create_staff(DB, user, body.email, body.full_name, body.password, body.role)


@app.patch("/api/admin/users/{user_id}")
def admin_update_user(user_id: int, body: UserPatch, user=Depends(current_user)):
    return S.update_user(DB, user, user_id, body.role, body.is_active)


@app.get("/api/admin/tables/{table}")
def admin_table(table: str, limit: int = Query(25, le=200), user=Depends(current_user)):
    S.require(user["role"], "db.read")
    return DB.table_preview(table, limit)


@app.get("/api/admin/audit")
def admin_audit(limit: int = Query(100, le=1000), user=Depends(current_user)):
    S.require(user["role"], "audit.read")
    return DB.audit_rows(limit)
