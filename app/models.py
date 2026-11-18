"""Relational schema for the Shodh contest platform.

Design notes (Stage 1):
- Problems are never edited in place: every edit creates a new row in
  problem_versions (with its own test_cases). A submission pins the exact
  problem_version it was made against, so later edits never change old verdicts.
- A submission has many attempts. Judging writes attempts, never the submission
  directly. The submission points at its *authoritative* attempt: the latest
  COMPLETED attempt. Standings are computed only from authoritative attempts,
  so a late-finishing replaced attempt cannot change the leaderboard.
- Verdicts: ACCEPTED, WRONG_ANSWER, RUNTIME_ERROR, TIME_LIMIT_EXCEEDED,
  JUDGE_FAILURE. JUDGE_FAILURE is an infrastructure outcome, never a student
  error, and is excluded from scoring/penalties.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Dev-auth fixture only: a static bearer token per seeded identity.
    # Production would replace this with real password/OIDC auth.
    auth_token: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("user_id", "org_id", name="uq_membership_user_org"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)  # organizer | participant


class Contest(Base):
    __tablename__ = "contests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Participation(Base):
    __tablename__ = "participations"
    __table_args__ = (
        UniqueConstraint("contest_id", "user_id", name="uq_participation_contest_user"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contest_id: Mapped[int] = mapped_column(ForeignKey("contests.id"), nullable=False, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Problem(Base):
    __tablename__ = "problems"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contest_id: Mapped[int] = mapped_column(ForeignKey("contests.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    versions: Mapped[list["ProblemVersion"]] = relationship(
        back_populates="problem", cascade="all, delete-orphan"
    )


class ProblemVersion(Base):
    __tablename__ = "problem_versions"
    __table_args__ = (
        UniqueConstraint("problem_id", "version", name="uq_problem_version"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    time_limit_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)
    memory_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=256)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    problem: Mapped[Problem] = relationship(back_populates="versions")
    test_cases: Mapped[list["TestCase"]] = relationship(
        back_populates="problem_version", cascade="all, delete-orphan"
    )


class TestCase(Base):
    __tablename__ = "test_cases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    problem_version_id: Mapped[int] = mapped_column(
        ForeignKey("problem_versions.id"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    input: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[str] = mapped_column(Text, nullable=False)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    problem_version: Mapped[ProblemVersion] = relationship(back_populates="test_cases")


class Submission(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        # Idempotency: one submission per (user, key). NULL keys are exempt
        # (Postgres and SQLite both treat NULLs as distinct in unique indexes).
        UniqueConstraint("user_id", "idempotency_key", name="uq_submission_user_idem"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    contest_id: Mapped[int] = mapped_column(ForeignKey("contests.id"), nullable=False, index=True)
    problem_id: Mapped[int] = mapped_column(ForeignKey("problems.id"), nullable=False, index=True)
    # Pinned at creation: the exact problem version this code was submitted against.
    problem_version_id: Mapped[int] = mapped_column(
        ForeignKey("problem_versions.id"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    code: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False, default="python")
    # queued | running | completed | failed  (mirrors the latest attempt)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # SHA-256 of the canonical request payload, for idempotency conflict checks.
    request_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Latest COMPLETED attempt; standings derive from this attempt only.
    authoritative_attempt_id: Mapped[int | None] = mapped_column(
        ForeignKey("attempts.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    attempts: Mapped[list["Attempt"]] = relationship(
        back_populates="submission",
        cascade="all, delete-orphan",
        foreign_keys="Attempt.submission_id",
    )


class Attempt(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        UniqueConstraint("submission_id", "attempt_no", name="uq_attempt_submission_no"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(
        ForeignKey("submissions.id"), nullable=False, index=True
    )
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    # The problem version this attempt is judged against (may differ from the
    # submission's pinned version after an organizer rejudge to a chosen version).
    problem_version_id: Mapped[int] = mapped_column(
        ForeignKey("problem_versions.id"), nullable=False
    )
    # queued | running | completed | failed
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="queued", index=True)
    # ACCEPTED | WRONG_ANSWER | RUNTIME_ERROR | TIME_LIMIT_EXCEEDED | JUDGE_FAILURE
    verdict: Mapped[str | None] = mapped_column(String(32), nullable=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # docker | local — how the code was actually executed for this attempt.
    execution_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Internal per-test detail: [{ordinal, hidden, verdict, time_ms}] — never
    # contains test inputs or expected outputs.
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    submission: Mapped[Submission] = relationship(
        back_populates="attempts", foreign_keys=[submission_id]
    )
