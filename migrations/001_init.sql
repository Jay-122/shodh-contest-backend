-- Shodh a Code — initial schema (PostgreSQL).
-- Mirrors app/models.py. The API and worker also create tables from the ORM
-- models on startup if they are missing, so `docker compose up --build`
-- works without applying this file manually; it is the canonical DDL for
-- review and for managed deployments.

CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    email VARCHAR(255) NOT NULL UNIQUE,
    name VARCHAR(255) NOT NULL,
    auth_token VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_users_email ON users (email);
CREATE INDEX IF NOT EXISTS ix_users_auth_token ON users (auth_token);

CREATE TABLE IF NOT EXISTS organizations (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE IF NOT EXISTS memberships (
    id SERIAL PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users (id),
    org_id INTEGER NOT NULL REFERENCES organizations (id),
    role VARCHAR(32) NOT NULL,
    CONSTRAINT uq_membership_user_org UNIQUE (user_id, org_id)
);
CREATE INDEX IF NOT EXISTS ix_memberships_user_id ON memberships (user_id);
CREATE INDEX IF NOT EXISTS ix_memberships_org_id ON memberships (org_id);

CREATE TABLE IF NOT EXISTS contests (
    id SERIAL PRIMARY KEY,
    org_id INTEGER NOT NULL REFERENCES organizations (id),
    title VARCHAR(255) NOT NULL,
    starts_at TIMESTAMPTZ NOT NULL,
    ends_at TIMESTAMPTZ NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users (id),
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_contests_org_id ON contests (org_id);

CREATE TABLE IF NOT EXISTS participations (
    id SERIAL PRIMARY KEY,
    contest_id INTEGER NOT NULL REFERENCES contests (id),
    user_id INTEGER NOT NULL REFERENCES users (id),
    joined_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_participation_contest_user UNIQUE (contest_id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_participations_contest_id ON participations (contest_id);
CREATE INDEX IF NOT EXISTS ix_participations_user_id ON participations (user_id);

CREATE TABLE IF NOT EXISTS problems (
    id SERIAL PRIMARY KEY,
    contest_id INTEGER NOT NULL REFERENCES contests (id),
    title VARCHAR(255) NOT NULL,
    current_version INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_problems_contest_id ON problems (contest_id);

CREATE TABLE IF NOT EXISTS problem_versions (
    id SERIAL PRIMARY KEY,
    problem_id INTEGER NOT NULL REFERENCES problems (id),
    version INTEGER NOT NULL,
    statement TEXT NOT NULL,
    time_limit_ms INTEGER NOT NULL DEFAULT 2000,
    memory_mb INTEGER NOT NULL DEFAULT 256,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_problem_version UNIQUE (problem_id, version)
);
CREATE INDEX IF NOT EXISTS ix_problem_versions_problem_id ON problem_versions (problem_id);

CREATE TABLE IF NOT EXISTS test_cases (
    id SERIAL PRIMARY KEY,
    problem_version_id INTEGER NOT NULL REFERENCES problem_versions (id),
    ordinal INTEGER NOT NULL,
    input TEXT NOT NULL,
    expected_output TEXT NOT NULL,
    is_hidden BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS ix_test_cases_problem_version_id ON test_cases (problem_version_id);

CREATE TABLE IF NOT EXISTS submissions (
    id SERIAL PRIMARY KEY,
    contest_id INTEGER NOT NULL REFERENCES contests (id),
    problem_id INTEGER NOT NULL REFERENCES problems (id),
    problem_version_id INTEGER NOT NULL REFERENCES problem_versions (id),
    user_id INTEGER NOT NULL REFERENCES users (id),
    code TEXT NOT NULL,
    language VARCHAR(32) NOT NULL DEFAULT 'python',
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    idempotency_key VARCHAR(255),
    request_hash VARCHAR(64),
    authoritative_attempt_id INTEGER,
    created_at TIMESTAMPTZ DEFAULT now(),
    CONSTRAINT uq_submission_user_idem UNIQUE (user_id, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_submissions_contest_id ON submissions (contest_id);
CREATE INDEX IF NOT EXISTS ix_submissions_problem_id ON submissions (problem_id);
CREATE INDEX IF NOT EXISTS ix_submissions_user_id ON submissions (user_id);
CREATE INDEX IF NOT EXISTS ix_submissions_status ON submissions (status);

CREATE TABLE IF NOT EXISTS attempts (
    id SERIAL PRIMARY KEY,
    submission_id INTEGER NOT NULL REFERENCES submissions (id),
    attempt_no INTEGER NOT NULL,
    problem_version_id INTEGER NOT NULL REFERENCES problem_versions (id),
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    verdict VARCHAR(32),
    score INTEGER NOT NULL DEFAULT 0,
    execution_mode VARCHAR(16),
    worker_id VARCHAR(64),
    retries INTEGER NOT NULL DEFAULT 0,
    detail JSONB,
    error TEXT,
    queued_at TIMESTAMPTZ DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CONSTRAINT uq_attempt_submission_no UNIQUE (submission_id, attempt_no)
);
CREATE INDEX IF NOT EXISTS ix_attempts_submission_id ON attempts (submission_id);
CREATE INDEX IF NOT EXISTS ix_attempts_status ON attempts (status);

-- Circular reference, added after both tables exist.
ALTER TABLE submissions
    ADD CONSTRAINT fk_submissions_authoritative_attempt
    FOREIGN KEY (authoritative_attempt_id) REFERENCES attempts (id);
