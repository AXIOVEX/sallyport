#!/usr/bin/env python3
"""Hosted smoke battery for the POSIX + Docker transports
(spec 010 T008/T009, FR-003/FR-004).

Extracts the Execute step's script body from the shipped
workflow files and runs it the way a runner would (same env
names, bash shell), against probe scripts that print markers:

  - marker output + cwd honored + exit code propagated, for
    linux.yml and macos.yml (different OS bodies; both run here);
  - for docker.yml, when a Docker CLI is available: the job
    runs inside the image, sees the declared mount, and a
    file it writes into the mount is present on the host
    afterwards (the grab-return path's precondition).
    Set SYNCYARD_SMOKE_DOCKER=1 to make a missing Docker a
    failure instead of a skip (CI's docker job does);
    SYNCYARD_SMOKE_IMAGE overrides the probe image.

Stdlib only. Run: python3 runner/test_transport_smoke.py
"""
import base64
import os
import platform
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
WF_DIR = os.path.join(HERE, "workflows")
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        fails.append(name)


def run_block(path):
    """The script body of the workflow's `run: |` step."""
    lines = open(path).read().splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "run: |":
            indent = len(ln) - len(ln.lstrip())
            body = []
            for b in lines[i + 1:]:
                if b.strip() and (len(b) - len(b.lstrip())) <= indent:
                    break
                body.append(b)
            pad = min((len(b) - len(b.lstrip())) for b in body if b.strip())
            return "\n".join(b[pad:] for b in body) + "\n"
    raise ValueError(f"no run block in {path}")


def run_body(body, env, cwd=None):
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as f:
        f.write(body)
        path = f.name
    try:
        full = dict(os.environ)
        full.update(env)
        return subprocess.run(["bash", "--noprofile", "--norc", "-eo",
                               "pipefail", path],
                              capture_output=True, text=True, env=full,
                              cwd=cwd, timeout=600)
    finally:
        os.unlink(path)


PROBE = "echo SMOKE_UNAME=$(uname -s)\necho SMOKE_CWD=$(pwd)\necho SMOKE_OK\n"
PROBE_EXIT = "echo SMOKE_EXITING\nexit 7\n"


def posix_smoke(fname):
    body = run_block(os.path.join(WF_DIR, fname))
    # The Linux door body contains GitHub expressions that the
    # runner substitutes before bash sees the script. Substitute
    # the two the body uses so the extracted body can run here.
    body = body.replace("${{ github.run_id }}", "12345")
    body = body.replace("${{ inputs.lane }}", "lane-long")
    tmp = tempfile.mkdtemp(prefix="smoke-")
    env = {"CMD_B64": base64.b64encode(PROBE.encode()).decode(),
           "JOB_CWD": tmp, "RUNNER_TEMP": tmp,
           "SYNCYARD_HOLD_FILE": ""}
    # linux.yml applies inputs.cwd through the step's
    # working-directory, which extraction cannot carry; run its
    # body in the probe directory instead. macos.yml's body
    # changes directory itself from JOB_CWD.
    body_cwd = tmp if fname == "linux.yml" else None
    r = run_body(body, env, cwd=body_cwd)
    check(f"{fname}: probe runs (rc 0)", r.returncode == 0,
          f"rc={r.returncode} err={r.stderr[-200:]}")
    check(f"{fname}: markers in output",
          "SMOKE_OK" in r.stdout and "SMOKE_UNAME=" in r.stdout)
    check(f"{fname}: cwd honored", f"SMOKE_CWD={tmp}" in r.stdout,
          r.stdout.replace("\n", " | ")[:200])
    env["CMD_B64"] = base64.b64encode(PROBE_EXIT.encode()).decode()
    r = run_body(body, env, cwd=body_cwd)
    check(f"{fname}: exit code propagates (7)", r.returncode == 7,
          f"rc={r.returncode}")


def docker_smoke():
    if not shutil.which("docker"):
        if os.environ.get("SYNCYARD_SMOKE_DOCKER") == "1":
            check("docker: CLI available (required by env)", False)
        else:
            print("SKIP docker: no Docker CLI on this machine")
        return
    image = os.environ.get("SYNCYARD_SMOKE_IMAGE", "debian:bookworm-slim")
    pull = subprocess.run(["docker", "pull", image],
                          capture_output=True, text=True, timeout=900)
    check("docker: probe image pulls", pull.returncode == 0,
          pull.stderr[-200:])
    if pull.returncode != 0:
        return
    tmp = tempfile.mkdtemp(prefix="smoke-docker-")
    outdir = os.path.join(tmp, "out")
    os.mkdir(outdir)
    probe = ("echo SMOKE_DOCKER_OK\n"
             "echo result-from-container > /mnt/out/result.txt\n")
    body = run_block(os.path.join(WF_DIR, "docker.yml"))
    env = {"CMD_B64": base64.b64encode(probe.encode()).decode(),
           "JOB_IMAGE": image, "JOB_MOUNTS": f"{outdir}:/mnt/out",
           "JOB_CWD": "/", "RUNNER_TEMP": tmp}
    r = run_body(body, env)
    check("docker: job runs in image (rc 0)", r.returncode == 0,
          f"rc={r.returncode} err={r.stderr[-200:]}")
    check("docker: marker in output", "SMOKE_DOCKER_OK" in r.stdout)
    landed = os.path.join(outdir, "result.txt")
    check("docker: mounted result file lands on host",
          os.path.isfile(landed)
          and open(landed).read().strip() == "result-from-container")


def main():
    print(f"smoke platform: {platform.system()} {platform.machine()}")
    posix_smoke("linux.yml")
    posix_smoke("macos.yml")
    docker_smoke()
    print()
    if fails:
        print(f"TEST_TRANSPORT_SMOKE: FAIL ({len(fails)} failing)")
        sys.exit(1)
    print("TEST_TRANSPORT_SMOKE: ALL PASS")


if __name__ == "__main__":
    main()
