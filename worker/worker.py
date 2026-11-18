"""Async judge worker (Stage 2 + 3).

Polls the database for queued attempts (DB-backed queue — no Redis, no
in-process-only locks). Claims work with a conditional UPDATE so multiple
worker processes can run against the same store without double-judging.
Recovers attempts abandoned by crashed workers (stuck in 'running'), with
bounded retries; a judge infrastructure failure is recorded as JUDGE_FAILURE
and never scored as a student error.

Run: python -m worker.worker
"""

import json
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app import models
from app.config import (
    JUDGE_MODE,
    MAX_JUDGE_RETRIES,
    POLL_INTERVAL_SECONDS,
    WORKER_ID,
)
from app.db import SessionLocal, init_db
from worker.runner import RunnerInfraError, judge_submission

STALE_RUNNING_SECONDS = 120


def log(event: str, **fields):
    print(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}), flush=True)


def refresh_submission_state(db, submission: models.Submission) -> None:
    """Submission status mirrors its latest attempt; the authoritative attempt
    (the one standings use) is the latest COMPLETED attempt. A late result
    from a replaced attempt therefore cannot move the leaderboard."""
    attempts = sorted(submission.attempts, key=lambda a: a.attempt_no)
    if not attempts:
        return
    latest = attempts[-1]
    submission.status = latest.status
    completed = [a for a in attempts if a.status == "completed"]
    submission.authoritative_attempt_id = completed[-1].id if completed else None


def claim_next_attempt(db) -> models.Attempt | None:
    candidate = (
        db.query(models.Attempt)
        .filter(models.Attempt.status == "queued")
        .order_by(models.Attempt.id.asc())
        .first()
    )
    if candidate is None:
        return None
    # Atomic claim: only one worker can flip queued -> running.
    updated = db.execute(
        update(models.Attempt)
        .where(models.Attempt.id == candidate.id, models.Attempt.status == "queued")
        .values(
            status="running",
            worker_id=WORKER_ID,
            started_at=datetime.now(timezone.utc),
        )
    ).rowcount
    db.commit()
    if updated == 0:
        return None  # another worker claimed it first
    db.expire_all()
    return db.get(models.Attempt, candidate.id)


def recover_stale_attempts(db) -> None:
    """Attempts stuck in 'running' past the stale window belong to a worker
    that died mid-judge. Requeue them (bounded by the retry budget)."""
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=STALE_RUNNING_SECONDS)
    stale = (
        db.query(models.Attempt)
        .filter(
            models.Attempt.status == "running",
            models.Attempt.started_at.isnot(None),
            models.Attempt.started_at < cutoff,
        )
        .all()
    )
    for attempt in stale:
        submission = db.get(models.Submission, attempt.submission_id)
        if attempt.retries >= MAX_JUDGE_RETRIES:
            attempt.status = "failed"
            attempt.verdict = "JUDGE_FAILURE"
            attempt.error = "Worker abandoned attempt and retry budget was exhausted"
            attempt.finished_at = datetime.now(timezone.utc)
            log("attempt_abandoned_failed", attempt_id=attempt.id, submission_id=attempt.submission_id)
        else:
            attempt.status = "queued"
            attempt.retries += 1
            attempt.worker_id = None
            attempt.started_at = None
            log("attempt_recovered", attempt_id=attempt.id, submission_id=attempt.submission_id)
        if submission is not None:
            refresh_submission_state(db, submission)
    if stale:
        db.commit()


def process_attempt(db, attempt: models.Attempt) -> None:
    submission = db.get(models.Submission, attempt.submission_id)
    version = db.get(models.ProblemVersion, attempt.problem_version_id)
    test_cases = sorted(version.test_cases, key=lambda t: t.ordinal)
    log(
        "judge_start",
        attempt_id=attempt.id,
        submission_id=submission.id,
        attempt_no=attempt.attempt_no,
        execution_mode=JUDGE_MODE,
    )
    try:
        verdict, detail = judge_submission(
            submission.code,
            test_cases,
            version.time_limit_ms,
            version.memory_mb,
            JUDGE_MODE,
        )
    except RunnerInfraError as exc:
        # Infrastructure failure: never a student error, never scored.
        attempt.error = str(exc)
        attempt.execution_mode = JUDGE_MODE
        if attempt.retries < MAX_JUDGE_RETRIES:
            attempt.retries += 1
            attempt.status = "queued"
            attempt.worker_id = None
            attempt.started_at = None
            log(
                "judge_infra_retry",
                attempt_id=attempt.id,
                submission_id=submission.id,
                retries=attempt.retries,
                error=str(exc),
            )
        else:
            attempt.status = "failed"
            attempt.verdict = "JUDGE_FAILURE"
            attempt.score = 0
            attempt.finished_at = datetime.now(timezone.utc)
            log(
                "judge_infra_failed",
                attempt_id=attempt.id,
                submission_id=submission.id,
                error=str(exc),
            )
        refresh_submission_state(db, submission)
        db.commit()
        return

    attempt.status = "completed"
    attempt.verdict = verdict
    attempt.score = 100 if verdict == "ACCEPTED" else 0
    attempt.detail = detail
    attempt.execution_mode = JUDGE_MODE
    attempt.finished_at = datetime.now(timezone.utc)
    refresh_submission_state(db, submission)
    db.commit()
    log(
        "judge_done",
        attempt_id=attempt.id,
        submission_id=submission.id,
        verdict=verdict,
        execution_mode=JUDGE_MODE,
    )


def main():
    init_db()
    log("worker_start", worker_id=WORKER_ID, execution_mode=JUDGE_MODE)
    while True:
        db = SessionLocal()
        try:
            recover_stale_attempts(db)
            attempt = claim_next_attempt(db)
            if attempt is None:
                db.close()
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            process_attempt(db, attempt)
        except Exception as exc:  # keep the worker alive; the attempt stays recoverable
            db.rollback()
            log("worker_error", error=str(exc))
            time.sleep(POLL_INTERVAL_SECONDS)
        finally:
            db.close()


if __name__ == "__main__":
    main()
