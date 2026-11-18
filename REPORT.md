# Shodh a Code — Backend Engineer Assessment Report

**Candidate:** Jay Kant · **Stack:** Python + FastAPI, SQLAlchemy, PostgreSQL (Docker Compose) · **Judge:** async worker, Docker-isolated execution

## 1. Architecture

Three processes share one relational store: the **API** (FastAPI), a **judge worker**
(a separate process that polls for queued attempts), and **PostgreSQL**. There is no
queue product and no in-process state: the database is the queue. Creating a submission
writes the submission and its first attempt in one transaction and returns the ID
immediately with status `queued`; the worker claims attempts with an atomic conditional
UPDATE (`... WHERE status='queued'`), so any number of API and worker processes can run
against the same store without double-judging. Clients poll the submission resource for
progress (`queued → running → completed/failed`).

**Data model:** users, organizations, memberships (user × org × role — the single source
of authorization), contests (`starts_at`/`ends_at` make open/close deterministic),
participations, problems + **problem_versions**, test_cases (with a hidden flag),
submissions, and **attempts**. A problem is never edited in place: an edit creates a new
version, and every submission pins the exact problem version and test-suite version it
was judged against, so fixing a problem can never silently rewrite an old verdict.

**Auth:** a documented dev-auth fixture — seeded identities authenticate with bearer
tokens; authorization is enforced on every read/write from memberships (role + org).
Knowing an object ID grants nothing: cross-org and non-owner reads return 404, and
organizer-only actions return 403 for participants (both demonstrated in verification).

## 2. Scoring and standings

100 points per problem whose authoritative attempt is Accepted. Tie-break: fewer failed
attempts, then earliest time of the last accepted submission. The leaderboard is computed
from **authoritative attempts only** — for each submission, its latest completed attempt —
so rebuilding standings from the stored records always equals the serving path, including
when results complete out of order.

## 3. The judge (Stage 2)

Submissions execute for real in Docker: `--network none`, CPU and memory limits,
`--pids-limit`, a non-root user, a read-only root filesystem, a wall-clock timeout, an
output-size cap, and no mounts of host data, credentials or the Docker socket; containers
and temp files are cleaned up after each run. Verdicts: Accepted, Wrong Answer,
Runtime Error, Time Limit Exceeded, and **Judge Failure** — an infrastructure failure is
recorded as its own state after bounded retries and never becomes a student verdict: it
costs no points and no failed-attempt penalty (demonstrated by injecting a runner
failure). Hidden test inputs, expected outputs and per-test verdicts are never included
in participant responses; organizers see them, participants see only aggregate results.

## 4. Retries, idempotency, rejudge (Stage 3)

Submission creation accepts an `Idempotency-Key`: the same key + same actor + equivalent
payload returns the original submission (backed by a unique constraint, so it survives
restarts and races); the same key with a different payload returns an explicit 409
conflict. Rejudging is an organizer action against an explicitly selected problem/test
version; it creates a **new attempt** and preserves all earlier ones. Only the latest
completed attempt is authoritative, so a late result from a replaced attempt cannot move
the standings. Verified live: code judged Wrong Answer against a faulty v1 test suite
was rejudged after the organizer published v2 — new attempt Accepted, leaderboard updated
(100 → 200 points). Competing workers were verified with two worker processes against
one store: 28 attempts split between them with **zero overlap and zero duplicates**.

## 5. Performance evidence (Stage 4)

Workload (`scripts/workload.py`, 2 workers): 20 concurrent submissions + 30 leaderboard
reads — **0 errors, 20/20 judged**, ~172 submissions/min including execution; API latency
(accepting a submission) is reported separately from judging time. One measured
bottleneck was investigated with a query plan (`scripts/explain_demo.py`): a
verdict-filtered attempts query over 30k rows ran a full SCAN + temp B-tree at 3.7 ms;
after adding a `(verdict, finished_at)` index it became an index SEARCH at 0.1 ms. The
fix ships as a migration; the trade-off (one extra index write per attempt) is acceptable
because attempts are written once per judging run and read on every stats/rejudge review.
No cache or extra infrastructure was added — the brief's checklist was deliberately not
chased.

## 6. Failure investigation

Injected judge failure (marker-based failpoint): the worker's runner raises mid-attempt;
the attempt is retried within its bound and then stored as `JUDGE_FAILURE` with the
submission left unscored — no points, no penalty — and the queue continues with the next
submission. Structured logs carry request/submission/attempt IDs across API and worker;
credentials and hidden test contents are never logged.

## 7. Verification

`scripts/verify.py` runs 34 end-to-end checks from a cold start (auth boundaries, versioning,
all four student verdicts, idempotency, rejudge, failure injection, pagination) — **34/34
pass**; full output is in `VERIFICATION.md`.

## 8. Known limitations (honest)

- **Docker path smoke test:** the build/verification machine had no Docker daemon, so
  functional verification ran the identical code against SQLite with the judge in a
  labelled `local` execution mode (real subprocess execution with timeout + rlimits;
  every attempt records its execution mode). The Compose stack (PostgreSQL +
  `JUDGE_MODE=docker`) is the same code and its configuration is validated, but the
  Docker judge path should get one smoke run (`python scripts/verify.py`) on a Docker
  host. Latency figures from the local run are inflated by SQLite's single-writer lock
  and are labelled as such.
- Dev-auth is a fixture, as the brief allows; production would use real identity tokens.
- One judge language (Python); the runner interface is per-language and extensible.
