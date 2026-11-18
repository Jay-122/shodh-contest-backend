"""Stage 4 bottleneck investigation: verdict-filtered attempt scans.

An organizer stats query — "recent failed attempts" (verdict + finished_at) —
has no supporting index, so the database scans the whole attempts table.
This script copies the live DB, bulk-loads synthetic attempts, and shows
EXPLAIN QUERY PLAN + timing before and after the fix
(migrations/002_attempts_verdict_index.sql).

Usage: python scripts/explain_demo.py [db_path]  (default ./shodh.db, SQLite)
For Postgres, run the two statements in migrations/002 against the DB and
compare EXPLAIN ANALYZE output the same way.
"""

import os
import shutil
import sqlite3
import sys
import time

SRC = sys.argv[1] if len(sys.argv) > 1 else "./shodh.db"
WORK = "/tmp/explain-demo.db"
QUERY = (
    "SELECT id, submission_id, verdict, finished_at FROM attempts "
    "WHERE verdict = 'WRONG_ANSWER' ORDER BY finished_at DESC LIMIT 20"
)
INDEX_SQL = (
    "CREATE INDEX ix_attempts_verdict_finished "
    "ON attempts (verdict, finished_at)"
)
SYNTHETIC_ROWS = 30000


def timed_query(cur):
    start = time.monotonic()
    rows = cur.execute(QUERY).fetchall()
    return rows, (time.monotonic() - start) * 1000


def main():
    if os.path.exists(WORK):
        os.remove(WORK)
    shutil.copy(SRC, WORK)
    conn = sqlite3.connect(WORK)
    cur = conn.cursor()

    sub_id = cur.execute("SELECT id FROM submissions LIMIT 1").fetchone()
    version_id = cur.execute("SELECT id FROM problem_versions LIMIT 1").fetchone()
    if not sub_id or not version_id:
        print("demo needs a seeded DB with at least one submission; run verify.py first")
        return
    verdicts = ["ACCEPTED", "WRONG_ANSWER", "RUNTIME_ERROR", "TIME_LIMIT_EXCEEDED"]
    rows = [
        (
            sub_id[0],
            1000 + i,
            version_id[0],
            "completed",
            verdicts[i % len(verdicts)],
            0,
            "local",
            0,
            f"2026-11-18 10:{i % 60:02d}:00+00:00",
            f"2026-11-18 10:{i % 60:02d}:00+00:00",
        )
        for i in range(SYNTHETIC_ROWS)
    ]
    cur.executemany(
        "INSERT INTO attempts (submission_id, attempt_no, problem_version_id, status,"
        " verdict, score, execution_mode, retries, queued_at, finished_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    total = cur.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
    print(f"dataset: {total} attempt rows (live data + {SYNTHETIC_ROWS} synthetic)")

    print("\nBEFORE index:")
    for line in cur.execute("EXPLAIN QUERY PLAN " + QUERY):
        print("  ", line[-1])
    _, ms = timed_query(cur)
    print(f"   query time: {ms:.1f} ms")

    cur.execute(INDEX_SQL)
    conn.commit()

    print("\nAFTER index (ix_attempts_verdict_finished on (verdict, finished_at)):")
    for line in cur.execute("EXPLAIN QUERY PLAN " + QUERY):
        print("  ", line[-1])
    _, ms = timed_query(cur)
    print(f"   query time: {ms:.1f} ms")
    print(
        "\nTrade-off: the composite index makes verdict-filtered scans an index"
        " SEARCH instead of a full SCAN, at the cost of one extra index update"
        " per attempt insert/update on the judge's write path — acceptable,"
        " because attempts are written once per judging run and read by every"
        " organizer stats/rejudge review."
    )
    conn.close()


if __name__ == "__main__":
    main()
