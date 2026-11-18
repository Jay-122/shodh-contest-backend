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

    if engine.dialect.name == "postgresql":
        # The API, the worker and the seed can all start at the same moment
        # against a fresh database, and concurrent CREATE TABLE races at the
        # catalog level (observed on Docker Desktop: duplicate key on
        # pg_class for the id sequence). Serialize schema creation with a
        # session-level advisory lock so exactly one process creates the
        # tables and the rest find them already present.
        with engine.begin() as conn:
            conn.exec_driver_sql("SELECT pg_advisory_lock(727272)")
            try:
                Base.metadata.create_all(bind=conn)
            finally:
                conn.exec_driver_sql("SELECT pg_advisory_unlock(727272)")
    else:
        Base.metadata.create_all(bind=engine)
