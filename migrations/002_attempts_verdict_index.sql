-- Stage 4 bottleneck fix (see scripts/explain_demo.py and VERIFICATION.md):
-- verdict-filtered attempt scans ("recent failed attempts" for organizer
-- stats / rejudge review) seq-scanned the attempts table. The composite
-- index turns them into an index search on (verdict, finished_at).
-- Trade-off: one extra index maintenance per attempt write on the judge path.
CREATE INDEX IF NOT EXISTS ix_attempts_verdict_finished
    ON attempts (verdict, finished_at);
