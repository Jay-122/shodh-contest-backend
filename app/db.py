from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import DATABASE_URL

connect_args = {}
if DATABASE_URL.startswith("sqlite"):
    # Needed so the API process and the worker process can share one file DB.
    connect_args = {"check_same_thread": False, "timeout": 30}

engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create tables from the ORM models (used for SQLite/local runs and as a
    safety net in compose; the canonical Postgres DDL lives in migrations/)."""
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
