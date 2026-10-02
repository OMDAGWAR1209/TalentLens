"""Milestone 4 acceptance tests.

Week-8 criteria from the project document:

    - Dashboard fully functional
    - Voice screening module operational
    - End-to-end recruitment workflow completed successfully
    - User satisfaction score >= 85%

The first three are asserted directly. The fourth cannot be asserted by a test suite —
satisfaction is a property of real users, not of the code — so what is tested here is
that the measurement is correct and that it refuses to report a score without enough
responses. Whether the gate is actually met is decided by the survey, not by pytest.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation import EVAL_JOBS  # noqa: E402
from app.parser import parse_file  # noqa: E402
from app.pipeline import run_pipeline  # noqa: E402
from app.satisfaction import (MIN_RESPONSES, SurveyError, summarise, sus_grade,  # noqa: E402
                              sus_score)
from app.voice import VoiceScreen, assess_transcript, summarise_screen  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "sample_resumes"

STRONG = ("I'm a data scientist with five years at Swiggy. I built a demand forecasting model in "
          "PyTorch, deployed it on AWS, and it cut stockouts by eighteen percent. I monitored "
          "drift weekly and retrained whenever the error rose above our threshold.")
FILLER = "Um, so, like, I think I basically, uh, do machine learning, you know, maybe kind of stuff."


@pytest.fixture(scope="module")
def run():
    return run_pipeline(FIXTURES)


@pytest.fixture(scope="module")
def nisha():
    for path in sorted(FIXTURES.glob("*.txt"), key=lambda p: p.name):
        parsed = parse_file(path)
        if parsed.get("name") == "Nisha Menon":
            return parsed.to_dict()
    raise AssertionError("Nisha Menon fixture missing")


# --------------------------------------------------------------------------- #
# end-to-end workflow
# --------------------------------------------------------------------------- #
def test_end_to_end_workflow_completes(run):
    assert run["complete"] is True
    assert [s["stage"] for s in run["stages"]] == ["parse", "match", "interview", "dashboard"]
    assert all(s["ok"] for s in run["stages"])


def test_every_earlier_gate_still_holds_inside_the_pipeline(run):
    """Wiring the milestones together must not degrade any of them."""
    assert run["milestone_1"]["field_recall"] >= 95.0
    assert run["milestone_2"]["accuracy"] >= 85.0
    assert run["milestone_2"]["reports"] == run["milestone_2"]["pairs"]


def test_every_candidate_reaches_the_ats(run):
    assert run["milestone_3"]["in_ats"] == run["milestone_1"]["candidates"]


def test_interviews_are_recorded_against_ats_records(run):
    assert run["milestone_3"]["interviews"] >= 1
    scored = [r for r in run["ats"].list_candidates() if r.get("interview_score") is not None]
    assert len(scored) == run["milestone_3"]["interviews"]


def test_pipeline_is_fast_enough_for_an_interactive_dashboard(run):
    assert run["total_ms"] < 5000, f"pipeline took {run['total_ms']} ms"


# --------------------------------------------------------------------------- #
# dashboard
# --------------------------------------------------------------------------- #
def test_dashboard_kpis_are_derived_not_invented(run):
    k = run["dashboard"]["kpis"]
    assert k["total_candidates"] == run["milestone_1"]["candidates"]
    assert 0 <= k["shortlist_rate"] <= 100
    assert 0 <= k["average_best_match"] <= 100


def test_kpis_without_data_are_none_not_a_placeholder(run):
    """No offers were made, so offer rate and time-to-hire must be blank, not a made-up 14d."""
    k = run["dashboard"]["kpis"]
    assert k["time_to_hire_days"] is None
    assert k["offer_rate"] is None
    assert run["dashboard"]["notes"]["time_to_hire_days"]


def test_pipeline_counts_add_up(run):
    d = run["dashboard"]
    assert sum(d["pipeline"].values()) + d["rejected"] == d["kpis"]["total_candidates"]


def test_streamlit_app_is_valid_python():
    import ast
    source = (Path(__file__).resolve().parents[1] / "streamlit_app.py").read_text(encoding="utf-8")
    ast.parse(source)


# --------------------------------------------------------------------------- #
# voice screening
# --------------------------------------------------------------------------- #
def test_voice_screen_builds_prompts_from_the_interview_kit(nisha):
    prompts = VoiceScreen(nisha, EVAL_JOBS[1]).prompts()
    assert prompts[0]["question_id"] == "intro"
    assert len(prompts) == 4
    assert all(p["question_text"] for p in prompts)


def test_substance_beats_filler(nisha):
    prompt = VoiceScreen(nisha, EVAL_JOBS[1]).prompts()[0]
    good = assess_transcript(STRONG, prompt, 40)
    bad = assess_transcript(FILLER, prompt, 40)
    assert good["score"] > bad["score"] + 25
    assert bad["signals"]["filler_words"] > good["signals"]["filler_words"]


def test_delivery_cannot_dominate_content(nisha):
    """A great answer said fast and nervously must still beat an empty one said calmly."""
    prompt = VoiceScreen(nisha, EVAL_JOBS[1]).prompts()[0]
    rushed = assess_transcript("um " * 3 + STRONG, prompt, 8)
    empty = assess_transcript("I like computers a lot and I am a hard worker okay.", prompt, 30)
    assert rushed["score"] > empty["score"]


def test_pace_is_measured_when_duration_is_known(nisha):
    prompt = VoiceScreen(nisha, EVAL_JOBS[1]).prompts()[0]
    out = assess_transcript(STRONG, prompt, 20)
    assert out["signals"]["words_per_minute"] is not None
    no_time = assess_transcript(STRONG, prompt)
    assert no_time["signals"]["words_per_minute"] is None


def test_empty_transcript_scores_zero_and_does_not_crash(nisha):
    prompt = VoiceScreen(nisha, EVAL_JOBS[1]).prompts()[0]
    assert assess_transcript("", prompt)["score"] == 0.0


def test_full_voice_screen_produces_an_assessment(nisha):
    screen = VoiceScreen(nisha, EVAL_JOBS[1])
    for p in screen.prompts():
        screen.record(p, STRONG, 40)
    summary = screen.summary()
    assert summary["answered"] == 4
    assert summary["score"] is not None
    assert summary["recommendation"]
    assert "averaged" in summary["assessment"]


def test_summary_with_no_responses_is_explicit():
    assert summarise_screen("X", "Role", [])["score"] is None


def test_listen_degrades_gracefully_without_hardware():
    """On a server with no microphone the adapter must report, not raise."""
    from app.voice import listen
    out = listen(timeout=0.1)
    assert set(out) == {"transcript", "duration_seconds", "error"}


# --------------------------------------------------------------------------- #
# user satisfaction
# --------------------------------------------------------------------------- #
def test_sus_formula_matches_the_published_definition():
    assert sus_score([5, 1, 5, 1, 5, 1, 5, 1, 5, 1]) == 100.0
    assert sus_score([1, 5, 1, 5, 1, 5, 1, 5, 1, 5]) == 0.0
    assert sus_score([3] * 10) == 50.0


def test_rating_everything_five_does_not_score_100():
    """The even items are negatively worded — that is the point of the instrument."""
    assert sus_score([5] * 10) == 50.0


def test_sus_rejects_malformed_input():
    with pytest.raises(SurveyError):
        sus_score([5] * 9)
    with pytest.raises(SurveyError):
        sus_score([6] + [3] * 9)


def test_sus_grade_bands():
    assert sus_grade(90) == "excellent"
    assert sus_grade(68) == "ok"


def test_no_responses_means_no_score():
    s = summarise([])
    assert s["csat_percent"] is None
    assert s["gate_met"] is False


def test_too_few_responses_cannot_pass_the_gate():
    s = summarise([{"csat": 5, "sus": [5, 1] * 5}] * (MIN_RESPONSES - 1))
    assert s["csat_percent"] == 100.0
    assert s["sample_sufficient"] is False
    assert s["gate_met"] is False


def test_gate_evaluates_once_the_sample_is_large_enough():
    happy = [{"csat": 5, "role": "Recruiter", "sus": [5, 1] * 5}] * 9
    unhappy = [{"csat": 2, "role": "Candidate", "sus": [2, 4] * 5}]
    s = summarise(happy + unhappy)
    assert s["responses"] == 10
    assert s["csat_percent"] == 90.0
    assert s["gate_met"] is True
    assert s["by_role"]["Candidate"] == 0.0


def test_gate_fails_honestly_below_target():
    mixed = [{"csat": c} for c in (5, 5, 4, 3, 3, 2, 4, 5, 3, 2)]
    s = summarise(mixed)
    assert s["csat_percent"] == 50.0
    assert s["gate_met"] is False
    assert "below target" in s["status"]


def test_no_two_api_routes_capture_each_others_urls():
    """FastAPI matches routes in declaration order, so an earlier `/x/{a}/{b}` silently
    swallows a later `/x/{id}/answer` and returns 422. This caught a real bug: interview
    answers could not be submitted because the session-start route captured them."""
    import re
    source = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    routes = re.findall(r'@app\.(get|post|put|patch|delete)\("([^"]+)"', source)

    def pattern(path):
        return re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$")

    def sample(path):
        return re.sub(r"\{[^}]+\}", "x1", path)

    for i, (method_a, path_a) in enumerate(routes):
        for method_b, path_b in routes[i + 1:]:
            if method_a != method_b or path_a == path_b:
                continue
            # an earlier route that would match a concrete URL for a later route shadows it
            assert not pattern(path_a).match(sample(path_b)), (
                f"{method_a.upper()} {path_a} is declared first and captures {path_b}")
