"""End-to-end recruitment workflow (Milestone 4).

    resume files -> parse (M1) -> match + gap report (M2)
                 -> interview kit, simulated interview, ATS stage moves (M3)
                 -> dashboard metrics (M4)

`run_pipeline` is the single function the Week-8 criterion "end-to-end recruitment
workflow completed successfully" is tested against. Each stage records how long it took
and how many items it handled, so a failure points at the stage that caused it.

`dashboard_metrics` computes every KPI from data the system actually holds. The reference
dashboard in the brief shows "1,247 candidates" and "92% hiring success rate" — numbers
with no source. Nothing here is a placeholder: a KPI with no underlying data is reported
as None and shown as a dash, never invented.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .evaluation import EVAL_JOBS, evaluate
from .interview import ATSPipeline, simulate_interview
from .matching import match, match_matrix
from .parser import cohort_metrics, parse_file

STRONG_SAMPLE = ("In my last role I owned this end to end. I profiled the bottleneck, redesigned "
                 "the approach, measured it against a held-out set and shipped it, which cut "
                 "latency by forty percent. I added monitoring so regressions are caught early.")


def _timed(label: str, fn: Callable[[], Any], log: List[Dict[str, Any]], count: Callable[[Any], int]):
    started = time.perf_counter()
    out = fn()
    log.append({"stage": label, "ms": round((time.perf_counter() - started) * 1000, 1),
                "items": count(out), "ok": True})
    return out


def run_pipeline(resume_dir: str | Path, jobs: Sequence[Dict[str, Any]] = tuple(EVAL_JOBS),
                 interview_top_n: int = 1) -> Dict[str, Any]:
    """Run every milestone in order on a folder of resumes."""
    log: List[Dict[str, Any]] = []
    resume_dir = Path(resume_dir)

    # ---- M1: parse --------------------------------------------------------
    def _parse():
        files = sorted((p for p in resume_dir.iterdir()
                        if p.suffix.lower() in {".pdf", ".docx", ".txt"}), key=lambda p: p.name)
        return [parse_file(p) for p in files]
    parsed = _timed("parse", _parse, log, len)
    m1 = cohort_metrics(parsed)

    seen, candidates = set(), []
    for p in parsed:
        if p.is_resume and p.identity_hash not in seen:
            seen.add(p.identity_hash)
            profile = p.to_dict()
            profile["candidate_id"] = len(candidates) + 1
            candidates.append(profile)

    # ---- M2: match --------------------------------------------------------
    matrix = _timed("match", lambda: match_matrix(candidates, list(jobs)), log,
                    lambda m: m["pairs_evaluated"])
    accuracy = evaluate(candidates, jobs)

    # ---- M3: ATS + interview the top candidates for each role --------------
    ats = ATSPipeline()

    def _interview():
        reports = []
        for c in candidates:
            best = max((match(c, j) for j in jobs), key=lambda r: r["hiring_score"])
            email = c.get("email") or f"candidate{c['candidate_id']}@example.com"
            ats.add_candidate(c["name"], email, best["job_title"])
            if best["band"] in ("strong", "possible"):
                ats.update_status(email, "screening", f"match {best['hiring_score']}%")
            else:
                ats.update_status(email, "rejected", "below the mandatory-skill gate")
        for job in jobs:
            ranked = sorted(candidates, key=lambda c: -match(c, job)["hiring_score"])
            for c in ranked[:interview_top_n]:
                email = c.get("email") or f"candidate{c['candidate_id']}@example.com"
                rec = ats.get(email)
                if rec.stage != "screening":
                    continue
                ats.update_status(email, "interview_scheduled", f"top match for {job['title']}")
                # no human is answering in a batch run, so a fixed sample answer set stands in;
                # the dashboard labels these scores as simulated
                sim = simulate_interview(c, job, answers=[STRONG_SAMPLE] * 8)
                ats.update_status(email, "interview_completed", sim["report"]["recommendation"])
                ats.attach_interview_result(email, sim["report"])
                reports.append(sim["report"])
        return reports
    interviews = _timed("interview", _interview, log, len)

    # ---- M4: dashboard ----------------------------------------------------
    metrics = _timed("dashboard", lambda: dashboard_metrics(candidates, matrix, ats, interviews),
                     log, lambda m: len(m["kpis"]))

    stages_ok = all(s["ok"] for s in log)
    complete = (stages_ok and bool(candidates) and matrix["coverage_complete"]
                and len(ats.list_candidates()) == len(candidates))
    return {
        "complete": complete,
        "stages": log,
        "total_ms": round(sum(s["ms"] for s in log), 1),
        "milestone_1": {"documents": m1["documents_in"], "candidates": len(candidates),
                        "field_recall": m1["field_recall"], "gate_met": m1["gate_met"]},
        "milestone_2": {"pairs": matrix["pairs_evaluated"], "reports": matrix["reports_generated"],
                        "accuracy": accuracy["accuracy"], "gate_met": accuracy["target_met"]},
        "milestone_3": {"interviews": len(interviews), "in_ats": len(ats.list_candidates()),
                        "funnel": ats.funnel()},
        "dashboard": metrics,
        "candidates": candidates,
        "ats": ats,
    }


def dashboard_metrics(candidates: Sequence[Dict[str, Any]], matrix: Dict[str, Any],
                      ats: ATSPipeline, interviews: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """KPIs for the recruiter dashboard, every one derived from held data."""
    funnel = ats.funnel()
    counts = funnel["counts"]
    total = funnel["total"]
    scheduled_or_later = (counts["interview_scheduled"] + counts["interview_completed"]
                          + counts["offer"] + counts["hired"])
    best_scores = [v["hiring_score"] for v in matrix["best_role_per_candidate"].values()]
    shortlisted = sum(1 for v in matrix["best_role_per_candidate"].values() if v["band"] == "strong")
    interview_scores = [r["score"] for r in interviews if r.get("score") is not None]

    kpis = {
        "total_candidates": total,
        "interviews_scheduled": scheduled_or_later,
        "shortlist_rate": round(shortlisted / len(best_scores) * 100, 1) if best_scores else None,
        "average_best_match": round(sum(best_scores) / len(best_scores), 1) if best_scores else None,
        "average_interview_score": (round(sum(interview_scores) / len(interview_scores), 1)
                                    if interview_scores else None),
        # offers and hires need real decisions over time; with none recorded these stay None
        "offer_rate": (round((counts["offer"] + counts["hired"]) / scheduled_or_later * 100, 1)
                       if scheduled_or_later and (counts["offer"] + counts["hired"]) else None),
        "time_to_hire_days": None,
    }
    return {
        "kpis": kpis,
        "pipeline": {s: counts[s] for s in ("applied", "screening", "interview_scheduled",
                                             "interview_completed", "offer", "hired")},
        "rejected": counts["rejected"],
        "band_counts": matrix["band_counts"],
        "notes": {
            "time_to_hire_days": "needs real offer/accept dates — not available from a demo cohort",
            "average_interview_score": "from simulated interviews using a fixed sample answer set, not real candidates",
            "offer_rate": "no offers recorded yet" if kpis["offer_rate"] is None else "",
        },
    }
