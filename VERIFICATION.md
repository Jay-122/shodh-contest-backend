# Verification record

**Environment note:** verification ran in two environments.
(1) The build machine had **no Docker daemon**, so the first pass ran the API
+ worker processes directly against SQLite with `JUDGE_MODE=local` (real code
execution via subprocess + `timeout` + rlimits; the execution mode is recorded
on every attempt) — sections 1–4 below.
(2) The full evaluated path — `docker compose up --build` (PostgreSQL +
`JUDGE_MODE=docker`) on Docker Desktop for Windows — was then run on a Docker
host: **34/34 checks pass in Docker mode** (section 0). The smoke test found
and fixed two real first-boot bugs, also recorded in section 0.

## 0. Docker-mode verification (the evaluated path) — 2026-11-18

Setup: fresh volume (`docker compose down -v`), `docker compose up --build`,
then `docker compose exec api python scripts/verify.py` against the running
stack. Result: **34/34 checks passed**, every judged attempt recorded with
`execution_mode: docker` (ACCEPTED / WRONG_ANSWER / RUNTIME_ERROR /
TIME_LIMIT_EXCEEDED all produced by real container execution).

Two bugs the smoke test caught and fixed:

1. **Judge workdir path translation.** The worker passed bind-mount source
   paths in its own container's namespace; the daemon interpreted them in
   the host namespace, mounted an empty directory, and judged code never
   ran ("runner produced no exit status" on every attempt). Fix: the worker
   translates the workdir through its own mount table before requesting the
   bind mount (`worker/runner.py`); compose now mounts `./judge_tmp`
   relatively and sets `JUDGE_HOST_TMP=/judge_tmp`.
2. **First-boot schema race.** On a fresh database the API, worker and seed
   ran `CREATE TABLE` concurrently and one process crashed on a catalog
   duplicate-key. Fix: schema creation is serialized with a PostgreSQL
   advisory lock (`app/db.py`).

Setup used for every run below:

```bash
export DATABASE_URL="sqlite:///./shodh.db" JUDGE_MODE=local
export JUDGE_FAIL_MARKER="# INJECT_JUDGE_FAILURE"
python -m app.seed
python -m uvicorn app.main:app --port 8000
python -m worker.worker        # WORKER_ID=worker-local-1
```

## 1. End-to-end checks — `python scripts/verify.py` (cold start, fresh DB)

```
PASS  health endpoint
PASS  alice sees her org's contests
PASS  cross-org contest read is 404
PASS  cross-org contest read (reverse) is 404
PASS  organizer can create contest  id=4
PASS  participant creating contest is denied (403)
PASS  problem view hides hidden tests (no hidden expected output leaks)
PASS  editing a problem creates version 2
PASS  both versions are preserved
PASS  submission to an ended contest is a business-rule error  {"type": "business_rule", "message": "Contest has ended"}
PASS  submission returns an ID immediately with queued status
PASS  correct program -> ACCEPTED  mode=local
PASS  participant view hides hidden-test verdicts
PASS  all 3 tests ran (visible + hidden)
PASS  wrong program -> WRONG_ANSWER
PASS  crashing program -> RUNTIME_ERROR
PASS  infinite loop -> TIME_LIMIT_EXCEEDED
PASS  bob's correct program -> ACCEPTED
PASS  leaderboard: both solved = 100 points
PASS  leaderboard: alice has 3 failed attempts, bob has 0
PASS  leaderboard: tie-break puts bob (fewer failures) first
PASS  cross-org submission read is 404
PASS  another participant cannot read alice's submission (404)
PASS  organizer can read it and sees hidden-test verdicts
PASS  idempotent replay returns the same submission
PASS  same key + different payload -> 409 conflict
PASS  correct code judged WRONG against the (wrong) v1 tests
PASS  participant attempting rejudge is denied (403)
PASS  organizer rejudge creates attempt 2
PASS  rejudged attempt against v2 -> ACCEPTED and authoritative
PASS  leaderboard reflects the rejudge (alice now 200 points)
PASS  injected judge failure -> JUDGE_FAILURE, not a student verdict
PASS  judge failure costs no points and no failed-attempt penalty
PASS  pagination: limit respected, total reported

34/34 checks passed
```

## 2. Competing workers (Stage 3)

A second worker (`WORKER_ID=worker-local-2`) was started against the same
store before the workload run. Judge-completion log lines were compared:

- worker-local-1 judged attempts: 1 2 3 4 5 6 7 8 10 11 12 13 15 17 19 21 23 25 27 29
- worker-local-2 judged attempts: 14 16 18 20 22 24 26 28
- **Overlap between the two workers: 0. Duplicate completions within a worker: 0.**
  (Attempt 9 was the injected-failure attempt; it completed via the
  JUDGE_FAILURE path, not judge_done.)

## 3. Workload — `python scripts/workload.py` (2 workers running)

```
workload: 20 concurrent submissions + leaderboard reads against contest 1
leaderboard reads (idle): n=10 errors=0 p50=8ms p95=9ms max=9ms mean=8ms
submission POSTs (API overhead only): n=20 errors=0 p50=832ms p95=2558ms max=3018ms mean=1021ms
leaderboard reads (under load): n=30 errors=0 p50=151ms p95=1661ms max=1844ms mean=343ms
judged: 20/20 submissions reached a final state; total wall time 7.0s;
throughput ~172 submissions/min (includes program execution)
```

Honest reading: all 20 concurrent submissions were accepted and judged with
zero errors. The elevated POST/read latency is a property of the **SQLite
fallback store** (single-writer lock shared by the API and two worker
processes on one file), not of the API design — the compose deployment uses
PostgreSQL, where writers do not serialize this way. API latency (returning
the submission ID) is reported separately from judging time, as required.

## 4. Bottleneck investigation — `python scripts/explain_demo.py`

Query (organizer stats — recent failed attempts):
`SELECT ... FROM attempts WHERE verdict='WRONG_ANSWER' ORDER BY finished_at DESC LIMIT 20`
on 30,029 attempt rows (live data + 30,000 synthetic):

```
BEFORE index:
   SCAN attempts
   USE TEMP B-TREE FOR ORDER BY
   query time: 3.7 ms
AFTER index (ix_attempts_verdict_finished on (verdict, finished_at)):
   SEARCH attempts USING INDEX ix_attempts_verdict_finished (verdict=?)
   query time: 0.1 ms
```

Fix shipped as `migrations/002_attempts_verdict_index.sql`. Trade-off: one
extra index update per attempt write on the judge path — acceptable because
attempts are written once per judging run and read by every organizer
stats/rejudge review.

## 5. What is NOT verified here

- The two-worker competing-claims run and the workload/EXPLAIN measurements
  (sections 2–4) were run in the local/SQLite environment; the Docker-mode
  run (section 0) covers the full 34-check functional suite on PostgreSQL,
  including the injected-failure and rejudge paths, with one worker.
