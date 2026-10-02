"""Interview assistance and ATS integration (Milestone 3).

The brief's reference implementation hardcodes a dictionary of questions for two job
titles and picks from it at random. That demonstrates the shape of the feature but breaks
the moment a third role appears, and it asks every candidate the same things regardless
of what their resume actually showed.

This module generates questions from the Milestone 2 match instead. A candidate who
already evidences Kubernetes gets asked to demonstrate depth in it; a candidate missing it
gets a probe that assesses whether they could pick it up. Difficulty tracks the seniority
the parser computed. Every question records why it was selected, the same "show your work"
principle as the M1 extraction ledger and the M2 score breakdown.

Three parts:
    generate_interview_kit   role- and candidate-specific questions with rationale
    ATSPipeline              candidate management with a validated stage machine
    score_answer / InterviewSession   simulation with a real rubric, not canned replies
"""

from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field as dc_field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .matching import match, skill_gap_report
from .taxonomy import CATEGORIES, TAXONOMY

# --------------------------------------------------------------------------- #
# question templates
# --------------------------------------------------------------------------- #
# Templates are per-category rather than per-job-title, so a new role needs no new
# content — it inherits questions from whatever skills it requires.
TECHNICAL_BY_CATEGORY: Dict[str, List[str]] = {
    "lang": [
        "Walk me through a non-trivial piece of {skill} you have written. What made it non-trivial?",
        "What is something about {skill} that trips up people who are new to it?",
        "How do you debug a problem in {skill} when the error message is unhelpful?",
    ],
    "front": [
        "How do you keep a {skill} application fast as the component tree grows?",
        "Describe how you manage state in a {skill} app and when you would reach for something heavier.",
        "How do you make a {skill} interface accessible to screen readers?",
    ],
    "back": [
        "How would you design a {skill} service that has to stay responsive under a traffic spike?",
        "Describe how you handle failures and retries in a {skill} service that calls other services.",
        "How do you version a {skill} API without breaking existing clients?",
    ],
    "data": [
        "Describe a {skill} project where the first approach did not work. What did you change?",
        "How do you decide whether a {skill} model is good enough to ship?",
        "How would you detect that a {skill} model in production has started to degrade?",
    ],
    "cloud": [
        "How do you keep {skill} costs under control as usage grows?",
        "Describe how you would recover a {skill} environment after a bad deployment.",
        "What does your {skill} setup look like for keeping staging and production in sync?",
    ],
    "db": [
        "How do you approach indexing in {skill} when a query starts getting slow?",
        "Describe a {skill} schema change you had to make on a live system. How did you do it safely?",
        "When would you denormalise in {skill}, and what does it cost you?",
    ],
    "test": [
        "What do you actually test with {skill}, and what do you deliberately not test?",
        "How do you keep a {skill} suite fast enough that people still run it?",
        "Describe a bug that your {skill} tests missed. What did you change afterwards?",
    ],
    "tool": [
        "How does {skill} fit into your day-to-day workflow?",
        "Describe a time {skill} caused a problem rather than solving one.",
    ],
    "soft": [
        "Tell me about a time your {skill} made a measurable difference to a project outcome.",
    ],
}
TECHNICAL_FALLBACK = [
    "Tell me about your hands-on experience with {skill}.",
    "Describe the most complex thing you have built using {skill}.",
]

# Asked about a skill the candidate does NOT have. The goal is to assess how they
# approach unfamiliar ground, not to catch them out.
PROBE_TEMPLATES = [
    "This role uses {skill}, which I did not see on your resume. How would you go about "
    "getting productive with it, and is there something adjacent you have picked up quickly before?",
    "We work with {skill} here. What is your understanding of what it is for, and how would "
    "you approach learning it on the job?",
]
ADJACENT_PROBE = [
    "The role asks for {skill}. You have {sibling}, which is close — what would you expect "
    "to transfer directly, and where do you think the real differences are?",
]

BEHAVIOURAL_BY_SENIORITY: Dict[str, List[str]] = {
    "Entry level": [
        "Tell me about something technical you taught yourself recently. How did you go about it?",
        "Describe a time you got stuck and had to ask for help. How did you handle it?",
        "What is a piece of feedback you received that changed how you work?",
    ],
    "Junior": [
        "Describe a time you disagreed with a code review comment. What happened?",
        "Tell me about a bug you caused. How did you find it and what did you change afterwards?",
        "How do you decide when a task is actually finished?",
    ],
    "Mid level": [
        "Tell me about a technical decision you made that you later regretted.",
        "Describe a time you had to push back on a deadline or a requirement.",
        "How do you handle it when you inherit code you think is badly written?",
    ],
    "Senior": [
        "Describe a time you had to bring a struggling project back on track.",
        "Tell me about a time you had to explain a technical trade-off to a non-technical stakeholder.",
        "How do you decide what to delegate versus what to do yourself?",
    ],
    "Principal / Lead": [
        "Describe an architectural decision you made whose consequences played out over years.",
        "Tell me about a time you changed the way a team worked, not just what it built.",
        "How do you handle disagreeing with a decision made above you that you have to implement?",
    ],
    "Unclassified": [
        "Tell me about a project you are proud of and what your specific contribution was.",
        "Describe a time you had to learn something quickly under pressure.",
    ],
}

SCENARIO_TEMPLATES = [
    "It is Friday afternoon and {role_short} production is throwing errors that started after "
    "this morning's deploy. Walk me through your first thirty minutes.",
    "You pick up a ticket and realise fixing it properly means changing something the rest of "
    "the team depends on. What do you do?",
    "A stakeholder asks for something you believe is technically the wrong approach, but they "
    "are firm about it. How do you handle that conversation?",
]

DIFFICULTY_BY_SENIORITY = {
    "Entry level": "easy", "Junior": "easy", "Mid level": "medium",
    "Senior": "hard", "Principal / Lead": "hard", "Unclassified": "medium",
}
DURATION = {"easy": "2–4 min", "medium": "3–5 min", "hard": "5–8 min"}


# --------------------------------------------------------------------------- #
# question generation
# --------------------------------------------------------------------------- #
@dataclass
class Question:
    id: str
    text: str
    category: str            # technical | probe | behavioural | scenario
    difficulty: str          # easy | medium | hard
    skill: Optional[str]
    rationale: str           # why this candidate got this question
    expected_points: List[str]
    duration: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "question_id": self.id, "question_text": self.text, "category": self.category,
            "difficulty_level": self.difficulty, "skill": self.skill,
            "rationale": self.rationale, "expected_points": self.expected_points,
            "estimated_duration": self.duration,
        }


def _qid(*parts: str) -> str:
    return "q_" + hashlib.sha1("|".join(parts).encode()).hexdigest()[:10]


def _expected_points(skill: Optional[str], category: str) -> List[str]:
    """What a strong answer should touch on. Used by the rubric, and shown to the panel."""
    if category == "behavioural":
        return ["a specific situation, not a generalisation", "their own actions, not the team's",
                "a concrete outcome", "what they would do differently"]
    if category == "scenario":
        return ["asks clarifying questions before acting", "prioritises stopping the bleeding",
                "mentions communication with others", "reflects on prevention"]
    if category == "probe":
        return ["honest about not knowing it", "a concrete learning strategy",
                "a credible example of picking something up before"]
    base = ["hands-on specifics rather than definitions", "a real project or example",
            "trade-offs they considered"]
    if skill:
        base.append(f"correct working understanding of {skill}")
    return base


def generate_interview_kit(candidate: Dict[str, Any], job: Dict[str, Any],
                           total: int = 8, seed: Optional[int] = None) -> Dict[str, Any]:
    """Build a question set targeted at this candidate against this role.

    The mix is derived from the match, not fixed: strong candidates get more depth
    questions on what they have, weaker ones get more probes on what they lack.
    """
    result = match(candidate, job)
    gap = skill_gap_report(candidate, job, result)
    rng = random.Random(seed if seed is not None else
                        int(hashlib.sha1(
                            (str(candidate.get("name")) + str(job.get("title"))).encode()
                        ).hexdigest()[:8], 16))

    seniority = candidate.get("seniority") or "Unclassified"
    difficulty = DIFFICULTY_BY_SENIORITY.get(seniority, "medium")
    role_short = (job.get("title") or "the").split("—")[0].strip().lower()

    held_required = [d["skill"] for d in result["skill_detail"]
                     if d["kind"] == "must" and d["credit"] >= 1.0]
    partial = [d for d in result["skill_detail"] if 0 < d["credit"] < 1.0]
    missing_must = [d["skill"] for d in result["skill_detail"]
                    if d["kind"] == "must" and d["credit"] == 0.0]

    questions: List[Question] = []

    # Budget the slots against a remaining counter, in priority order. Reserving each
    # block independently can overshoot `total` for a candidate with many gaps, and
    # truncating a flat list at the end silently drops whatever was appended last —
    # which is how the scenario question went missing.
    remaining = total
    n_scenario = 1 if remaining >= 5 else 0
    remaining -= n_scenario
    n_behavioural = min(2 if remaining >= 5 else 1, remaining)
    remaining -= n_behavioural
    # keep at least one technical question, then split what is left between depth and probes
    n_tech = min(len(held_required), max(1, (remaining + 1) // 2)) if remaining else 0
    remaining -= n_tech
    n_probe_partial = min(len(partial), max(0, remaining))
    remaining -= n_probe_partial
    n_probe_missing = min(len(missing_must), max(0, remaining))
    remaining -= n_probe_missing
    # anything still unspent goes back to technical depth
    if remaining > 0:
        n_tech = min(len(held_required), n_tech + remaining)

    # --- technical depth on skills they claim and the role needs ---
    for skill in held_required[:n_tech]:
        entry = TAXONOMY.get(skill)
        cat = entry[0] if entry else None
        pool = TECHNICAL_BY_CATEGORY.get(cat or "", []) or TECHNICAL_FALLBACK
        text = rng.choice(pool).format(skill=skill)
        questions.append(Question(
            _qid(skill, "tech", text), text, "technical", difficulty, skill,
            f"Required by the role and evidenced on the resume — verify the depth is real.",
            _expected_points(skill, "technical"), DURATION[difficulty]))

    # --- probes on gaps, so the panel learns something the resume could not tell them ---
    for d in partial[:n_probe_partial]:
        sibling = re.search(r"has ([^(]+) \(", d["note"])
        text = rng.choice(ADJACENT_PROBE).format(
            skill=d["skill"], sibling=sibling.group(1).strip() if sibling else "related experience")
        questions.append(Question(
            _qid(d["skill"], "adj", text), text, "probe", difficulty, d["skill"],
            f"Partial match — {d['note']}. Test whether the transfer is real.",
            _expected_points(d["skill"], "probe"), DURATION[difficulty]))
    for skill in missing_must[:n_probe_missing]:
        text = rng.choice(PROBE_TEMPLATES).format(skill=skill)
        questions.append(Question(
            _qid(skill, "probe", text), text, "probe", difficulty, skill,
            "Mandatory for the role and not evidenced — assess learnability rather than assume a no.",
            _expected_points(skill, "probe"), DURATION[difficulty]))

    # --- behavioural, calibrated to the seniority the parser computed ---
    pool = BEHAVIOURAL_BY_SENIORITY.get(seniority, BEHAVIOURAL_BY_SENIORITY["Unclassified"])
    for text in rng.sample(pool, min(n_behavioural, len(pool))):
        questions.append(Question(
            _qid(seniority, "beh", text), text, "behavioural", difficulty, None,
            f"Calibrated to {seniority.lower()} — the resume computed "
            f"{candidate.get('experience_years') or (candidate.get('experience') or {}).get('total_years')}y of experience.",
            _expected_points(None, "behavioural"), DURATION[difficulty]))

    # --- one scenario ---
    if n_scenario:
        text = rng.choice(SCENARIO_TEMPLATES).format(role_short=role_short)
        questions.append(Question(
            _qid(role_short, "scen", text), text, "scenario", difficulty, None,
            "Unscripted judgement under pressure — hardest thing to fake and hardest to read from a resume.",
            _expected_points(None, "scenario"), DURATION[difficulty]))
    mix: Dict[str, int] = {}
    for q in questions:
        mix[q.category] = mix.get(q.category, 0) + 1

    return {
        "candidate": candidate.get("name"),
        "candidate_id": candidate.get("candidate_id"),
        "job_title": job.get("title"),
        "job_id": job.get("job_id"),
        "hiring_score": result["hiring_score"],
        "band": result["band"],
        "seniority": seniority,
        "difficulty": difficulty,
        "questions": [q.to_dict() for q in questions],
        "mix": mix,
        "focus": {
            "verify": held_required[:n_tech],
            "probe_partial": [d["skill"] for d in partial[:2]],
            "probe_missing": missing_must[:2],
        },
        "estimated_minutes": sum(int(q.duration.split("–")[1].split()[0]) for q in questions),
        "gap_summary": gap["summary"],
    }


def generate_role_question_bank(job: Dict[str, Any], per_category: int = 3) -> Dict[str, Any]:
    """Role-level questions with no candidate attached — what a panel reuses across a slate.

    This is the criterion "interview questions generated for multiple job roles": it works
    for any role, including ones never seen before, because it derives from the role's
    skill list rather than a hardcoded title lookup.
    """
    from .jobspec import job_from_record
    spec = job_from_record(job)
    rng = random.Random(int(hashlib.sha1(str(spec["title"]).encode()).hexdigest()[:8], 16))

    technical: List[Dict[str, Any]] = []
    for skill in spec["must_have"]:
        entry = TAXONOMY.get(skill)
        cat = entry[0] if entry else None
        pool = TECHNICAL_BY_CATEGORY.get(cat or "", []) or TECHNICAL_FALLBACK
        text = rng.choice(pool).format(skill=skill)
        technical.append(Question(
            _qid(spec["title"], skill, text), text, "technical", "medium", skill,
            f"Mandatory skill for {spec['title']}.",
            _expected_points(skill, "technical"), DURATION["medium"]).to_dict())

    seniority = spec.get("seniority_target") or (
        "Senior" if (spec.get("experience_required") or 0) >= 6
        else "Mid level" if (spec.get("experience_required") or 0) >= 3 else "Junior")
    behavioural = [
        Question(_qid(spec["title"], "beh", t), t, "behavioural",
                 DIFFICULTY_BY_SENIORITY.get(seniority, "medium"), None,
                 f"Standard for a {seniority.lower()} hire.",
                 _expected_points(None, "behavioural"),
                 DURATION[DIFFICULTY_BY_SENIORITY.get(seniority, "medium")]).to_dict()
        for t in BEHAVIOURAL_BY_SENIORITY.get(seniority, [])[:per_category]
    ]
    scenario = [
        Question(_qid(spec["title"], "scen", t), t, "scenario", "hard", None,
                 "Judgement under pressure.", _expected_points(None, "scenario"),
                 DURATION["hard"]).to_dict()
        for t in SCENARIO_TEMPLATES[:2]
    ]
    return {
        "job_id": spec.get("job_id"), "job_title": spec["title"],
        "seniority_target": seniority,
        "technical": technical[:per_category * 3],
        "behavioural": behavioural,
        "scenario": scenario,
        "total": len(technical[:per_category * 3]) + len(behavioural) + len(scenario),
    }


# --------------------------------------------------------------------------- #
# answer scoring — the rubric that makes the simulation more than theatre
# --------------------------------------------------------------------------- #
FILLER = re.compile(r"\b(um|uh|like|basically|actually|kind of|sort of|you know)\b", re.I)
SPECIFIC = re.compile(r"\b(\d+|percent|%|ms|seconds?|minutes?|hours?|days?|weeks?|months?|years?|"
                      r"reduced|increased|improved|migrated|shipped|launched|built|led|owned)\b", re.I)
STAR = {
    "situation": re.compile(r"\b(when|at the time|we were|the team was|project|client|our)\b", re.I),
    "action": re.compile(r"\b(i |i'd|i've|my approach|i decided|i built|i wrote|i led|i changed)\b", re.I),
    "result": re.compile(r"\b(result|outcome|so that|which meant|ended up|impact|after that|"
                         r"reduced|improved|saved|shipped|cut|increased|decreased|grew|doubled|halved|lowered|raised|"
                         r"launched|delivered|achieved)\b", re.I),
}


def score_answer(answer: str, question: Dict[str, Any]) -> Dict[str, Any]:
    """Score a free-text answer against the question's rubric.

    Deliberately transparent and rule-based rather than a black box: the panel can see
    exactly why a score landed where it did and override it. An LLM would read nuance
    better, but an interview score a hiring manager cannot interrogate is worse than a
    simpler one they can.
    """
    text = (answer or "").strip()
    words = len(text.split())
    if words < 5:
        return {"score": 0.0, "verdict": "no answer",
                "signals": {}, "feedback": "Too short to assess — no substantive answer given.",
                "matched_points": [], "missed_points": question.get("expected_points", [])}

    category = question.get("category", "technical")
    signals: Dict[str, Any] = {}

    # length: enough to be substantive, not rambling
    signals["length_words"] = words
    length_score = min(1.0, words / 90) if words < 90 else max(0.55, 1.0 - (words - 90) / 400)

    # concrete specifics beat abstractions
    specifics = len(SPECIFIC.findall(text))
    signals["concrete_markers"] = specifics
    specific_score = min(1.0, specifics / 5)

    # filler density
    fillers = len(FILLER.findall(text))
    signals["filler_words"] = fillers
    filler_score = max(0.0, 1.0 - (fillers / max(words, 1)) * 12)

    # STAR structure matters most for behavioural and scenario answers
    star_hits = {k: bool(p.search(text)) for k, p in STAR.items()}
    signals["structure"] = star_hits
    star_score = sum(star_hits.values()) / 3

    # does it touch the skill in question
    skill = question.get("skill")
    on_topic = bool(skill and re.search(re.escape(skill), text, re.I))
    signals["mentions_skill"] = on_topic

    if category in ("behavioural", "scenario"):
        total = star_score * 0.45 + specific_score * 0.25 + length_score * 0.20 + filler_score * 0.10
    elif category == "probe":
        total = specific_score * 0.30 + length_score * 0.30 + star_score * 0.25 + filler_score * 0.15
    else:
        total = (specific_score * 0.35 + length_score * 0.25 + star_score * 0.20 +
                 filler_score * 0.10 + (0.10 if on_topic else 0.0))

    score = round(min(1.0, total) * 100, 1)
    verdict = ("strong" if score >= 75 else "adequate" if score >= 55
               else "weak" if score >= 35 else "poor")

    matched, missed = [], []
    for point in question.get("expected_points", []):
        key = point.split()[0].lower()
        (matched if re.search(re.escape(key), text, re.I) else missed).append(point)

    notes = []
    if not star_hits["result"] and category in ("behavioural", "scenario"):
        notes.append("no clear outcome stated — ask them what actually happened")
    if not star_hits["action"]:
        notes.append("talks in 'we' rather than 'I' — probe for their personal contribution")
    if specifics < 2:
        notes.append("light on concrete detail — ask for numbers or a specific example")
    if fillers / max(words, 1) > 0.06:
        notes.append("high filler density, possibly unprepared on this topic")
    if skill and not on_topic:
        notes.append(f"did not actually address {skill}")
    if not notes:
        notes.append("well structured and specific")

    return {
        "score": score, "verdict": verdict, "signals": signals,
        "feedback": "; ".join(notes),
        "matched_points": matched, "missed_points": missed,
    }


# --------------------------------------------------------------------------- #
# interview session
# --------------------------------------------------------------------------- #
@dataclass
class InterviewSession:
    candidate: str
    job_title: str
    questions: List[Dict[str, Any]]
    candidate_id: Optional[int] = None
    job_id: Optional[int] = None
    answers: List[Dict[str, Any]] = dc_field(default_factory=list)
    started_at: str = dc_field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    cursor: int = 0

    @property
    def finished(self) -> bool:
        return self.cursor >= len(self.questions)

    def current(self) -> Optional[Dict[str, Any]]:
        return None if self.finished else self.questions[self.cursor]

    def submit(self, answer: str) -> Dict[str, Any]:
        question = self.current()
        if question is None:
            raise ValueError("interview already complete")
        scored = score_answer(answer, question)
        record = {"question_id": question["question_id"], "question": question["question_text"],
                  "category": question["category"], "skill": question.get("skill"),
                  "answer": answer, **scored}
        self.answers.append(record)
        self.cursor += 1
        return record

    def report(self) -> Dict[str, Any]:
        """Aggregate result, written to `interview_results` at the end of a session."""
        if not self.answers:
            return {"candidate": self.candidate, "job_title": self.job_title,
                    "answered": 0, "score": None,
                    "recommendation": "no answers recorded", "by_category": {}}
        overall = round(sum(a["score"] for a in self.answers) / len(self.answers), 1)
        by_cat: Dict[str, List[float]] = {}
        for a in self.answers:
            by_cat.setdefault(a["category"], []).append(a["score"])
        category_scores = {k: round(sum(v) / len(v), 1) for k, v in sorted(by_cat.items())}

        strongest = max(self.answers, key=lambda a: a["score"])
        weakest = min(self.answers, key=lambda a: a["score"])
        recommendation = ("advance to final round" if overall >= 72
                          else "second interview recommended" if overall >= 58
                          else "hold — significant gaps in the answers" if overall >= 40
                          else "do not advance")
        return {
            "candidate": self.candidate, "candidate_id": self.candidate_id,
            "job_title": self.job_title, "job_id": self.job_id,
            "answered": len(self.answers), "of": len(self.questions),
            "score": overall, "by_category": category_scores,
            "recommendation": recommendation,
            "strongest_answer": {"question": strongest["question"], "score": strongest["score"]},
            "weakest_answer": {"question": weakest["question"], "score": weakest["score"],
                               "feedback": weakest["feedback"]},
            "feedback": " ".join(
                f"[{a['category']}] {a['feedback']}." for a in self.answers if a["score"] < 60
            ) or "No weak answers flagged.",
            "interview_date": date.today().isoformat(),
            "answers": self.answers,
        }


def simulate_interview(candidate: Dict[str, Any], job: Dict[str, Any],
                       answers: Optional[Sequence[str]] = None,
                       total: int = 6) -> Dict[str, Any]:
    """Run a full session end to end. Used for the demo path and by the tests."""
    kit = generate_interview_kit(candidate, job, total=total)
    session = InterviewSession(
        candidate=kit["candidate"] or "Candidate", job_title=kit["job_title"] or "Role",
        candidate_id=kit.get("candidate_id"), job_id=kit.get("job_id"),
        questions=kit["questions"])
    for i, question in enumerate(kit["questions"]):
        reply = answers[i] if answers and i < len(answers) else ""
        session.submit(reply)
    return {"kit": kit, "transcript": session.answers, "report": session.report()}


# --------------------------------------------------------------------------- #
# ATS integration
# --------------------------------------------------------------------------- #
# A real ATS is a state machine, not a list. Invalid transitions are the main thing an
# integration has to prevent — you cannot extend an offer to someone already rejected.
STAGES = ["applied", "screening", "interview_scheduled", "interview_completed",
          "offer", "hired", "rejected", "withdrawn"]
TRANSITIONS: Dict[str, List[str]] = {
    "applied": ["screening", "rejected", "withdrawn"],
    "screening": ["interview_scheduled", "rejected", "withdrawn"],
    "interview_scheduled": ["interview_completed", "rejected", "withdrawn"],
    "interview_completed": ["offer", "rejected", "withdrawn"],
    "offer": ["hired", "rejected", "withdrawn"],
    "hired": [],
    "rejected": [],
    "withdrawn": [],
}
TERMINAL = {"hired", "rejected", "withdrawn"}


class ATSError(Exception):
    pass


@dataclass
class ATSRecord:
    email: str
    name: str
    job_title: str
    stage: str = "applied"
    candidate_id: Optional[int] = None
    job_id: Optional[int] = None
    hiring_score: Optional[float] = None
    interview_score: Optional[float] = None
    history: List[Dict[str, str]] = dc_field(default_factory=list)
    external_ref: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "email": self.email, "name": self.name, "job_title": self.job_title,
            "stage": self.stage, "candidate_id": self.candidate_id, "job_id": self.job_id,
            "hiring_score": self.hiring_score, "interview_score": self.interview_score,
            "external_ref": self.external_ref, "history": self.history,
            "is_terminal": self.stage in TERMINAL,
            "next_stages": TRANSITIONS.get(self.stage, []),
        }


class ATSPipeline:
    """In-memory ATS with a validated stage machine and a full audit trail.

    Stands in for Workday / Greenhouse / Lever. The point of the exercise is the
    integration contract — stage validation, idempotent upserts, an event log a real
    webhook could replay — not the storage, which swaps for a vendor API without the
    calling code changing.
    """

    def __init__(self) -> None:
        self._records: Dict[str, ATSRecord] = {}
        self.events: List[Dict[str, Any]] = []

    def _log(self, kind: str, email: str, detail: str) -> None:
        self.events.append({"at": datetime.now(timezone.utc).isoformat(),
                            "event": kind, "email": email, "detail": detail})

    def add_candidate(self, name: str, email: str, job_title: str,
                      stage: str = "applied", **extra: Any) -> ATSRecord:
        if stage not in STAGES:
            raise ATSError(f"unknown stage '{stage}'")
        key = email.lower().strip()
        if key in self._records:
            # idempotent: re-submitting an application updates, never duplicates
            rec = self._records[key]
            rec.name, rec.job_title = name, job_title
            for k, v in extra.items():
                if hasattr(rec, k) and v is not None:
                    setattr(rec, k, v)
            self._log("candidate.updated", key, f"resubmitted for {job_title}")
            return rec
        rec = ATSRecord(email=key, name=name, job_title=job_title, stage=stage,
                        external_ref="ATS-" + hashlib.sha1(key.encode()).hexdigest()[:8].upper(),
                        **{k: v for k, v in extra.items()
                           if k in {"candidate_id", "job_id", "hiring_score", "interview_score"}})
        rec.history.append({"at": datetime.now(timezone.utc).isoformat(),
                            "from": "—", "to": stage, "note": "application received"})
        self._records[key] = rec
        self._log("candidate.added", key, f"{name} -> {job_title} @ {stage}")
        return rec

    def get(self, email: str) -> ATSRecord:
        rec = self._records.get(email.lower().strip())
        if rec is None:
            raise ATSError(f"no candidate with email {email}")
        return rec

    def update_status(self, email: str, stage: str, note: str = "") -> ATSRecord:
        rec = self.get(email)
        if stage not in STAGES:
            raise ATSError(f"unknown stage '{stage}'")
        if stage == rec.stage:
            return rec
        allowed = TRANSITIONS.get(rec.stage, [])
        if stage not in allowed:
            raise ATSError(
                f"cannot move {rec.name} from '{rec.stage}' to '{stage}'. "
                f"Allowed from here: {allowed or 'none — this is a terminal stage'}")
        rec.history.append({"at": datetime.now(timezone.utc).isoformat(),
                            "from": rec.stage, "to": stage, "note": note or "stage change"})
        self._log("stage.changed", rec.email, f"{rec.stage} -> {stage}")
        rec.stage = stage
        return rec

    def attach_interview_result(self, email: str, report: Dict[str, Any]) -> ATSRecord:
        rec = self.get(email)
        rec.interview_score = report.get("score")
        self._log("interview.recorded", rec.email,
                  f"score {report.get('score')} — {report.get('recommendation')}")
        if rec.stage == "interview_scheduled":
            self.update_status(email, "interview_completed",
                               f"AI interview: {report.get('recommendation')}")
        return rec

    def list_candidates(self, stage: Optional[str] = None,
                        job_title: Optional[str] = None) -> List[Dict[str, Any]]:
        rows = list(self._records.values())
        if stage:
            rows = [r for r in rows if r.stage == stage]
        if job_title:
            rows = [r for r in rows if r.job_title == job_title]
        rows.sort(key=lambda r: (-(r.hiring_score or 0), r.name))
        return [r.to_dict() for r in rows]

    def funnel(self) -> Dict[str, Any]:
        counts = {s: 0 for s in STAGES}
        for rec in self._records.values():
            counts[rec.stage] += 1
        active = sum(v for k, v in counts.items() if k not in TERMINAL)
        return {
            "counts": counts, "total": len(self._records), "active": active,
            "terminal": sum(v for k, v in counts.items() if k in TERMINAL),
            "conversion": {
                "screened_of_applied": _pct(counts["screening"] + counts["interview_scheduled"] +
                                            counts["interview_completed"] + counts["offer"] +
                                            counts["hired"], len(self._records)),
                "interviewed_of_applied": _pct(counts["interview_completed"] + counts["offer"] +
                                               counts["hired"], len(self._records)),
                "offer_of_interviewed": _pct(counts["offer"] + counts["hired"],
                                             counts["interview_completed"] + counts["offer"] +
                                             counts["hired"]),
            },
            "events_logged": len(self.events),
        }


def _pct(part: int, whole: int) -> Optional[float]:
    return None if not whole else round(part / whole * 100, 1)
