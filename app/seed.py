"""Deterministic seed (idempotent): 2 organizations, 5 identities, 3 contests,
4 problems with visible + hidden test cases.

Run: python -m app.seed
"""

from datetime import datetime, timedelta, timezone

from . import models
from .db import SessionLocal, init_db

ADD_STATEMENT = (
    "Read two integers a and b separated by a space and print their sum."
)
ADD_TESTS = [
    ("2 3", "5", False),
    ("100 250", "350", True),
    ("-5 5", "0", True),
]


def _add_problem(db, contest, title, statement, tests, time_limit_ms=2000):
    problem = models.Problem(contest_id=contest.id, title=title, current_version=1)
    db.add(problem)
    db.flush()
    version = models.ProblemVersion(
        problem_id=problem.id,
        version=1,
        statement=statement,
        time_limit_ms=time_limit_ms,
        memory_mb=256,
    )
    db.add(version)
    db.flush()
    for i, (inp, out, hidden) in enumerate(tests):
        db.add(
            models.TestCase(
                problem_version_id=version.id,
                ordinal=i,
                input=inp,
                expected_output=out,
                is_hidden=hidden,
            )
        )
    return problem


def seed() -> None:
    init_db()
    db = SessionLocal()
    try:
        if db.query(models.User).first() is not None:
            print("seed: database already seeded, skipping")
            return

        now = datetime.now(timezone.utc)

        acme = models.Organization(name="Acme Org")
        beta = models.Organization(name="Beta Org")
        db.add_all([acme, beta])
        db.flush()

        users = {}
        for email, name, token in [
            ("olivia@acme.test", "Olivia Organizer", "org1-organizer-token"),
            ("alice@acme.test", "Alice Participant", "org1-alice-token"),
            ("bob@acme.test", "Bob Participant", "org1-bob-token"),
            ("oscar@beta.test", "Oscar Organizer", "org2-organizer-token"),
            ("carol@beta.test", "Carol Participant", "org2-carol-token"),
        ]:
            user = models.User(email=email, name=name, auth_token=token)
            db.add(user)
            users[token] = user
        db.flush()

        for token, org, role in [
            ("org1-organizer-token", acme, "organizer"),
            ("org1-alice-token", acme, "participant"),
            ("org1-bob-token", acme, "participant"),
            ("org2-organizer-token", beta, "organizer"),
            ("org2-carol-token", beta, "participant"),
        ]:
            db.add(models.Membership(user_id=users[token].id, org_id=org.id, role=role))
        db.flush()

        sprint = models.Contest(
            org_id=acme.id,
            title="Weekly Sprint 1",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=2),
            created_by=users["org1-organizer-token"].id,
        )
        past = models.Contest(
            org_id=acme.id,
            title="Past Cup",
            starts_at=now - timedelta(days=7),
            ends_at=now - timedelta(days=6),
            created_by=users["org1-organizer-token"].id,
        )
        beta_open = models.Contest(
            org_id=beta.id,
            title="Beta Open",
            starts_at=now - timedelta(days=1),
            ends_at=now + timedelta(days=2),
            created_by=users["org2-organizer-token"].id,
        )
        db.add_all([sprint, past, beta_open])
        db.flush()

        _add_problem(db, sprint, "Add Two Numbers", ADD_STATEMENT, ADD_TESTS)
        _add_problem(
            db,
            sprint,
            "Reverse a String",
            "Read a single line of text and print it reversed.",
            [("hello", "olleh", False), ("shodh", "hdohs", True)],
        )
        _add_problem(
            db,
            sprint,
            "Factorial",
            "Read a non-negative integer n and print n! (factorial).",
            [("5", "120", False), ("0", "1", True), ("10", "3628800", True)],
        )
        _add_problem(db, beta_open, "Add Two Numbers", ADD_STATEMENT, ADD_TESTS)

        for contest, token in [
            (sprint, "org1-alice-token"),
            (sprint, "org1-bob-token"),
            (beta_open, "org2-carol-token"),
        ]:
            db.add(
                models.Participation(
                    contest_id=contest.id, user_id=users[token].id
                )
            )
        db.commit()
        print("seed: done — 2 orgs, 5 users, 3 contests, 4 problems")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
