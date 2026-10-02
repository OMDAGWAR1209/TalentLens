-- ============================================================================
-- TalentLens — AI Recruitment Copilot database schema (PostgreSQL)
--
-- Column-for-column translation of schema.sql. Differences from the SQLite version,
-- and why:
--   INTEGER PRIMARY KEY AUTOINCREMENT  -> SERIAL PRIMARY KEY     (Postgres's auto-increment)
--   TEXT holding JSON                  -> TEXT                  (indexable, validated on write)
--   TIMESTAMP DEFAULT (datetime('now'))-> TIMESTAMPTZ DEFAULT now()
--   DATE DEFAULT (date('now'))         -> DATE DEFAULT CURRENT_DATE
--   "candidate_id IS ?" (app-level)    -> handled in db_backend.py as IS NOT DISTINCT FROM
--
-- Run once, by Database.migrate() -> PostgresConn.init_schema(). Safe to run again:
-- every statement is CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
--
-- HONEST STATUS: written carefully against the SQLite original and PostgreSQL's
-- documented syntax, but not run against a live PostgreSQL server in this build
-- environment. See app/db_backend.py for how to verify it yourself before trusting it.
-- ============================================================================

CREATE TABLE IF NOT EXISTS schema_version (
    version     INTEGER PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------- diagram ----
CREATE TABLE IF NOT EXISTS candidates (
    candidate_id     SERIAL PRIMARY KEY,
    name             VARCHAR(120),
    email            VARCHAR(160),
    phone            VARCHAR(24),
    education        TEXT,
    experience       TEXT,
    resume_path      VARCHAR(255) NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    location         VARCHAR(120),
    skills           TEXT,
    experience_years REAL,
    source_format    VARCHAR(8),
    field_recall     REAL NOT NULL DEFAULT 0,
    mean_confidence  REAL NOT NULL DEFAULT 0,
    field_provenance TEXT,
    identity_hash    CHAR(40) NOT NULL UNIQUE,
    needs_review     BOOLEAN NOT NULL DEFAULT false,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (field_recall BETWEEN 0 AND 100)
);
CREATE INDEX IF NOT EXISTS ix_candidates_email ON candidates(email);

CREATE TABLE IF NOT EXISTS users (
    user_id       SERIAL PRIMARY KEY,
    email         VARCHAR(160) NOT NULL UNIQUE,
    full_name     VARCHAR(120) NOT NULL,
    password_hash TEXT NOT NULL,
    role          VARCHAR(12) NOT NULL,
    candidate_id  INTEGER UNIQUE REFERENCES candidates(candidate_id) ON DELETE SET NULL,
    is_active     BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    CHECK (role IN ('candidate','recruiter','admin'))
);
-- case-insensitive email lookup/uniqueness, since SQLite's email column uses NOCASE
CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email_ci ON users (lower(email));

CREATE TABLE IF NOT EXISTS jobs (
    job_id              SERIAL PRIMARY KEY,
    job_title           VARCHAR(140) NOT NULL,
    department          VARCHAR(80),
    required_skills     TEXT NOT NULL DEFAULT '[]',
    experience_required TEXT,
    job_description     TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    must_have           TEXT,
    nice_to_have        TEXT,
    education_required  VARCHAR(40),
    location            VARCHAR(120),
    is_open             BOOLEAN NOT NULL DEFAULT true,
    created_by          INTEGER REFERENCES users(user_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS candidate_match (
    match_id        SERIAL PRIMARY KEY,
    candidate_id    INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id          INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    match_score     REAL NOT NULL,
    hiring_score    REAL NOT NULL,
    status          VARCHAR(24) NOT NULL DEFAULT 'applied',
    band            VARCHAR(12),
    score_breakdown TEXT,
    matched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (candidate_id, job_id),
    CHECK (hiring_score BETWEEN 0 AND 100),
    CHECK (status IN ('applied','screening','interview_scheduled','interview_completed',
                      'offer','hired','rejected','withdrawn'))
);
CREATE INDEX IF NOT EXISTS ix_match_job_score ON candidate_match(job_id, hiring_score DESC);

CREATE TABLE IF NOT EXISTS skill_gap_report (
    report_id            SERIAL PRIMARY KEY,
    candidate_id         INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id               INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    missing_skills       TEXT NOT NULL DEFAULT '[]',
    recommended_courses  TEXT NOT NULL DEFAULT '[]',
    generated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    coverage_by_category TEXT,
    summary              TEXT,
    UNIQUE (candidate_id, job_id)
);

CREATE TABLE IF NOT EXISTS interview_questions (
    question_id      SERIAL PRIMARY KEY,
    job_id           INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    question_text    TEXT NOT NULL,
    difficulty_level VARCHAR(16),
    category         VARCHAR(40),
    skill            VARCHAR(80),
    candidate_id     INTEGER REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    rationale        TEXT,
    CHECK (difficulty_level IN ('easy','medium','hard') OR difficulty_level IS NULL)
);
CREATE INDEX IF NOT EXISTS ix_questions_job ON interview_questions(job_id);

CREATE TABLE IF NOT EXISTS interview_results (
    result_id      SERIAL PRIMARY KEY,
    candidate_id   INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id         INTEGER REFERENCES jobs(job_id) ON DELETE SET NULL,
    score          INTEGER,
    feedback       TEXT,
    interview_date DATE NOT NULL DEFAULT CURRENT_DATE,
    mode           VARCHAR(12) NOT NULL DEFAULT 'text',
    recommendation VARCHAR(80),
    transcript     TEXT,
    CHECK (score BETWEEN 0 AND 100 OR score IS NULL),
    CHECK (mode IN ('text','voice'))
);

-- ------------------------------------------------------------- beyond the diagram --
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash CHAR(64) PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS ats_events (
    event_id     SERIAL PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(candidate_id) ON DELETE CASCADE,
    job_id       INTEGER NOT NULL REFERENCES jobs(job_id) ON DELETE CASCADE,
    from_stage   VARCHAR(24),
    to_stage     VARCHAR(24) NOT NULL,
    note         TEXT,
    actor_id     INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    at           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ats_events_pair ON ats_events(candidate_id, job_id);

CREATE TABLE IF NOT EXISTS survey_responses (
    response_id SERIAL PRIMARY KEY,
    user_id     INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    role        VARCHAR(40),
    csat        INTEGER NOT NULL,
    sus         TEXT,
    sus_score   REAL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (csat BETWEEN 1 AND 5)
);

CREATE TABLE IF NOT EXISTS audit_log (
    log_id   SERIAL PRIMARY KEY,
    user_id  INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    action   VARCHAR(60) NOT NULL,
    detail   TEXT,
    at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
