"""Dev-auth fixture + authorization.

Identities are seeded users with static bearer tokens (documented in the
README). This is a *trusted fixture* as allowed by the brief — but every
authorization rule (org membership, role, ownership) is genuinely enforced
on top of it.
"""

from fastapi import Depends, Header
from sqlalchemy.orm import Session

from . import models
from .db import get_db
from .errors import ApiError, forbidden, not_found


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> models.User:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()
    user = db.query(models.User).filter(models.User.auth_token == token).first()
    if user is None:
        raise ApiError(401, "unauthorized", "Invalid token")
    return user


def get_membership(db: Session, user_id: int, org_id: int) -> models.Membership | None:
    return (
        db.query(models.Membership)
        .filter(models.Membership.user_id == user_id, models.Membership.org_id == org_id)
        .first()
    )


def require_member(db: Session, user: models.User, org_id: int) -> models.Membership:
    membership = get_membership(db, user.id, org_id)
    if membership is None:
        raise forbidden("You are not a member of this organization")
    return membership


def require_organizer(db: Session, user: models.User, org_id: int) -> models.Membership:
    membership = require_member(db, user, org_id)
    if membership.role != "organizer":
        raise forbidden("Only organizers can perform this action")
    return membership


def get_contest_visible(db: Session, user: models.User, contest_id: int) -> models.Contest:
    """Fetch a contest the user may see. Non-members get 404, so the existence
    of another org's contest is not leaked by its ID."""
    contest = db.get(models.Contest, contest_id)
    if contest is None:
        raise not_found("Contest not found")
    if get_membership(db, user.id, contest.org_id) is None:
        raise not_found("Contest not found")
    return contest


def get_problem_visible(db: Session, user: models.User, problem_id: int) -> models.Problem:
    problem = db.get(models.Problem, problem_id)
    if problem is None:
        raise not_found("Problem not found")
    get_contest_visible(db, user, problem.contest_id)
    return problem
