"""End-to-end verification against a running API + worker.

Usage: python scripts/verify.py [base_url]   (default http://127.0.0.1:8000)
Requires the seed data (python -m app.seed) and a running judge worker.
Prints PASS/FAIL lines; exit code 0 only if everything passes.
"""

import json
import sys
import time
import urllib.error
import urllib.request

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

ORG1 = "org1-organizer-token"
ALICE = "org1-alice-token"
BOB = "org1-bob-token"
ORG2 = "org2-organizer-token"
CAROL = "org2-carol-token"

RESULTS = []


def check(name, condition, extra=""):
    RESULTS.append((name, bool(condition)))
    print(f"{'PASS' if condition else 'FAIL'}  {name}  {extra}")


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
    try:
        with urllib.request.urlopen(req, data=data, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def wait_final(token, submission_id, timeout=90):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        _, body = call("GET", f"/submissions/{submission_id}", token=token)
        last = body
        if body.get("status") in ("completed", "failed"):
            return body
        time.sleep(0.5)
    return last


def read_program(name):
    with open(f"seed_programs/{name}") as fh:
        return fh.read()


def main():
    status, health = call("GET", "/health")
    check("health endpoint", status == 200 and health.get("status") == "ok")

    _, contests = call("GET", "/contests", token=ALICE)
    titles = {c["title"]: c["id"] for c in contests["items"]}
    check("alice sees her org's contests", "Weekly Sprint 1" in titles and "Beta Open" not in titles)
    sprint_id = titles["Weekly Sprint 1"]
    past_id = titles["Past Cup"]

    _, beta_as_carol = call("GET", "/contests", token=CAROL)
    beta_id = {c["title"]: c["id"] for c in beta_as_carol["items"]}["Beta Open"]

    status, _ = call("GET", f"/contests/{beta_id}", token=ALICE)
    check("cross-org contest read is 404", status == 404)
    status, _ = call("GET", f"/contests/{sprint_id}", token=CAROL)
    check("cross-org contest read (reverse) is 404", status == 404)

    # --- organizer-only actions -------------------------------------------
    status, created = call(
        "POST", "/contests", token=ORG1,
        body={"org_id": 1, "title": "Verify Cup", "starts_at": "2026-01-01T00:00:00Z",
              "ends_at": "2030-01-01T00:00:00Z"},
    )
    check("organizer can create contest", status == 201, f"id={created.get('id')}")
    status, _ = call(
        "POST", "/contests", token=ALICE,
        body={"org_id": 1, "title": "Nope", "starts_at": "2026-01-01T00:00:00Z",
              "ends_at": "2030-01-01T00:00:00Z"},
    )
    check("participant creating contest is denied (403)", status == 403)

    _, problems = call("GET", f"/contests/{sprint_id}/problems", token=ALICE)
    add_problem = next(p for p in problems["items"] if p["title"] == "Add Two Numbers")
    add_id = add_problem["id"]

    _, problem_detail = call("GET", f"/problems/{add_id}", token=ALICE)
    serialized = json.dumps(problem_detail)
    check(
        "problem view hides hidden tests (no hidden expected output leaks)",
        "350" not in serialized and problem_detail.get("hidden_test_count") == 2,
    )

    # --- problem versioning -------------------------------------------------
    status, updated = call(
        "PUT", f"/problems/{add_id}", token=ORG1,
        body={"statement": "Read two integers a and b separated by a space and print their sum. (v2)"},
    )
    check("editing a problem creates version 2", status == 200 and updated["current_version"] == 2)
    _, versions = call("GET", f"/problems/{add_id}/versions", token=ORG1)
    check("both versions are preserved", len(versions) == 2)

    # --- business rules ------------------------------------------------------
    call("POST", f"/contests/{past_id}/join", token=ALICE)
    _, past_problem = call(
        "POST", f"/contests/{past_id}/problems", token=ORG1,
        body={"title": "Past Problem", "statement": "Print 1.",
              "test_cases": [{"input": "", "expected_output": "1", "is_hidden": False}]},
    )
    status, body = call(
        "POST", "/submissions", token=ALICE,
        body={"contest_id": past_id, "problem_id": past_problem["id"], "code": "print(1)"},
    )
    check(
        "submission to an ended contest is a business-rule error",
        status == 409 and body["detail"]["type"] == "business_rule",
        json.dumps(body.get("detail", {})),
    )

    # --- judging: four verdicts ---------------------------------------------
    def submit(token, code, key=None):
        headers = {"Idempotency-Key": key} if key else None
        return call(
            "POST", "/submissions", token=token,
            body={"contest_id": sprint_id, "problem_id": add_id, "code": code},
            headers=headers,
        )

    status, sub = submit(ALICE, read_program("add_correct.py"))
    check("submission returns an ID immediately with queued status",
          status == 201 and sub["status"] in ("queued", "running"))
    correct_sub_id = sub["id"]
    final = wait_final(ALICE, correct_sub_id)
    attempt = final["attempts"][-1]
    check("correct program -> ACCEPTED", final["status"] == "completed" and attempt["verdict"] == "ACCEPTED",
          f"mode={attempt.get('execution_mode')}")
    hidden_entries = [t for t in attempt["test_results"] if t["hidden"]]
    check("participant view hides hidden-test verdicts",
          all("verdict" not in t for t in hidden_entries) and len(hidden_entries) == 2)
    check("all 3 tests ran (visible + hidden)", attempt["tests_total"] == 3 and attempt["tests_passed"] == 3)

    _, sub = submit(ALICE, read_program("add_wrong.py"))
    final = wait_final(ALICE, sub["id"])
    check("wrong program -> WRONG_ANSWER", final["attempts"][-1]["verdict"] == "WRONG_ANSWER")

    _, sub = submit(ALICE, read_program("add_runtime_error.py"))
    final = wait_final(ALICE, sub["id"])
    check("crashing program -> RUNTIME_ERROR", final["attempts"][-1]["verdict"] == "RUNTIME_ERROR")

    _, sub = submit(ALICE, read_program("add_timeout.py"))
    final = wait_final(ALICE, sub["id"])
    check("infinite loop -> TIME_LIMIT_EXCEEDED", final["attempts"][-1]["verdict"] == "TIME_LIMIT_EXCEEDED")

    _, sub = submit(BOB, read_program("add_correct.py"))
    bob_final = wait_final(BOB, sub["id"])
    check("bob's correct program -> ACCEPTED", bob_final["attempts"][-1]["verdict"] == "ACCEPTED")

    # --- leaderboard -----------------------------------------------------------
    _, board = call("GET", f"/contests/{sprint_id}/leaderboard", token=ALICE)
    standings = board["standings"]
    by_name = {r["user_name"]: r for r in standings}
    alice_row, bob_row = by_name["Alice Participant"], by_name["Bob Participant"]
    check("leaderboard: both solved = 100 points",
          alice_row["points"] == 100 and bob_row["points"] == 100)
    check("leaderboard: alice has 3 failed attempts, bob has 0",
          alice_row["failed_attempts"] == 3 and bob_row["failed_attempts"] == 0,
          json.dumps(standings))
    check("leaderboard: tie-break puts bob (fewer failures) first",
          standings[0]["user_name"] == "Bob Participant")

    # --- submission read permissions -------------------------------------------
    status, _ = call("GET", f"/submissions/{correct_sub_id}", token=CAROL)
    check("cross-org submission read is 404", status == 404)
    status, _ = call("GET", f"/submissions/{correct_sub_id}", token=BOB)
    check("another participant cannot read alice's submission (404)", status == 404)
    status, org_view = call("GET", f"/submissions/{correct_sub_id}", token=ORG1)
    org_attempt = org_view["attempts"][-1]
    check("organizer can read it and sees hidden-test verdicts",
          status == 200 and all("verdict" in t for t in org_attempt["test_results"]))

    # --- idempotency --------------------------------------------------------------
    status, first = submit(ALICE, read_program("add_correct.py"), key="verify-key-1")
    status2, replay = submit(ALICE, read_program("add_correct.py"), key="verify-key-1")
    check("idempotent replay returns the same submission",
          status == 201 and status2 == 201 and first["id"] == replay["id"])
    status, body = submit(ALICE, read_program("add_wrong.py"), key="verify-key-1")
    check("same key + different payload -> 409 conflict",
          status == 409 and body["detail"]["type"] == "conflict")
    wait_final(ALICE, first["id"])

    # --- rejudge against a corrected version --------------------------------------
    status, echo_problem = call(
        "POST", f"/contests/{sprint_id}/problems", token=ORG1,
        body={"title": "Echo Number", "statement": "Read an integer and print it back.",
              "test_cases": [{"input": "7", "expected_output": "8", "is_hidden": False}]},
    )
    echo_id = echo_problem["id"]
    echo_code = "import sys\nprint(int(sys.stdin.read()))\n"
    _, sub = call("POST", "/submissions", token=ALICE,
                  body={"contest_id": sprint_id, "problem_id": echo_id, "code": echo_code})
    echo_sub_id = sub["id"]
    final = wait_final(ALICE, echo_sub_id)
    check("correct code judged WRONG against the (wrong) v1 tests",
          final["attempts"][-1]["verdict"] == "WRONG_ANSWER")
    call("PUT", f"/problems/{echo_id}", token=ORG1,
         body={"test_cases": [{"input": "7", "expected_output": "7", "is_hidden": False}]})
    _, versions = call("GET", f"/problems/{echo_id}/versions", token=ORG1)
    v2_id = versions[-1]["id"]
    status, _ = call("POST", f"/submissions/{echo_sub_id}/rejudge", token=ALICE, body={})
    check("participant attempting rejudge is denied (403)", status == 403)
    status, rejudged = call("POST", f"/submissions/{echo_sub_id}/rejudge", token=ORG1,
                            body={"problem_version_id": v2_id})
    check("organizer rejudge creates attempt 2", status == 200 and len(rejudged["attempts"]) == 2)
    final = wait_final(ALICE, echo_sub_id)
    check("rejudged attempt against v2 -> ACCEPTED and authoritative",
          final["attempts"][-1]["verdict"] == "ACCEPTED"
          and final["authoritative_attempt_id"] == final["attempts"][-1]["id"])
    _, board = call("GET", f"/contests/{sprint_id}/leaderboard", token=ALICE)
    alice_row = next(r for r in board["standings"] if r["user_name"] == "Alice Participant")
    check("leaderboard reflects the rejudge (alice now 200 points)",
          alice_row["points"] == 200, json.dumps(alice_row))

    # --- judge failure injection ---------------------------------------------------
    _, sub = submit(ALICE, "# INJECT_JUDGE_FAILURE\nprint(1)\n")
    final = wait_final(ALICE, sub["id"], timeout=120)
    check("injected judge failure -> JUDGE_FAILURE, not a student verdict",
          final["status"] == "failed" and final["attempts"][-1]["verdict"] == "JUDGE_FAILURE")
    _, board = call("GET", f"/contests/{sprint_id}/leaderboard", token=ALICE)
    alice_row = next(r for r in board["standings"] if r["user_name"] == "Alice Participant")
    check("judge failure costs no points and no failed-attempt penalty",
          alice_row["points"] == 200 and alice_row["failed_attempts"] == 3)

    # --- pagination ------------------------------------------------------------------
    _, page = call("GET", "/contests?limit=1&offset=0", token=ALICE)
    check("pagination: limit respected, total reported",
          len(page["items"]) == 1 and page["total"] >= 3)

    failed = [n for n, ok in RESULTS if not ok]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    if failed:
        print("FAILED:", "; ".join(failed))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
