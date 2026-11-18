# \# Shodh a Code — coding-contest platform backend

# 

# A contest platform backend with an asynchronous, sandboxed judge: organizers

# run versioned problems, participants submit Python code, a separate worker

# process judges submissions against visible + hidden tests, and standings are

# computed from authoritative attempts only.

# 

# Built with FastAPI + SQLAlchemy + PostgreSQL. The judge queue is the database

# itself (polling + atomic claims) — no Redis, no extra infra.

# 

# \## Run it

# 

# \### With Docker (the evaluated path)

# 

# ```bash

# docker compose up --build

# ```

# 

# Starts Postgres, the API (which seeds the demo data on boot), and the judge

# worker. API on http://localhost:8000 — interactive OpenAPI docs at `/docs`.

# Run compose from the project root so the `./judge\_tmp` bind mount resolves

# (the worker translates judge workdir paths through its own mount table, so

# this works on Linux and Docker Desktop for Windows alike).

# 

# \### Without Docker (local fallback)

# 

# ```bash

# python -m venv .venv \&\& .venv/bin/pip install -r requirements.txt

# export DATABASE\_URL="sqlite:///./shodh.db" JUDGE\_MODE=local

# .venv/bin/python -m app.seed

# .venv/bin/python -m uvicorn app.main:app --port 8000   # terminal 1

# .venv/bin/python -m worker.worker                      # terminal 2

# ```

# 

# In `local` judge mode code runs as a subprocess under `timeout` with

# rlimits. Verdicts are still produced by really executing the code, but the

# network is \*\*not\*\* isolated in this mode — use Docker mode for isolation.

# Every attempt records which `execution\_mode` produced it.

# 

# \## Demo identities (dev-auth fixture)

# 

# Auth is `Authorization: Bearer <token>`. These static tokens are a trusted

# development fixture (real password/OIDC auth is out of scope), but every

# authorization rule below is genuinely enforced on top of them.

# 

# | Token                  | User              | Org       | Role        |

# |------------------------|-------------------|-----------|-------------|

# | `org1-organizer-token` | Olivia Organizer  | Acme Org  | organizer   |

# | `org1-alice-token`     | Alice Participant | Acme Org  | participant |

# | `org1-bob-token`       | Bob Participant   | Acme Org  | participant |

# | `org2-organizer-token` | Oscar Organizer   | Beta Org  | organizer   |

# | `org2-carol-token`     | Carol Participant | Beta Org  | participant |

# 

# Seeded content: Acme's \*\*Weekly Sprint 1\*\* (open; Alice + Bob joined) with

# \*Add Two Numbers\*, \*Reverse a String\*, \*Factorial\*; Acme's \*\*Past Cup\*\*

# (ended); Beta's \*\*Beta Open\*\* (Carol joined). Sample programs for the four

# verdicts live in `seed\_programs/` (correct / wrong / runtime-error / timeout).

# 

# \## API summary

# 

# | Endpoint | Who | Notes |

# |---|---|---|

# | `GET /health` | — | liveness + DB check |

# | `GET /me` | any | identity + memberships |

# | `POST /contests` | organizer | creates a contest in their org |

# | `GET /contests` | member | only own orgs' contests; `limit`/`offset` pagination |

# | `GET /contests/{id}` | member | non-members get 404 |

# | `POST /contests/{id}/join` | member | idempotent |

# | `POST /contests/{id}/problems` | organizer | creates problem + version 1 |

# | `GET /contests/{id}/problems` | member | paginated |

# | `GET /problems/{id}` | member | current version; \*\*sample tests only\*\* |

# | `PUT /problems/{id}` | organizer | creates a \*\*new version\*\*, never edits in place |

# | `GET /problems/{id}/versions` | organizer | version history (metadata only) |

# | `POST /submissions` | participant | returns the ID immediately, status `queued`; honors `Idempotency-Key` |

# | `GET /submissions/{id}` | owner / org organizer | everyone else gets 404 |

# | `GET /submissions` | any | own submissions, paginated |

# | `POST /submissions/{id}/rejudge` | organizer | new attempt vs an explicitly chosen problem version |

# | `GET /contests/{id}/leaderboard` | member | standings from authoritative attempts |

# 

# Errors are typed so callers can react correctly:

# `{"detail": {"type": "validation" | "business\_rule" | "conflict" | "forbidden" | "not\_found" | "infra", "message": ...}}`

# with HTTP 422 / 409 / 409 / 403 / 404 / 503 respectively.

# 

# \## Rules of the system

# 

# \*\*Problem versioning.\*\* A problem's statement, limits and test suite live in

# immutable versions. Editing creates the next version. A submission pins the

# version current at submit time; a rejudge may pin a different, explicitly

# selected version. Old verdicts are never silently changed by an edit.

# 

# \*\*Submission / attempt state model.\*\* A submission is `queued → running →

# completed | failed`, mirroring its latest attempt. Each judging run is an

# attempt (`queued → running → completed | failed`) with a verdict:

# `ACCEPTED`, `WRONG\_ANSWER`, `RUNTIME\_ERROR`, `TIME\_LIMIT\_EXCEEDED`, or

# `JUDGE\_FAILURE`. `JUDGE\_FAILURE` means the \*infrastructure\* failed — it is

# retried (bounded, default 2) and never scored as a student error.

# 

# \*\*Authoritative attempts.\*\* The latest \*completed\* attempt of a submission is

# authoritative. Standings are a pure function of authoritative attempts, so a

# late-finishing replaced attempt cannot change the leaderboard, and rebuilding

# standings from stored attempts always equals the served leaderboard.

# 

# \*\*Scoring.\*\* 100 points per problem with an authoritative `ACCEPTED`.

# Failed attempts = authoritative attempts with a student-error verdict

# (`JUDGE\_FAILURE` excluded). Order: points desc → failed attempts asc →

# earliest last-accepted time → user id asc (deterministic).

# 

# \*\*Idempotency.\*\* `POST /submissions` with an `Idempotency-Key` header: the

# same key + same payload + same user returns the original submission; the same

# key with a different payload returns `409 conflict`. Enforced by a unique

# constraint, so it survives restarts and concurrent retries.

# 

# \*\*Authorization.\*\* Membership gates everything; organizers manage contests,

# problems and rejudges; participants read only their own submissions. Objects

# from another org are indistinguishable from non-existent ones (404), so

# knowing an ID grants nothing. Hidden test inputs/outputs never appear in any

# response; participants see per-test verdicts for visible tests only, plus

# aggregate pass counts.

# 

# \*\*Judge isolation (Docker mode).\*\* Each test run is a fresh container:

# `--network none`, 1 CPU, per-problem memory limit (also swap-capped),

# pids-limit 64, non-root user, read-only root filesystem, wall-clock timeout,

# output capped at 1 MiB. The only mount is the per-run workdir the judge

# created (submission code + one test input). Judged code has no Docker socket

# access; only the trusted worker process talks to the daemon. Containers and

# workdirs are removed after every run.

# 

# \*\*Failure injection (for recovery testing).\*\* If the worker env

# `JUDGE\_FAIL\_MARKER` is set, code containing that exact string makes the

# runner raise an infrastructure error, exercising the retry → `JUDGE\_FAILURE`

# path. Compose sets it to `# INJECT\_JUDGE\_FAILURE`. It is inert for any code

# that does not contain the marker.

# 

# \## Verification

# 

# ```bash

# python scripts/verify.py        # 34 end-to-end checks (needs API + worker + seed)

# python scripts/workload.py      # 20 concurrent submissions + leaderboard reads, latency report

# python scripts/explain\_demo.py  # the Stage 4 index investigation (EXPLAIN before/after)

# ```

# 

# Recorded results: see `VERIFICATION.md`.

# 

# \## Layout

# 

# ```

# app/        API: models, auth, errors, leaderboard, seed, main (routes)

# worker/     judge worker + runner (docker / local execution modes)

# scripts/    verify.py, workload.py, explain\_demo.py

# migrations/ canonical Postgres DDL (001) + the verdict-index fix (002)

# seed\_programs/  sample correct / wrong / runtime-error / timeout programs

# ```

# 

# \## Known limitations (honest list)

# 

# \- Auth is a static-token fixture, not real credential auth.

# \- Only Python submissions are judged.

# \- `local` judge mode does not isolate the network (Docker mode does).

# \- The leaderboard is computed per request from stored attempts (no cache) —

# &#x20; correct by construction, and fast enough at this scale (see workload

# &#x20; numbers in `VERIFICATION.md`); a materialized standings table is the

# &#x20; obvious next step for very large contests.

# \- One API process + N worker processes are supported; the API itself is

# &#x20; stateless and can be replicated, but this was verified with one API

# &#x20; process and two competing workers.

# \- In the local SQLite fallback, concurrent write latency is dominated by

# &#x20; SQLite's single-writer lock; the compose deployment uses PostgreSQL.



