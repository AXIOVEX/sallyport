#!/usr/bin/env python3
"""Thin AEE policy layer over optional token-economy tools.

Tools compress or route context; they never originate evidence. Token savings
are measured, never estimated. Every subcommand degrades gracefully when the
optional tool it wraps is absent: rtk, headroom, token-router, ollama.

The three wrapped tools are external runtimes with their own upstreams:
- rtk: https://github.com/rtk-ai/rtk (Apache-2.0) - shell-output compression.
- headroom: https://github.com/headroomlabs-ai/headroom (Apache-2.0).
- token-router: https://github.com/sleeplesshan/token-router (MIT) - local
  line router over logs and source files.

Path-safety helpers mirror scripts/python/run_aee.py so this adapter stays
runnable standalone.

RT3 hardening: the token-router is executed code, so discovery is
confined to the project root / its parent (an out-of-domain
TOKEN_ROUTER_HOME is ignored), router ranges are strictly typed, and
the route record names the router path + SHA-256. `shell` runs in the
project root under a timeout and redacts secret-looking argv values
from telemetry — it is a telemetry wrapper, not a sandbox (see
docs/token-economy.md). `route` refuses files over a byte cap instead
of loading them whole.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Verified append (fleet standard, 2026-10-28), embedded because this
# script ships as a single declared file (extension.yml): record the
# pre-append size, append + fsync, read back the exact appended
# range, and on any mismatch roll back to the pre-append size and
# raise SafeWriteError — an append is never blind-retried, so a
# telemetry record can never land twice or stand torn.
# ---------------------------------------------------------------------------


class SafeWriteError(RuntimeError, ValueError):
    """Every verified-append attempt failed; the file was rolled back."""


_CHILD_APPEND_BOOTSTRAP = (
    "import base64, os, sys\n"
    "data = base64.b64decode(sys.argv[1])\n"
    "target = sys.argv[2]\n"
    "size_before = os.path.getsize(target) if os.path.exists(target) else 0\n"
    "ok = False\n"
    "try:\n"
    "    with open(target, 'ab') as handle:\n"
    "        handle.write(data); handle.flush(); os.fsync(handle.fileno())\n"
    "    with open(target, 'rb') as handle:\n"
    "        handle.seek(size_before)\n"
    "        ok = handle.read() == data\n"
    "except OSError:\n"
    "    ok = False\n"
    "if not ok:\n"
    "    try:\n"
    "        with open(target, 'r+b') as handle:\n"
    "            handle.truncate(size_before)\n"
    "            handle.flush(); os.fsync(handle.fileno())\n"
    "    except OSError: pass\n"
    "sys.exit(0 if ok else 1)\n"
)


def _append_once(path: Path, data: bytes, existed: bool) -> bool:
    size_before = path.stat().st_size if existed else 0
    try:
        with path.open("ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        with path.open("rb") as handle:
            handle.seek(size_before)
            landed = handle.read()
        if landed == data and path.stat().st_size == size_before + len(data):
            return True
    except OSError:
        pass
    try:
        if existed:
            with path.open("r+b") as handle:
                handle.truncate(size_before)
                handle.flush()
                os.fsync(handle.fileno())
        elif path.exists():
            path.unlink()
    except OSError:
        pass
    return False


def _try_child_append(path: Path, data: bytes) -> bool:
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-c",
                _CHILD_APPEND_BOOTSTRAP,
                base64.b64encode(data).decode("ascii"),
                str(path),
            ],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


def append_verified(path: Path, data: bytes) -> dict:
    """Append bytes with the verified-append guarantee (see header).

    Returns {"path", "bytes", "sha256", "via"}; raises ValueError on
    empty content and SafeWriteError when the append cannot be
    landed and proven (the file is rolled back in that case)."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if not data:
        raise ValueError(f"refusing an empty append: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    existed = path.exists()
    attempts = 0
    for index in range(3):
        attempts += 1
        if _append_once(path, data, existed):
            return {
                "path": str(path),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "via": f"local-{index + 1}",
            }
        time.sleep(0.25 * (index + 1))
    for index in range(2):
        attempts += 1
        if _try_child_append(path, data):
            return {
                "path": str(path),
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "via": f"child-{index + 1}",
            }
        time.sleep(0.5)
    raise SafeWriteError(
        f"verified append failed after {attempts} attempts: {path} "
        f"({len(data)} bytes, sha256 {hashlib.sha256(data).hexdigest()})"
    )

DEFAULT_MAX_DIRECT_LINES = 300
DEFAULT_WINDOW = 8
DEFAULT_MAX_OUTPUT_LINES = 160
DEFAULT_HEADROOM_HOURS = 168
ROUTER_TIMEOUT_SECONDS = 300
# route refuses files above this size rather than loading them whole
# into memory (RT3-18); operators slice oversized logs first.
ROUTE_MAX_FILE_BYTES = 256 * 1024 * 1024
SHELL_TIMEOUT_SECONDS = 600

EVIDENCE_POLICY = (
    "Router output is not evidence. The local router selects lines; it never "
    "analyzes. Cite the original file and line ranges; treat router prose as "
    "unsupported unless grounded in verbatim slices."
)

SAVINGS_POLICY = (
    "Measured only. RTK and Headroom are separate savings channels; "
    "token-router and Ollama are workflow tools, not savings. Never estimate; "
    "never double-count; report n/a with a reason when a measurement is "
    "unavailable."
)

# Name fragments that fail closed: routing secrets would put them in
# model context. Matching is done per path component (not just the
# basename), with underscores normalized to hyphens, so a secret-bearing
# directory or an underscore spelling cannot smuggle a file past the
# guard (RT3-06). This list is intentionally conservative.
_SECRET_FRAGMENTS = (
    ".env",
    ".pem",
    ".key",
    "id-rsa",
    "id-ed25519",
    "credentials",
    "secret",
    "service-account",
    "private-key",
)

# Suffixes that mark a component as secret-bearing wherever they sit in
# the path, including compound names such as "tls.key.bak" handled by
# the fragment check above and plain "server.pem" handled here.
_SECRET_SUFFIXES = (".pem", ".key", ".p12", ".pfx", ".jks", ".keystore")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--project-root", type=Path, default=Path.cwd())
    sub = root.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status", help="Report optional tool availability.")
    status.add_argument("--format", choices=("json", "text"), default="json")

    route = sub.add_parser("route", help="Route a large file to raw slices.")
    route.add_argument("--file", type=Path, required=True)
    route.add_argument(
        "--mode",
        choices=("error_log", "heavy_code", "agent_context"),
        default="error_log",
    )
    route.add_argument("--query", default="")
    route.add_argument("--max-direct-lines", type=int, default=DEFAULT_MAX_DIRECT_LINES)
    route.add_argument("--window", type=int, default=DEFAULT_WINDOW)
    route.add_argument("--max-output-lines", type=int, default=DEFAULT_MAX_OUTPUT_LINES)
    route.add_argument(
        "--max-file-bytes",
        type=int,
        default=ROUTE_MAX_FILE_BYTES,
        help="refuse to route files larger than this (default: %(default)s)",
    )

    report = sub.add_parser("report", help="Measured-only savings report.")
    report.add_argument("--hours", type=int, default=DEFAULT_HEADROOM_HOURS)

    shell = sub.add_parser(
        "shell", help="Run a shell command with token-economy telemetry."
    )
    shell.add_argument(
        "--telemetry",
        type=Path,
        default=Path(".specify/extensions/aee/telemetry/shell-calls.jsonl"),
    )
    shell.add_argument(
        "--timeout",
        type=int,
        default=SHELL_TIMEOUT_SECONDS,
        help="seconds before the wrapped command is killed "
        "(default: %(default)s)",
    )
    shell.add_argument("argv", nargs=argparse.REMAINDER)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    root = args.project_root.resolve(strict=True)
    _reject_symlink_chain(root, root)
    if args.command == "status":
        return _status(root, args)
    if args.command == "route":
        return _route(root, args)
    if args.command == "report":
        return _report(args)
    if args.command == "shell":
        return _shell(root, args)
    raise AssertionError("unreachable")


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def _inside_any(path: Path, bases: tuple[Path, ...]) -> bool:
    resolved = path.resolve(strict=False)
    return any(resolved == base or base in resolved.parents for base in bases)


def _find_token_router(root: Path) -> Path | None:
    """Locate the optional token-router backend without vendoring it.

    Discovery anchors on the project root (and its parent, for a
    sibling checkout), never on the caller's working directory — the
    tool accepts --project-root, so results must not depend on where
    the agent happens to invoke it from.

    The router is executed with this interpreter, so discovery is a
    trust decision: an env-supplied TOKEN_ROUTER_HOME is honored only
    when it resolves inside the same trust domain as the built-in
    candidates (the project root or its parent). An out-of-domain
    router home is ignored — routing falls back deterministically
    rather than executing arbitrary out-of-root code (RT3-10).
    """
    trusted = (root.resolve(strict=False), root.parent.resolve(strict=False))
    candidates: list[Path] = []
    env_home = os.environ.get("TOKEN_ROUTER_HOME")
    if env_home:
        env_candidate = Path(env_home) / "scripts" / "router.py"
        if _inside_any(env_candidate, trusted):
            candidates.append(env_candidate)
    candidates.append(root / "token-router" / "scripts" / "router.py")
    candidates.append(root.parent / "token-router" / "scripts" / "router.py")
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def tool_status(root: Path) -> dict:
    """Availability of each optional tool. Never raises when tools are absent."""
    status: dict = {}
    for name in ("rtk", "headroom", "ollama"):
        path = shutil.which(name)
        status[name] = {"available": path is not None, "path": path}
    router = _find_token_router(root)
    status["token-router"] = {
        "available": router is not None,
        "path": str(router) if router else None,
    }
    return status


def _status(root: Path, args: argparse.Namespace) -> int:
    status = tool_status(root)
    if args.format == "text":
        for name, info in status.items():
            state = info["path"] if info["available"] else "unavailable"
            print(f"- {name}: {state}")
    else:
        print(json.dumps(status, indent=2))
    return 0


# ---------------------------------------------------------------------------
# route
# ---------------------------------------------------------------------------


def _looks_like_secret(path: Path) -> bool:
    """Fail-closed name check over EVERY component of the path.

    The basename alone is not enough: `credentials/notes.log` and
    `private_key.log` both routed secret slices before this check
    looked at directory components and normalized `_` to `-`
    (RT3-06). The check is name-based by design — it cannot detect
    secret *content* under an innocuous name; see
    commands/speckit.aee.route-evidence.md for the residual risk.
    """
    for part in path.parts:
        lowered = part.lower()
        normalized = lowered.replace("_", "-")
        if any(fragment in normalized for fragment in _SECRET_FRAGMENTS):
            return True
        if lowered.endswith(_SECRET_SUFFIXES):
            return True
    return False


def _route(root: Path, args: argparse.Namespace) -> int:
    if _looks_like_secret(args.file):
        print(
            f"refused: {args.file} looks like a secret-bearing file; "
            "token routing must never move secrets into model context.",
            file=sys.stderr,
        )
        return 2
    try:
        source = _safe_existing(root, args.file)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    # Byte cap BEFORE any read (RT3-18): the file used to be loaded
    # whole via read_text().splitlines(), so a multi-GB log exhausted
    # memory before the line gates ever ran. Under the cap the file
    # is streamed line by line instead of split from one giant string.
    size = source.stat().st_size
    if size > args.max_file_bytes:
        print(
            f"refused: {args.file} is {size} bytes, over the route "
            f"byte cap ({args.max_file_bytes} bytes); slice the file "
            "first (for example split or tail) and route a bounded "
            "piece.",
            file=sys.stderr,
        )
        return 2
    with source.open(encoding="utf-8", errors="replace") as handle:
        lines = [line.rstrip("\n") for line in handle]
    total = len(lines)
    record: dict = {
        "file": source.relative_to(root).as_posix(),
        "mode": args.mode,
        "query": args.query,
        "total_lines": total,
        "policy": EVIDENCE_POLICY,
    }
    if total <= args.max_direct_lines:
        record["action"] = "read_directly"
        record["reason"] = (
            f"{total} lines is at or below max_direct_lines "
            f"({args.max_direct_lines}); read the file directly."
        )
        print(json.dumps(record, indent=2))
        return 0
    status = tool_status(root)
    router = _find_token_router(root) if status["token-router"]["available"] else None
    ollama = status["ollama"]["available"]
    if router is not None and ollama:
        ranges = _router_backend(router, source, args)
        backend = "token-router"
    else:
        ranges = _deterministic_ranges(lines, args.query, args.window)
        backend = "deterministic"
        record["fallback_reason"] = (
            "token-router backend unavailable"
            if router is None
            else "ollama unavailable for the token-router backend"
        )
    if backend == "token-router" and router is not None:
        # Name the exact code that was executed, with its hash, so a
        # route decision can be audited back to a router build (RT3-10).
        record["router"] = {
            "path": str(router),
            "sha256": hashlib.sha256(router.read_bytes()).hexdigest(),
        }
    ranges = _clamp_ranges(ranges, total, args.max_output_lines)
    if not ranges:
        record["action"] = "no_matches"
        record["backend"] = backend
        record["reason"] = (
            "No line ranges matched; refusing to invent ranges. Sharpen the "
            "query or widen the window."
        )
        print(json.dumps(record, indent=2))
        return 0
    record.update(
        {
            "action": "routed",
            "backend": backend,
            "ranges": ranges,
            "slices": [
                {
                    "start": start,
                    "end": end,
                    "text": "\n".join(lines[start - 1 : end]),
                }
                for start, end in ranges
            ],
        }
    )
    print(json.dumps(record, indent=2))
    return 0


def _router_backend(
    router: Path, source: Path, args: argparse.Namespace
) -> list[tuple[int, int]]:
    """Ask the upstream token-router for line ranges; never trust prose."""
    env = dict(os.environ)
    env["OLLAMA_NUM_CTX"] = "8192"
    env["OLLAMA_KEEP_ALIVE"] = "0s"
    command = [
        sys.executable,
        str(router),
        args.mode,
        str(source),
        "--query",
        args.query,
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=ROUTER_TIMEOUT_SECONDS,
            env=env,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return []
    raw_ranges = payload.get("ranges", payload.get("line_ranges", []))
    ranges: list[tuple[int, int]] = []
    if isinstance(raw_ranges, list):
        for item in raw_ranges:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                # Strict int check (RT3-10): bool is an int subclass,
                # so JSON [[true, 3]] used to be accepted as start=1.
                and all(
                    isinstance(v, int) and not isinstance(v, bool) for v in item
                )
            ):
                ranges.append((item[0], item[1]))
    return ranges


def _deterministic_ranges(
    lines: list[str], query: str, window: int
) -> list[tuple[int, int]]:
    """Literal case-insensitive match windows. No model involved."""
    if not query.strip():
        return []
    needle = query.lower()
    hits = [i + 1 for i, line in enumerate(lines) if needle in line.lower()]
    if not hits:
        return []
    merged: list[tuple[int, int]] = []
    for hit in hits:
        start = max(1, hit - window)
        end = min(len(lines), hit + window)
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _clamp_ranges(
    ranges: list[tuple[int, int]], total: int, max_output_lines: int
) -> list[tuple[int, int]]:
    clamped: list[tuple[int, int]] = []
    budget = max_output_lines
    for start, end in ranges:
        start = max(1, start)
        end = min(total, end)
        if end < start or budget <= 0:
            continue
        width = min(end - start + 1, budget)
        clamped.append((start, start + width - 1))
        budget -= width
    return clamped


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _walk_number(payload: object, keys: tuple[str, ...]) -> int | float | None:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in keys and isinstance(value, (int, float)) and not isinstance(
                value, bool
            ):
                return value
        for value in payload.values():
            found = _walk_number(value, keys)
            if found is not None:
                return found
    elif isinstance(payload, list):
        for item in payload:
            found = _walk_number(item, keys)
            if found is not None:
                return found
    return None


def _measure_rtk() -> dict:
    if shutil.which("rtk") is None:
        return {
            "used": False,
            "saved_tokens": "n/a",
            "reason": "rtk not installed",
        }
    try:
        completed = subprocess.run(
            ["rtk", "gain", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "used": True,
            "saved_tokens": "n/a",
            "reason": "`rtk gain --format json` did not complete",
        }
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "used": True,
            "saved_tokens": "n/a",
            "reason": "no parseable JSON from `rtk gain --format json`",
        }
    saved = _walk_number(payload, ("saved_tokens", "total_saved", "tokens_saved"))
    if saved is None:
        return {
            "used": True,
            "saved_tokens": "n/a",
            "reason": "no parseable savings field in `rtk gain --format json` output",
        }
    return {
        "used": True,
        "saved_tokens": saved,
        "source": "rtk gain --format json",
    }


def _measure_headroom(hours: int) -> dict:
    if shutil.which("headroom") is None:
        return {
            "used": False,
            "saved_tokens": "n/a",
            "reason": "headroom not installed",
        }
    try:
        completed = subprocess.run(
            ["headroom", "perf", "--hours", str(hours)],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {
            "used": True,
            "saved_tokens": "n/a",
            "reason": f"`headroom perf --hours {hours}` did not complete",
        }
    saved: int | float | str = "n/a"
    reason = (
        "headroom perf output has no parseable token field; "
        "see output_excerpt"
    )
    try:
        payload = json.loads(completed.stdout)
        found = _walk_number(payload, ("saved_tokens", "total_saved", "tokens_saved"))
        if found is not None:
            saved = found
            reason = ""
    except json.JSONDecodeError:
        pass
    record: dict = {
        "used": True,
        "saved_tokens": saved,
        "source": f"headroom perf --hours {hours}",
        "output_excerpt": completed.stdout[:500],
    }
    if reason:
        record["reason"] = reason
    return record


def _report(args: argparse.Namespace) -> int:
    rtk = _measure_rtk()
    headroom = _measure_headroom(args.hours)
    combined: int | float | str = "n/a"
    if isinstance(rtk["saved_tokens"], (int, float)) and isinstance(
        headroom["saved_tokens"], (int, float)
    ):
        combined = rtk["saved_tokens"] + headroom["saved_tokens"]
    print(
        json.dumps(
            {
                "rtk": rtk,
                "headroom": headroom,
                "combined_saved_tokens": combined,
                "policy": SAVINGS_POLICY,
            },
            indent=2,
        )
    )
    return 0


# ---------------------------------------------------------------------------
# shell
# ---------------------------------------------------------------------------


# Argv redaction for shell telemetry (RT3-09). Commands routinely
# carry tokens and passwords as arguments; the telemetry record used
# to store them verbatim. Values under a secret-named key, the value
# following a secret-named flag, and known token shapes are replaced
# with the marker below. Redaction is a telemetry hygiene measure,
# not a guarantee: operators must still keep secrets out of argv.
_REDACTED = "«redacted»"
_SECRET_KEY = re.compile(
    r"(?i)(token|secret|password|passwd|api[-_]?key|access[-_]?key|"
    r"auth|credential|private[-_]?key)"
)
_SECRET_ASSIGNMENT = re.compile(
    r"^(?P<key>[^=:\s]*?(?:token|secret|password|passwd|api[-_]?key|"
    r"access[-_]?key|auth|credential)[^=:\s]*?)(?P<sep>[=:])",
    re.IGNORECASE,
)
_TOKEN_SHAPE = re.compile(
    r"^(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|"
    r"sk-[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN)"
)


def _redact_argv(command: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for arg in command:
        if redact_next:
            redacted.append(_REDACTED)
            redact_next = False
            continue
        assignment = _SECRET_ASSIGNMENT.match(arg)
        if assignment and len(arg) > assignment.end():
            redacted.append(f"{assignment.group('key')}{assignment.group('sep')}{_REDACTED}")
            continue
        if _TOKEN_SHAPE.match(arg):
            redacted.append(_REDACTED)
            continue
        if arg.startswith("--") and _SECRET_KEY.search(arg) and "=" not in arg:
            redacted.append(arg)
            redact_next = True
            continue
        redacted.append(arg)
    return redacted


def _shell(root: Path, args: argparse.Namespace) -> int:
    command = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
    if not command:
        print("refused: no command supplied after `shell`", file=sys.stderr)
        return 2
    rtk_available = shutil.which("rtk") is not None
    # Validate the telemetry destination BEFORE running the wrapped
    # command: an invalid --telemetry path must refuse up front, not
    # raise after the command's side effects have already happened.
    try:
        telemetry = _safe_output(root, args.telemetry)
    except ValueError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    # Trust model (RT3-09): this wrapper is telemetry, not a sandbox.
    # It confines the working directory to the project root and bounds
    # the command's lifetime; it does not restrict what the command
    # itself may read or write. See docs/token-economy.md.
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            cwd=root,
            timeout=args.timeout,
        )
        stdout, stderr, exit_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or b""
        stderr = exc.stderr or b""
        exit_code = None
        print(
            f"refused: command timed out after {args.timeout} seconds "
            "and was killed",
            file=sys.stderr,
        )
    except OSError as exc:
        print(f"refused: cannot execute {command[0]!r}: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    record = {
        "timestamp": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": _redact_argv(command),
        "cwd": root.as_posix(),
        "rtk_available": rtk_available,
        "stdout_bytes": len(stdout),
        "stderr_bytes": len(stderr),
        "exit_code": exit_code,
        "timed_out": timed_out,
        "note": (
            "Shell-output filtering is handled by rtk tool hooks "
            "(`rtk init -g`) when installed; this wrapper records telemetry "
            "only. Savings remain measured via `rtk gain`. argv values "
            "that look like secrets are redacted in this record."
        ),
    }
    # Verified append (fleet standard, 2026-10-28): the telemetry
    # record is this wrapper's product; losing it silently would be
    # a degraded run reported as success, so a failed append fails
    # the run closed (exit 2) with the reason on stderr.
    try:
        append_verified(telemetry, json.dumps(record) + "\n")
    except SafeWriteError as exc:
        print(f"error: telemetry record not written: {exc}", file=sys.stderr)
        return 2
    return 2 if timed_out else exit_code


# ---------------------------------------------------------------------------
# path safety (mirrors run_aee.py adapter policy)
# ---------------------------------------------------------------------------


def _safe_existing(root: Path, value: Path) -> Path:
    candidate = value if value.is_absolute() else root / value
    _reject_symlink_chain(root, candidate)
    resolved = candidate.resolve(strict=True)
    _require_inside(root, resolved)
    if not resolved.is_file():
        raise ValueError(f"not a regular file: {value}")
    return resolved


def _safe_output(root: Path, value: Path) -> Path:
    candidate = value if value.is_absolute() else root / value
    _require_inside(root, candidate.resolve(strict=False))
    _reject_symlink_chain(root, candidate)
    candidate.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_chain(root, candidate)
    # Return the RESOLVED path (RT3-17), mirroring run_aee.py: the
    # unresolved candidate left a check-then-use window between this
    # validation and the later write.
    return candidate.resolve(strict=False)


def _require_inside(root: Path, candidate: Path) -> None:
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {candidate}") from exc


def _reject_symlink_chain(root: Path, candidate: Path) -> None:
    absolute = candidate if candidate.is_absolute() else root / candidate
    _require_inside(root, absolute.resolve(strict=False))
    current = root
    try:
        parts = absolute.relative_to(root).parts
    except ValueError as exc:
        raise ValueError(f"path escapes project root: {absolute}") from exc
    for part in parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError(f"symlinked path component refused: {current}")


if __name__ == "__main__":
    sys.exit(main())
