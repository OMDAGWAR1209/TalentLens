"""Matching accuracy evaluation.

"Candidate–job matching accuracy >= 85%" needs a definition before it can be measured,
because a matcher has no single correct output the way a parser does. Milestone 1 could
count fields against the source document. There is no equivalent ground truth here, so
one is constructed and stated openly.

**How accuracy is defined.** Every candidate in the cohort is paired with every open
role, and each pair carries a label for the decision a recruiter would make:

    shortlist  strong fit, take to interview
    screen     plausible, worth a call — usually a partial skills fit or a tenure gap
    hold       not a fit for this role right now

Accuracy is the proportion of pairs where the engine's band matches the label. That is
the headline number. Because a three-class problem can be gamed by always predicting the
majority class ("hold" dominates any realistic matrix), this module also reports
per-class precision and recall, and the top-1 hit rate — whether the best-scoring
candidate for each role is genuinely a labelled shortlist.

**Honest limitation.** The labels in `LABELLED_PAIRS` were authored by reading each
resume against each role's requirements, and they encode a judgement about what matters
in a role. Two recruiters would disagree on some of the `screen` cases. They are also
authored by the same person who wrote the engine, so this is not an independent
benchmark — it measures self-consistency and catches regressions, not real-world hiring
quality. Validating properly needs labels from practising recruiters on resumes nobody
involved has seen. The band thresholds in `matching.py` were fixed before these labels
were scored, and are not tuned per pair.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from .matching import ACCURACY_TARGET, match, rank_candidates

# --------------------------------------------------------------------------- #
# roles under evaluation
# --------------------------------------------------------------------------- #
EVAL_JOBS: List[Dict[str, Any]] = [
    {
        "job_id": 1, "title": "Software Engineer II — Platform",
        "must_have": ["Java", "Spring Boot", "REST APIs", "Microservices", "SQL",
                      "PostgreSQL", "Docker", "Kubernetes", "AWS", "CI/CD"],
        "nice_to_have": ["Git", "JUnit", "System design", "Agile", "Problem solving"],
        "experience_required": 4, "education_required": "Bachelor's",
    },
    {
        "job_id": 2, "title": "Machine Learning Engineer",
        "must_have": ["Python", "Machine learning", "Deep learning", "NLP", "PyTorch",
                      "TensorFlow", "scikit-learn", "Pandas", "NumPy", "LLMs"],
        "nice_to_have": ["SQL", "Docker", "AWS", "Statistics", "Git"],
        "experience_required": 4, "education_required": "Master's",
    },
    {
        "job_id": 3, "title": "Full-stack Developer",
        "must_have": ["JavaScript", "TypeScript", "React", "Node.js", "Express",
                      "REST APIs", "MongoDB", "PostgreSQL", "HTML", "CSS"],
        "nice_to_have": ["Docker", "Git", "Jest", "Agile", "Communication"],
        "experience_required": 3, "education_required": "Bachelor's",
    },
    {
        "job_id": 4, "title": "Data Analyst",
        "must_have": ["SQL", "Python", "Excel", "Power BI", "Tableau", "Statistics",
                      "Pandas", "Data visualisation"],
        "nice_to_have": ["ETL", "Communication", "Presentation", "Problem solving"],
        "experience_required": 2, "education_required": "Bachelor's",
    },
]

# --------------------------------------------------------------------------- #
# ground truth: (candidate name, job_id) -> expected band
#   strong   = shortlist        possible = screen        weak = hold
# Rationale is recorded for every non-obvious call so the label can be argued with.
# --------------------------------------------------------------------------- #
LABELLED_PAIRS: Dict[Tuple[str, int], Tuple[str, str]] = {
    # --- Software Engineer II — Platform -----------------------------------
    ("Ananya Krishnan", 1): ("strong", "Java/Spring/K8s/AWS platform engineer, 6.2y — direct hit"),
    ("Omkar Kakumanu", 1): ("possible", "owns the whole infra half (K8s, AWS, Terraform, CI) but no Java application stack"),
    ("Michael Chen", 1): ("weak", "10y frontend/Node — wrong stack for a Java platform role"),
    ("Sailatha Kakumanu", 1): ("weak", "capable backend dev but Python/Django, missing Java, Spring, K8s, AWS"),
    ("Sathwik Bacchewal", 1): ("weak", "Java and JUnit present, but QA automation not service development"),
    ("Nisha Menon", 1): ("weak", "data scientist, none of the platform stack"),
    ("Priyanshu Rauniyar", 1): ("weak", "0.2y and no backend stack"),
    ("Vijayalakshmi V", 1): ("weak", "business analyst, no engineering skills"),

    # --- Machine Learning Engineer -----------------------------------------
    ("Nisha Menon", 2): ("strong", "PyTorch/sklearn/Pandas/Stats, 5.2y, M.Sc — misses only NLP and LLMs"),
    ("Priyanshu Rauniyar", 2): ("possible", "hard case: near-complete ML skill set incl. TF and NLP, but 0.2y against a 4y role"),
    ("Ananya Krishnan", 2): ("weak", "Python and Docker only, no ML stack"),
    ("Omkar Kakumanu", 2): ("weak", "Python and cloud, no modelling experience"),
    ("Sailatha Kakumanu", 2): ("weak", "Python web dev, not ML"),
    ("Sathwik Bacchewal", 2): ("weak", "Python for test automation, not ML"),
    ("Michael Chen", 2): ("weak", "no Python, no ML"),
    ("Vijayalakshmi V", 2): ("weak", "no ML or programming depth"),

    # --- Full-stack Developer ----------------------------------------------
    ("Michael Chen", 3): ("strong", "JS/TS/React/Node/Express/PG/HTML/CSS, 10.2y — textbook fit"),
    ("Sailatha Kakumanu", 3): ("possible", "JS/TS/React/Mongo/PG and REST, but Node and Express absent — Django/FastAPI instead"),
    ("Sathwik Bacchewal", 3): ("weak", "JavaScript is for Cypress/Selenium, no framework work"),
    ("Ananya Krishnan", 3): ("weak", "no JavaScript at all despite strong backend"),
    ("Nisha Menon", 3): ("weak", "no web stack"),
    ("Omkar Kakumanu", 3): ("weak", "infra, not application development"),
    ("Priyanshu Rauniyar", 3): ("weak", "no web framework experience and 0.2y"),
    ("Vijayalakshmi V", 3): ("weak", "no development skills"),

    # --- Data Analyst -------------------------------------------------------
    ("Vijayalakshmi V", 4): ("possible", "10y with SQL/Excel/Power BI/Tableau and stakeholder skills, but no Python or Pandas"),
    ("Nisha Menon", 4): ("possible", "over-qualified data scientist — has SQL/Python/Pandas/Stats/Tableau, missing only BI tooling"),
    ("Priyanshu Rauniyar", 4): ("possible", "strong analyst toolkit (SQL, Python, Pandas, Power BI, Excel, viz) held back by 0.2y tenure"),
    ("Ananya Krishnan", 4): ("weak", "SQL and Python but no analytics or BI tooling"),
    ("Sathwik Bacchewal", 4): ("weak", "SQL and Python from a QA context, no analytics"),
    ("Sailatha Kakumanu", 4): ("weak", "no BI or statistics"),
    ("Omkar Kakumanu", 4): ("weak", "no analytics tooling"),
    ("Michael Chen", 4): ("weak", "no analytics or BI"),
}

BANDS = ("strong", "possible", "weak")


def evaluate(candidates: Sequence[Dict[str, Any]],
             jobs: Sequence[Dict[str, Any]] = tuple(EVAL_JOBS)) -> Dict[str, Any]:
    """Score every labelled pair and report agreement with the ground truth."""
    by_name = {c.get("name"): c for c in candidates}
    rows: List[Dict[str, Any]] = []
    jobs_by_id = {j["job_id"]: j for j in jobs}

    for (name, job_id), (expected, rationale) in sorted(LABELLED_PAIRS.items()):
        candidate = by_name.get(name)
        job = jobs_by_id.get(job_id)
        if candidate is None or job is None:
            continue
        result = match(candidate, job)
        rows.append({
            "candidate": name,
            "job_title": job["title"],
            "hiring_score": result["hiring_score"],
            "predicted": result["band"],
            "expected": expected,
            "correct": result["band"] == expected,
            "rationale": rationale,
            "missing_must_have": result["missing_must_have"],
        })

    if not rows:
        return {"error": "no labelled pairs matched the supplied candidates"}

    correct = sum(1 for r in rows if r["correct"])
    accuracy = round(correct / len(rows) * 100, 1)

    # per-class precision / recall
    per_class: Dict[str, Dict[str, Any]] = {}
    for band in BANDS:
        tp = sum(1 for r in rows if r["predicted"] == band and r["expected"] == band)
        fp = sum(1 for r in rows if r["predicted"] == band and r["expected"] != band)
        fn = sum(1 for r in rows if r["predicted"] != band and r["expected"] == band)
        precision = tp / (tp + fp) if tp + fp else None
        recall = tp / (tp + fn) if tp + fn else None
        f1 = (2 * precision * recall / (precision + recall)
              if precision and recall else None)
        per_class[band] = {
            "support": tp + fn,
            "precision": round(precision * 100, 1) if precision is not None else None,
            "recall": round(recall * 100, 1) if recall is not None else None,
            "f1": round(f1 * 100, 1) if f1 is not None else None,
        }

    confusion = {e: {p: sum(1 for r in rows if r["expected"] == e and r["predicted"] == p)
                     for p in BANDS} for e in BANDS}

    # a majority-class predictor is the baseline this has to beat to mean anything
    majority = max(BANDS, key=lambda b: sum(1 for r in rows if r["expected"] == b))
    baseline = round(sum(1 for r in rows if r["expected"] == majority) / len(rows) * 100, 1)

    # ranking quality: is the top-scoring candidate the best one actually available?
    # Scoring against "strong" alone is unfair on a role where no candidate in the
    # cohort is a shortlist — there the correct behaviour is to surface the best
    # screen, so the bar is the highest label that exists for that role.
    rank_order = {"strong": 3, "possible": 2, "weak": 1}
    top1: List[Dict[str, Any]] = []
    for job in jobs:
        ranked = rank_candidates(list(by_name.values()), job, top_k=1)
        if not ranked:
            continue
        winner = ranked[0]
        labels_for_job = [b for (n, jid), (b, _) in LABELLED_PAIRS.items()
                          if jid == job["job_id"] and n in by_name]
        if not labels_for_job:
            continue
        best_available = max(labels_for_job, key=lambda b: rank_order[b])
        label = LABELLED_PAIRS.get((winner["candidate"], job["job_id"]), ("unlabelled", ""))[0]
        top1.append({"job_title": job["title"], "top_candidate": winner["candidate"],
                     "hiring_score": winner["hiring_score"], "expected_band": label,
                     "best_available_band": best_available,
                     "hit": label == best_available})

    # a wrong call is much worse when it crosses shortlist/hold than when it lands
    # one band away, so severity is reported separately from raw accuracy
    severe = [r for r in rows if {r["predicted"], r["expected"]} == {"strong", "weak"}]

    return {
        "pairs_evaluated": len(rows),
        "correct": correct,
        "accuracy": accuracy,
        "target": ACCURACY_TARGET,
        "target_met": accuracy >= ACCURACY_TARGET,
        "majority_class_baseline": baseline,
        "lift_over_baseline": round(accuracy - baseline, 1),
        "per_class": per_class,
        "confusion_matrix": confusion,
        "severe_errors": severe,
        "severe_error_count": len(severe),
        "top1_by_role": top1,
        "top1_hit_rate": round(sum(1 for t in top1 if t["hit"]) / len(top1) * 100, 1) if top1 else None,
        "disagreements": [r for r in rows if not r["correct"]],
        "rows": rows,
    }
