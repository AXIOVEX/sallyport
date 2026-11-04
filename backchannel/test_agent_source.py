#!/usr/bin/env python3
"""Static battery for the backchannel agent (spec 011 Phase 3).

The agent runs on the host's WSL2 distro, which CI cannot
reproduce, so — like the tray battery (spec 007) — these are
source-shape checks. They pin the going-down writer's
transport contract: write_heartbeat merges the host guard's
two files into the heartbeat payload verbatim, under the
exact keys the gatekeeper's Phase 1 ingest and guard-beat
recorder read (`going_down`, `guard`), from the paths the
guard writes, and a bad or absent file can never fail the
heartbeat.

Run: python3 backchannel/test_agent_source.py
"""
import pathlib
import sys

SRC = pathlib.Path(__file__).with_name("agent.sh").read_text(
    encoding="utf-8")


def check(name, ok):
    print(("PASS " if ok else "FAIL ") + name)
    return ok


def main():
    results = [
        check("going-down source path is the guard's file "
              "(SYNCBC_GOING_DOWN_FILE override, fleet default)",
              'GOING_DOWN_FILE="${SYNCBC_GOING_DOWN_FILE:-'
              '/mnt/c/Users/trist/Development/Syncyard/'
              'going-down.json}"' in SRC),
        check("guard heartbeat source path (SYNCBC_GUARD_FILE "
              "override, fleet default)",
              'GUARD_FILE="${SYNCBC_GUARD_FILE:-'
              '/mnt/c/Users/trist/Development/Syncyard/'
              'guard-heartbeat.json}"' in SRC),
        check("write_heartbeat receives both source paths",
              '"$GOING_DOWN_FILE" "$GUARD_FILE"' in SRC),
        check("merge keys are the ingest contract's keys",
              '(("going_down", going_down_path),' in SRC
              and '("guard", guard_path))' in SRC),
        check("only non-empty objects are merged (a cleared "
              "record rides as no field at all)",
              "if isinstance(extra, dict) and extra:" in SRC),
        check("a bad guard file can never fail the heartbeat "
              "(merge errors are swallowed)",
              "except Exception:\n        pass" in SRC),
        check("the merged payload is the pushed heartbeat "
              "(push_channel_files stages heartbeat.json)",
              '"$CHANNEL_REL/out.json" "$CHANNEL_REL/heartbeat.json"'
              in SRC),
        check("the agent still never writes queue records "
              "(spec 009 FR-004 charter line intact)",
              "The agent NEVER writes gatekeeper queue records."
              in SRC),
        check("envelope timeout: non-digit values fall back to "
              "the default (the digit guard is intact)",
              'case "$timeout_s" in *[!0-9]*) '
              'timeout_s="$DEFAULT_TIMEOUT_S" ;; esac' in SRC),
        check("envelope timeout: a 10+ digit value is clamped on "
              "length before any arithmetic (bash -gt errors on "
              "19+ digits and reads as not-clamped)",
              'if [ "${#timeout_s}" -gt 9 ]; then' in SRC),
        check("envelope timeout: 0 falls back to the default "
              "(GNU timeout treats a 0 duration as no timeout, "
              "so a 0 envelope must never reach it)",
              'if [ "$timeout_s" -eq 0 ]; then' in SRC
              and 'timeout_s=0 is not a bound' in SRC),
        check("replay guard: a lost guard file is re-seeded from "
              "out.json before the execution gate compares ids "
              "(FR-004(b) survives the guard file's loss)",
              'json_get "$REPO_DIR/$CHANNEL_REL/out.json" id'
              in SRC
              and "replay guard restored from out.json" in SRC),
        check("channel default: SYNCBC_CHANNEL unset means the "
              "original syncyard-dev channel (PR #66); a second "
              "machine must name its own",
              'CHANNEL_REL="channels/${SYNCBC_CHANNEL:-'
              'syncyard-dev}"' in SRC),
    ]
    print("AGENT_SOURCE %s" % ("OK" if all(results) else "FAILURES"))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
