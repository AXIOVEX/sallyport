#!/usr/bin/env python3
"""Syncyard configuration loader (spec 010 FR-001/FR-002/FR-007).

Stdlib only, like the rest of the gatekeeper. Reads
`syncyard.yaml` (or a path given by the caller / SYNCARD_CONFIG)
and produces the effective configuration: file values where
present, built-in defaults everywhere else. The defaults ARE
the pre-config constants, so a fleet with no config file
behaves exactly as before.

The YAML support is a deliberate subset — nested block maps,
block scalar lists, inline [a, b] lists, and scalar values
(string / int / float / bool / null), with # comments. Anything
outside the subset raises ConfigError naming the line: the
loader fails closed, it never guesses.

Validation (load time, all named errors):
  - version, when present, must be 1
  - lane names unique (a mapping cannot repeat a key at all —
    the parser rejects duplicate keys in any map)
  - every lane's host exists in hosts
  - watermark pairs are (warn, escalate) with warn < escalate
  - executing mode is fixed (needs values) or expectation
    (needs floor)
  - budget critical < standdown
  - transport ceiling positive
  - liveness threshold (spec 011 FR-001) a positive number
  - reboot grace (spec 011 FR-015) a positive number
  - checkpoint contract (spec 013 FR-007): exit code an integer
    in 1..255, marker a valid marker token, env var a valid
    name, holds_dir a mapping of declared workflows to
    non-empty venue directories
  - drain (spec 011 FR-005/FR-007): the host drain window a
    positive number of seconds, per-lane overrides keyed by
    declared lanes with positive values, and the guard
    staleness bound a positive number of seconds
  - lane lock TTLs (spec 015 FR-011): default and maximum TTL
    integers, positive, with default <= maximum
"""
import os
import re

CONFIG_FILENAME = "syncyard.yaml"
ENV_VAR = "SYNCARD_CONFIG"


class ConfigError(Exception):
    pass


# --- Built-in defaults: the pre-config constants, verbatim. ---
DEFAULTS = {
    "version": 1,
    "door_repo": "tbitcs/desktop-runner",
    "project_repo": "AXIOVEX/syncyard",
    "budget": {
        "standdown_remaining": 1000,
        "critical_remaining": 500,
        "reserve_for_completion": 0,
    },
    "dispatch": {
        "max_active_dispatched": 2,
        "stale_dispatching_min": 10,
        "lock_stale_min": 10,
        "duplicate_policy": "refuse",
        "soft_cap_min": 30,
        # Priority optimizer (spec 013 FR-012): auto-assigned
        # effective priorities never exceed this ceiling (a
        # human-set priority above it is unreachable by any
        # auto job), and an auto job gains +1 per this many
        # minutes waited.
        "optimizer_ceiling": 100,
        "optimizer_aging_step_min": 10,
    },
    "transport": {"max_script_bytes": 49152},
    "markers": {"pattern": r"^[A-Z][A-Z0-9_]{2,}([ :]|$)"},
    # Host liveness (spec 011 FR-001): the gatekeeper reads each
    # host's heartbeat on its sweep cadence and keeps its own
    # last-seen record; a host whose heartbeat is older than this
    # many seconds is shown DOWN.
    "liveness": {"down_after_s": 900},
    # Controlled reboot (spec 011 FR-015): `gk reboot` signals
    # the host OS with this grace delay in seconds unless
    # --delay overrides it, so in-flight uploads and log
    # writes finish before the host goes down.
    "reboot": {"grace_s": 60},
    # Checkpoint hold (spec 013 FR-007): the cooperative
    # job-level pause contract. A dispatch passes each script
    # job the venue path of its per-job hold file (holds_dir
    # per workflow + "<job-id>.hold"); the transport workflow
    # exports that path to the job's script as the env var
    # named here. A job that polls the file stops at its next
    # checkpoint, prints the marker, and exits with exit_code.
    "checkpoint": {
        "exit_code": 86,
        "marker": "CHECKPOINT_PAUSED",
        "env_var": "SYNCYARD_HOLD_FILE",
        "holds_dir": {
            "run.yml": r"C:\Users\trist\Development\Syncyard\holds",
            "wsl.yml": "/home/tpierson/.syncyard/holds",
        },
    },
    # Host drain (spec 011 FR-005/FR-007): `gk drain --host`
    # holds dispatch to one host, signals its running
    # checkpoint-capable jobs, and waits out the window —
    # per lane when a lane overrides — before printing its
    # CLEAN / INTERRUPTING verdict. guard_stale_after_s bounds
    # the guard heartbeat the backchannel agent merges into
    # its heartbeat payload: a guard beat older than this
    # (relative to the heartbeat carrying it) is stale and is
    # ledgered + shown in status (spec 011 T007: a stopped
    # guard is never a silent loss of protection).
    "drain": {
        "window_s": 120,
        "per_lane_window_s": {},
        "guard_stale_after_s": 180,
    },
    # Lane priority lock (spec 015 FR-007/FR-011): every lane
    # lock carries a TTL so a forgotten claim cannot squat a
    # lane forever. A lock taken without --ttl-min gets the
    # default; no lock may exceed the maximum.
    "lock": {
        "default_ttl_min": 240,
        "max_ttl_min": 1440,
    },
    "hosts": {
        "desktop": {
            "type": "windows",
            "workflows": {"run.yml": "execute", "wsl.yml": "execute",
                          "grab.yml": "grab"},
            "identity": ["LID_HOST", "LID_WINUSER", "LID_OS"],
            "cwd": {"run.yml": r"C:\Users\trist", "grab.yml": None},
        },
        "desktop-wsl": {
            "type": "wsl2",
            "via": "desktop",
            "distro": "Ubuntu-24.04",
            "workflows": {"wsl.yml": "execute", "grab.yml": "grab"},
            "identity": ["LID_HOST", "LID_WSLUSER", "LID_OS"],
            "cwd": {"wsl.yml": "/home/tpierson"},
        },
    },
    "workflows": ["run.yml", "wsl.yml", "grab.yml"],
    "lanes": {
        "quick": {"host": "desktop", "runner_label": "lane-quick",
                  "concurrency": 1, "queued": [10, 30],
                  "executing": {"mode": "fixed", "values": [5, 15]}},
        "normal": {"host": "desktop", "runner_label": "lane-normal",
                   "concurrency": 1, "queued": [20, 45],
                   "executing": {"mode": "fixed", "values": [45, 90]}},
        "long": {"host": "desktop", "runner_label": "lane-long",
                 "concurrency": 1, "queued": [45, 120],
                 "executing": {"mode": "expectation", "floor": [90, 180]}},
        "gpu": {"host": "desktop", "runner_label": "lane-gpu",
                "concurrency": 1, "queued": [30, 90],
                "executing": {"mode": "expectation", "floor": [120, 240]}},
    },
    "endpoints": {},
    "retention": {"human_log_max_bytes": 1000000},
}

HOST_TYPES = ("windows", "wsl2", "macos", "linux", "docker")

# The identity-marker contract per host type (FR-003): the markers a
# venue of this type proves itself with. A host may name its own
# `identity` list; it must be non-empty.
IDENTITY_BY_TYPE = {
    "windows": ["LID_HOST", "LID_WINUSER", "LID_OS"],
    "wsl2": ["LID_HOST", "LID_WSLUSER", "LID_OS"],
    "macos": ["LID_HOST", "LID_USER", "LID_OS"],
    "linux": ["LID_HOST", "LID_USER", "LID_OS"],
    "docker": ["LID_HOST", "LID_IMAGE", "LID_OS"],
}

WORKFLOW_KINDS = ("execute", "grab")


# --- Restricted YAML-subset parser --------------------------------

def _scalar(tok, lineno):
    tok = tok.strip()
    if tok == "":
        return None
    if tok == "{}":
        return {}
    if tok.startswith("[") and tok.endswith("]"):
        inner = tok[1:-1].strip()
        if not inner:
            return []
        return [_scalar(part, lineno) for part in _split_inline(inner, lineno)]
    if tok.startswith('"') and tok.endswith('"') and len(tok) >= 2:
        return tok[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if tok.startswith("'") and tok.endswith("'") and len(tok) >= 2:
        return tok[1:-1].replace("''", "'")
    low = tok.lower()
    if low in ("null", "~"):
        return None
    if low in ("true", "false"):
        return low == "true"
    try:
        return int(tok)
    except ValueError:
        pass
    try:
        return float(tok)
    except ValueError:
        pass
    if ":" in tok and not tok.startswith(("http://", "https://")):
        # A bare colon inside an unquoted scalar is outside the subset;
        # paths like C:\Users are fine (no colon-space), URLs exempted.
        if ": " in tok:
            raise ConfigError(f"line {lineno}: unquoted scalar contains ': ' — quote it")
    return tok


def _split_inline(inner, lineno):
    parts, depth, cur, quote = [], 0, "", None
    for ch in inner:
        if quote:
            cur += ch
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            cur += ch
        elif ch == "[":
            depth += 1
            cur += ch
        elif ch == "]":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            parts.append(cur)
            cur = ""
        else:
            cur += ch
    if quote:
        raise ConfigError(f"line {lineno}: unterminated quote in inline list")
    parts.append(cur)
    return parts


def parse_yaml_subset(text):
    """Parse the restricted subset into nested dicts/lists."""
    lines = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        stripped = _strip_comment(raw).rstrip()
        if not stripped.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if "\t" in raw[:indent + 1]:
            raise ConfigError(f"line {lineno}: tab indentation is not supported")
        lines.append((indent, stripped.strip(), lineno))
    if not lines:
        return {}
    pos = [0]

    def parse_block(indent):
        # Decide map vs list by the first line at this indent.
        first = lines[pos[0]]
        if first[1].startswith("- "):
            out = []
            while pos[0] < len(lines):
                ind, content, lineno = lines[pos[0]]
                if ind != indent or not content.startswith("- "):
                    break
                item = content[2:].strip()
                pos[0] += 1
                if item == "":
                    out.append(parse_block(_next_indent(indent)))
                elif ":" in item and not item.startswith(('"', "'")):
                    # Inline first pair of a map inside a list item:
                    # outside the subset on purpose.
                    raise ConfigError(
                        f"line {lineno}: maps inside list items are outside the supported subset")
                else:
                    out.append(_scalar(item, lineno))
            return out
        out = {}
        while pos[0] < len(lines):
            ind, content, lineno = lines[pos[0]]
            if ind != indent or content.startswith("- "):
                break
            if ":" not in content:
                raise ConfigError(f"line {lineno}: expected 'key: value'")
            key, _, rest = content.partition(":")
            key = key.strip().strip('"').strip("'")
            if key in out:
                raise ConfigError(f"line {lineno}: duplicate key {key!r}")
            rest = rest.strip()
            pos[0] += 1
            if rest == "":
                if pos[0] < len(lines) and lines[pos[0]][0] > indent:
                    out[key] = parse_block(lines[pos[0]][0])
                else:
                    out[key] = None
            else:
                out[key] = _scalar(rest, lineno)
        return out

    def _next_indent(indent):
        if pos[0] < len(lines):
            return lines[pos[0]][0]
        return indent

    result = parse_block(lines[0][0])
    if pos[0] != len(lines):
        raise ConfigError(f"line {lines[pos[0]][2]}: inconsistent indentation")
    if not isinstance(result, dict):
        raise ConfigError("top level of the config must be a mapping")
    return result


def _strip_comment(raw):
    """Remove a trailing # comment, honoring single/double quotes."""
    out, quote = [], None
    for ch in raw:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out)


# --- Merge + validation -------------------------------------------

def _deep_merge(base, over):
    if isinstance(base, dict) and isinstance(over, dict):
        out = dict(base)
        for k, v in over.items():
            out[k] = _deep_merge(base.get(k), v) if k in base else v
        return out
    return over


def _check_pair(value, where):
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(x, (int, float)) for x in value)):
        raise ConfigError(f"{where}: watermark must be [warn, escalate] in minutes")
    if not value[0] < value[1]:
        raise ConfigError(f"{where}: warn ({value[0]}) must be < escalate ({value[1]})")


def validate(cfg):
    if cfg.get("version") != 1:
        raise ConfigError(f"version must be 1, got {cfg.get('version')!r}")
    budget = cfg.get("budget") or {}
    if not (budget.get("critical_remaining", 0) < budget.get("standdown_remaining", 0)):
        raise ConfigError("budget.critical_remaining must be < budget.standdown_remaining")
    if (cfg.get("transport") or {}).get("max_script_bytes", 0) <= 0:
        raise ConfigError("transport.max_script_bytes must be positive")
    down_after = (cfg.get("liveness") or {}).get("down_after_s")
    if (isinstance(down_after, bool)
            or not isinstance(down_after, (int, float))
            or down_after <= 0):
        raise ConfigError(
            "liveness.down_after_s must be a positive number of seconds")
    grace = (cfg.get("reboot") or {}).get("grace_s")
    if (isinstance(grace, bool)
            or not isinstance(grace, (int, float))
            or grace <= 0):
        raise ConfigError(
            "reboot.grace_s must be a positive number of seconds")
    ck = cfg.get("checkpoint") or {}
    code = ck.get("exit_code")
    if (isinstance(code, bool) or not isinstance(code, int)
            or not 1 <= code <= 255):
        raise ConfigError(
            "checkpoint.exit_code must be an integer in 1..255")
    marker = ck.get("marker")
    if (not isinstance(marker, str)
            or not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", marker or "")):
        raise ConfigError(
            "checkpoint.marker must be a marker token "
            "(ALL_CAPS, at least 3 characters)")
    env_var = ck.get("env_var")
    if (not isinstance(env_var, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_var or "")):
        raise ConfigError(
            "checkpoint.env_var must be a valid environment "
            "variable name")
    holds = ck.get("holds_dir")
    if not isinstance(holds, dict):
        raise ConfigError(
            "checkpoint.holds_dir must be a mapping of workflow "
            "to venue directory")
    for wname, wdir in holds.items():
        if wname not in (cfg.get("workflows") or []):
            raise ConfigError(
                f"checkpoint.holds_dir: workflow {wname!r} is not "
                f"in the top-level workflows list")
        if not (isinstance(wdir, str) and wdir):
            raise ConfigError(
                f"checkpoint.holds_dir.{wname} must be a non-empty "
                f"venue directory path")
    lk = cfg.get("lock") or {}
    for lkey in ("default_ttl_min", "max_ttl_min"):
        lval = lk.get(lkey)
        if (isinstance(lval, bool) or not isinstance(lval, int)
                or lval <= 0):
            raise ConfigError(
                f"lock.{lkey} must be a positive integer "
                f"number of minutes")
    if lk.get("default_ttl_min", 0) > lk.get("max_ttl_min", 0):
        raise ConfigError(
            "lock.default_ttl_min must be <= lock.max_ttl_min")
    hosts = cfg.get("hosts") or {}
    declared = cfg.get("workflows") or []
    served = set()
    for name, host in hosts.items():
        if not isinstance(host, dict) or host.get("type") not in HOST_TYPES:
            raise ConfigError(f"hosts.{name}: type must be one of {HOST_TYPES}")
        if host.get("type") == "wsl2" and host.get("via") not in hosts:
            raise ConfigError(f"hosts.{name}: wsl2 host needs a `via` naming another host")
        wf = host.get("workflows") or {}
        if not isinstance(wf, dict) or not wf:
            raise ConfigError(f"hosts.{name}: workflows must name at least one served workflow")
        for wname, kind in wf.items():
            if wname not in declared:
                raise ConfigError(f"hosts.{name}: workflow {wname!r} is not in the top-level workflows list")
            if kind not in WORKFLOW_KINDS:
                raise ConfigError(f"hosts.{name}: workflow {wname!r} kind must be execute|grab")
            served.add(wname)
        ident = host.get("identity")
        if ident is not None:
            if (not isinstance(ident, list) or not ident
                    or not all(isinstance(m, str) and m for m in ident)):
                raise ConfigError(f"hosts.{name}: identity must be a non-empty list of marker names")
        image = host.get("image")
        if image is not None and not (isinstance(image, str) and image):
            raise ConfigError(f"hosts.{name}: image must be a non-empty string")
        if host.get("type") == "wsl2":
            via_wf = (hosts.get(host.get("via")) or {}).get("workflows") or {}
            for wname, kind in wf.items():
                if via_wf.get(wname) != kind:
                    raise ConfigError(
                        f"hosts.{name}: workflow {wname!r} rides via host "
                        f"{host.get('via')!r}, which must serve it with the same kind")
    for wname in declared:
        if wname not in served:
            raise ConfigError(f"workflows: {wname!r} is served by no host")
    lanes = cfg.get("lanes") or {}
    if not lanes:
        raise ConfigError("at least one lane is required")
    for name, lane in lanes.items():
        if not isinstance(lane, dict):
            raise ConfigError(f"lanes.{name}: must be a mapping")
        if lane.get("host") not in hosts:
            raise ConfigError(f"lanes.{name}: unknown host {lane.get('host')!r}")
        _check_pair(lane.get("queued"), f"lanes.{name}.queued")
        ex = lane.get("executing") or {}
        mode = ex.get("mode")
        if mode == "fixed":
            _check_pair(ex.get("values"), f"lanes.{name}.executing.values")
        elif mode == "expectation":
            _check_pair(ex.get("floor"), f"lanes.{name}.executing.floor")
        else:
            raise ConfigError(f"lanes.{name}.executing.mode must be fixed|expectation")
        if lane.get("concurrency", 1) < 1:
            raise ConfigError(f"lanes.{name}.concurrency must be >= 1")
    dr = cfg.get("drain") or {}
    win = dr.get("window_s")
    if (isinstance(win, bool) or not isinstance(win, (int, float))
            or win <= 0):
        raise ConfigError(
            "drain.window_s must be a positive number of seconds")
    per_lane = dr.get("per_lane_window_s")
    if not isinstance(per_lane, dict):
        raise ConfigError(
            "drain.per_lane_window_s must be a mapping of lane "
            "to window seconds")
    for lname, lwin in per_lane.items():
        if lname not in (cfg.get("lanes") or {}):
            raise ConfigError(
                f"drain.per_lane_window_s: lane {lname!r} is not "
                f"a declared lane")
        if (isinstance(lwin, bool)
                or not isinstance(lwin, (int, float)) or lwin <= 0):
            raise ConfigError(
                f"drain.per_lane_window_s.{lname} must be a "
                f"positive number of seconds")
    gstale = dr.get("guard_stale_after_s")
    if (isinstance(gstale, bool)
            or not isinstance(gstale, (int, float)) or gstale <= 0):
        raise ConfigError(
            "drain.guard_stale_after_s must be a positive "
            "number of seconds")
    dp = (cfg.get("dispatch") or {}).get("duplicate_policy")
    if dp not in ("refuse", "allow"):
        raise ConfigError("dispatch.duplicate_policy must be refuse|allow")
    ceil = (cfg.get("dispatch") or {}).get("optimizer_ceiling")
    if (isinstance(ceil, bool) or not isinstance(ceil, int)
            or ceil <= 0):
        raise ConfigError(
            "dispatch.optimizer_ceiling must be a positive integer")
    step = (cfg.get("dispatch") or {}).get("optimizer_aging_step_min")
    if (isinstance(step, bool)
            or not isinstance(step, (int, float)) or step <= 0):
        raise ConfigError(
            "dispatch.optimizer_aging_step_min must be a positive "
            "number of minutes")
    try:
        re.compile((cfg.get("markers") or {}).get("pattern", ""))
    except re.error as e:
        raise ConfigError(f"markers.pattern does not compile: {e}")
    return cfg


def load_config(path=None):
    """Effective config: file (if any) deep-merged over defaults, validated."""
    path = path or os.environ.get(ENV_VAR)
    if path is None:
        # Default location: beside this file (the gatekeeper dir).
        candidate = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 CONFIG_FILENAME)
        path = candidate if os.path.isfile(candidate) else None
    merged = _deep_merge(DEFAULTS, {})
    if path is not None:
        try:
            with open(path, "r", encoding="utf-8") as f:
                parsed = parse_yaml_subset(f.read())
        except OSError as e:
            raise ConfigError(f"cannot read config {path}: {e}")
        merged = _deep_merge(merged, parsed)
    return validate(merged)


if __name__ == "__main__":
    import json
    import sys
    try:
        cfg = load_config(sys.argv[1] if len(sys.argv) > 1 else None)
    except ConfigError as e:
        print(f"CONFIG_ERROR {e}")
        sys.exit(1)
    print(json.dumps(cfg, indent=1, sort_keys=True))
