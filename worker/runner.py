"""Code execution for the judge.

Two execution modes, both reported on every attempt:
- "docker": the code runs in a throwaway container — no network, CPU/memory/
  pids limits, non-root user, read-only root filesystem, no host mounts other
  than the per-run workdir the judge itself created (code + input only), no
  Docker socket inside the judged container. This is the mode docker-compose
  uses and the one the assessment is evaluated with.
- "local": fallback for machines without a Docker daemon (e.g. a dev laptop).
  The code runs as a subprocess under `timeout` with rlimits. Network is NOT
  isolated in this mode — documented limitation; verdicts are still real
  executions, never hardcoded.

Failure taxonomy: a *student* outcome (runtime error, timeout, wrong output)
is returned as data. An *infrastructure* outcome (daemon unreachable, image
missing, runner crash) raises RunnerInfraError and must never be scored as a
student error.
"""

import math
import os
import resource
import shutil
import subprocess
import tempfile
import time

from app.config import JUDGE_HOST_TMP, JUDGE_IMAGE, OUTPUT_CAP_BYTES

RUN_SCRIPT = """#!/bin/bash
ulimit -f 2048
timeout -s KILL {wall_s} python3 solution.py < input.txt > out.txt 2> err.txt
echo $? > exit.txt
"""


class RunnerInfraError(Exception):
    pass


def normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.strip().splitlines())


def _prepare_workdir(code: str, input_text: str, wall_s: int) -> str:
    base = JUDGE_HOST_TMP if os.path.isdir(JUDGE_HOST_TMP) else None
    workdir = tempfile.mkdtemp(prefix="judge-", dir=base)
    os.chmod(workdir, 0o777)
    files = {
        "solution.py": code,
        "input.txt": input_text,
        "run.sh": RUN_SCRIPT.format(wall_s=wall_s),
    }
    for name, content in files.items():
        path = os.path.join(workdir, name)
        with open(path, "w") as fh:
            fh.write(content)
        os.chmod(path, 0o644)
    return workdir


def _read_result(workdir: str, elapsed_ms: int) -> dict:
    exit_path = os.path.join(workdir, "exit.txt")
    if not os.path.exists(exit_path):
        raise RunnerInfraError("runner produced no exit status")
    with open(exit_path) as fh:
        exit_code = int(fh.read().strip() or "-1")
    out_path = os.path.join(workdir, "out.txt")
    stdout = ""
    if os.path.exists(out_path):
        with open(out_path, "rb") as fh:
            raw = fh.read(OUTPUT_CAP_BYTES + 1)
        if len(raw) > OUTPUT_CAP_BYTES:
            return {
                "verdict": "OUTPUT_LIMIT_EXCEEDED",
                "time_ms": elapsed_ms,
                "stdout": raw[:OUTPUT_CAP_BYTES].decode(errors="replace"),
            }
        stdout = raw.decode(errors="replace")
    if exit_code in (124, 137):  # timeout(1) / SIGKILL from timeout -s KILL
        return {"verdict": "TIME_LIMIT_EXCEEDED", "time_ms": elapsed_ms, "stdout": stdout}
    if exit_code != 0:
        return {"verdict": "RUNTIME_ERROR", "time_ms": elapsed_ms, "stdout": stdout}
    return {"verdict": "OK", "time_ms": elapsed_ms, "stdout": stdout}


def _run_local(workdir: str, wall_s: int, memory_mb: int) -> dict:
    def limits():
        # Approximate memory bound: RLIMIT_AS needs generous headroom for the
        # CPython interpreter itself; wall-time is the hard bound in this mode.
        vmem = max(memory_mb, 512) * 4 * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_AS, (vmem, vmem))
        resource.setrlimit(resource.RLIMIT_CPU, (wall_s + 1, wall_s + 2))

    start = time.monotonic()
    try:
        subprocess.run(
            ["bash", "run.sh"],
            cwd=workdir,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=wall_s + 10,
            preexec_fn=limits,
        )
    except subprocess.TimeoutExpired as exc:
        raise RunnerInfraError(f"local runner watchdog expired: {exc}") from exc
    except OSError as exc:
        raise RunnerInfraError(f"local runner failed to start: {exc}") from exc
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return _read_result(workdir, elapsed_ms)


def _run_docker(workdir: str, wall_s: int, memory_mb: int) -> dict:
    try:
        import docker
        from docker.errors import DockerException
    except ImportError as exc:
        raise RunnerInfraError(f"docker SDK unavailable: {exc}") from exc
    try:
        client = docker.from_env()
        client.ping()
    except Exception as exc:  # daemon down / socket missing = infra failure
        raise RunnerInfraError(f"docker daemon unreachable: {exc}") from exc

    # The daemon interprets bind-mount sources in ITS OWN filesystem
    # namespace, not this worker container's. When the worker itself runs in
    # a container (docker compose), a workdir like /judge_tmp/judge-123 must
    # be translated through this container's mount table, otherwise the
    # daemon mounts an empty directory and the judged code never runs
    # (observed on Docker Desktop for Windows, where the host path is a
    # D:\ path that can never equal the in-container path).
    host_workdir = workdir
    try:
        import socket

        me = client.containers.get(socket.gethostname())
        for mount in me.attrs.get("Mounts", []):
            dest = mount.get("Destination", "")
            if workdir == dest or workdir.startswith(dest + "/"):
                host_workdir = mount["Source"] + workdir[len(dest):]
                break
    except Exception:
        pass  # fall back to the untranslated path (bare-metal worker)

    container = None
    start = time.monotonic()
    try:
        container = client.containers.run(
            JUDGE_IMAGE,
            ["bash", "/work/run.sh"],
            volumes={host_workdir: {"bind": "/work", "mode": "rw"}},
            network_mode="none",
            nano_cpus=1_000_000_000,  # 1 CPU
            mem_limit=f"{memory_mb}m",
            memswap_limit=f"{memory_mb}m",
            pids_limit=64,
            user="65534:65534",  # nobody
            read_only=True,
            tmpfs={"/tmp": ""},
            working_dir="/work",
            detach=True,
        )
        try:
            container.wait(timeout=wall_s + 15)
        except Exception:
            # The in-container `timeout` should have fired first; if the wait
            # itself expires, kill and treat as a timeout of the program.
            try:
                container.kill()
            except DockerException:
                pass
            elapsed_ms = int((time.monotonic() - start) * 1000)
            return {"verdict": "TIME_LIMIT_EXCEEDED", "time_ms": elapsed_ms, "stdout": ""}
    except RunnerInfraError:
        raise
    except Exception as exc:
        raise RunnerInfraError(f"docker run failed: {exc}") from exc
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass
    elapsed_ms = int((time.monotonic() - start) * 1000)
    return _read_result(workdir, elapsed_ms)


def run_test_case(
    code: str,
    input_text: str,
    time_limit_ms: int,
    memory_mb: int,
    mode: str,
) -> dict:
    """Execute one test case. Returns {verdict, time_ms, stdout} where verdict
    is OK | WRONG-side raw outcomes: RUNTIME_ERROR | TIME_LIMIT_EXCEEDED |
    OUTPUT_LIMIT_EXCEEDED. Raises RunnerInfraError on infrastructure failure."""
    fail_marker = os.environ.get("JUDGE_FAIL_MARKER")
    if fail_marker and fail_marker in code:
        # Documented failure-injection hook for recovery testing (see README).
        raise RunnerInfraError("injected judge failure (JUDGE_FAIL_MARKER present)")

    wall_s = max(1, math.ceil(time_limit_ms / 1000))
    workdir = _prepare_workdir(code, input_text, wall_s)
    try:
        if mode == "docker":
            return _run_docker(workdir, wall_s, memory_mb)
        return _run_local(workdir, wall_s, memory_mb)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def judge_submission(
    code: str,
    test_cases: list,  # list of models.TestCase, ordered by ordinal
    time_limit_ms: int,
    memory_mb: int,
    mode: str,
) -> tuple[str, list[dict]]:
    """Run every test case. Returns (overall_verdict, per-test detail).
    Detail entries never contain inputs or expected outputs."""
    detail = []
    overall = "ACCEPTED"
    for tc in test_cases:
        result = run_test_case(code, tc.input, time_limit_ms, memory_mb, mode)
        if result["verdict"] == "OK":
            verdict = (
                "ACCEPTED"
                if normalize(result["stdout"]) == normalize(tc.expected_output)
                else "WRONG_ANSWER"
            )
        elif result["verdict"] == "OUTPUT_LIMIT_EXCEEDED":
            verdict = "WRONG_ANSWER"
        else:
            verdict = result["verdict"]
        detail.append(
            {
                "ordinal": tc.ordinal,
                "hidden": bool(tc.is_hidden),
                "verdict": verdict,
                "time_ms": result["time_ms"],
            }
        )
        if verdict != "ACCEPTED" and overall == "ACCEPTED":
            overall = verdict
    return overall, detail
