"""Milestone 2 acceptance tests.

The two graded criteria from the project document:

    - Candidate-job matching accuracy >= 85%
    - Skill-gap analysis generated successfully for all evaluated candidates

Both are asserted here rather than claimed in a slide. `test_matching_accuracy_gate`
fails the build if agreement with the labelled pairs drops below 85%, and
`test_gap_report_for_every_evaluated_pair` fails if a single candidate/job pair produces
no report.

Run with:  pytest -q
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.evaluation import EVAL_JOBS, LABELLED_PAIRS, evaluate  # noqa: E402
from app.jobspec import (adjacent_credit, analyse_job_description,  # noqa: E402
                         job_from_record)
from app.matching import (ACCURACY_TARGET, GATE_SCREEN, GATE_SHORTLIST,  # noqa: E402
                          match, match_matrix, rank_candidates, score_education,
                          score_experience, skill_gap_report)
from app.parser import parse_file  # noqa: E402

FIXTURES = Path(__file__).resolve().parents[1] / "sample_resumes"


@pytest.fixture(scope="module")
def candidates():
    """The Milestone 1 cohort, deduplicated — matching runs on real parsed profiles."""
    files = sorted(FIXTURES.glob("*.txt"), key=lambda p: p.name)
    assert files, f"no fixtures in {FIXTURES}"
    seen, out = set(), []
    for path in files:
        parsed = parse_file(path)
        if not parsed.is_resume or parsed.identity_hash in seen:
            continue
        seen.add(parsed.identity_hash)
        profile = parsed.to_dict()
        profile["candidate_id"] = len(out) + 1
        out.append(profile)
    return out


@pytest.fixture(scope="module")
def report(candidates):
    return evaluate(candidates)


@pytest.fixture(scope="module")
def matrix(candidates):
    return match_matrix(candidates, EVAL_JOBS)


# --------------------------------------------------------------------------- #
# the graded criteria
# --------------------------------------------------------------------------- #
def test_matching_accuracy_gate(report):
    """Agreement with the labelled candidate/job decisions must clear 85%."""
    assert report["accuracy"] >= ACCURACY_TARGET, (
        f"matching accuracy {report['accuracy']}% is below the {ACCURACY_TARGET}% gate. "
        f"Disagreements: "
        + "; ".join(f"{d['candidate']} vs {d['job_title']}: "
                    f"predicted {d['predicted']}, expected {d['expected']}"
                    for d in report["disagreements"])
    )
    assert report["target_met"] is True


def test_gap_report_for_every_evaluated_pair(matrix, candidates):
    """'Generated for all evaluated candidates' means every pair, with no gaps."""
    assert matrix["reports_generated"] == len(candidates) * len(EVAL_JOBS)
    assert matrix["coverage_complete"] is True
    for rep in matrix["reports"]:
        assert rep["summary"], f"empty summary for {rep['candidate']} / {rep['job_title']}"
        assert "coverage_by_category" in rep
        assert isinstance(rep["missing_skills"], list)


def test_a_perfect_candidate_still_receives_a_report(candidates):
    """A candidate with no gaps must get a report that says so, not an empty object."""
    job = {"title": "Trivially satisfied role", "must_have": ["Python"],
           "nice_to_have": [], "experience_required": 1}
    nisha = next(c for c in candidates if c["name"] == "Nisha Menon")
    rep = skill_gap_report(nisha, job)
    assert rep["ready_now"] is True
    assert rep["missing_must_have"] == []
    assert rep["recommendations"] == []
    assert "no development gap" in rep["summary"].lower()


# --------------------------------------------------------------------------- #
# the accuracy number has to be worth something
# --------------------------------------------------------------------------- #
def test_accuracy_beats_a_majority_class_predictor(report):
    """Most pairs are a 'hold', so always guessing 'hold' scores well. Beat it clearly."""
    assert report["lift_over_baseline"] >= 15.0, (
        f"only {report['lift_over_baseline']} points above the "
        f"{report['majority_class_baseline']}% majority-class baseline")


def test_no_severe_errors(report):
    """Confusing a shortlist for a hold is the error that actually costs a hire."""
    assert report["severe_error_count"] == 0, report["severe_errors"]


def test_top_candidate_per_role_is_the_best_available(report):
    assert report["top1_hit_rate"] == 100.0, report["top1_by_role"]


def test_shortlist_precision_is_perfect(report):
    """A false shortlist wastes interview time — precision on 'strong' must hold."""
    assert report["per_class"]["strong"]["precision"] == 100.0


def test_every_labelled_pair_was_actually_scored(report, candidates):
    names = {c["name"] for c in candidates}
    expected = sum(1 for (name, _), _ in LABELLED_PAIRS.items() if name in names)
    assert report["pairs_evaluated"] == expected


# --------------------------------------------------------------------------- #
# engine behaviour
# --------------------------------------------------------------------------- #
def test_adjacent_skills_earn_partial_not_full_credit():
    credit, sibling, group = adjacent_credit("TensorFlow", {"PyTorch", "Python"})
    assert 0 < credit < 1
    assert sibling == "PyTorch"
    assert group == "ML frameworks"


def test_unrelated_skills_earn_nothing():
    credit, _, _ = adjacent_credit("Kubernetes", {"Excel", "Tableau"})
    assert credit == 0.0


def test_mandatory_gate_blocks_a_high_tenure_low_skill_candidate():
    """The bug this gate exists for: 15 years and a Master's must not screen in
    when almost none of the mandatory skills are present."""
    veteran = {"name": "Tenured Mismatch", "skills": ["Excel"], "experience_years": 15,
               "education": {"degree": "Master's (M.Tech)"}}
    job = {"title": "Backend Engineer",
           "must_have": ["Java", "Spring Boot", "Kubernetes", "AWS", "PostgreSQL"],
           "nice_to_have": [], "experience_required": 4, "education_required": "Bachelor's"}
    result = match(veteran, job)
    assert result["band"] == "weak"
    assert result["score_breakdown"]["gate"], "the demotion should record its reason"
    assert result["score_breakdown"]["skills"]["must_have_coverage"] < GATE_SCREEN * 100


def test_partial_mandatory_coverage_caps_at_screen():
    candidate = {"name": "Partial", "skills": ["JavaScript", "TypeScript", "React"],
                 "experience_years": 8, "education": {"degree": "Bachelor's (B.Tech)"}}
    job = {"title": "Frontend", "must_have": ["JavaScript", "TypeScript", "React", "HTML", "CSS"],
           "nice_to_have": [], "experience_required": 3, "education_required": "Bachelor's"}
    result = match(candidate, job)
    coverage = result["score_breakdown"]["skills"]["must_have_coverage"]
    assert GATE_SCREEN * 100 <= coverage < GATE_SHORTLIST * 100
    assert result["band"] == "possible"


def test_nice_to_have_skills_never_reduce_a_score():
    """Preferred skills are upside only — adding more of them cannot hurt."""
    candidate = {"name": "X", "skills": ["Python", "SQL"], "experience_years": 5,
                 "education": {"degree": "Bachelor's (B.Tech)"}}
    lean = {"title": "R", "must_have": ["Python", "SQL"], "nice_to_have": [],
            "experience_required": 3}
    padded = {"title": "R", "must_have": ["Python", "SQL"],
              "nice_to_have": ["Kafka", "Terraform", "Rust"], "experience_required": 3}
    assert match(candidate, padded)["hiring_score"] <= match(candidate, lean)["hiring_score"]
    assert match(candidate, padded)["band"] in ("strong", "possible")


def test_overqualification_is_flagged_but_not_penalised():
    score, note = score_experience(14.0, 3.0)
    assert score == 1.0
    assert "overqualified" in note


def test_missing_tenure_is_not_treated_as_zero_experience():
    """A resume the parser could not date should be screened, not auto-rejected."""
    score, note = score_experience(None, 4.0)
    assert 0 < score < 1
    assert "screened" in note


def test_higher_degree_satisfies_a_lower_requirement():
    assert score_education("Doctorate (Ph.D)", 3)[0] == 1.0
    assert score_education("Master's (M.Tech)", 3)[0] == 1.0
    assert score_education("Bachelor's (B.Tech)", 4)[0] < 1.0


def test_scores_are_explainable_and_add_up(candidates):
    """Every component contribution must sum to the headline score."""
    result = match(candidates[0], EVAL_JOBS[0])
    parts = result["score_breakdown"]
    total = sum(parts[k]["contribution"] for k in ("skills", "experience", "education"))
    assert abs(total - result["hiring_score"]) < 0.05
    for entry in result["skill_detail"]:
        assert entry["note"], "every skill decision needs a stated reason"


def test_ranking_is_ordered_and_stable(candidates):
    ranked = rank_candidates(candidates, EVAL_JOBS[1])
    scores = [r["hiring_score"] for r in ranked]
    assert scores == sorted(scores, reverse=True)
    assert [r["rank"] for r in ranked] == list(range(1, len(ranked) + 1))
    assert ranked == rank_candidates(candidates, EVAL_JOBS[1])


# --------------------------------------------------------------------------- #
# job description analysis
# --------------------------------------------------------------------------- #
def test_jd_analysis_separates_required_from_preferred():
    posting = """Senior Backend Engineer

    Requirements:
    - 5+ years of professional experience building services
    - Strong experience with Java and Spring Boot
    - Solid SQL and PostgreSQL knowledge
    - Bachelor's degree in Computer Science

    Nice to have:
    - Exposure to Kubernetes
    - Familiarity with Kafka
    """
    spec = analyse_job_description(posting, "Senior Backend Engineer")
    assert "Java" in spec["must_have"]
    assert "Spring Boot" in spec["must_have"]
    assert "Kubernetes" in spec["nice_to_have"]
    assert "Kubernetes" not in spec["must_have"]
    assert spec["experience_required"] == 5.0
    assert spec["education_required"] == "Bachelor's"


def test_jd_analysis_resolves_aliases_to_canonical_skills():
    spec = analyse_job_description("Requirements:\n- Strong JS and ES6\n- React.js\n")
    assert "JavaScript" in spec["skills_required"]
    assert "React" in spec["skills_required"]


def test_inline_preferred_cue_overrides_the_enclosing_section():
    posting = ("Requirements:\n"
               "- Strong Python experience\n"
               "- Docker would be a plus\n")
    spec = analyse_job_description(posting)
    assert "Python" in spec["must_have"]
    assert "Docker" in spec["nice_to_have"]


def test_flat_skill_list_from_milestone_1_still_works():
    """Jobs created in Milestone 1 have no must/nice split — they must not break."""
    spec = job_from_record({"job_id": 9, "title": "Legacy",
                            "skills_required": ["Python", "SQL", "Docker", "AWS", "Git", "Agile"]})
    assert spec["must_have"] and spec["nice_to_have"]
    assert set(spec["must_have"]) | set(spec["nice_to_have"]) == {
        "Python", "SQL", "Docker", "AWS", "Git", "Agile"}


def test_matching_consumes_the_milestone_1_profile_contract(candidates):
    """The parser's output feeds the matcher directly, with no reshaping in between."""
    profile = candidates[0]
    assert "skills" in profile and "experience" in profile and "education" in profile
    result = match(profile, EVAL_JOBS[0])
    assert result["candidate"] == profile["name"]
    assert 0 <= result["hiring_score"] <= 100
