#!/usr/bin/env bash
# agent.sh - Syncyard failsafe backchannel agent (spec 009 FR-002/FR-004).
#
# Runs as a WSL systemd user service (syncyard-backchannel.service)
# under tpierson on TRISTEN-DESKTOP. Independent of the Actions
# runners, the Syncyard tray, and the gatekeeper.
#
# Loop (~60s): git pull the PRIVATE repo tbitcs/syncyard-backchannel,
# check the STOP kill switch, execute channels/syncyard-dev/cmd.json
# only if its id is new (and its sha256 matches its body), write
# channels/syncyard-dev/out.json matched by id, write heartbeat.json,
# commit + push out.json and heartbeat.json. Sync is over the git
# protocol only - this loop burns no GitHub REST quota. Every
# network git operation is bounded by SYNCBC_GIT_TIMEOUT_S
# (default 45s), and the heartbeat is written at the TOP of every
# cycle, before any git operation, as well as at the end: liveness
# never waits on sync (field lessons: 2026-10-31, an unbounded pull
# froze the heartbeat for ~13 minutes; 2026-11-03, four bounded
# 120s git timeouts stacked in one post-reboot cycle and stretched
# the beat to 904s, past the tray's 900s down threshold, while the
# channel itself worked - the tray honestly reported a heartbeat
# gap the agent had no business producing). Command execution is likewise
# bounded: timeout_s from the envelope plus a SIGKILL escalation
# SYNCBC_KILL_AFTER_S (default 10s) for bodies that ignore
# SIGTERM, and the body is verified and executed byte-exactly
# from a file, never through a shell variable (issue #36).
# 0.2.2 (2026-11-03, red team on the fleet-status publisher):
# sync is its own step with its own recovery — an ff-only pull
# that fails falls back to a rebase pull in the same cycle,
# a failed rebase is ABORTED (a tree left mid-rebase wedges
# every later sync behind confusing errors), and the outcome
# rides the heartbeat as `sync` {ok, ts}, so a dead inbound
# path is visible instead of silent. The envelope's timeout_s
# is clamped agent-side to SYNCBC_MAX_TIMEOUT_S (default 600):
# the sender's number is a request, not a bound — an unbounded
# one stretches the heartbeat gap past every tray threshold
# while the channel works (the 0.2.1 fix bounded git, not the
# dominant term). The startup self-test and the periodic
# refresh now run AFTER a heartbeat write, so the header's
# "heartbeat before any git operation" holds on those cycles
# too — the self-test's own ls-remote is a git operation.
#
# The agent NEVER writes gatekeeper queue records. Queue access from
# here is read-only status unless a command explicitly directs
# otherwise (spec 009 FR-004).
#
# Layout:
#   ~/syncyard-backchannel/agent.sh    this file (installed copy)
#   ~/syncyard-backchannel/repo/       the channel repo clone
#   ~/syncyard-backchannel/agent.log   full local log
#   ~/syncyard-backchannel/.last_executed_id   local replay guard

set -u

BASE="${SYNCBC_BASE:-$HOME/syncyard-backchannel}"
REPO_DIR="${SYNCBC_REPO_DIR:-$BASE/repo}"
REPO_URL="${SYNCBC_REPO_URL:-https://github.com/tbitcs/syncyard-backchannel.git}"
# One channel per machine (2026-11-04): SYNCBC_CHANNEL names this
# host's channel under channels/ in the backchannel repo. The desktop
# sets nothing and keeps the original syncyard-dev channel; a second
# host must set its own (for example syncyard-laptop) so two agents
# never share cmd/out/heartbeat state.
CHANNEL_REL="channels/${SYNCBC_CHANNEL:-syncyard-dev}"
POLL_S="${SYNCBC_POLL_S:-60}"
DEFAULT_TIMEOUT_S="${SYNCBC_TIMEOUT_S:-600}"
MAX_TIMEOUT_S="${SYNCBC_MAX_TIMEOUT_S:-600}"
KILL_AFTER_S="${SYNCBC_KILL_AFTER_S:-10}"
GIT_TIMEOUT_S="${SYNCBC_GIT_TIMEOUT_S:-45}"
SELFTEST_EVERY="${SYNCBC_SELFTEST_EVERY:-15}"
TAIL_BYTES="${SYNCBC_TAIL_BYTES:-8000}"
# Spec 011 Phase 3 (the going-down writer's transport): the host
# guard writes its going-down record and its guard heartbeat as
# files on the Windows side; write_heartbeat merges both into
# the heartbeat payload verbatim (as `going_down` / `guard`)
# every cycle. This is the fastest channel the gatekeeper's
# Phase 1 ingest contract supports — the sweep already reads
# exactly this payload once per pass, so the record rides a
# read the fleet already spends, over git protocol only. The
# agent never interprets either record: the gatekeeper dedupes
# going-down records per (host, ts) and judges guard staleness
# itself, so a stale file is harmless here.
GOING_DOWN_FILE="${SYNCBC_GOING_DOWN_FILE:-/mnt/c/Users/trist/Development/Syncyard/going-down.json}"
GUARD_FILE="${SYNCBC_GUARD_FILE:-/mnt/c/Users/trist/Development/Syncyard/guard-heartbeat.json}"
# Spec 025 (tray liveness): the tray writes its heartbeat file
# beside its log; write_heartbeat merges it into the heartbeat
# payload verbatim (as `tray`) every cycle, exactly like the
# guard's records. The gatekeeper judges tray staleness from
# it (agent alive + tray beat stale = the tray is dead on a
# live host). A missing or unreadable tray file is skipped —
# the heartbeat never fails because the tray is absent; that
# absence is precisely what the gatekeeper needs to see.
TRAY_FILE="${SYNCBC_TRAY_FILE:-/mnt/c/Users/trist/AppData/Local/SyncyardTray/tray-heartbeat.json}"
LOG_FILE="$BASE/agent.log"
LAST_ID_FILE="$BASE/.last_executed_id"
SELFTEST_FILE="$BASE/.self_test.json"
SYNC_FILE="$BASE/.sync_state.json"

mkdir -p "$BASE"

log() {
    printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >> "$LOG_FILE" 2>/dev/null || true
}

json_get() {
    # json_get <file> <key> - print a string/number field or empty.
    python3 - "$1" "$2" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as fh:
        data = json.load(fh)
    val = data.get(sys.argv[2], "")
    print("" if val is None else val)
except Exception:
    print("")
PY
}

write_heartbeat() {
    # write_heartbeat <stopped 0|1>
    local stopped="$1"
    local last_id=""
    [ -f "$LAST_ID_FILE" ] && last_id="$(cat "$LAST_ID_FILE" 2>/dev/null)"
    python3 - "$REPO_DIR/$CHANNEL_REL/heartbeat.json" "$last_id" "$stopped" "$SELFTEST_FILE" "$GOING_DOWN_FILE" "$GUARD_FILE" "$SYNC_FILE" "$TRAY_FILE" <<'PY' 2>/dev/null || true
import json, os, socket, sys, datetime
path, last_id, stopped, selftest_path = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
going_down_path, guard_path, sync_path = sys.argv[5], sys.argv[6], sys.argv[7]
tray_path = sys.argv[8]
data = {
    "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "host": socket.gethostname(),
    "user": os.environ.get("USER", ""),
    "agent": "syncyard-backchannel",
    "version": "0.2.2",
    "last_executed_id": last_id or None,
    "stopped": stopped,
}
try:
    with open(selftest_path, encoding="utf-8") as fh:
        data["self_test"] = json.load(fh)
except Exception:
    pass
try:
    with open(sync_path, encoding="utf-8") as fh:
        data["sync"] = json.load(fh)
except Exception:
    pass
# Spec 011 Phase 3: merge the guard's records verbatim. A
# missing, unreadable, or non-object file is skipped — the
# heartbeat never fails because a guard file is absent or
# mid-write (the guard writes with temp + rename, so a read
# here sees either the old record or the new one, whole).
# Spec 025: the tray's heartbeat file rides the same merge
# under `tray` (the tray also writes with temp + replace).
for key, src in (("going_down", going_down_path),
                 ("guard", guard_path),
                 ("tray", tray_path)):
    try:
        with open(src, encoding="utf-8") as fh:
            extra = json.load(fh)
        if isinstance(extra, dict) and extra:
            data[key] = extra
    except Exception:
        pass
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=1, sort_keys=True)
    fh.write("\n")
os.replace(tmp, path)
PY
}

run_self_test() {
    # Self-test (spec 010 T010): tools present, base and log
    # writable, channel repo reachable, channel dir writable. Runs
    # at start-up and again every SELFTEST_EVERY cycles, so a
    # transient failure read during a chaotic boot (2026-11-03:
    # repo_reachable=0 while the network was still settling) clears
    # itself instead of riding every heartbeat until the next
    # start. The result is written to .self_test.json and rides
    # every heartbeat. A failed check never stops the agent - it
    # reports, so the failure is visible remotely.
    local git_ok=0 python_ok=0 base_ok=0 log_ok=0 repo_ok=0 channel_ok=0
    command -v git >/dev/null 2>&1 && git_ok=1
    command -v python3 >/dev/null 2>&1 && python_ok=1
    [ -w "$BASE" ] && base_ok=1
    ( printf '' >> "$LOG_FILE" ) 2>/dev/null && log_ok=1
    if [ -d "$REPO_DIR/.git" ]; then
        timeout "$GIT_TIMEOUT_S" git -C "$REPO_DIR" ls-remote -q origin HEAD >/dev/null 2>&1 && repo_ok=1
        [ -w "$REPO_DIR/$CHANNEL_REL" ] && channel_ok=1
    fi
    python3 - "$SELFTEST_FILE" "$git_ok" "$python_ok" "$base_ok" "$log_ok" "$repo_ok" "$channel_ok" <<'PY' 2>/dev/null || true
import datetime, json, os, sys
path = sys.argv[1]
checks = {
    "git": sys.argv[2] == "1",
    "python3": sys.argv[3] == "1",
    "base_writable": sys.argv[4] == "1",
    "log_writable": sys.argv[5] == "1",
    "repo_reachable": sys.argv[6] == "1",
    "channel_writable": sys.argv[7] == "1",
}
data = {
    "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "ok": all(checks.values()),
    "checks": checks,
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=1, sort_keys=True)
    fh.write("\n")
os.replace(tmp, path)
PY
    log "self-test written (ok checks: git=$git_ok python3=$python_ok base=$base_ok log=$log_ok repo=$repo_ok channel=$channel_ok)"
}

write_out() {
    # write_out <id> <exit_code> <started> <stdout_file> <stderr_file>
    python3 - "$REPO_DIR/$CHANNEL_REL/out.json" "$1" "$2" "$3" "$4" "$5" "$TAIL_BYTES" <<'PY' 2>/dev/null || true
import datetime, json, os, sys
path, cid, rc, started, out_f, err_f, tail = sys.argv[1:8]

def tail_text(p, n):
    try:
        with open(p, "rb") as fh:
            data = fh.read()
        return data[-int(n):].decode("utf-8", "replace")
    except Exception:
        return ""

data = {
    "id": cid,
    "exit_code": int(rc),
    "state": "completed",
    "started_at": started,
    "finished_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "stdout_tail": tail_text(out_f, tail),
    "stderr_tail": tail_text(err_f, tail),
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=1, sort_keys=True)
    fh.write("\n")
os.replace(tmp, path)
PY
}

note_sync() {
    # note_sync <ok 0|1> — the sync step's outcome, merged into
    # the heartbeat by write_heartbeat so a failing inbound
    # path is visible on the tray's own surface.
    local ok="$1"
    python3 - "$SYNC_FILE" "$ok" <<'PY' 2>/dev/null || true
import json, os, sys, datetime
path, ok = sys.argv[1], sys.argv[2] == "1"
data = {"ok": ok,
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as fh:
    json.dump(data, fh, indent=1, sort_keys=True)
    fh.write("\n")
os.replace(tmp, path)
PY
}

abort_rebase_if_any() {
    # A failed rebase must never leave the tree mid-rebase —
    # but aborting when none is in progress only adds fatal
    # noise to the log, so check the state dirs first.
    if [ -d "$REPO_DIR/.git/rebase-merge" ] \
        || [ -d "$REPO_DIR/.git/rebase-apply" ]; then
        git -C "$REPO_DIR" rebase --abort >> "$LOG_FILE" 2>&1 || true
    fi
}

sync_repo() {
    # One inbound sync step with its own recovery (0.2.2):
    # ff-only first; on failure (the normal case once a local
    # commit is unpushed and the remote has moved — the
    # fleet-status publisher moves it every 2 minutes), fall
    # back to a rebase pull in the same cycle; a failed rebase
    # is aborted so the tree is never left mid-rebase. The
    # outcome is noted for the heartbeat either way. The
    # rebase needs --autostash: the top-of-cycle heartbeat
    # write has always dirtied the tree by pull time, and a
    # plain rebase pull refuses a dirty tree outright (found
    # by chaos scenario 13, not by reading).
    if timeout "$GIT_TIMEOUT_S" git -C "$REPO_DIR" pull -q --ff-only >> "$LOG_FILE" 2>&1; then
        note_sync 1
        return 0
    fi
    log "pull ff-only failed; attempting rebase recovery"
    if timeout "$GIT_TIMEOUT_S" git -C "$REPO_DIR" pull -q --rebase --autostash >> "$LOG_FILE" 2>&1; then
        log "pull recovered via rebase"
        note_sync 1
        return 0
    fi
    abort_rebase_if_any
    log "pull failed (continuing with local state)"
    note_sync 0
    return 1
}

push_channel_files() {
    # Commit + push out.json / heartbeat.json if they changed.
    git -C "$REPO_DIR" add -- "$CHANNEL_REL/out.json" "$CHANNEL_REL/heartbeat.json" 2>/dev/null || return 0
    if git -C "$REPO_DIR" diff --cached --quiet 2>/dev/null; then
        return 0
    fi
    git -C "$REPO_DIR" commit -q -m "backchannel: agent update $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG_FILE" 2>&1 || return 0
    if ! timeout "$GIT_TIMEOUT_S" git -C "$REPO_DIR" push -q origin HEAD >> "$LOG_FILE" 2>&1; then
        log "push failed; will retry next cycle"
        if ! timeout "$GIT_TIMEOUT_S" git -C "$REPO_DIR" pull --rebase -q >> "$LOG_FILE" 2>&1; then
            # Never leave the tree mid-rebase (0.2.2).
            abort_rebase_if_any
        fi
        timeout "$GIT_TIMEOUT_S" git -C "$REPO_DIR" push -q origin HEAD >> "$LOG_FILE" 2>&1 || log "push retry failed"
    fi
}

ensure_repo() {
    if [ -d "$REPO_DIR/.git" ]; then
        return 0
    fi
    log "cloning $REPO_URL into $REPO_DIR"
    timeout $((GIT_TIMEOUT_S * 2)) git clone -q "$REPO_URL" "$REPO_DIR" >> "$LOG_FILE" 2>&1
}

execute_command() {
    # execute_command <cmd.json path>
    local cmd_file="$1"
    local cid claimed_sha timeout_s started rc
    cid="$(json_get "$cmd_file" id)"
    claimed_sha="$(json_get "$cmd_file" sha256)"
    timeout_s="$(json_get "$cmd_file" timeout_s)"
    [ -z "$timeout_s" ] && timeout_s="$DEFAULT_TIMEOUT_S"
    case "$timeout_s" in *[!0-9]*) timeout_s="$DEFAULT_TIMEOUT_S" ;; esac
    # A digit string too long for bash arithmetic defeats the
    # clamp below outright: [ -gt ] errors "integer expression
    # expected" (rc 2, read as false) on a 19+ digit value, and
    # GNU timeout parses the same string as an effectively
    # unbounded double. Any timeout with 10+ digits is beyond
    # every sane bound by construction, so clamp it on length
    # before any arithmetic touches it.
    if [ "${#timeout_s}" -gt 9 ]; then
        log "timeout_s=$timeout_s clamped to SYNCBC_MAX_TIMEOUT_S=$MAX_TIMEOUT_S"
        timeout_s="$MAX_TIMEOUT_S"
    fi
    # Zero is not a bound either: GNU timeout treats a duration
    # of 0 as "no timeout", so a 0 envelope would run unbounded
    # and stall this loop (and its heartbeat) behind one body.
    # Fall back to the default timeout instead.
    if [ "$timeout_s" -eq 0 ]; then
        log "timeout_s=0 is not a bound (GNU timeout disables a 0 duration); using SYNCBC_TIMEOUT_S=$DEFAULT_TIMEOUT_S"
        timeout_s="$DEFAULT_TIMEOUT_S"
    fi
    # The envelope's timeout is a request, not a bound (0.2.2):
    # clamp it agent-side so no sender can stretch the
    # heartbeat gap past the tray's thresholds with one
    # oversized value.
    if [ "$timeout_s" -gt "$MAX_TIMEOUT_S" ]; then
        log "timeout_s=$timeout_s clamped to SYNCBC_MAX_TIMEOUT_S=$MAX_TIMEOUT_S"
        timeout_s="$MAX_TIMEOUT_S"
    fi

    # Extract the body byte-exactly to the command file. The body
    # must never pass through a bash variable or $(...): command
    # substitution strips trailing newlines, so the agent hashed
    # (and ran) different bytes than the ones bc.py signed -
    # every `send --file` of an ordinary file, which ends in a
    # newline, was sha-refused (issue #36). Python writes the
    # exact UTF-8 bytes; the sha256 and the execution below both
    # work on that file. The other json_get fields (id, sha256,
    # timeout_s) are scalars whose values never end in a newline,
    # so the print/$(...) round-trip is exact for them.
    local body_file="$BASE/.current-command.sh"
    if ! python3 - "$cmd_file" "$body_file" <<'PY' 2>/dev/null
import json, sys
with open(sys.argv[1], encoding="utf-8") as fh:
    body = json.load(fh).get("body", "")
data = body.encode("utf-8") if isinstance(body, str) else b""
with open(sys.argv[2], "wb") as fh:
    fh.write(data)
sys.exit(0 if data else 1)
PY
    then
        log "cmd.json unreadable or empty body; skipping"
        rm -f "$body_file"
        return 0
    fi
    if [ -z "$cid" ]; then
        log "cmd.json unreadable or empty id; skipping"
        rm -f "$body_file"
        return 0
    fi

    local actual_sha
    actual_sha="$(sha256sum "$body_file" | awk '{print $1}')"
    started="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    local out_tmp err_tmp
    out_tmp="$(mktemp)"; err_tmp="$(mktemp)"

    if [ -n "$claimed_sha" ] && [ "$claimed_sha" != "$actual_sha" ]; then
        log "SHA MISMATCH id=$cid claimed=$claimed_sha actual=$actual_sha - not executing"
        printf 'backchannel: sha256 mismatch, command not executed\n' > "$err_tmp"
        write_out "$cid" 65 "$started" "$out_tmp" "$err_tmp"
        printf '%s' "$cid" > "$LAST_ID_FILE"
        rm -f "$out_tmp" "$err_tmp" "$body_file"
        return 0
    fi

    log "executing id=$cid timeout_s=$timeout_s sha256=$actual_sha"
    # --kill-after bounds a body that ignores SIGTERM: plain
    # timeout sends TERM and then waits for the body to die, and
    # a trapping body defers TERM until its current child exits
    # (measured: a TERM-ignoring `sleep 60` under timeout_s=3
    # ran its full 60s, stalling this single-threaded loop and
    # its heartbeat). After KILL_AFTER_S the wrapper escalates
    # to SIGKILL and timeout reports 137 instead of 124; both
    # are the wrapper's honest kill, never the body's own 0.
    timeout --kill-after="$KILL_AFTER_S" "$timeout_s" bash "$body_file" > "$out_tmp" 2> "$err_tmp"
    rc=$?
    log "finished id=$cid exit_code=$rc"
    write_out "$cid" "$rc" "$started" "$out_tmp" "$err_tmp"
    printf '%s' "$cid" > "$LAST_ID_FILE"
    rm -f "$out_tmp" "$err_tmp" "$body_file"
    return 0
}

main_loop() {
    log "agent starting (base=$BASE repo=$REPO_DIR poll_s=$POLL_S)"
    ensure_repo || log "initial clone failed; self-test will report repo unreachable"
    # The first beat lands BEFORE the startup self-test (0.2.2):
    # the self-test's own ls-remote is a git operation, and the
    # header's rule is heartbeat before any git operation.
    local stopped=0
    if [ -f "$REPO_DIR/$CHANNEL_REL/STOP" ]; then
        stopped=1
    fi
    mkdir -p "$REPO_DIR/$CHANNEL_REL" 2>/dev/null || true
    write_heartbeat "$stopped"
    run_self_test
    local cycle=0
    while true; do
        if ! ensure_repo; then
            log "clone failed; retrying next cycle"
            sleep "$POLL_S"
            continue
        fi
        mkdir -p "$REPO_DIR/$CHANNEL_REL"
        cycle=$((cycle + 1))

        stopped=0
        if [ -f "$REPO_DIR/$CHANNEL_REL/STOP" ]; then
            stopped=1
        fi

        # Heartbeat FIRST, before any git operation (2026-11-03
        # field lesson in the header): a cycle whose sync calls
        # all time out must not stretch the beat - the tray reads
        # this file's ts as the host's connection signal. This
        # beat's stopped flag is the pre-pull snapshot and can lag
        # one cycle; the end-of-cycle beat corrects it. The
        # periodic self-test refresh runs AFTER this beat for
        # the same reason (0.2.2); its result rides the
        # end-of-cycle beat.
        write_heartbeat "$stopped"

        if [ "$cycle" -gt 1 ] && [ $(( (cycle - 1) % SELFTEST_EVERY )) -eq 0 ]; then
            run_self_test
        fi

        sync_repo || true

        # The execution gate re-reads STOP on the POST-pull state:
        # a STOP that lands in the same pull as a command must
        # still halt that command (chaos scenario 5 pins this -
        # the kill switch may never be snapshotted pre-pull).
        stopped=0
        if [ -f "$REPO_DIR/$CHANNEL_REL/STOP" ]; then
            stopped=1
            log "STOP file present; execution halted, heartbeat continues"
        fi

        if [ "$stopped" -eq 0 ] && [ -f "$REPO_DIR/$CHANNEL_REL/cmd.json" ]; then
            local cid last_id=""
            cid="$(json_get "$REPO_DIR/$CHANNEL_REL/cmd.json" id)"
            [ -f "$LAST_ID_FILE" ] && last_id="$(cat "$LAST_ID_FILE" 2>/dev/null)"
            if [ -z "$last_id" ] && [ -f "$REPO_DIR/$CHANNEL_REL/out.json" ]; then
                # The replay guard file is gone (base wiped, a
                # reinstall that kept the clone) but the channel
                # still carries the last result: seed the guard
                # from out.json, whose id only ever names a
                # command that already ran or was refused, so
                # FR-004(b) - a re-pushed old command never
                # re-runs - survives the guard file's loss.
                # Restoring the file makes the repair durable.
                last_id="$(json_get "$REPO_DIR/$CHANNEL_REL/out.json" id)"
                if [ -n "$last_id" ]; then
                    printf '%s' "$last_id" > "$LAST_ID_FILE"
                    log "replay guard restored from out.json (last_executed_id=$last_id)"
                fi
            fi
            if [ -n "$cid" ] && [ "$cid" != "$last_id" ]; then
                execute_command "$REPO_DIR/$CHANNEL_REL/cmd.json" || log "execute_command error (continuing)"
            fi
        fi

        write_heartbeat "$stopped"
        push_channel_files
        sleep "$POLL_S"
    done
}

main_loop
