"""User satisfaction measurement (Milestone 4).

The brief lists "user satisfaction score >= 85%" as a Week-8 criterion, and its reference
solution marks it "achieved via interactive UI, clear analytics, and voice-based
screening". That is a claim, not a measurement. Satisfaction only exists once real users
have used the system and said so, so this module measures it and refuses to report a
number it does not have.

Two instruments, both standard:

**System Usability Scale (SUS).** Ten statements, each rated 1–5. Odd items are positively
worded, even items negatively worded, so a respondent who rates everything 5 does not
score 100. The standard formula maps the result to 0–100. Published benchmarks put the
average across thousands of products at about 68; 85 sits around the "excellent" band.

**CSAT.** One question — "how satisfied are you overall?" on 1–5 — reported as the share of
respondents choosing 4 or 5. This is the number most teams mean by "satisfaction %".

The Milestone 4 gate is evaluated against CSAT, with SUS reported alongside it, and only
once a minimum sample exists. With five responses one unhappy reviewer moves the score 20
points, so a gate decided on that little evidence would be noise.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

SUS_ITEMS: List[str] = [
    "I think that I would like to use this system frequently.",
    "I found the system unnecessarily complex.",
    "I thought the system was easy to use.",
    "I think that I would need the support of a technical person to use this system.",
    "I found the various functions in this system were well integrated.",
    "I thought there was too much inconsistency in this system.",
    "I would imagine that most people would learn to use this system very quickly.",
    "I found the system very cumbersome to use.",
    "I felt very confident using the system.",
    "I needed to learn a lot of things before I could get going with this system.",
]
CSAT_QUESTION = "Overall, how satisfied are you with TalentLens?"
SATISFACTION_TARGET = 85.0
MIN_RESPONSES = 8


class SurveyError(ValueError):
    pass


def sus_score(ratings: Sequence[int]) -> float:
    """Standard SUS: odd items contribute (r - 1), even items (5 - r); sum x 2.5."""
    if len(ratings) != 10:
        raise SurveyError(f"SUS needs exactly 10 ratings, got {len(ratings)}")
    if any((not isinstance(r, int)) or r < 1 or r > 5 for r in ratings):
        raise SurveyError("every SUS rating must be an integer from 1 to 5")
    total = sum((r - 1) if i % 2 == 0 else (5 - r) for i, r in enumerate(ratings))
    return total * 2.5


def sus_grade(score: float) -> str:
    """Adjective band from the published SUS benchmarks (Bangor, Kortum & Miller, 2009)."""
    if score >= 85:
        return "excellent"
    if score >= 72:
        return "good"
    if score >= 52:
        return "ok"
    if score >= 38:
        return "poor"
    return "awful"


def _ci95(values: Sequence[float]) -> Optional[List[float]]:
    n = len(values)
    if n < 2:
        return None
    mean = sum(values) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (n - 1))
    half = 1.96 * sd / math.sqrt(n)
    return [round(mean - half, 1), round(mean + half, 1)]


def summarise(responses: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate survey responses. Each is {"sus": [10 ints], "csat": 1-5, "role": str?}."""
    sus_values: List[float] = []
    csat_values: List[int] = []
    by_role: Dict[str, List[int]] = {}
    for r in responses:
        if r.get("sus") is not None:
            sus_values.append(sus_score(list(r["sus"])))
        c = r.get("csat")
        if c is not None:
            if not isinstance(c, int) or not 1 <= c <= 5:
                raise SurveyError("CSAT must be an integer from 1 to 5")
            csat_values.append(c)
            by_role.setdefault(r.get("role") or "unspecified", []).append(c)

    n = len(csat_values)
    csat_pct = round(sum(1 for c in csat_values if c >= 4) / n * 100, 1) if n else None
    sus_mean = round(sum(sus_values) / len(sus_values), 1) if sus_values else None
    enough = n >= MIN_RESPONSES

    if not n:
        status = "no responses yet — satisfaction cannot be reported"
    elif not enough:
        status = (f"only {n} response(s); need at least {MIN_RESPONSES} before the gate is "
                  f"evaluated")
    elif csat_pct >= SATISFACTION_TARGET:
        status = "gate met"
    else:
        status = f"{round(SATISFACTION_TARGET - csat_pct, 1)} pts below target"

    return {
        "responses": n,
        "csat_percent": csat_pct,
        "csat_mean": round(sum(csat_values) / n, 2) if n else None,
        "sus_mean": sus_mean,
        "sus_grade": sus_grade(sus_mean) if sus_mean is not None else None,
        "sus_ci95": _ci95(sus_values),
        "by_role": {k: round(sum(1 for c in v if c >= 4) / len(v) * 100, 1)
                    for k, v in sorted(by_role.items())},
        "target": SATISFACTION_TARGET,
        "min_responses": MIN_RESPONSES,
        "sample_sufficient": enough,
        "gate_met": bool(enough and csat_pct is not None and csat_pct >= SATISFACTION_TARGET),
        "status": status,
    }
