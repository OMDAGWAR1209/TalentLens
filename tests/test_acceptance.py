"""Milestone 1 acceptance tests.

The evaluation criteria for week 2 are two sentences in the project document:

    - Resume parser successfully extracts >= 95% of candidate information
    - Supports PDF and DOCX resume formats

Rather than claim those in a slide, they are asserted here. `test_week2_acceptance_gate`
fails the build if weighted field recall across the sample cohort drops below 95%, so a
regression in any extractor shows up as a red test instead of a quietly worse dashboard.

Run with:  pytest -q
These tests need only the standard library — the PDF and DOCX readers are imported
lazily inside the parser, so the extraction logic is testable without installing them.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.parser import (ACCEPTANCE_THRESHOLD, REQUIRED_FIELDS, cohort_metrics,  # noqa: E402
                        merge_tenure, month_value, parse_file, parse_text, segment)

FIXTURES = Path(__file__).resolve().parents[1] / "sample_resumes"


def _has(module: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(module) is not None


@pytest.fixture(scope="module")
def cohort():
    # sort by the filename string, not the Path object — Windows compares
    # Path objects case-insensitively, which silently reorders "Ananya..."
    # vs "ananya..." and flips which file the duplicate detector treats
    # as the original on different operating systems
    files = sorted(FIXTURES.glob("*.txt"), key=lambda p: p.name)
    assert files, f"no fixtures in {FIXTURES}"
    return [parse_file(path) for path in files]


@pytest.fixture(scope="module")
def metrics(cohort):
    return cohort_metrics(cohort)


# --------------------------------------------------------------------------- #
# the graded criteria
# --------------------------------------------------------------------------- #
def test_week2_acceptance_gate(metrics):
    """Weighted field recall across the cohort must clear 95%."""
    assert metrics["field_recall"] >= ACCEPTANCE_THRESHOLD, (
        f"field recall {metrics['field_recall']}% is below the {ACCEPTANCE_THRESHOLD}% gate. "
        f"Profiles needing review: {metrics['needs_review']}"
    )
    assert metrics["gate_met"] is True


def test_every_required_field_has_an_extractor(cohort):
    """No required field may be missing from every single resume — that means a dead extractor."""
    for key, label, _weight in REQUIRED_FIELDS:
        hits = sum(1 for r in cohort if r.fields[key].found)
        assert hits > 0, f"'{label}' was never extracted from any fixture — the extractor is broken"


def test_confidence_is_calibrated(cohort):
    """A field that was found must carry a real confidence, and never claim certainty."""
    for parsed in cohort:
        for key, label, _ in REQUIRED_FIELDS:
            rec = parsed.fields[key]
            if rec.found:
                assert 0.0 < rec.confidence <= 0.99, f"{parsed.source_file}/{label}: {rec.confidence}"
                assert rec.method, f"{parsed.source_file}/{label}: no extraction method recorded"
            else:
                assert rec.confidence == 0.0


def test_every_captured_field_is_traceable(cohort):
    """Provenance is the point: a recruiter must be able to find the line a value came from."""
    for parsed in cohort:
        provenance = parsed.to_dict()["provenance"]
        for key, _label, _ in REQUIRED_FIELDS:
            if parsed.fields[key].found and key != "total_years":
                assert key in provenance
                assert provenance[key]["line"] >= 1


# --------------------------------------------------------------------------- #
# format support
# --------------------------------------------------------------------------- #
def test_text_format_supported():
    parsed = parse_text("Asha Nair\nasha.nair@example.com\n+91 98111 22334\nBengaluru, Karnataka\n\n"
                        "SKILLS\nPython, SQL, Docker\n\nEDUCATION\nB.Tech Computer Science, VIT, 2021\n\n"
                        "EXPERIENCE\nSoftware Engineer, Infosys Limited\nJul 2021 - Present\n")
    assert parsed.get("name") == "Asha Nair"
    assert parsed.get("email") == "asha.nair@example.com"
    assert parsed.source_format == "txt"


@pytest.mark.skipif(not _has("pdfplumber"), reason="pdfplumber not installed")
def test_pdf_reader_available():
    import pdfplumber  # noqa: F401


@pytest.mark.skipif(not _has("docx2txt"), reason="docx2txt not installed")
def test_docx_reader_available():
    import docx2txt  # noqa: F401


# --------------------------------------------------------------------------- #
# specific extractor behaviour worth pinning down
# --------------------------------------------------------------------------- #
def test_contact_extraction_precision(cohort):
    by_file = {r.source_file: r for r in cohort}
    ananya = by_file["Ananya_Krishnan_Resume.txt"]
    assert ananya.get("name") == "Ananya Krishnan"
    assert ananya.get("email") == "ananya.krishnan28@gmail.com"
    assert ananya.get("location") == "Bengaluru, Karnataka"
    assert ananya.get("degree").startswith("Bachelor's")
    assert ananya.get("institution") == "National Institute of Technology, Surathkal"


def test_university_of_x_is_not_read_as_a_field_of_study(cohort):
    """'University of California' must not become the discipline."""
    chen = next(r for r in cohort if r.source_file == "M_Chen_CV.txt")
    assert chen.get("discipline") == "Computer Science"
    assert chen.get("institution") == "University of California, Berkeley"


def test_job_titles_are_not_stored_as_employers(cohort):
    for parsed in cohort:
        for employer in parsed.get("employers") or []:
            assert "engineer" not in employer.lower()
            assert "analyst" not in employer.lower()


def test_skills_aliases_collapse_to_one_canonical_name():
    parsed = parse_text("Dev Patel\ndev@example.com\n\nSKILLS\njs, JavaScript, ES6, reactjs, React.js, nodejs\n")
    names = [s["skill"] for s in parsed.skills]
    assert names.count("JavaScript") == 1
    assert names.count("React") == 1
    assert "Node.js" in names


def test_short_aliases_are_ignored_outside_a_skills_section():
    """'ML' in prose is too weak a signal; inside a skills block it is fine."""
    prose = parse_text("Ravi Kumar\nravi@example.com\n\nSUMMARY\nI ship ML and TS work daily.\n")
    assert "Machine learning" not in [s["skill"] for s in prose.skills]
    listed = parse_text("Ravi Kumar\nravi@example.com\n\nSKILLS\nML, TS, SQL\n")
    assert "Machine learning" in [s["skill"] for s in listed.skills]


def test_c_is_not_matched_inside_cpp():
    parsed = parse_text("A B\nab@example.com\n\nSKILLS\nC++, Python\n")
    names = [s["skill"] for s in parsed.skills]
    assert "C++" in names
    assert "C" not in names


def test_overlapping_roles_are_not_double_counted():
    """Two concurrent roles over the same two years is two years, not four."""
    months, gaps = merge_tenure([
        {"start": month_value("jan", "2020"), "end": month_value("jan", "2022"), "coarse": False},
        {"start": month_value("jun", "2020"), "end": month_value("jan", "2022"), "coarse": False},
    ])
    assert 23 <= months <= 25
    assert gaps == []


def test_employment_gaps_are_detected():
    months, gaps = merge_tenure([
        {"start": month_value("jan", "2018"), "end": month_value("jan", "2020"), "coarse": False},
        {"start": month_value("jan", "2021"), "end": month_value("jan", "2023"), "coarse": False},
    ])
    assert len(gaps) == 1
    assert gaps[0]["months"] == 12


def test_a_skills_line_is_not_treated_as_a_section_heading():
    """'Languages: Java, Python' belongs to the skills block, not a languages section."""
    lines = ["SKILLS", "Languages: Java, Python, SQL", "Frameworks: Spring Boot"]
    assert "languages" not in segment(lines)
    assert "skills" in segment(lines)


def test_summary_tenure_conflict_lowers_confidence_but_keeps_the_computed_value():
    parsed = parse_text(
        "Sam Roy\nsam@example.com\n\nSUMMARY\nEngineer with 2 years of experience.\n\n"
        "EXPERIENCE\nSoftware Engineer, Infosys Limited\nJan 2015 - Jan 2023\n")
    assert parsed.get("total_years") and parsed.get("total_years") > 7
    assert "conflict" in parsed.fields["total_years"].method
    assert parsed.fields["total_years"].confidence < 0.85


# --------------------------------------------------------------------------- #
# cohort-level behaviour
# --------------------------------------------------------------------------- #
def test_non_resume_documents_are_rejected_not_scored(metrics):
    """A vendor note must not quietly lower the acceptance number."""
    assert "unstructured_scan_notes.txt" in metrics["rejected"]
    assert metrics["resumes_scored"] == metrics["documents_in"] - len(metrics["rejected"])


def test_duplicate_uploads_resolve_to_one_candidate(metrics):
    assert metrics["unique_candidates"] < metrics["resumes_scored"]
    assert any(d["duplicate_of"] == "Ananya_Krishnan_Resume.txt" for d in metrics["duplicates"])


def test_identity_hash_is_stable_and_email_driven():
    a = parse_text("Priya Rao\npriya.rao@example.com\n\nSKILLS\nPython\n")
    b = parse_text("PRIYA RAO\nSenior Engineer\npriya.rao@example.com\n\nSKILLS\nPython, SQL\n")
    assert a.identity_hash == b.identity_hash


def test_incomplete_profiles_land_in_the_review_queue(metrics):
    assert "Nisha_Menon_Profile.txt" in metrics["needs_review"]


def test_profile_contract_shape(cohort):
    profile = cohort[0].to_dict()
    for key in ("identity_hash", "name", "email", "education", "experience", "skills",
                "provenance", "quality"):
        assert key in profile
    assert set(profile["education"]) == {"degree", "discipline", "institution", "graduation_year"}
    assert profile["quality"]["field_recall"] >= 0
