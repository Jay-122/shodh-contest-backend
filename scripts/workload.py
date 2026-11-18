"""Repeatable concurrent workload (Stage 4).

Fires N concurrent submission POSTs (API overhead only — judging is async)
while M leaderboard reads run against the same store, then measures queue
delay (submit -> final verdict) separately from API latency.

Usage: python scripts/workload.py [base_url] [n_submissions]
Requires seed data and at least one running judge worker.
"""

import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
N = int(sys.argv[2]) if len(sys.argv) > 2 else 20
TOKENS = ["org1-alice-token", "org1-bob-token"]

CORRECT = "import sys\na, b = map(int, sys.stdin.read().split())\nprint(a + b)\n"


def call(method, path, token=None, body=None, headers=None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    start = time.monotonic()
    try:
        with urllib.request.urlopen(req, data=data, timeout=60) as resp:
            payload = json.loads(resp.read().decode() or "{}")
            return resp.status, payload, (time.monotonic() - start) * 1000
    except urllib.error.HTTPError as e:
        return e.code, {}, (time.monotonic() - start) * 1000


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, int(round((p / 100) * (len(values) - 1))))
    return values[idx]


def report(name, latencies, errors):
    if not latencies:
        print(f"{name}: no successful calls ({errors} errors)")
        return
    print(
        f"{name}: n={len(latencies)} errors={errors} "
        f"p50={pct(latencies, 50):.0f}ms p95={pct(latencies, 95):.0f}ms "
        f"max={max(latencies):.0f}ms mean={statistics.mean(latencies):.0f}ms"
    )


def main():
    _, contests, _ = call("GET", "/contests", token=TOKENS[0])
    sprint_id = next(c["id"] for c in contests["items"] if c["title"] == "Weekly Sprint 1")
    _, problems, _ = call("GET", f"/contests/{sprint_id}/problems", token=TOKENS[0])
    add_id = next(p["id"] for p in problems["items"] if p["title"] == "Add Two Numbers")

    print(f"workload: {N} concurrent submissions + leaderboard reads against contest {sprint_id}")

    # Baseline: leaderboard reads with no write load.
    base_lat, base_err = [], 0
    for _ in range(10):
        status, _, ms = call("GET", f"/contests/{sprint_id}/leaderboard", token=TOKENS[0])
        (base_lat.append(ms) if status == 200 else None) or (base_err := base_err + (status != 200))
    report("leaderboard reads (idle)", base_lat, base_err)

    # Concurrent submissions.
    def submit(i):
        token = TOKENS[i % len(TOKENS)]
        return call(
            "POST", "/submissions", token=token,
            body={"contest_id": sprint_id, "problem_id": add_id, "code": CORRECT},
            headers={"Idempotency-Key": f"workload-{time.time()}-{i}"},
        )

    started = time.time()
    with ThreadPoolExecutor(max_workers=10) as pool:
        results = list(pool.map(submit, range(N)))
    post_lat = [ms for status, _, ms in results if status == 201]
    post_err = sum(1 for status, _, _ in results if status != 201)
    submission_ids = [body["id"] for status, body, _ in results if status == 201]
    report("submission POSTs (API overhead only)", post_lat, post_err)

    # Leaderboard reads under judging load.
    def read_board(_):
        return call("GET", f"/contests/{sprint_id}/leaderboard", token=TOKENS[0])

    with ThreadPoolExecutor(max_workers=10) as pool:
        reads = list(pool.map(read_board, range(30)))
    read_lat = [ms for status, _, ms in reads if status == 200]
    read_err = sum(1 for status, _, _ in reads if status != 200)
    report("leaderboard reads (under load)", read_lat, read_err)

    # Queue delay: time from POST until each submission reaches a final state.
    pending = set(submission_ids)
    final_at = {}
    deadline = time.time() + 300
    while pending and time.time() < deadline:
        for sid in list(pending):
            _, body, _ = call("GET", f"/submissions/{sid}", token=TOKENS[0])
            # Owner-only reads: bob's submissions aren't visible to alice; poll
            # with both tokens by trying the other on 404.
            if not body:
                _, body, _ = call("GET", f"/submissions/{sid}", token=TOKENS[1])
            if body.get("status") in ("completed", "failed"):
                attempts = body.get("attempts") or []
                if attempts and attempts[-1].get("finished_at"):
                    final_at[sid] = attempts[-1]["finished_at"]
                pending.discard(sid)
        if pending:
            time.sleep(1)
    judged = len(final_at)
    wall = time.time() - started
    print(f"judged: {judged}/{len(submission_ids)} submissions reached a final state; "
          f"total wall time {wall:.1f}s; throughput ~{judged / wall * 60:.0f} submissions/min "
          f"(includes program execution; API latency above is measured separately)")
    if pending:
        print(f"WARNING: {len(pending)} submissions did not finish within the deadline")


if __name__ == "__main__":
    main()
