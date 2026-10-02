# TalentLens

**Candidate intelligence console for the AI Recruitment Copilot**
Infosys Springboard Internship 7.0 · Batch 3 · Milestones 1 & 2

---

## 0. Status

| Milestone | Weeks | Scope | Status |
|---|---|---|---|
| **M1** | 1–2 | Resume Parsing & Candidate Profiling | ✅ shipped — 98.2% field recall |
| **M2** | 3–4 | Candidate Matching & Skill-Gap Analysis | ✅ shipped — 96.9% matching accuracy |
| **M3** | 5–6 | Interview Assistance & ATS Integration | ✅ shipped — 4/4 roles, validated ATS, scored simulation |
| **M4** | 7–8 | Dashboard & Deployment | ✅ shipped — end-to-end workflow in ~140 ms; satisfaction gate awaits real users |

**131 automated tests** (130 passing, 1 skipped where `pdfplumber` is absent) across five suites, plus 87 browser checks covering every role in both offline and connected mode. A sixth suite (11 PostgreSQL tests) skips itself unless a PostgreSQL server is configured — see section 14 for why.

**Run it the full way (sign-in + database):**

```bash
cd backend
python seed.py --reset        # creates talentlens.db with demo accounts, 4 jobs, 8 candidates
uvicorn app.main:app --reload # then open http://localhost:8000
```

| Portal | Email | Password |
|---|---|---|
| Candidate | candidate@talentlens.dev | Candidate@2026 |
| Recruiter | recruiter@talentlens.dev | Recruit@2026 |
| Admin | admin@talentlens.dev | Admin@2026 |


Sections 1–8 cover Milestone 1, section 9 Milestone 2, section 10 Milestone 3, section 11 Milestone 4, section 12 deployment.

---

## 1. What this milestone had to deliver

From the project document, Milestone 1 covers weeks 1–2:

| Requirement | Where it lives |
|---|---|
| Implement resume upload and parsing functionality | `TalentLens_Console.html` → Ingest tab · `POST /api/resumes` |
| Extract candidate information from resumes | `backend/app/parser.py` — 16 extractors |
| Create structured candidate profiles | `ParsedResume.to_dict()` → `candidates` table |

And the week-2 evaluation criteria:

| Criterion | Result |
|---|---|
| Resume parser successfully extracts ≥95% of candidate information | **98.2%** weighted field recall on the sample cohort |
| Supports PDF and DOCX resume formats | PDF via pdf.js / pdfplumber, DOCX via mammoth / docx2txt, plus plain text |

Both are asserted in `backend/tests/test_acceptance.py`, so a regression in any extractor
breaks the test run rather than quietly lowering a number on a slide.

---

## 2. How to run it

### The console (no install)

Open `TalentLens_Console.html` in a browser. It loads a nine-document sample cohort on
first paint, so there is something to look at immediately. Drop your own `.pdf`, `.docx`
or `.txt` resumes onto the intake panel to parse them — everything runs in the browser,
nothing is uploaded anywhere.

### The API

```bash
cd backend
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

python seed.py                    # parse sample_resumes/ into talentlens.db, print the gate
uvicorn app.main:app --reload     # http://localhost:8000/docs

pytest -q                         # 22 tests, including the acceptance gate
```

`python seed.py` prints the acceptance report:

```
parsed 10 documents from sample_resumes/
  rejected (not a resume): unstructured_scan_notes.txt
  duplicate, updated existing candidate: ananya.krishnan28_dup_copy.txt
{
  "candidates_in_db": 8,
  "jobs_in_db": 2,
  "field_recall": 98.2,
  "mean_confidence": 94.2,
  "gate_met": true,
  "needs_review": ["Nisha Menon"]
}
```

---

## 3. The measurement, defined properly

"Extracts ≥95% of candidate information" can be read several ways, and the reading you
pick changes the number a lot. This project reports **two** numbers instead of blurring
them into one, because they answer different questions.

**Weighted field recall** — *of the information the ATS requires, how much did we get?*
This is the graded number. Twelve required fields, each weighted by how much a recruiter
depends on it (name 12, email 12, phone 10, … skills 6). A field either landed or it did
not.

```
recall = Σ weight(field) for captured fields / Σ weight(field) for all required fields
```

**Mean confidence** — *of what we captured, how sure is the extractor?*
Every extractor returns a confidence value derived from how it found the value. An email
matched by pattern scores 0.99. A name taken from line 1 of the document scores 0.98; the
same name inferred from an email local-part scores 0.52. Tenure computed from month-level
date ranges scores 0.93, and drops to 0.77 if the summary paragraph claims a different
number of years.

```
confidence = Σ weight × confidence over captured fields / Σ weight over captured fields
```

Reporting recall alone would hide a parser that guesses. Reporting confidence alone would
let a parser look excellent by capturing three easy fields and skipping the rest. Together
they are hard to game — which is the point of a metric.

Two more decisions that affect the number, both deliberate:

- **Optional fields never penalise the score.** A resume with no LinkedIn URL is not a
  parsing failure. LinkedIn, portfolio, certifications and languages are tracked as
  enrichment and reported separately.
- **Documents that are not resumes are rejected, not scored.** A vendor note with three
  readable fields and no recognisable sections is excluded from the gate and shown as
  `skipped` in the parse log. Scoring it would drag the acceptance number down by nine
  points and tell you nothing about parser quality. `sample_resumes/unstructured_scan_notes.txt`
  exists specifically to exercise this path.

### Result on the sample cohort

10 documents in → 1 rejected as not a resume → 9 scored → 1 duplicate merged → 8 candidates.

| Resume | Format | Recall | Confidence | Note |
|---|---|---|---|---|
| Ananya Krishnan | pdf | 100% | 95.0% | 12/12 fields |
| Michael Chen | docx | 100% | 92.8% | summary claims 8y, dates compute 10.3y — flagged |
| Priyanshu Rauniyar | pdf | 100% | 92.9% | student resume, expected graduation 2026 |
| Sailatha Kakumanu | docx | 100% | 94.9% | 12/12 fields |
| Omkar Kakumanu | pdf | 100% | 94.9% | two degrees, higher one selected |
| Vijayalakshmi V | docx | 100% | 93.9% | non-engineering resume, MBA + B.Com |
| Sathwik Bacchewal | txt | 100% | 94.9% | 12/12 fields |
| Nisha Menon | pdf | 83.5% | 94.6% | LinkedIn export: no phone, no location → review queue |
| *duplicate upload* | pdf | 100% | 93.9% | merged into Ananya Krishnan on identity hash |
| *vendor note* | txt | — | — | rejected, not a resume |
| **Cohort** | | **98.2%** | **94.2%** | gate met |

The one profile below the gate is below it for an honest reason — the source document
genuinely has no phone number — and it lands in the review queue rather than being
rounded away.

---

## 4. What makes this different

The brief is a resume parser. Six things here go past that, and each one exists because
of a specific failure mode.

**1. The extraction ledger.** The signature screen. Raw document on the left with line
numbers, extracted fields on the right. Click any field and the exact source line
highlights. Recruiters do not trust a parser that hands them a number; they trust one that
can show its work. This also makes debugging an extractor a five-second job.

**2. Per-field confidence with a recorded method.** Every value carries a score *and* the
name of the technique that produced it — "labelled line", "gazetteer: city + region",
"phrase after degree, education section", "inferred from email". Low-confidence fields are
surfaced on the profile rather than presented as fact.

**3. Tenure reconstruction with overlap merging and conflict detection.** Date ranges are
parsed to month precision, overlapping roles are merged so two concurrent jobs over two
years count as two years and not four, gaps over four months are flagged, and the computed
total is cross-checked against any "N years of experience" claim in the summary. When they
disagree the computed value wins and confidence drops — visible on Michael Chen's record.

**4. A canonical skill taxonomy.** 120 canonical skills across nine categories with ~180
aliases. "js", "JavaScript", "ES6" and "Java Script" collapse to one skill. Short aliases
like "ML" and "TS" are only trusted inside a skills section, where they are unambiguous.
Regex boundaries are built so "C" never matches inside "C++". Without this, a skill
frequency chart shows four bars where there should be one — the failure mode visible in
most first-pass parsers.

**5. Identity resolution.** Each profile gets a fingerprint from email, then phone, then
normalised name. Re-uploading a resume updates the existing candidate instead of creating a
second person. The sample cohort includes the same person twice, in different formats, to
demonstrate it.

**6. Bias shield.** One toggle masks name, email, phone, location and links across every
view — table, ledger, profile detail, raw document pane — while leaving skills, education
and experience fully readable. Structured screening on merit, and the kind of control a
real hiring platform is expected to have.

---

## 5. Architecture

```
Presentation      Resume intake · Extraction ledger · Candidate directory · Talent analytics
                  (single-file console; React + Vite in the deployed build)
                          │
Application       Upload service · Parsing orchestrator · Profile builder · Confidence scorer
                  (FastAPI)     └─ Milestone 2+: matching engine, skill-gap service, question generator
                          │
Intelligence      Layout-aware text extraction · Section segmenter · Entity extractors
                  · Skill taxonomy resolver     └─ Milestone 3+: embedding index, LLM reasoning
                          │
Persistence       candidates · jobs · resume object store
                  (PostgreSQL; SQLite for local dev)
                                └─ Milestone 2+: candidate_match, skill_gap_report,
                                   interview_questions, interview_results
                          │
Integration       Audit + parse ledger      └─ Milestone 3+: ATS connectors, email/calendar, voice
```

### Parsing pipeline

```
file → text acquisition → normalisation → section segmentation → field extractors
                                                                → taxonomy resolver
                                                                → scoring → profile + provenance
```

**Text acquisition.** PDF text is not read page-by-page as a blob. `pdf.js` items are
grouped by their y-coordinate and sorted by x within each row, which rebuilds real lines
and keeps two-column resume layouts intact. Naive extraction interleaves the columns and
destroys section structure — the single biggest cause of bad PDF parses.

**Section segmentation.** Headings are matched on a pattern list, but a line only counts as
a heading if it carries no content: no comma, nothing after a colon, four words or fewer.
Without that rule `Languages: Java, Python, SQL` inside a skills block registers as a
languages section and the real one at the bottom of the document is lost.

**Field extractors.** 16 extractors, each returning `(value, confidence, line, method)`.
Education picks the highest degree by tier and reads the field of study only from the text
between the degree token and the first institution keyword — otherwise "University of
California" becomes the discipline. Employers are matched on company suffixes and a known
employer list, and any candidate that matches a job-title pattern is rejected, so
"Systems Engineer" is never stored as a company.

### Schema

Eleven tables, defined in `backend/app/schema.sql` (see section 13). The six from the project diagram are reproduced column for column, so the foreign keys are
real. Milestone 1 writes `candidates` and `jobs`; the other four are created empty, which
means Milestone 2 adds queries rather than a migration.

```
candidates ──┬── candidate_match ──── jobs ──┬── interview_questions
             ├── skill_gap_report ───────────┘
             └── interview_results
```

Two columns beyond the original document:

- `identity_hash` — lets a re-upload update a candidate instead of duplicating them
- `field_provenance` — line, method and confidence per field, which is what the ledger reads

---

## 6. API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/resumes` | Upload and persist. Duplicates update in place. |
| `POST` | `/api/resumes/preview` | Parse without saving — field breakdown + document lines for the ledger. |
| `GET` | `/api/candidates` | Structured profiles. Filter by skill, minimum years, review status, free text. |
| `GET` | `/api/candidates/{id}` | One profile with full field provenance. |
| `GET` | `/api/analytics/cohort` | Everything the charts need, in one round trip. |
| `GET` | `/api/acceptance` | The week-2 gate, computed from the database. |
| `POST` | `/api/jobs` | Create a requisition. |
| `POST` | `/api/jobs/analyse` | Turn raw job-posting text into structured, weighted requirements. |
| `GET` | `/api/match/{candidate_id}/{job_id}` | Score one candidate against one job, full breakdown. |
| `GET` | `/api/jobs/{job_id}/ranking` | Rank every candidate against one job, best first. |
| `GET` | `/api/candidates/{id}/gap-report/{job_id}` | Skill-gap report for one pair. |
| `GET` | `/api/match/matrix` | Every candidate × every open job, with a gap report per pair. |
| `GET` | `/api/match/accuracy` | The week-4 gate, computed live against the labelled pairs. |

---

## 7. Tests

`pytest -q` → 22 tests. Beyond the acceptance gate, the suite pins down the specific bugs
found while building this:

- `test_university_of_x_is_not_read_as_a_field_of_study`
- `test_job_titles_are_not_stored_as_employers`
- `test_c_is_not_matched_inside_cpp`
- `test_short_aliases_are_ignored_outside_a_skills_section`
- `test_a_skills_line_is_not_treated_as_a_section_heading`
- `test_overlapping_roles_are_not_double_counted`
- `test_summary_tenure_conflict_lowers_confidence_but_keeps_the_computed_value`
- `test_non_resume_documents_are_rejected_not_scored`
- `test_duplicate_uploads_resolve_to_one_candidate`

Each of those was a real defect that produced plausible-looking wrong output. That is the
category of bug worth a regression test.

---

## 8. Known limits

Stated plainly, because a mentor will find them anyway:

- **Scanned PDFs are not read.** No text layer means no extraction. The parser rejects the
  file with a message instead of returning an empty profile. OCR (Tesseract) is the fix and
  is not in this milestone.
- **The gazetteer is a list, not a model.** Location extraction covers ~55 cities and ~22
  regions. An unlisted city falls back to a "City, Region" pattern at lower confidence.
- **Employer detection is rule-based.** A company with no suffix and no entry in the known
  list can be missed. A fine-tuned NER model would generalise better and is the natural
  Milestone 2 upgrade.
- **Non-English resumes are out of scope** for this milestone.
- **The console parses client-side.** Good for a demo and for privacy; the FastAPI service
  is the path for volume, persistence and audit.

---

## 9. Milestone 2 — Candidate Matching & Skill-Gap Analysis (weeks 3–4)

### 9.1 What this milestone had to deliver

| Requirement | Where it lives |
|---|---|
| Match candidate profiles against job requirements | `backend/app/matching.py` · `app_match.js` |
| Calculate hiring scores (compatibility %) | `match()` — skills 60% / experience 25% / education 15% |
| Generate skill-gap analysis reports | `skill_gap_report()`, one per candidate/role pair |

Week-4 evaluation criteria:

| Criterion | Result |
|---|---|
| Candidate–job matching accuracy ≥ 85% | **96.9%** against 32 labelled candidate/role pairs |
| Skill-gap analysis generated for all evaluated candidates | **32/32** pairs — `coverage_complete: true` |

Both are asserted in `backend/tests/test_matching.py`, and both engines — the browser
console and the FastAPI backend — are checked to produce identical scores on every pair
(`round2()` in `matching.py` matches JavaScript's rounding exactly, since Python's
built-in `round()` uses banker's rounding and would drift by a cent on some scores).

### 9.2 The measurement, defined honestly

"Matching accuracy ≥ 85%" needs a definition before it can be measured, because unlike
Milestone 1 there is no document to check an answer against. This project constructs one
and states its limits rather than leaving the number to imply more than it proves.

**How accuracy is defined.** Every candidate is paired with every open role. Each pair
carries a label for the decision a recruiter would make — `shortlist`, `screen`, or
`hold` — written by reading the resume against the role's requirements, with a one-line
rationale for every non-obvious call (`backend/app/evaluation.py`). Accuracy is the
fraction of pairs where the engine's band matches the label.

Because a three-class problem can be gamed by always predicting the majority class
(`hold` dominates any realistic matrix — a naive predictor scores 71.9% just by always
guessing "no"), the evaluation also reports:

- **Lift over the majority-class baseline** — 96.9% vs. 71.9% is a real 25-point gain, not a
  restatement of the class imbalance.
- **Per-class precision and recall** — a matcher can be highly accurate while never once
  correctly identifying a shortlist. `strong` precision is 100% here: every candidate the
  engine calls a shortlist really is one.
- **Severe-error count** — confusing a `strong` for a `weak` is the mistake that actually
  costs a hire; this project has zero.
- **Top-1 hit rate** — for every role, is the single best-scoring candidate genuinely the
  best one available in the cohort? 100% here.

**Honest limitation, stated plainly.** The 32 labels were authored by the same person who
built the engine. This is not an independent benchmark. It catches regressions — if a
future change makes a strong candidate score as a hold, the test suite turns red — but it
does not prove real-world hiring quality. That requires labels from practising recruiters
scoring resumes nobody involved has seen. One disagreement was deliberately left
unresolved rather than tuned away: the engine calls Omkar Kakumanu (strong infra skills,
zero Java) a `hold` against a Java platform role where the label says `screen`. On
reflection the label was probably too generous — a good argument for either side is a
healthier sign than a suspiciously perfect number.

### 9.3 What makes the matching engine more than a keyword filter

The reference implementation in the project brief does one thing: `set.intersection()` on
raw skill strings, then a flat weighted sum. Four gaps in that approach showed up
immediately against real resumes, and are fixed here.

**1. Alias resolution.** A job description saying "JS/ES6" and a resume saying
"JavaScript" are the same requirement. Both sides resolve through the Milestone 1
taxonomy before comparison, so a skill-frequency chart doesn't show four bars for one
skill.

**2. Must-have vs. nice-to-have.** A flat requirement list treats "Kubernetes" and
"Agile" as equally important, which they never are. `jobspec.py` splits a job description
into mandatory and preferred requirements — reading section headings ("Requirements" vs.
"Nice to have") and inline cues ("Docker would be a plus") — and nice-to-have skills can
only ever add credit, never subtract it.

**3. Skill adjacency.** A candidate who knows PyTorch but not TensorFlow is not a zero on
"TensorFlow" — those are substitutable frameworks a competent engineer moves between in
days. 16 adjacency groups (ML frameworks, cloud platforms, relational databases, JVM
languages, and so on) award conservative partial credit for a sibling skill. Every group
member is checked against the real taxonomy so there are no dead entries that silently
award nothing.

**4. Mandatory skills gate the band, not just weight it.** This came from a real failure
during testing: the first version scored a 15-year veteran with a Master's degree and
almost no relevant skills as "worth a screen," because experience and education together
contribute 40% of the score — enough to clear the threshold before skills are considered
at all. `GATE_SCREEN` and `GATE_SHORTLIST` in `matching.py` cap the achievable band by
mandatory-skill coverage, the same way a real ATS filters on requirements before ranking.
Fixing this took accuracy from 81.2% to 96.9% and is pinned down by
`test_mandatory_gate_blocks_a_high_tenure_low_skill_candidate`.

**5. Every score is explainable.** `score_breakdown` records each component's
contribution, and `skill_detail` records a stated reason for every single requirement —
matched directly, matched via an adjacent skill and which one, or missing. Same "show
your work" principle as the Milestone 1 extraction ledger; a rejected candidate's report
should never be a mystery.

### 9.4 Skill-gap reports

`skill_gap_report()` runs for every candidate/role pair, including candidates who meet
every requirement — Milestone 2 asks for a report "for all evaluated candidates," which
this project reads as *all*, not just the ones with gaps. A perfect match gets a report
that says so, not an empty object.

Each report contains:
- matched skills, partially-matched skills (with which sibling skill earned the credit),
  and missing skills split into mandatory and preferred
- coverage by skill category, so a gap reads as "missing one Cloud & DevOps skill" rather
  than a bare list
- prioritised recommendations — high priority for a missing mandatory skill, medium for a
  partial match worth converting to direct experience, low for optional enrichment — each
  with an estimated time to close

### 9.5 Job description analysis

`analyse_job_description()` (Module 2 in the project document) turns raw posting text into
the structured spec the matcher needs: it separates mandatory from preferred requirements
by reading section headings and inline cues, extracts years of experience and the minimum
degree, and resolves every mentioned skill through the taxonomy. A job created with only a
flat `skills_required` list (the Milestone 1 shape) still works — `job_from_record()`
treats the first two-thirds of an untagged list as implicitly mandatory, so nothing from
Milestone 1 breaks.

### 9.6 Extending toward the brief's LLM suggestion

The project brief suggests integrating an LLM to extract skills and experience dynamically
from free text instead of a fixed list. The taxonomy-based resolver here already does the
"dynamic from free text" part without needing an LLM call — it's regex-driven and runs in
milliseconds in the browser, which matters for a console that promises resumes never leave
the client. An LLM becomes worth the added latency and cost mainly for is the parts a
taxonomy genuinely cannot do: reading semantic seniority ("led a team of 6" implying a
lead-level title the resume never states), or scoring project descriptions for depth
rather than just presence. That is a natural Milestone 3 addition rather than a
Milestone 2 requirement, since M2's gate is about accuracy and coverage, both already met
without it.

---

## 10. Milestone 3 — Interview Assistance & ATS Integration (weeks 5–6)

| Criterion | Result |
|---|---|
| Interview questions generated for multiple job roles | All 4 evaluation roles produce technical, behavioural and scenario banks |
| ATS integration functions correctly | Stage machine with validated transitions and an event log |
| AI interview simulation operational | Full session → per-answer rubric → scored report → written back to the ATS |

**Questions come from the match, not a lookup table.** The brief's reference keeps a
dictionary of questions for two job titles and picks at random, so a third role gets
nothing and every candidate for a role gets the same questions. `generate_interview_kit`
reads the Milestone 2 result instead: a skill the resume evidences becomes a *depth*
question ("verify it is real"), an adjacent skill becomes a *transfer* probe (Nisha has
PyTorch, the role wants TensorFlow — so she is asked how she would make the switch), and a
missing mandatory skill becomes a *learning-ability* probe. Difficulty follows the
seniority the parser computed. Every question carries its rationale and what a good
answer should cover, so an interviewer is never handed a question without knowing why.

**The ATS refuses invalid moves.** The reference `update_status` accepts any string, so a
candidate can be moved from "applied" to "hired" or to a typo. `ATSPipeline` defines eight
stages and the legal transitions between them; `applied → hired` raises `ATSError` and
says which moves are allowed. Rejection and withdrawal are reachable from any active stage.
Every change is logged as an event — the shape a Workday or Greenhouse webhook would replay.

**The simulation scores answers.** The reference prints "Sarah provides a thoughtful
answer about Describe…" for every question. Here each answer is scored against a stated
rubric — structure (situation/action/result), concrete detail, filler density, and whether
it actually addressed the skill — with weights that change by question type. Tests pin
down that a substantive answer outscores an empty one and that a structured story
outscores a vague claim. The finished report writes back to the candidate's ATS record.

**Limit stated plainly:** the rubric measures *how* an answer is built, not whether it is
technically correct. A fluent, well-structured wrong answer will score well. A
domain-aware LLM grader is the natural next step and is the one place in this project where
an LLM would earn its cost.

---

## 11. Milestone 4 — Dashboard & Deployment (weeks 7–8)

| Criterion | Result |
|---|---|
| Dashboard fully functional | Browser **Dashboard** tab and a **Streamlit** app (`streamlit_app.py`), both driven by real data |
| Voice screening module operational | Web Speech API in the browser; `SpeechRecognition` + `pyttsx3` adapters on the server |
| End-to-end workflow completed | `run_pipeline()`: parse → match → interview → ATS → dashboard, timed per stage |
| User satisfaction ≥ 85% | **Measured, not claimed** — SUS + CSAT survey; gate evaluates after 8 responses |

**No invented numbers.** The reference dashboard shows "1,247 candidates", "92% hiring
success" and "14d time to hire" — figures with no source. Every KPI here is computed from
the loaded resumes and the live ATS. Offer rate and time-to-hire need real offer dates,
so they show a dash rather than a guess, and a test fails if a placeholder ever appears.
The pipeline chart counts how many candidates *reached* each stage (from ATS history), so
it reads as a real funnel with stage-to-stage conversion.

**Voice screening is testable.** The reference ties microphone capture, speech output and
logic together in one function that cannot run without hardware. `voice.py` separates
them: `assess_transcript()` is a pure function that scores text, and `listen()`/`speak()`
are optional adapters imported only when called — on a server with no microphone they
report the problem instead of crashing. Scoring is 80% content (the Milestone 3 rubric)
and 20% delivery (filler rate, hedging, pace). Delivery is kept minor on purpose: weighting
accent, pace or nerves heavily would turn a screen into a test of presentation, which is
unfair and a known source of bias. Speech-to-text errors become scoring errors, so a real
deployment should store the transcript beside the score for review.

**Satisfaction is a measurement.** The brief marks "≥85% user satisfaction" as achieved
"via interactive UI". Satisfaction belongs to users, not code, so TalentLens measures it:
the standard 10-item **System Usability Scale** (odd items positive, even negative — rating
everything 5 scores 50, not 100) plus one **CSAT** question reported as the share rating
4–5. The gate is evaluated on CSAT and only after **8 responses**; with fewer, one person
moves the number by 12+ points. **To pass this criterion, ask your mentor and at least seven
classmates to complete the survey on the Dashboard tab** — that number is the evidence.

---

## 12. Deployment

```bash
cd backend
docker compose up --build        # dashboard → http://localhost:8501, API → http://localhost:8000/docs
# or, without Docker:
pip install -r requirements.txt
streamlit run streamlit_app.py
```

The image is `python:3.11-slim`, installs dependencies before copying code (so edits do not
reinstall packages), runs as a non-root user, and has a health check on both services.
For Streamlit Community Cloud, point it at `backend/streamlit_app.py`. Microphone capture
needs `pyaudio` and a real microphone, so it is a local feature — a container serves voice
screening through the browser console or by posting a transcript to
`POST /api/voice/assess/{candidate}/{job}`.

**Milestone 3 & 4 endpoints:** `GET /api/interview/kit/...`, `/api/ats/*`,
`GET /api/pipeline/run`, `GET /api/dashboard`, `GET /api/voice/prompts/{c}/{j}`,
`POST /api/voice/assess/{c}/{j}`, `GET /api/feedback/questions`, `POST /api/feedback`,
`GET /api/feedback/summary`.

---

## 13. Accounts, roles and the database

### Three portals

The console opens on a sign-in screen with three portals. Each sends its role with the
login, so a candidate account is refused on the admin portal instead of quietly let in.

| Role | Sees | Can do |
|---|---|---|
| **Candidate** | My applications · Interview practice | Register, upload their own resume, see their match and skill-gap report for every open role, practise interviews, withdraw, give feedback. **Never sees another candidate.** |
| **Recruiter** | Milestone 1–4 workspaces · Blueprint | Parse resumes, rank and match, run interviews, move candidates through the ATS, read the dashboard. |
| **Admin** | Everything a recruiter sees · Administration | Create recruiter/admin accounts, change roles, disable accounts, browse every database table, read the audit log. |

Candidates register themselves; staff accounts can only be created by an admin. An admin
cannot disable or demote their own account, so the system can never be left with no admin.

### Tabs follow the milestone

For staff, clicking a milestone in the left rail shows **only that milestone's tabs**:
Milestone 1 → Ingest & ledger, Candidate profiles, Talent analytics · Milestone 2 → Match
workspace · Milestone 3 → Interview studio · Milestone 4 → Dashboard. Blueprint (and, for
admins, Administration) stays available in every milestone. The page heading changes with it.

### Where the rules are enforced

Permissions live in one table (`auth.PERMISSIONS`) and are checked in `services.py` —
not in the HTTP routes and not in the browser. Hiding a tab is only a convenience; a
candidate who calls `/api/gap-report/{someone_else}/1` directly still gets **403**. Tests
call the service layer directly to prove each rule holds however it is reached.

### Security, briefly

* Passwords: PBKDF2-HMAC-SHA256, 260,000 iterations, random salt per user (Python standard
  library; the construction Django uses). Rules: 8+ characters, a capital letter, a number.
* Sessions: a random 256-bit token. The database stores only its SHA-256, so a copied
  database file does not contain usable sessions. Sessions expire after 12 hours.
* A failed login gives the same message whether the email or the password was wrong, so
  the form cannot be used to find out which emails are registered.
* Every sign-in, failed sign-in, upload, stage change and admin action goes to `audit_log`.
* The demo passwords are public — change them before anything real is stored.

### The database

SQLite, defined in **`backend/app/schema.sql`**. The six tables in the project diagram are
reproduced **column for column** (`candidates`, `jobs`, `candidate_match` with
`match_score`/`hiring_score`/`status`, `skill_gap_report` with `recommended_courses`,
`interview_questions`, `interview_results`), and a test fails if any diagram column goes
missing. Five tables are added: `users`, `auth_sessions`, `ats_events`, `survey_responses`,
`audit_log`. Foreign keys and check constraints are enforced (an unknown ATS stage or role
is rejected by the database itself), and the ATS stage machine is checked in `db.py` before
any write.

SQLite was chosen over an ORM because it ships with Python, needs no server or install,
and keeps the whole schema in one readable file. The earlier SQLAlchemy layer could never
be run in the build environment and so had never actually executed; replacing it with a
schema that runs in every test was the safer choice. Moving to PostgreSQL means replacing
`db.py` only — nothing above it writes SQL.

You can open `backend/talentlens.db` in **DB Browser for SQLite** (free) to show the tables
and rows directly, or use the Administration → Database browser tab.

### Two modes, always labelled

* **Connected** — open `http://localhost:8000` (or the HTML file while the server runs).
  Sign-in, uploads, ATS moves and survey responses are saved to the database, so a survey
  completed on eight different laptops adds up to one result.
* **Offline demo** — the HTML file with no server. The same accounts work against the
  in-browser engines, nothing is saved, and the sign-in screen says so.

The session is kept in memory, so refreshing the page signs you out.

### Bugs found while building this

* **A thin duplicate overwrote a complete profile.** The sample set uploads Ananya
  Krishnan twice; the second copy has 9 skills instead of 22. Saving to the database made
  the second overwrite the first and she dropped out of the shortlist (37.5% → 25%). A
  recruiter's duplicate now keeps the more complete version and logs why; a candidate's
  own re-upload always replaces, because updating it is the point.
* **Every session would have crashed on first use.** Timestamps were being converted to
  Python datetimes on read while the expiry check expected text.
* **Answers to an interview could not be submitted through the API** — an earlier route
  captured the URL. A test now fails if any two routes can shadow each other.
* **The upload size limit was not a limit.** `copyfileobj(length=…)` sets a buffer size;
  uploads are now read with a hard 8 MB cap.

---

## 14. PostgreSQL and containers — what is tested and what is not

SQLite is the default and is the backend the 130-test suite runs against. Setting one
environment variable switches the same code to PostgreSQL instead, for a deployment
running several API instances where a single file will not do.

**Status, stated plainly: the PostgreSQL path has never been run.** This project's build
environment had no PostgreSQL server and no network access to install the `psycopg`
driver, so unlike everything else here it could not be executed even once. It was written
carefully against the SQLite original and PostgreSQL's documented behaviour, and reading
it closely turned up four defects that would each have broken the app on first use:

| Defect | SQLite | PostgreSQL | Fix |
|---|---|---|---|
| `candidate_id IS ?` | legal | syntax error — `IS` takes only `NULL`/`TRUE`/`FALSE` | `IS NOT DISTINCT FROM ?`, portable |
| JSON columns | stored as text | `psycopg` auto-decodes `JSONB` into a dict, which the app's decoder would have swallowed, **returning empty data for every JSON field** | JSON stays `TEXT` on both backends, so both decode identically |
| Timestamps | returned as text | returned as `datetime` objects — `auth.is_expired()` calls `strptime()` on them and **would have crashed on the first login** | normalised to text in `Database.one()`/`.all()`, the one place every row passes through |
| Booleans | `1`/`0` accepted | no implicit int→boolean cast | real `True`/`False` and `TRUE`/`FALSE` literals, correct on both |

The timestamp fix is the one that mattered most, and it is also the one piece of this that
*could* be tested without a server, because it is pure Python — it is verified, and a real
`datetime` now converts correctly before reaching the session check.

### Verify it yourself before trusting it

```bash
docker compose --profile postgres up -d db        # PostgreSQL 16 + pgAdmin
pip install -r requirements.txt                   # now includes psycopg[binary]
export DATABASE_URL=postgresql://talentlens:talentlens@localhost:5432/talentlens
#   Windows: set DATABASE_URL=postgresql://talentlens:talentlens@localhost:5432/talentlens

python scripts/check_postgres.py                  # 11 smoke checks, names what breaks
pytest tests/test_postgres.py -v                  # mirrors the proven SQLite tests
```

`tests/test_postgres.py` **skips itself entirely** unless `DATABASE_URL` points at
PostgreSQL, so `pytest` still reports a clean run on a machine without it. If either
command reports a failure, that is expected of code that has never run — report it and it
gets fixed. What should *not* happen is the rest of the project breaking: SQLite was
re-tested after every change above and stayed at **130 passed, 1 skipped** throughout.

### Containers

```bash
docker compose up --build                 # API + console :8000, dashboard :8501  (SQLite — tested path)
docker compose --profile postgres up      # adds PostgreSQL :5432, pgAdmin :5050, API on :8010 (untested)
```

PostgreSQL sits behind a Compose **profile**, so a plain `docker compose up` never starts
a database server that has not been verified. The PostgreSQL API runs on port **8010**
rather than 8000, so it cannot collide with the SQLite one and you can compare them side
by side. pgAdmin at `localhost:5050` (admin@talentlens.dev / admin) is a browser GUI for
inspecting tables directly.

`deploy/nginx.conf` is the reverse-proxy reference for a real server: TLS termination,
an 8 MB request cap matching the app's own upload limit, gzip for the ~290 KB console,
WebSocket upgrade headers for Streamlit, and a 10-per-minute rate limit on
`/api/auth/login` — worth having because password hashing at 260,000 PBKDF2 iterations is
deliberately expensive, which costs the server as much as the attacker. It is reference
configuration, not part of `docker compose up`, and has not been load-tested.

### Which to use

For your review, **use SQLite**. It is the tested path, it needs no server, and
`backend/talentlens.db` opens directly in DB Browser for SQLite if you want to show the
tables. PostgreSQL is worth mentioning as the deployment story — the architecture supports
it and the code is there — but describe it as written-and-not-yet-verified, because that
is what it is.

---

## 15. Files

```
TalentLens_Console.html            the dashboard — open this first (Ingest, Profiles, Analytics, Match, Blueprint)
README.md                          this report
backend/
  requirements.txt
  seed.py                          parses resumes, runs matching, prints both milestone gates
  app/
    parser.py                      M1 — extraction engine, scoring, cohort metrics
    taxonomy.py                    M1/M2 — 120 canonical skills, 9 categories, ~180 aliases
    main.py                        FastAPI — 36 role-protected routes; serves the console at /
    schema.sql                     SQLite schema (diagram tables + users, sessions, ATS history, surveys, audit)
    schema_postgres.sql            PostgreSQL translation of the same schema (untested — section 14)
    db.py                          data-access layer, backend-neutral
    db_backend.py                  SQLite / PostgreSQL connection shim
    auth.py                        password hashing, sessions, permission matrix
    services.py                    every operation, with role and ownership checks
    jobspec.py                     M2 — skill adjacency graph, job-description analysis
    matching.py                    M2 — hiring-score engine, skill-gap reports
    evaluation.py                  M2 — labelled pairs, accuracy/precision/recall
    interview.py                   M3 — question kits, answer rubric, simulation, ATS pipeline
    voice.py                       M4 — voice screening (testable core + optional mic/TTS)
    satisfaction.py                M4 — SUS + CSAT scoring
    pipeline.py                    M4 — end-to-end workflow and dashboard KPIs
  streamlit_app.py                 M4 — Streamlit recruitment dashboard
  Dockerfile, docker-compose.yml   M4 — deployment
  tests/
    test_acceptance.py             M1 — 22 tests, 95% recall gate
    test_matching.py               M2 — 23 tests, 85% accuracy gate
    test_interview.py              M3 — 27 tests
    test_milestone4.py             M4 — 26 tests
    test_database.py               database, auth and role tests — 33 tests
    test_postgres.py               optional: 11 PostgreSQL tests, skipped unless DATABASE_URL is set
  scripts/check_postgres.py        PostgreSQL smoke test — run before trusting that backend
  deploy/nginx.conf                reverse-proxy reference config (TLS, gzip, rate limiting)
  sample_resumes/                  10 fixtures, varied layouts on purpose
```

Both milestones share one definition of the field contract, the taxonomy, and — for
matching — the adjacency table and scoring formula between the browser console
(`app_match.js`, inlined into the HTML) and the backend (`matching.py`). Both were
checked pair-by-pair to produce identical scores on the full sample cohort before this
was written up, so the number on the dashboard is the number the test suite enforces on
either side.
"# TalentLens" 
