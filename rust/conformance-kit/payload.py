#!/usr/bin/env python3
"""Syncyard conformance-kit payload (Python) — spec 034 section 7.

Emits the full progress line set to $SYNCYARD_PROGRESS_FILE
and exits 0. The kit's three payloads (bash, Python,
PowerShell) must produce IDENTICAL parsed event streams.
"""
import os
import sys

LINES = [
    "STARTED",
    "MARKER kit-start",
    "PROGRESS 1/2 first-half",
    "HEARTBEAT",
    "PROGRESS 2/2 second-half",
    "COMPLETED done",
]


def main() -> int:
    path = os.environ.get("SYNCYARD_PROGRESS_FILE")
    if not path:
        print("SYNCYARD_PROGRESS_FILE must be set by the agent", file=sys.stderr)
        return 2
    with open(path, "a", encoding="utf-8") as fh:
        for line in LINES:
            fh.write(line + "\n")
    print("conformance payload (python): full line set emitted")
    return 0


if __name__ == "__main__":
    sys.exit(main())
