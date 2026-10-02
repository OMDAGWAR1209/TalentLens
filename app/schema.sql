-- ============================================================================
-- TalentLens — AI Recruitment Copilot database schema (SQLite)
--
-- The six tables in the project document's schema diagram are reproduced column
-- for column: candidates, jobs, candidate_match, skill_gap_report,
-- interview_questions, interview_results. Columns added beyond the diagram are
-- marked "-- ext" and exist because the application needs them (field provenance,
-- identity de-duplication, per-component score breakdown).
--
-- Tables beyond the diagram:
--   users            three roles: candidate, recruiter, admin
--   auth_sessions    login sessions (only a hash of each token is stored)
--   ats_events       every pipeline stage change, append-only
--   survey_responses Milestone 4 satisfaction measurement
--   audit_log        who did what, for the admin console
-- ============================================================================

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------- diagram ----
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name             VARCHAR(120),
    email            VARCHAR(160),
    phone            VARCHAR(24),
    education        TEXT,                          -- JSON: degree, discipline, institution, year
    experience       TEXT,                          -- JSON: total_years, seniority, title, employers
    resume_path      VARCHAR(255) NOT NULL,
    created_at       TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    location         VARCHAR(120),                  -- ext
    skills           TEXT,                          -- ext  JSON [{skill, category, confidence}]
    experience_years REAL,                          -- ext  numeric copy for filtering/sorting
    source_format    VARCHAR(8),                    -- ext
    field_recall     REAL NOT NULL DEFAULT 0,       -- ext  M1 parse quality
    mean_confidence  REAL NOT NULL DEFAULT 0,       -- ext
    field_provenance TEXT,                          -- ext  JSON: line/method/confidence per field
    identity_hash    CHAR(40) NOT NULL UNIQUE,      -- ext  re-upload updates, never duplicates
    needs_review     INTEGER NOT NULL DEFAULT 0,    -- ext
    updated_at       TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    CHECK (field_recall BETWEEN 0 AND 100)
);
CREATE INDEX IF NOT EXISTS ix_candidates_email ON candidates(email);

CREATE TABLE IF NOT EXISTS jobs (
    job_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    job_title           VARCHAR(140) NOT NULL,
    department          VARCHAR(80),
    required_skills     TEXT NOT NULL DEFAULT '[]', -- JSON list (all requirements)
    experience_required TEXT,                       -- years, stored as text per the diagram
    job_description     TEXT,
    created_at          TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    must_have           TEXT,                       -- ext  JSON list
    nice_to_have        TEXT,                       -- ext  JSON list
    education_required  VARCHAR(40),                -- ext
    location            VARCHAR(120),               -- ext
    is_open             INTEGER NOT NULL DEFAULT 1, -- ext
    created_by          INTEGER REFERENCES users(user_id) ON DELETE SET NULL  -- ext
);

CREATE TABLE IF NOT EXISTS candidate_match (
    match_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id    INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id          INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    match_score     FLOAT NOT NULL,                 -- skill coverage, 0–100
    hiring_score    FLOAT NOT NULL,                 -- weighted compatibility, 0–100
    status          VARCHAR(24) NOT NULL DEFAULT 'applied',   -- ATS stage
    band            VARCHAR(12),                    -- ext  strong / possible / weak
    score_breakdown TEXT,                           -- ext  JSON
    matched_at      TIMESTAMP NOT NULL DEFAULT (datetime('now')),  -- ext
    UNIQUE (candidate_id, job_id),
    CHECK (hiring_score BETWEEN 0 AND 100),
    CHECK (status IN ('applied','screening','interview_scheduled','interview_completed',
                      'offer','hired','rejected','withdrawn'))
);
CREATE INDEX IF NOT EXISTS ix_match_job_score ON candidate_match(job_id, hiring_score DESC);

CREATE TABLE IF NOT EXISTS skill_gap_report (
    report_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id         INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id               INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    missing_skills       TEXT NOT NULL DEFAULT '[]', -- JSON list
    recommended_courses  TEXT NOT NULL DEFAULT '[]', -- JSON list of recommendations
    generated_at         TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    coverage_by_category TEXT,                       -- ext  JSON
    summary              TEXT,                       -- ext
    UNIQUE (candidate_id, job_id)
);

CREATE TABLE IF NOT EXISTS interview_questions (
    question_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id           INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    question_text    TEXT NOT NULL,
    difficulty_level VARCHAR(16),
    category         VARCHAR(40),
    skill            VARCHAR(80),                    -- ext
    candidate_id     INTEGER REFERENCES candidates(candidate_id) ON DELETE CASCADE, -- ext: NULL = role bank
    rationale        TEXT,                           -- ext
    CHECK (difficulty_level IN ('easy','medium','hard') OR difficulty_level IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_questions_job ON interview_questions(job_id);

CREATE TABLE IF NOT EXISTS interview_results (
    result_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id   INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id         INTEGER REFERENCES jobs(job_id) ON DELETE SET NULL,
    score          INTEGER,                          -- 0–100, rounded, per the diagram
    feedback       TEXT,
    interview_date DATE NOT NULL DEFAULT (date('now')),
    mode           VARCHAR(12) NOT NULL DEFAULT 'text',  -- ext  text / voice
    recommendation VARCHAR(80),                      -- ext
    transcript     TEXT,                             -- ext  JSON list of {question, answer, score}
    CHECK (score BETWEEN 0 AND 100 OR score IS NULL),
    CHECK (mode IN ('text','voice'))
);

-- ------------------------------------------------------------- beyond the diagram --
CREATE TABLE IF NOT EXISTS users (
    user_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    email         VARCHAR(160) NOT NULL UNIQUE COLLATE NOCASE,
    full_name     VARCHAR(120) NOT NULL,
    password_hash TEXT NOT NULL,                     -- pbkdf2_sha256$iterations$salt$hash
    role          VARCHAR(12) NOT NULL,
    candidate_id  INTEGER UNIQUE REFERENCES candidates(candidate_id) ON DELETE SET NULL,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    last_login_at TIMESTAMP,
    CHECK (role IN ('candidate','recruiter','admin'))
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash CHAR(64) PRIMARY KEY,                 -- sha256 of the bearer token
    user_id    INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    expires_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS ats_events (
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id       INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    from_stage   VARCHAR(24),
    to_stage     VARCHAR(24) NOT NULL,
    note         TEXT,
    actor_id     INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    at           TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS ix_ats_events_pair ON ats_events(candidate_id, job_id);

CREATE TABLE IF NOT EXISTS survey_responses (
    response_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    role        VARCHAR(40),
    csat        INTEGER NOT NULL,
    sus         TEXT,                                -- JSON list of 10 ratings, or NULL
    sus_score   REAL,
    created_at  TIMESTAMP NOT NULL DEFAULT (datetime('now')),
    CHECK (csat BETWEEN 1 AND 5)
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    action   VARCHAR(60) NOT NULL,
    detail   TEXT,
    at       TIMESTAMP NOT NULL DEFAULT (datetime('now'))
);
