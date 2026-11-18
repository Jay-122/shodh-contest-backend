"""Shodh a Code — contest platform API (Stages 1, 3, 4 surface).

Run: uvicorn app.main:app --host 0.0.0.0 --port 8000
Interactive OpenAPI docs: /docs
"""

import hashlib
import json
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import models
from .auth import (
    get_contest_visible,
    get_current_user,
    get_membership,
    get_problem_visible,
    require_organizer,
)
from .db import get_db, init_db
from .errors import business_rule, conflict, not_found, validation
from .leaderboard import compute_leaderboard

app = FastAPI(title="Shodh a Code", version="1.0.0")


@app.on_event("startup")
def _startup():
    init_db()


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": {"type": "validation", "message": str(exc.errors()[:1])}},
    )


# ---------------------------------------------------------------- schemas

class ContestCreate(BaseModel):
    org_id: int
    title: str = Field(min_length=1, max_length=255)
    starts_at: datetime
    ends_at: datetime


class TestCaseIn(BaseModel):
    input: str
    expected_output: str
    is_hidden: bool = False


class ProblemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    statement: str = Field(min_length=1)
    time_limit_ms: int = Field(default=2000, ge=100, le=30000)
    memory_mb: int = Field(default=256, ge=16, le=4096)
    test_cases: list[TestCaseIn] = Field(min_length=1)


class ProblemUpdate(BaseModel):
    statement: str | None = None
    time_limit_ms: int | None = Field(default=None, ge=100, le=30000)
    memory_mb: int | None = Field(default=None, ge=16, le=4096)
    test_cases: list[TestCaseIn] | None = None


class SubmissionCreate(BaseModel):
    contest_id: int
    problem_id: int
    code: str = Field(min_length=1)
    language: str = "python"


class RejudgeIn(BaseModel):
    problem_version_id: int | None = None


# ---------------------------------------------------------------- helpers

def aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def contest_view(c: models.Contest) -> dict:
    return {
        "id": c.id,
        "org_id": c.org_id,
        "title": c.title,
        "starts_at": c.starts_at,
        "ends_at": c.ends_at,
        "created_by": c.created_by,
    }


def problem_view(db: Session, p: models.Problem, include_tests: bool) -> dict:
    version = (
        db.query(models.ProblemVersion)
        .filter(
            models.ProblemVersion.problem_id == p.id,
            models.ProblemVersion.version == p.current_version,
        )
        .first()
    )
    view = {
        "id": p.id,
        "contest_id": p.contest_id,
        "title": p.title,
        "current_version": version.version if version else p.current_version,
        "statement": version.statement if version else None,
        "time_limit_ms": version.time_limit_ms if version else None,
        "memory_mb": version.memory_mb if version else None,
    }
    if include_tests and version is not None:
        # Only visible (sample) test cases are ever exposed. Hidden test inputs
        # and expected outputs never leave the server in participant responses.
        view["sample_test_cases"] = [
            {"ordinal": tc.ordinal, "input": tc.input, "expected_output": tc.expected_output}
            for tc in sorted(version.test_cases, key=lambda t: t.ordinal)
            if not tc.is_hidden
        ]
        view["hidden_test_count"] = sum(1 for tc in version.test_cases if tc.is_hidden)
    return view


def attempt_view(attempt: models.Attempt, organizer_view: bool) -> dict:
    detail = []
    tests_passed = 0
    tests_total = 0
    for entry in attempt.detail or []:
        tests_total += 1
        if entry.get("verdict") == "ACCEPTED":
            tests_passed += 1
        if entry.get("hidden") and not organizer_view:
            # Participants learn THAT a hidden test exists, never its data or
            # its individual verdict.
            detail.append({"ordinal": entry["ordinal"], "hidden": True})
        else:
            detail.append(
                {
                    "ordinal": entry["ordinal"],
                    "hidden": bool(entry.get("hidden")),
                    "verdict": entry.get("verdict"),
                    "time_ms": entry.get("time_ms"),
                }
            )
    return {
        "id": attempt.id,
        "attempt_no": attempt.attempt_no,
        "problem_version_id": attempt.problem_version_id,
        "status": attempt.status,
        "verdict": attempt.verdict,
        "score": attempt.score,
        "execution_mode": attempt.execution_mode,
        "tests_passed": tests_passed,
        "tests_total": tests_total,
        "test_results": detail,
        "queued_at": attempt.queued_at,
        "started_at": attempt.started_at,
        "finished_at": attempt.finished_at,
    }


def submission_view(db: Session, s: models.Submission, organizer_view: bool) -> dict:
    attempts = sorted(s.attempts, key=lambda a: a.attempt_no)
    return {
        "id": s.id,
        "contest_id": s.contest_id,
        "problem_id": s.problem_id,
        "problem_version_id": s.problem_version_id,
        "user_id": s.user_id,
        "language": s.language,
        "code": s.code,
        "status": s.status,
        "authoritative_attempt_id": s.authoritative_attempt_id,
        "attempts": [attempt_view(a, organizer_view) for a in attempts],
        "created_at": s.created_at,
    }


def paginate(query, limit: int, offset: int):
    total = query.count()
    items = query.offset(offset).limit(limit).all()
    return {"items": items, "total": total, "limit": limit, "offset": offset}


# ---------------------------------------------------------------- routes

@app.get("/health")
def health(db: Session = Depends(get_db)):
    from sqlalchemy import text

    db.execute(text("SELECT 1"))
    return {"status": "ok"}


@app.get("/me")
def me(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    memberships = (
        db.query(models.Membership).filter(models.Membership.user_id == user.id).all()
    )
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "memberships": [
            {"org_id": m.org_id, "role": m.role} for m in memberships
        ],
    }


@app.post("/contests", status_code=201)
def create_contest(
    body: ContestCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    require_organizer(db, user, body.org_id)
    if aware(body.ends_at) <= aware(body.starts_at):
        raise validation("ends_at must be after starts_at")
    contest = models.Contest(
        org_id=body.org_id,
        title=body.title,
        starts_at=aware(body.starts_at),
        ends_at=aware(body.ends_at),
        created_by=user.id,
    )
    db.add(contest)
    db.commit()
    db.refresh(contest)
    return contest_view(contest)


@app.get("/contests")
def list_contests(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    org_ids = [
        m.org_id
        for m in db.query(models.Membership)
        .filter(models.Membership.user_id == user.id)
        .all()
    ]
    query = (
        db.query(models.Contest)
        .filter(models.Contest.org_id.in_(org_ids))
        .order_by(models.Contest.id.asc())
    )
    page = paginate(query, limit, offset)
    page["items"] = [contest_view(c) for c in page["items"]]
    return page


@app.get("/contests/{contest_id}")
def get_contest(
    contest_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return contest_view(get_contest_visible(db, user, contest_id))


@app.post("/contests/{contest_id}/join")
def join_contest(
    contest_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    contest = get_contest_visible(db, user, contest_id)
    existing = (
        db.query(models.Participation)
        .filter(
            models.Participation.contest_id == contest.id,
            models.Participation.user_id == user.id,
        )
        .first()
    )
    if existing is not None:
        return {"contest_id": contest.id, "user_id": user.id, "joined_at": existing.joined_at}
    participation = models.Participation(contest_id=contest.id, user_id=user.id)
    db.add(participation)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()  # concurrent join — treat as already joined
    return {"contest_id": contest.id, "user_id": user.id, "joined_at": participation.joined_at}


@app.post("/contests/{contest_id}/problems", status_code=201)
def create_problem(
    contest_id: int,
    body: ProblemCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    contest = get_contest_visible(db, user, contest_id)
    require_organizer(db, user, contest.org_id)
    problem = models.Problem(contest_id=contest.id, title=body.title, current_version=1)
    db.add(problem)
    db.flush()
    version = models.ProblemVersion(
        problem_id=problem.id,
        version=1,
        statement=body.statement,
        time_limit_ms=body.time_limit_ms,
        memory_mb=body.memory_mb,
    )
    db.add(version)
    db.flush()
    for i, tc in enumerate(body.test_cases):
        db.add(
            models.TestCase(
                problem_version_id=version.id,
                ordinal=i,
                input=tc.input,
                expected_output=tc.expected_output,
                is_hidden=tc.is_hidden,
            )
        )
    db.commit()
    db.refresh(problem)
    return problem_view(db, problem, include_tests=True)


@app.get("/contests/{contest_id}/problems")
def list_problems(
    contest_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    contest = get_contest_visible(db, user, contest_id)
    query = (
        db.query(models.Problem)
        .filter(models.Problem.contest_id == contest.id)
        .order_by(models.Problem.id.asc())
    )
    page = paginate(query, limit, offset)
    page["items"] = [problem_view(db, p, include_tests=False) for p in page["items"]]
    return page


@app.get("/problems/{problem_id}")
def get_problem(
    problem_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    problem = get_problem_visible(db, user, problem_id)
    return problem_view(db, problem, include_tests=True)


@app.put("/problems/{problem_id}")
def update_problem(
    problem_id: int,
    body: ProblemUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Editing a problem creates a NEW version; old versions (and the verdicts
    pinned to them) are never mutated."""
    problem = get_problem_visible(db, user, problem_id)
    contest = db.get(models.Contest, problem.contest_id)
    require_organizer(db, user, contest.org_id)
    current = (
        db.query(models.ProblemVersion)
        .filter(
            models.ProblemVersion.problem_id == problem.id,
            models.ProblemVersion.version == problem.current_version,
        )
        .first()
    )
    new_version = models.ProblemVersion(
        problem_id=problem.id,
        version=problem.current_version + 1,
        statement=body.statement if body.statement is not None else current.statement,
        time_limit_ms=body.time_limit_ms if body.time_limit_ms is not None else current.time_limit_ms,
        memory_mb=body.memory_mb if body.memory_mb is not None else current.memory_mb,
    )
    db.add(new_version)
    db.flush()
    if body.test_cases is not None:
        cases = [
            (tc.input, tc.expected_output, tc.is_hidden) for tc in body.test_cases
        ]
    else:
        cases = [
            (tc.input, tc.expected_output, tc.is_hidden)
            for tc in sorted(current.test_cases, key=lambda t: t.ordinal)
        ]
    for i, (inp, out, hidden) in enumerate(cases):
        db.add(
            models.TestCase(
                problem_version_id=new_version.id,
                ordinal=i,
                input=inp,
                expected_output=out,
                is_hidden=hidden,
            )
        )
    problem.current_version = new_version.version
    db.commit()
    db.refresh(problem)
    return problem_view(db, problem, include_tests=True)


@app.get("/problems/{problem_id}/versions")
def list_problem_versions(
    problem_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    problem = get_problem_visible(db, user, problem_id)
    contest = db.get(models.Contest, problem.contest_id)
    require_organizer(db, user, contest.org_id)
    versions = (
        db.query(models.ProblemVersion)
        .filter(models.ProblemVersion.problem_id == problem.id)
        .order_by(models.ProblemVersion.version.asc())
        .all()
    )
    return [
        {
            "id": v.id,
            "version": v.version,
            "time_limit_ms": v.time_limit_ms,
            "memory_mb": v.memory_mb,
            "test_count": len(v.test_cases),
            "hidden_test_count": sum(1 for tc in v.test_cases if tc.is_hidden),
            "created_at": v.created_at,
        }
        for v in versions
    ]


def _request_hash(body: SubmissionCreate) -> str:
    canonical = json.dumps(
        {
            "contest_id": body.contest_id,
            "problem_id": body.problem_id,
            "code": body.code,
            "language": body.language,
        },
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


@app.post("/submissions", status_code=201)
def create_submission(
    body: SubmissionCreate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    if body.language != "python":
        raise validation(f"Unsupported language: {body.language} (only 'python')")

    request_hash = _request_hash(body)
    if idempotency_key:
        existing = (
            db.query(models.Submission)
            .filter(
                models.Submission.user_id == user.id,
                models.Submission.idempotency_key == idempotency_key,
            )
            .first()
        )
        if existing is not None:
            if existing.request_hash != request_hash:
                raise conflict(
                    "Idempotency-Key was already used with a different request payload"
                )
            return submission_view(db, existing, organizer_view=False)

    contest = get_contest_visible(db, user, body.contest_id)
    problem = db.get(models.Problem, body.problem_id)
    if problem is None or problem.contest_id != contest.id:
        raise validation("problem_id does not belong to this contest")

    joined = (
        db.query(models.Participation)
        .filter(
            models.Participation.contest_id == contest.id,
            models.Participation.user_id == user.id,
        )
        .first()
    )
    if joined is None:
        raise business_rule("You must join the contest before submitting")

    now = datetime.now(timezone.utc)
    if now < aware(contest.starts_at):
        raise business_rule("Contest has not started yet")
    if now > aware(contest.ends_at):
        raise business_rule("Contest has ended")

    version = (
        db.query(models.ProblemVersion)
        .filter(
            models.ProblemVersion.problem_id == problem.id,
            models.ProblemVersion.version == problem.current_version,
        )
        .first()
    )
    submission = models.Submission(
        contest_id=contest.id,
        problem_id=problem.id,
        problem_version_id=version.id,
        user_id=user.id,
        code=body.code,
        language=body.language,
        status="queued",
        idempotency_key=idempotency_key,
        request_hash=request_hash,
    )
    db.add(submission)
    db.flush()
    db.add(
        models.Attempt(
            submission_id=submission.id,
            attempt_no=1,
            problem_version_id=version.id,
            status="queued",
        )
    )
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request with the same Idempotency-Key won the race.
        db.rollback()
        if idempotency_key:
            existing = (
                db.query(models.Submission)
                .filter(
                    models.Submission.user_id == user.id,
                    models.Submission.idempotency_key == idempotency_key,
                )
                .first()
            )
            if existing is not None and existing.request_hash == request_hash:
                return submission_view(db, existing, organizer_view=False)
            if existing is not None:
                raise conflict(
                    "Idempotency-Key was already used with a different request payload"
                )
        raise
    db.refresh(submission)
    # The ID is returned immediately; judging happens asynchronously in the worker.
    return submission_view(db, submission, organizer_view=False)


def _get_submission_visible(
    db: Session, user: models.User, submission_id: int
) -> tuple[models.Submission, bool]:
    """Owner or an organizer of the owning org may read a submission. Everyone
    else — including other participants and other orgs — gets 404."""
    submission = db.get(models.Submission, submission_id)
    if submission is None:
        raise not_found("Submission not found")
    if submission.user_id == user.id:
        return submission, False
    contest = db.get(models.Contest, submission.contest_id)
    membership = get_membership(db, user.id, contest.org_id)
    if membership is not None and membership.role == "organizer":
        return submission, True
    raise not_found("Submission not found")


@app.get("/submissions/{submission_id}")
def get_submission(
    submission_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    submission, organizer_view = _get_submission_visible(db, user, submission_id)
    return submission_view(db, submission, organizer_view)


@app.get("/submissions")
def list_submissions(
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
    contest_id: int | None = None,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
):
    query = db.query(models.Submission).filter(models.Submission.user_id == user.id)
    if contest_id is not None:
        get_contest_visible(db, user, contest_id)
        query = query.filter(models.Submission.contest_id == contest_id)
    query = query.order_by(models.Submission.id.asc())
    page = paginate(query, limit, offset)
    page["items"] = [submission_view(db, s, organizer_view=False) for s in page["items"]]
    return page


@app.post("/submissions/{submission_id}/rejudge")
def rejudge_submission(
    submission_id: int,
    body: RejudgeIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Organizer-only. Creates a NEW attempt judged against an explicitly
    selected problem version (default: the submission's pinned version).
    Earlier attempts are preserved; only the latest completed attempt is
    authoritative for standings."""
    submission = db.get(models.Submission, submission_id)
    if submission is None:
        raise not_found("Submission not found")
    contest = db.get(models.Contest, submission.contest_id)
    if get_membership(db, user.id, contest.org_id) is None:
        raise not_found("Submission not found")
    require_organizer(db, user, contest.org_id)

    version_id = body.problem_version_id or submission.problem_version_id
    version = db.get(models.ProblemVersion, version_id)
    if version is None or version.problem_id != submission.problem_id:
        raise validation("problem_version_id does not belong to this submission's problem")

    next_no = max((a.attempt_no for a in submission.attempts), default=0) + 1
    db.add(
        models.Attempt(
            submission_id=submission.id,
            attempt_no=next_no,
            problem_version_id=version.id,
            status="queued",
        )
    )
    submission.status = "queued"
    db.commit()
    db.refresh(submission)
    return submission_view(db, submission, organizer_view=True)


@app.get("/contests/{contest_id}/leaderboard")
def get_leaderboard(
    contest_id: int,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    contest = get_contest_visible(db, user, contest_id)
    rows = compute_leaderboard(db, contest.id)
    return {"contest_id": contest.id, "standings": rows}
