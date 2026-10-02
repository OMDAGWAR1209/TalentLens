"""Milestone 3 acceptance tests.

The three graded criteria from the project document:

    - Interview questions generated for multiple job roles
    - ATS integration functions correctly
    - AI interview simulation operational

"Functions correctly" is the interesting one. A mock that accepts every write always
"works", so these tests assert the things a real ATS integration has to get right:
invalid stage transitions are refused, re-submitting an application updates rather than
duplicates, and every change leaves an audit trail.

Run with:  pytest -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation import EVAL_JOBS  # noqa: E402
from app.interview import (ATSError, ATSPipeline, InterviewSession, STAGES,  # noqa: E402
                           TERMINAL, generate_interview_kit,
                           generate_role_question_bank, score_answer,
                           simulate_interview)
from app.parser import parse_file  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "sample_resumes"


@pytest.fixture(scope="module")
def candidates():
    files = sorted(FIXTURES.glob("*.txt"), key=lambda p: p.name)
    seen, out = set(), []
    for path in files:
        parsed = parse_file(path)
        if not parsed.is_resume or parsed.identity_hash in seen:
            continue
        seen.add(parsed.identity_hash)
        profile = parsed.to_dict()
        profile["candidate_id"] = len(out) + 1
        profile["seniority"] = parsed.seniority
        out.append(profile)
    return out


# --------------------------------------------------------------------------- #
# criterion 1 — questions for multiple job roles
# --------------------------------------------------------------------------- #
def test_questions_generated_for_every_role():
    for job in EVAL_JOBS:
        bank = generate_role_question_bank(job)
        assert bank["total"] >= 8, f"{job['title']} produced only {bank['total']} questions"
        assert bank["technical"], f"{job['title']} has no technical questions"
        assert bank["behavioural"], f"{job['title']} has no behavioural questions"
        assert bank["scenario"], f"{job['title']} has no scenario questions"


def test_a_brand_new_role_needs_no_new_content():
    """The reference implementation returns 'no questions available' for an unknown
    title. Deriving from the skill list instead means any role works immediately."""
    novel = {"job_id": 99, "title": "Site Reliability Engineer — Edge",
             "must_have": ["Kubernetes", "Terraform", "Go", "PostgreSQL", "AWS"],
             "nice_to_have": ["Prometheus"], "experience_required": 5,
             "education_required": "Bachelor's"}
    bank = generate_role_question_bank(novel)
    assert bank["total"] >= 8
    asked_about = {q["skill"] for q in bank["technical"]}
    assert "Kubernetes" in asked_about and "Terraform" in asked_about


def test_questions_are_unique_within_a_kit(candidates):
    for job in EVAL_JOBS:
        for candidate in candidates[:4]:
            kit = generate_interview_kit(candidate, job, total=8)
            texts = [q["question_text"] for q in kit["questions"]]
            assert len(texts) == len(set(texts)), f"duplicate question for {candidate['name']}"


def test_generation_is_deterministic(candidates):
    """The same candidate and role must produce the same kit — a panel comparing two
    candidates cannot have the questions reshuffle underneath them."""
    a = generate_interview_kit(candidates[0], EVAL_JOBS[0], total=8)
    b = generate_interview_kit(candidates[0], EVAL_JOBS[0], total=8)
    assert [q["question_id"] for q in a["questions"]] == [q["question_id"] for q in b["questions"]]


# --------------------------------------------------------------------------- #
# questions are actually targeted, not generic
# --------------------------------------------------------------------------- #
def test_kit_probes_the_candidates_real_gaps(candidates):
    """Nisha is missing NLP and LLMs for the ML role — the kit must ask about them."""
    nisha = next(c for c in candidates if c["name"] == "Nisha Menon")
    kit = generate_interview_kit(nisha, EVAL_JOBS[1], total=8)
    probed = {q["skill"] for q in kit["questions"] if q["category"] == "probe"}
    assert {"NLP", "LLMs"} & probed, f"gaps not probed, only got {probed}"


def test_adjacent_skill_produces_a_transfer_question(candidates):
    """She has PyTorch but the role wants TensorFlow — ask what transfers."""
    nisha = next(c for c in candidates if c["name"] == "Nisha Menon")
    kit = generate_interview_kit(nisha, EVAL_JOBS[1], total=8)
    tf = [q for q in kit["questions"] if q["skill"] == "TensorFlow"]
    assert tf, "no TensorFlow question generated"
    assert "PyTorch" in tf[0]["question_text"] or "PyTorch" in tf[0]["rationale"]


def test_difficulty_tracks_seniority(candidates):
    junior = next(c for c in candidates if c["name"] == "Priyanshu Rauniyar")
    senior = next(c for c in candidates if c["name"] == "Michael Chen")
    assert generate_interview_kit(junior, EVAL_JOBS[2])["difficulty"] == "easy"
    assert generate_interview_kit(senior, EVAL_JOBS[2])["difficulty"] == "hard"


def test_every_question_states_why_it_was_asked(candidates):
    for job in EVAL_JOBS[:2]:
        kit = generate_interview_kit(candidates[0], job, total=8)
        for q in kit["questions"]:
            assert q["rationale"], f"no rationale for {q['question_text'][:40]}"
            assert q["expected_points"], "no rubric for this question"


def test_kit_mix_always_includes_behavioural_and_scenario(candidates):
    """A bug caught in testing: truncating a flat list at the end dropped the scenario
    question entirely. Slots are now budgeted up front."""
    for candidate in candidates:
        kit = generate_interview_kit(candidate, EVAL_JOBS[0], total=8)
        assert kit["mix"].get("behavioural", 0) >= 1, f"{candidate['name']}: no behavioural"
        assert kit["mix"].get("scenario", 0) >= 1, f"{candidate['name']}: no scenario"
        assert len(kit["questions"]) <= 8


# --------------------------------------------------------------------------- #
# criterion 2 — ATS integration functions correctly
# --------------------------------------------------------------------------- #
def test_ats_add_and_retrieve():
    ats = ATSPipeline()
    rec = ats.add_candidate("Sarah Johnson", "sarah@example.com", "Senior ML Engineer")
    assert rec.stage == "applied"
    assert ats.get("SARAH@EXAMPLE.COM").name == "Sarah Johnson"  # email is case-insensitive
    assert rec.external_ref and rec.external_ref.startswith("ATS-")


def test_ats_resubmission_updates_instead_of_duplicating():
    ats = ATSPipeline()
    ats.add_candidate("Sarah Johnson", "sarah@example.com", "Senior ML Engineer")
    ats.add_candidate("Sarah Johnson", "sarah@example.com", "Staff ML Engineer")
    assert len(ats.list_candidates()) == 1
    assert ats.get("sarah@example.com").job_title == "Staff ML Engineer"


def test_ats_valid_transitions_are_allowed():
    ats = ATSPipeline()
    ats.add_candidate("A", "a@x.com", "Role")
    for stage in ("screening", "interview_scheduled", "interview_completed", "offer", "hired"):
        ats.update_status("a@x.com", stage)
    assert ats.get("a@x.com").stage == "hired"


def test_ats_refuses_an_illegal_stage_jump():
    """Skipping straight from applied to hired is the bug a mock would let through."""
    ats = ATSPipeline()
    ats.add_candidate("A", "a@x.com", "Role")
    with pytest.raises(ATSError) as exc:
        ats.update_status("a@x.com", "hired")
    assert "cannot move" in str(exc.value)
    assert ats.get("a@x.com").stage == "applied", "stage changed despite the error"


def test_ats_terminal_stages_are_final():
    ats = ATSPipeline()
    ats.add_candidate("A", "a@x.com", "Role")
    ats.update_status("a@x.com", "rejected")
    for stage in STAGES:
        if stage == "rejected":
            continue
        with pytest.raises(ATSError):
            ats.update_status("a@x.com", stage)


def test_ats_rejects_unknown_stage_names():
    ats = ATSPipeline()
    ats.add_candidate("A", "a@x.com", "Role")
    with pytest.raises(ATSError):
        ats.update_status("a@x.com", "promoted_to_cto")


def test_ats_unknown_candidate_raises():
    with pytest.raises(ATSError):
        ATSPipeline().get("nobody@example.com")


def test_ats_keeps_an_audit_trail():
    ats = ATSPipeline()
    ats.add_candidate("A", "a@x.com", "Role")
    ats.update_status("a@x.com", "screening", note="strong resume")
    rec = ats.get("a@x.com")
    assert len(rec.history) == 2
    assert rec.history[-1]["from"] == "applied" and rec.history[-1]["to"] == "screening"
    assert rec.history[-1]["note"] == "strong resume"
    assert any(e["event"] == "stage.changed" for e in ats.events)


def test_ats_funnel_counts_and_conversion():
    ats = ATSPipeline()
    for i in range(4):
        ats.add_candidate(f"C{i}", f"c{i}@x.com", "Role")
    ats.update_status("c0@x.com", "screening")
    ats.update_status("c0@x.com", "interview_scheduled")
    ats.update_status("c0@x.com", "interview_completed")
    ats.update_status("c1@x.com", "rejected")
    funnel = ats.funnel()
    assert funnel["total"] == 4
    assert funnel["counts"]["interview_completed"] == 1
    assert funnel["counts"]["rejected"] == 1
    assert funnel["active"] == 3
    assert funnel["conversion"]["interviewed_of_applied"] == 25.0


def test_ats_filters_by_stage_and_role():
    ats = ATSPipeline()
    ats.add_candidate("A", "a@x.com", "Role A", hiring_score=90)
    ats.add_candidate("B", "b@x.com", "Role B", hiring_score=70)
    ats.update_status("b@x.com", "screening")
    assert len(ats.list_candidates(stage="applied")) == 1
    assert len(ats.list_candidates(job_title="Role B")) == 1
    assert ats.list_candidates()[0]["name"] == "A"  # sorted by hiring score


# --------------------------------------------------------------------------- #
# criterion 3 — AI interview simulation operational
# --------------------------------------------------------------------------- #
def test_simulation_runs_end_to_end(candidates):
    nisha = next(c for c in candidates if c["name"] == "Nisha Menon")
    out = simulate_interview(nisha, EVAL_JOBS[1], answers=["A detailed answer."] * 8, total=6)
    assert out["report"]["answered"] == 6
    assert out["report"]["score"] is not None
    assert out["report"]["recommendation"]
    assert len(out["transcript"]) == 6


def test_session_advances_and_completes(candidates):
    kit = generate_interview_kit(candidates[0], EVAL_JOBS[0], total=4)
    session = InterviewSession(candidate="X", job_title="Y", questions=kit["questions"])
    assert session.current() is not None
    for _ in range(4):
        session.submit("Some answer of reasonable length for the rubric to assess properly.")
    assert session.finished
    assert session.current() is None
    with pytest.raises(ValueError):
        session.submit("too late")


def test_scoring_discriminates_answer_quality():
    question = {"category": "behavioural", "skill": None,
                "expected_points": ["a specific situation", "their own actions", "a concrete outcome"]}
    empty = score_answer("", question)
    vague = score_answer("We generally try to handle things well as a team.", question)
    strong = score_answer(
        "When our checkout service started timing out last March, I profiled the slow path "
        "and found an N+1 query. I added an index and batched the calls, which reduced p99 "
        "latency from 1800ms to 240ms. Next time I would add the alert before it shipped.",
        question)
    assert empty["score"] == 0.0
    assert vague["score"] < strong["score"]
    assert strong["score"] >= 70
    assert strong["verdict"] == "strong"


def test_scoring_explains_itself():
    question = {"category": "behavioural", "skill": None, "expected_points": []}
    result = score_answer("We did the thing and it was fine.", question)
    assert result["feedback"], "a score with no explanation is not reviewable"
    assert "structure" in result["signals"]
    assert isinstance(result["signals"]["structure"], dict)


def test_report_aggregates_by_category(candidates):
    nisha = next(c for c in candidates if c["name"] == "Nisha Menon")
    out = simulate_interview(nisha, EVAL_JOBS[1],
                             answers=["I led the migration and cut latency by 40%."] * 8, total=8)
    report = out["report"]
    assert report["by_category"], "no per-category breakdown"
    assert set(report["by_category"]) <= {"technical", "probe", "behavioural", "scenario"}
    assert report["strongest_answer"] and report["weakest_answer"]


def test_empty_interview_reports_cleanly(candidates):
    session = InterviewSession(candidate="X", job_title="Y", questions=[])
    report = session.report()
    assert report["answered"] == 0
    assert report["score"] is None


# --------------------------------------------------------------------------- #
# the three milestones join up
# --------------------------------------------------------------------------- #
def test_interview_result_flows_back_into_the_ats(candidates):
    """M1 parses, M2 matches, M3 interviews, and the outcome lands on the ATS record."""
    nisha = next(c for c in candidates if c["name"] == "Nisha Menon")
    out = simulate_interview(nisha, EVAL_JOBS[1], answers=["A reasonably detailed answer here."] * 8, total=6)

    ats = ATSPipeline()
    ats.add_candidate(nisha["name"], nisha["email"], EVAL_JOBS[1]["title"],
                      candidate_id=nisha["candidate_id"], hiring_score=out["kit"]["hiring_score"])
    ats.update_status(nisha["email"], "screening")
    ats.update_status(nisha["email"], "interview_scheduled")
    ats.attach_interview_result(nisha["email"], out["report"])

    rec = ats.get(nisha["email"])
    assert rec.stage == "interview_completed", "attaching a result should advance the stage"
    assert rec.interview_score == out["report"]["score"]
    assert rec.hiring_score == out["kit"]["hiring_score"]
    assert any(e["event"] == "interview.recorded" for e in ats.events)


def test_kit_is_built_from_the_milestone_2_match(candidates):
    """The kit must reflect the match, not be generated independently of it."""
    ananya = next(c for c in candidates if c["name"] == "Ananya Krishnan")
    kit = generate_interview_kit(ananya, EVAL_JOBS[0], total=8)
    assert kit["hiring_score"] > 90
    assert kit["band"] == "strong"
    assert kit["gap_summary"]
    # a strong match should be weighted toward verifying depth, not probing gaps
    assert kit["mix"].get("technical", 0) >= kit["mix"].get("probe", 0)
