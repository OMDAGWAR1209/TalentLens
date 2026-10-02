"""Candidate–job matching and skill-gap analysis (Milestone 2).

The project document specifies a weighted score over skills, experience and education.
This implements that, and then fixes four things that make the naive version misleading:

1. **Aliases.** A resume saying "JS" and a JD saying "JavaScript" are the same
   requirement. Both sides resolve through the Milestone 1 taxonomy first, so matching
   compares canonical skills rather than raw strings.

2. **Not all required skills matter equally.** A flat list treats "Kubernetes" and
   "Agile" as equal weight. Requirements are split into must-have and nice-to-have, and
   nice-to-have skills can only ever add credit — never subtract it.

3. **Adjacent skills earn partial credit.** PyTorch against a TensorFlow requirement
   scores 0.6, not 0. Without this the engine rejects genuinely strong candidates for
   framework trivia, which is the most common failure of keyword matching.

4. **Every score is explainable.** `score_breakdown` records each component, each skill
   decision and the reason for it. A recruiter rejecting a candidate on this score can
   see exactly why, which is the same "show your work" principle as the Milestone 1
   extraction ledger.

Overqualification is reported, not punished: twelve years against a three-year role
still scores full marks on experience but is flagged, because that is a hiring judgement
and not a matching error.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .jobspec import DEGREE_TIER, adjacent_credit, job_from_record
from .taxonomy import CATEGORIES


def round2(value: float) -> float:
    """Round half away from zero, to two places.

    Python's built-in round() uses banker's rounding, so round(23.795, 2) gives 23.79
    while the browser engine's Math.round gives 23.80. The console and the API must
    report the same number, so both sides round half-up explicitly.
    """
    import math
    return math.floor(value * 100 + 0.5) / 100 if value >= 0 else -(math.floor(-value * 100 + 0.5) / 100)

# component weights — mirror the project document (0.60 / 0.25 / 0.15)
W_SKILLS = 0.60
W_EXPERIENCE = 0.25
W_EDUCATION = 0.15

# within the skills component
MUST_SHARE = 0.80          # must-have skills carry most of the skill score
NICE_SHARE = 0.20

# decision bands, used for the accuracy evaluation and the UI
BAND_STRONG = 75.0         # shortlist
BAND_POSSIBLE = 55.0       # worth a screen
ACCURACY_TARGET = 85.0     # Milestone 2 gate

# Mandatory requirements are a gate, not a weighted preference. Experience and
# education together contribute 40% of the score, which is enough for a long-tenured
# candidate to clear the screen threshold while missing most of the must-have skills.
# Real screening filters on mandatory requirements first and ranks afterwards, so
# must-have coverage caps the achievable band.
GATE_SHORTLIST = 0.70      # below this, cannot be a shortlist
GATE_SCREEN = 0.50         # below this, cannot even be a screen


# --------------------------------------------------------------------------- #
# components
# --------------------------------------------------------------------------- #
def score_skills(held: Set[str], must: Sequence[str],
                 nice: Sequence[str]) -> Tuple[float, List[Dict[str, Any]], float]:
    """Coverage of the requirement list, with adjacency credit.

    Returns (combined_score, detail, must_coverage). `must_coverage` is returned
    separately because mandatory requirements act as a gate on the final band, not
    just as a weighted contribution.
    """
    detail: List[Dict[str, Any]] = []

    def cover(requirements: Sequence[str], kind: str) -> float:
        if not requirements:
            return 1.0
        total = 0.0
        for skill in requirements:
            if skill in held:
                total += 1.0
                detail.append({"skill": skill, "kind": kind, "credit": 1.0,
                               "via": "direct", "note": "held"})
                continue
            credit, sibling, group = adjacent_credit(skill, held)
            if credit:
                total += credit
                detail.append({"skill": skill, "kind": kind, "credit": round(credit, 2),
                               "via": "adjacent", "note": f"has {sibling} ({group})"})
            else:
                detail.append({"skill": skill, "kind": kind, "credit": 0.0,
                               "via": "missing", "note": "not evidenced in resume"})
        return total / len(requirements)

    must_cov = cover(must, "must")
    nice_cov = cover(nice, "nice")
    if not nice:
        return must_cov, detail, must_cov
    return must_cov * MUST_SHARE + nice_cov * NICE_SHARE, detail, must_cov


def score_experience(years: Optional[float], required: Optional[float]) -> Tuple[float, str]:
    """Ratio capped at 1.0. Below requirement scales down but keeps a floor for near-misses."""
    if required is None:
        return 1.0, "no experience requirement stated"
    if years is None:
        return 0.35, "resume did not yield a computable tenure — screened, not scored"
    if years >= required:
        if required and years >= required * 2.5 and years - required >= 4:
            return 1.0, f"{years}y vs {required}y required — meets it, flagged as overqualified"
        return 1.0, f"{years}y vs {required}y required — meets it"
    ratio = years / required
    # a candidate one year short of three is not half a candidate
    return round(max(0.0, ratio ** 0.7), 4), f"{years}y vs {required}y required — short by {round(required - years, 1)}y"


def score_education(degree: Optional[str], required_tier: int) -> Tuple[float, str]:
    """Tier comparison, not string equality — a PhD satisfies a Master's requirement."""
    if not required_tier:
        return 1.0, "no degree requirement stated"
    held_tier = 0
    if degree:
        for label, tier in DEGREE_TIER.items():
            if degree.lower().startswith(label.lower().rstrip("'s")) or label.lower() in degree.lower():
                held_tier = max(held_tier, tier)
    if not held_tier:
        return 0.3, "no degree parsed from resume"
    if held_tier >= required_tier:
        return 1.0, f"holds tier {held_tier} against tier {required_tier} required"
    return 0.55, f"holds tier {held_tier}, role asks tier {required_tier}"


# --------------------------------------------------------------------------- #
# main entry points
# --------------------------------------------------------------------------- #
def _held_skills(candidate: Dict[str, Any]) -> Set[str]:
    raw = candidate.get("skills") or []
    if raw and isinstance(raw[0], dict):
        return {s["skill"] for s in raw}
    return set(raw)


def match(candidate: Dict[str, Any], job: Dict[str, Any]) -> Dict[str, Any]:
    """Score one candidate against one job. Returns hiring score plus full breakdown."""
    spec = job_from_record(job)
    held = _held_skills(candidate)

    skill_score, skill_detail, must_coverage = score_skills(
        held, spec["must_have"], spec["nice_to_have"])
    exp_years = candidate.get("experience_years")
    if exp_years is None:
        exp_years = (candidate.get("experience") or {}).get("total_years")
    exp_score, exp_note = score_experience(exp_years, spec["experience_required"])

    degree = candidate.get("degree") or (candidate.get("education") or {}).get("degree")
    edu_score, edu_note = score_education(degree, spec["education_tier"])

    total = skill_score * W_SKILLS + exp_score * W_EXPERIENCE + edu_score * W_EDUCATION
    hiring_score = round2(total * 100)

    matched = [d["skill"] for d in skill_detail if d["credit"] >= 1.0]
    partial = [d for d in skill_detail if 0 < d["credit"] < 1.0]
    missing_must = [d["skill"] for d in skill_detail
                    if d["kind"] == "must" and d["credit"] == 0.0]

    band = ("strong" if hiring_score >= BAND_STRONG
            else "possible" if hiring_score >= BAND_POSSIBLE else "weak")

    # apply the mandatory-coverage gate. The reason is recorded whenever coverage is
    # short, not only when the band actually changes — "fails the mandatory filter" is
    # worth showing even for a candidate who was already scoring low.
    gate_note = None
    if spec["must_have"]:
        pct = round(must_coverage * 100)
        if must_coverage < GATE_SCREEN:
            if band != "weak":
                gate_note = (f"score {hiring_score}% demoted to hold: only {pct}% of "
                             f"mandatory skills evidenced (gate is "
                             f"{round(GATE_SCREEN * 100)}%)")
                band = "weak"
            else:
                gate_note = (f"fails the mandatory filter: {pct}% coverage against a "
                             f"{round(GATE_SCREEN * 100)}% gate")
        elif must_coverage < GATE_SHORTLIST and band == "strong":
            gate_note = (f"score {hiring_score}% capped at screen: {pct}% mandatory "
                         f"coverage is below the {round(GATE_SHORTLIST * 100)}% "
                         f"shortlist gate")
            band = "possible"

    return {
        "candidate_id": candidate.get("candidate_id"),
        "candidate": candidate.get("name"),
        "job_id": spec["job_id"],
        "job_title": spec["title"],
        "hiring_score": hiring_score,
        "band": band,
        "status": {"strong": "shortlist", "possible": "screen", "weak": "hold"}[band],
        "matched_skills": matched,
        "partial_skills": [{"skill": d["skill"], "credit": d["credit"], "note": d["note"]}
                           for d in partial],
        "missing_must_have": missing_must,
        "score_breakdown": {
            "skills": {"score": round(skill_score, 4), "weight": W_SKILLS,
                       "contribution": round2(skill_score * W_SKILLS * 100),
                       "must_have_total": len(spec["must_have"]),
                       "must_have_met": len(spec["must_have"]) - len(missing_must),
                       "must_have_coverage": round(must_coverage * 100, 1)},
            "experience": {"score": round(exp_score, 4), "weight": W_EXPERIENCE,
                           "contribution": round2(exp_score * W_EXPERIENCE * 100),
                           "note": exp_note},
            "education": {"score": round(edu_score, 4), "weight": W_EDUCATION,
                          "contribution": round2(edu_score * W_EDUCATION * 100),
                          "note": edu_note},
            "gate": gate_note,
        },
        "skill_detail": skill_detail,
    }


def skill_gap_report(candidate: Dict[str, Any], job: Dict[str, Any],
                     result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Per-candidate, per-role gap report with prioritised, actionable recommendations.

    Milestone 2 requires a report for every evaluated candidate, so this never returns
    empty: a candidate who meets everything gets a report that says so.
    """
    result = result or match(candidate, job)
    spec = job_from_record(job)
    detail = result["skill_detail"]

    missing_must = [d for d in detail if d["kind"] == "must" and d["credit"] == 0.0]
    missing_nice = [d for d in detail if d["kind"] == "nice" and d["credit"] == 0.0]
    partial = [d for d in detail if 0 < d["credit"] < 1.0]

    # coverage per category shows whether a gap is one skill or a whole discipline
    by_category: Dict[str, Dict[str, float]] = {}
    for d in detail:
        code = _category_of(d["skill"])
        cat = CATEGORIES.get(code, "Other") if code else "Other"
        slot = by_category.setdefault(cat, {"required": 0.0, "covered": 0.0})
        slot["required"] += 1
        slot["covered"] += d["credit"]
    coverage = {k: round(v["covered"] / v["required"] * 100, 1)
                for k, v in sorted(by_category.items())}

    recommendations: List[Dict[str, Any]] = []
    for d in missing_must:
        recommendations.append({
            "skill": d["skill"], "priority": "high",
            "action": f"Required for this role and not evidenced — build {d['skill']} to a "
                      f"working level and add a project that demonstrates it.",
            "estimated_effort": "3–6 weeks",
        })
    for d in partial:
        recommendations.append({
            "skill": d["skill"], "priority": "medium",
            "action": f"Transferable from what they already have — {d['note']}. Convert this "
                      f"to direct experience with a focused conversion exercise.",
            "estimated_effort": "1–2 weeks",
        })
    for d in missing_nice[:4]:
        recommendations.append({
            "skill": d["skill"], "priority": "low",
            "action": f"Preferred, not required. Worth adding to strengthen the profile.",
            "estimated_effort": "1–3 weeks",
        })

    if not recommendations:
        summary = ("Meets every listed requirement for this role, including the preferred "
                   "skills. No development gap identified.")
    else:
        high = sum(1 for r in recommendations if r["priority"] == "high")
        weeks = 3 * high + 1 * len(partial)
        summary = (
            f"{result['candidate'] or 'Candidate'} scores {result['hiring_score']}% against "
            f"{spec['title']}. "
            + (f"{high} mandatory skill{'s' if high != 1 else ''} missing"
               if high else "All mandatory skills covered")
            + (f", {len(partial)} partially covered by adjacent experience" if partial else "")
            + f". Estimated time to close the required gaps: {max(weeks, 1)}–{max(weeks, 1) + 3} weeks."
        )

    return {
        "candidate_id": candidate.get("candidate_id"),
        "candidate": candidate.get("name"),
        "job_id": spec["job_id"],
        "job_title": spec["title"],
        "hiring_score": result["hiring_score"],
        "matched_skills": result["matched_skills"],
        "partially_matched": result["partial_skills"],
        "missing_skills": [d["skill"] for d in missing_must + missing_nice],
        "missing_must_have": [d["skill"] for d in missing_must],
        "missing_nice_to_have": [d["skill"] for d in missing_nice],
        "coverage_by_category": coverage,
        "recommendations": recommendations,
        "summary": summary,
        "ready_now": not missing_must,
    }


def _category_of(skill: str) -> Optional[str]:
    """Category code for a canonical skill. TAXONOMY maps skill -> (category, aliases)."""
    from .taxonomy import TAXONOMY
    entry = TAXONOMY.get(skill)
    return entry[0] if entry else None


def rank_candidates(candidates: Sequence[Dict[str, Any]], job: Dict[str, Any],
                    top_k: Optional[int] = None) -> List[Dict[str, Any]]:
    """Score every candidate against one job, best first."""
    results = [match(c, job) for c in candidates]
    results.sort(key=lambda r: (-r["hiring_score"], r["candidate"] or ""))
    for i, r in enumerate(results, 1):
        r["rank"] = i
    return results[:top_k] if top_k else results


def match_matrix(candidates: Sequence[Dict[str, Any]],
                 jobs: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Every candidate against every job, plus the gap report each pair produces.

    This is what satisfies "skill-gap analysis generated for all evaluated candidates":
    the report count is asserted to equal candidates × jobs.
    """
    matches: List[Dict[str, Any]] = []
    reports: List[Dict[str, Any]] = []
    for job in jobs:
        for candidate in candidates:
            result = match(candidate, job)
            matches.append(result)
            reports.append(skill_gap_report(candidate, job, result))

    best: Dict[Any, Dict[str, Any]] = {}
    for m in matches:
        key = m["candidate_id"] or m["candidate"]
        if key not in best or m["hiring_score"] > best[key]["hiring_score"]:
            best[key] = m

    return {
        "matches": matches,
        "reports": reports,
        "pairs_evaluated": len(matches),
        "reports_generated": len(reports),
        "coverage_complete": len(reports) == len(candidates) * len(jobs),
        "best_role_per_candidate": {
            (m["candidate"] or str(k)): {"job_title": m["job_title"],
                                         "hiring_score": m["hiring_score"],
                                         "band": m["band"]}
            for k, m in best.items()},
        "band_counts": {
            band: sum(1 for m in matches if m["band"] == band)
            for band in ("strong", "possible", "weak")},
    }
