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
# (default 120s): a stalled pull/push fails the cycle, never the
# heartbeat (field lesson, 2026-10-31: an unbounded pull froze
# the heartbeat for ~13 minutes). Command execution is likewise
# bounded: timeout_s from the envelope plus a SIGKILL escalation
# SYNCBC_KILL_AFTER_S (default 10s) for bodies that ignore
# SIGTERM, and the body is verified and executed byte-exactly
# from a file, never through a shell variable (issue #36).
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
CHANNEL_REL="channels/syncyard-dev"
POLL_S="${SYNCBC_POLL_S:-60}"
DEFAULT_TIMEOUT_S="${SYNCBC_TIMEOUT_S:-600}"
KILL_AFTER_S="${SYNCBC_KILL_AFTER_S:-10}"
GIT_TIMEOUT_S="${SYNCBC_GIT_TIMEOUT_S:-120}"
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
LOG_FILE="$BASE/agent.log"
LAST_ID_FILE="$BASE/.last_executed_id"
SELFTEST_FILE="$BASE/.self_test.json"

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
    python3 - "$REPO_DIR/$CHANNEL_REL/heartbeat.json" "$last_id" "$stopped" "$SELFTEST_FILE" "$GOING_DOWN_FILE" "$GUARD_FILE" <<'PY' 2>/dev/null || true
import json, os, socket, sys, datetime
path, last_id, stopped, selftest_path = sys.argv[1], sys.argv[2], sys.argv[3] == "1", sys.argv[4]
going_down_path, guard_path = sys.argv[5], sys.argv[6]
data = {
    "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "host": socket.gethostname(),
    "user": os.environ.get("USER", ""),
    "agent": "syncyard-backchannel",
    "version": "0.2.0",
    "last_executed_id": last_id or None,
    "stopped": stopped,
}
try:
    with open(selftest_path, encoding="utf-8") as fh:
        data["self_test"] = json.load(fh)
except Exception:
    pass
# Spec 011 Phase 3: merge the guard's records verbatim. A
# missing, unreadable, or non-object file is skipped — the
# heartbeat never fails because a guard file is absent or
# mid-write (the guard writes with temp + rename, so a read
# here sees either the old record or the new one, whole).
for key, src in (("going_down", going_down_path),
                 ("guard", guard_path)):
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
    # Start-up self-test (spec 010 T010): tools present, base and
    # log writable, channel repo reachable, channel dir writable.
    # The result is written to .self_test.json and rides every
    # heartbeat until the next start. A failed check never stops
    # the agent - it reports, so the failure is visible remotely.
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

push_channel_files() {
    # Commit + push out.json / heartbeat.json if they changed.
    git -C "$REPO_DIR" add -- "$CHANNEL_REL/out.json" "$CHANNEL_REL/heartbeat.json" 2>/dev/null || return 0
    if git -C "$REPO_DIR" diff --cached --quiet 2>/dev/null; then
        return 0
    fi
    git -C "$REPO_DIR" commit -q -m "backchannel: agent update $(date -u +%Y-%m-%dT%H:%M:%SZ)" >> "$LOG_FILE" 2>&1 || return 0
    if ! timeout "$GIT_TIMEOUT_S" git -C "$REPO_DIR" push -q origin HEAD >> "$LOG_FILE" 2>&1; then
        log "push failed; will retry next cycle"
        timeout "$GIT_TIMEOUT_S" git -C "$REPO_DIR" pull --rebase -q >> "$LOG_FILE" 2>&1 || true
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
    run_self_test
    while true; do
        if ! ensure_repo; then
            log "clone failed; retrying next cycle"
            sleep "$POLL_S"
            continue
        fi
        mkdir -p "$REPO_DIR/$CHANNEL_REL"
        timeout "$GIT_TIMEOUT_S" git -C "$REPO_DIR" pull -q --ff-only >> "$LOG_FILE" 2>&1 || log "pull failed (continuing with local state)"

        local stopped=0
        if [ -f "$REPO_DIR/$CHANNEL_REL/STOP" ]; then
            stopped=1
            log "STOP file present; execution halted, heartbeat continues"
        fi

        if [ "$stopped" -eq 0 ] && [ -f "$REPO_DIR/$CHANNEL_REL/cmd.json" ]; then
            local cid last_id=""
            cid="$(json_get "$REPO_DIR/$CHANNEL_REL/cmd.json" id)"
            [ -f "$LAST_ID_FILE" ] && last_id="$(cat "$LAST_ID_FILE" 2>/dev/null)"
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
