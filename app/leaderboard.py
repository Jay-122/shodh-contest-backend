"""Standings are a pure function of *authoritative* attempts.

Rules (documented in the README):
- Each submission's authoritative attempt is its latest COMPLETED attempt.
- A problem is solved for a participant if any of their submissions to it has
  an authoritative ACCEPTED attempt. Solved problem = 100 points.
- Failed attempts = authoritative attempts with a student-error verdict
  (WRONG_ANSWER / RUNTIME_ERROR / TIME_LIMIT_EXCEEDED). JUDGE_FAILURE is an
  infrastructure outcome and never counts against a participant.
- Ordering: points desc, failed attempts asc, earliest last-accepted time,
  then user_id asc (stable, deterministic).
"""

from sqlalchemy.orm import Session

from . import models

STUDENT_ERROR_VERDICTS = {"WRONG_ANSWER", "RUNTIME_ERROR", "TIME_LIMIT_EXCEEDED"}
POINTS_PER_PROBLEM = 100


def compute_leaderboard(db: Session, contest_id: int) -> list[dict]:
    participations = (
        db.query(models.Participation)
        .filter(models.Participation.contest_id == contest_id)
        .order_by(models.Participation.user_id.asc())
        .all()
    )

    rows = []
    for participation in participations:
        user = db.get(models.User, participation.user_id)
        submissions = (
            db.query(models.Submission)
            .filter(
                models.Submission.contest_id == contest_id,
                models.Submission.user_id == participation.user_id,
                models.Submission.authoritative_attempt_id.isnot(None),
            )
            .all()
        )
        solved_problems: set[int] = set()
        failed_attempts = 0
        last_accepted_at = None
        for submission in submissions:
            attempt = db.get(models.Attempt, submission.authoritative_attempt_id)
            if attempt is None or attempt.status != "completed":
                continue
            if attempt.verdict == "ACCEPTED":
                solved_problems.add(submission.problem_id)
                if last_accepted_at is None or attempt.finished_at > last_accepted_at:
                    last_accepted_at = attempt.finished_at
            elif attempt.verdict in STUDENT_ERROR_VERDICTS:
                failed_attempts += 1
        rows.append(
            {
                "user_id": participation.user_id,
                "user_name": user.name if user else None,
                "points": POINTS_PER_PROBLEM * len(solved_problems),
                "problems_solved": len(solved_problems),
                "failed_attempts": failed_attempts,
                "last_accepted_at": last_accepted_at,
            }
        )

    rows.sort(
        key=lambda r: (
            -r["points"],
            r["failed_attempts"],
            r["last_accepted_at"] is None,
            r["last_accepted_at"],
            r["user_id"],
        )
    )
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows
