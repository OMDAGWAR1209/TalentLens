"""Job description analysis and skill adjacency.

Two things live here.

**Skill adjacency.** A candidate who knows PyTorch but not TensorFlow is not a zero on
"TensorFlow" — those are substitutable frameworks and a competent engineer moves between
them in days. A flat set intersection cannot express that, so this module defines groups
of interchangeable skills and awards partial credit inside a group. This is the single
biggest accuracy win over naive matching, because it is how a human recruiter actually
reads a resume.

**JD analysis.** Milestone 2 in the project document expects job descriptions to be
analysed, not hand-typed as clean lists. `analyse_job_description` reads raw posting text
and returns structured requirements: which skills are genuinely mandatory versus preferred,
years of experience, and the minimum degree. It reuses the Milestone 1 taxonomy, so a JD
saying "JS/ES6" and a resume saying "JavaScript" resolve to the same canonical skill.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from .taxonomy import ALIASES, CATEGORIES, TAXONOMY

# --------------------------------------------------------------------------- #
# adjacency: skills that substitute for one another
# --------------------------------------------------------------------------- #
# Credit is the fraction of a requirement satisfied by holding a sibling skill.
# Deliberately conservative — a sibling narrows the gap, it does not close it.
ADJACENCY_GROUPS: List[Tuple[str, float, List[str]]] = [
    ("ML frameworks", 0.60, ["TensorFlow", "PyTorch", "scikit-learn"]),
    ("Cloud platforms", 0.55, ["AWS", "Azure"]),
    ("Relational databases", 0.70, ["PostgreSQL", "MySQL", "SQL Server", "Oracle", "SQLite"]),
    ("Document stores", 0.65, ["MongoDB", "DynamoDB"]),
    ("Frontend frameworks", 0.55, ["React", "Angular", "Vue"]),
    ("Backend web frameworks", 0.55, ["Spring Boot", "Django", "Flask", "FastAPI", "Express"]),
    ("JVM languages", 0.55, ["Java", "Kotlin", "Scala"]),
    ("Typed JavaScript", 0.75, ["JavaScript", "TypeScript"]),
    ("BI tools", 0.65, ["Power BI", "Tableau"]),
    ("CI systems", 0.65, ["Jenkins", "CI/CD"]),
    ("Infrastructure as code", 0.60, ["Terraform", "Ansible"]),
    ("Python data stack", 0.55, ["Pandas", "NumPy"]),
    ("Unit test frameworks", 0.70, ["JUnit", "PyTest", "Jest"]),
    ("Message queues", 0.60, ["Kafka", "RabbitMQ"]),
    ("Systems languages", 0.45, ["C++", "C", "Rust", "Go"]),
    ("Container tooling", 0.45, ["Docker", "Kubernetes"]),
]

_ADJACENCY_INDEX: Dict[str, List[Tuple[str, float, List[str]]]] = {}
for _label, _credit, _members in ADJACENCY_GROUPS:
    for _m in _members:
        _ADJACENCY_INDEX.setdefault(_m, []).append((_label, _credit, _members))


def adjacent_credit(required: str, held: Set[str]) -> Tuple[float, Optional[str], Optional[str]]:
    """Best partial credit for `required` from any sibling in `held`.

    Returns (credit, matched_sibling, group_label). Credit is 0 when nothing is adjacent.
    """
    best = (0.0, None, None)
    for label, credit, members in _ADJACENCY_INDEX.get(required, []):
        for sibling in members:
            if sibling != required and sibling in held and credit > best[0]:
                best = (credit, sibling, label)
    return best


# --------------------------------------------------------------------------- #
# JD analysis
# --------------------------------------------------------------------------- #
MUST_CUES = re.compile(
    r"\b(must[\s-]?have|required|requirement|essential|mandatory|minimum|"
    r"you\s+(?:will\s+)?need|proven|strong\s+(?:experience|background)|expert(?:ise)?\s+in)\b", re.I)
NICE_CUES = re.compile(
    r"\b(nice[\s-]?to[\s-]?have|preferred|preferable|desirable|bonus|plus|advantage|"
    r"good\s+to\s+have|familiarity|exposure|awareness|would\s+be\s+(?:a\s+)?(?:plus|bonus))\b", re.I)

SECTION_CUES: List[Tuple[str, re.Pattern]] = [
    ("must", re.compile(r"^\s*(?:required|requirements|must[\s-]?haves?|essential|"
                        r"minimum\s+qualifications?|what\s+you.{0,4}ll\s+need|qualifications?)\b[:\s]*$", re.I)),
    ("nice", re.compile(r"^\s*(?:nice[\s-]?to[\s-]?haves?|preferred(?:\s+qualifications?)?|"
                        r"bonus(?:\s+points)?|desirable|good\s+to\s+have)\b[:\s]*$", re.I)),
    ("other", re.compile(r"^\s*(?:responsibilities|about\s+(?:the\s+)?(?:role|us|team)|"
                         r"benefits|perks|what\s+you.{0,4}ll\s+do|compensation)\b[:\s]*$", re.I)),
]

DEGREE_CUES: List[Tuple[int, re.Pattern, str]] = [
    (5, re.compile(r"\b(ph\.?\s?d|doctorate)\b", re.I), "Doctorate"),
    (4, re.compile(r"\b(m\.?\s?tech|m\.?\s?sc|m\.?s\.?\b|mca|mba|master(?:'s)?)\b", re.I), "Master's"),
    (3, re.compile(r"\b(b\.?\s?tech|b\.?e\.?\b|b\.?\s?sc|b\.?s\.?\b|bca|bachelor(?:'s)?|"
                   r"undergraduate\s+degree)\b", re.I), "Bachelor's"),
    (2, re.compile(r"\b(diploma)\b", re.I), "Diploma"),
]
DEGREE_TIER = {"Secondary": 1, "Diploma": 2, "Bachelor's": 3, "Master's": 4, "Doctorate": 5}

YEARS_RE = re.compile(
    r"(\d{1,2})\s*(?:\+|plus)?\s*(?:-|–|to)?\s*(\d{1,2})?\s*\+?\s*(?:years?|yrs?)"
    r"(?:\s+of)?(?:\s+(?:relevant|professional|hands[\s-]?on|industry|total))?"
    r"(?:\s+experience)?", re.I)


def _resolve_skills(text: str) -> Dict[str, str]:
    """Canonical skill -> category, resolved through the Milestone 1 taxonomy."""
    low = text.lower()
    found: Dict[str, str] = {}
    for alias, canon, cat, pattern in ALIASES:
        if len(alias) <= 2:
            continue  # too ambiguous in prose
        if pattern.search(low):
            found[canon] = cat
    return found


def analyse_job_description(text: str, title: str = "") -> Dict[str, Any]:
    """Parse raw job-posting text into structured, weighted requirements."""
    lines = [ln.strip() for ln in text.replace("\r", "").split("\n")]

    # walk the posting, tracking which kind of block we are inside
    bucket = "must"  # requirements stated before any heading read as mandatory
    per_line: List[Tuple[str, str]] = []
    for raw in lines:
        if not raw:
            continue
        bare = re.sub(r"[•\-\u2013*]\s*", "", raw).strip()
        heading = None
        for kind, pattern in SECTION_CUES:
            if pattern.match(bare) and len(bare.split()) <= 6:
                heading = kind
                break
        if heading:
            bucket = heading
            continue
        # an inline cue on the line itself beats the enclosing section
        if NICE_CUES.search(raw):
            per_line.append(("nice", raw))
        elif MUST_CUES.search(raw):
            per_line.append(("must", raw))
        else:
            per_line.append((bucket, raw))

    must: Dict[str, str] = {}
    nice: Dict[str, str] = {}
    for kind, line in per_line:
        for canon, cat in _resolve_skills(line).items():
            if kind == "must":
                must[canon] = cat
                nice.pop(canon, None)          # mandatory wins
            elif kind == "nice" and canon not in must:
                nice[canon] = cat
            elif kind == "other" and canon not in must and canon not in nice:
                nice[canon] = cat              # mentioned only in prose — treat as preferred

    # experience: prefer a line that also carries a requirement cue
    years: Optional[float] = None
    for kind, line in per_line:
        m = YEARS_RE.search(line)
        if m and (kind == "must" or MUST_CUES.search(line)):
            years = float(m.group(1))
            break
    if years is None:
        m = YEARS_RE.search(text)
        if m:
            years = float(m.group(1))

    degree, tier = None, 0
    for level, pattern, label in DEGREE_CUES:
        if pattern.search(text) and level > tier:
            degree, tier = label, level

    return {
        "title": title or (lines[0] if lines else "Untitled role"),
        "must_have": sorted(must),
        "nice_to_have": sorted(nice),
        "skills_required": sorted(set(must) | set(nice)),
        "experience_required": years,
        "education_required": degree,
        "education_tier": tier,
        "categories_covered": sorted({CATEGORIES.get(c, c) for c in {**must, **nice}.values()}),
        "parsed_counts": {"must": len(must), "nice": len(nice)},
    }


def job_from_record(job: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise a job dict from any source into the shape the matcher expects.

    Accepts a Milestone 1 `jobs` row (flat `skills_required`), an already-structured
    spec with `must_have`/`nice_to_have`, or a raw `job_description` to analyse.
    """
    if job.get("must_have") or job.get("nice_to_have"):
        must = list(job.get("must_have") or [])
        nice = [s for s in (job.get("nice_to_have") or []) if s not in must]
    elif job.get("job_description") and not job.get("skills_required"):
        parsed = analyse_job_description(job["job_description"], job.get("title", ""))
        must, nice = parsed["must_have"], parsed["nice_to_have"]
        job = {**parsed, **{k: v for k, v in job.items() if v is not None}}
    else:
        # a flat list carries no priority information: the first two thirds of a
        # posting's skill list is, in practice, the part that is actually required
        flat = list(job.get("skills_required") or [])
        cut = max(1, round(len(flat) * 0.67))
        must, nice = flat[:cut], flat[cut:]

    return {
        "job_id": job.get("job_id"),
        "title": job.get("title", "Untitled role"),
        "must_have": must,
        "nice_to_have": nice,
        "experience_required": job.get("experience_required"),
        "education_required": job.get("education_required"),
        "education_tier": job.get("education_tier")
                          or DEGREE_TIER.get(job.get("education_required") or "", 0),
        "seniority_target": job.get("seniority_target"),
    }
