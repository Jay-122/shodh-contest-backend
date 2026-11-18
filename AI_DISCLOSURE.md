# AI assistance disclosure

This submission was built with material assistance from an AI coding assistant
(Claude, via Muse), as the brief explicitly permits.

**What the AI did:** drafted the project scaffold, data model implementation,
API endpoints, judge worker, seed data, verification/workload scripts and the
first drafts of the README and report, working from a stage-by-stage plan.

**What the candidate did:** chose the stack and architecture direction
(FastAPI + PostgreSQL, DB-backed polling worker instead of a queue product,
versioned problems, attempts model), reviewed the data model before code was
written, and set the scoring and tie-break rules.

**How it was verified (not taken on trust):** nothing was accepted because the
AI said it worked. The system was executed and checked end-to-end:
`scripts/verify.py` runs 34 functional checks from a cold start (all passing,
output in `VERIFICATION.md`), a two-worker run proved no attempt is judged
twice, a workload run measured latency and throughput, and a query-plan
before/after proved the one performance fix. The candidate then ran the full
Docker stack on his own machine (Docker Desktop): the smoke test caught two
real first-boot bugs (a bind-mount path-translation failure and a concurrent
schema-creation race), which were diagnosed from the logs, fixed, and re-run
until the complete suite passed **34/34 in Docker mode** on PostgreSQL —
recorded in `VERIFICATION.md`.
