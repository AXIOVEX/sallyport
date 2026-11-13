#!/usr/bin/env python3
"""Sandbox install battery for the packaged fleet-status
publisher installer.

Runs the REAL gatekeeper/install-publisher.sh in temp-dir
sandboxes where every path it can touch is redirected
(FLEETPUB_ETC_DIR / FLEETPUB_SYSTEMD_DIR / FLEETPUB_GK_DIR)
and systemctl is a stub that really executes the installed
unit's ExecStart. The publisher under install talks to a stub
GitHub contents API on 127.0.0.1 (FLEET_STATUS_API_BASE), so
a fresh packaged install publishes a real snapshot end to end
without touching any live service, repo, or unit.

Cases:
  A. fresh install, default channel: completes, gate-verified,
     snapshot lands in the stub API, installed copy's sha256
     matches the checkout source, env file is 0600 and the
     captured secret never appears in installer output;
  B. fresh install, two channels: both channel paths hold the
     snapshot, byte-identical;
  C. second run over A: idempotent (artifacts byte-identical,
     reported UNCHANGED);
  D. HTTPS_PROXY missing: refuses by name (proxy-env),
     installs nothing;
  E. no token source (env tokens absent, gh stubbed to fail):
     refuses by name (github-token), installs nothing.

Run: python3 gatekeeper/test_install_publisher.py
Stdlib only.
"""
import base64
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
INSTALLER = os.path.join(HERE, "install-publisher.sh")
SOURCE = os.path.join(HERE, "fleet_status.py")
TOKEN = "battery-token-123"
fails = []


def check(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name
          + (f" — {detail}" if detail and not cond else ""), flush=True)
    if not cond:
        fails.append(name)


STUB_SYSTEMCTL = r'''#!/usr/bin/env python3
"""Test stub for systemctl: records unit state and, on start,
really executes the unit's ExecStart with its EnvironmentFile
loaded, exactly far enough for the installer's gate reads."""
import os
import shlex
import subprocess
import sys

STATE = os.environ["FLEETPUB_STUB_STATE"]
UNITS = os.environ["FLEETPUB_SYSTEMD_DIR"]
os.makedirs(STATE, exist_ok=True)


def record(unit, result, status):
    with open(os.path.join(STATE, unit + ".result"), "w") as f:
        f.write(result)
    with open(os.path.join(STATE, unit + ".status"), "w") as f:
        f.write(str(status))


def read(unit, kind, default=""):
    try:
        with open(os.path.join(STATE, unit + "." + kind)) as f:
            return f.read().strip()
    except OSError:
        return default


def do_start(unit):
    env = dict(os.environ)
    execstart = None
    with open(os.path.join(UNITS, unit)) as f:
        for line in f:
            line = line.strip()
            if line.startswith("EnvironmentFile="):
                with open(line.split("=", 1)[1].strip()) as ef:
                    for kv in ef:
                        kv = kv.strip()
                        if kv and "=" in kv and not kv.startswith("#"):
                            k, v = kv.split("=", 1)
                            env[k] = v
            elif line.startswith("ExecStart="):
                execstart = line.split("=", 1)[1].strip()
    if not execstart:
        record(unit, "failed", 1)
        return 1
    rc = subprocess.run(shlex.split(execstart), env=env).returncode
    record(unit, "success" if rc == 0 else "failed", rc)
    return 0


def main(argv):
    if not argv:
        return 1
    cmd = argv[0]
    if cmd == "daemon-reload":
        return 0
    if cmd == "enable":
        unit = argv[-1]
        with open(os.path.join(STATE, "enabled"), "a") as f:
            f.write(unit + "\n")
        if "--now" in argv and unit.endswith(".service"):
            return do_start(unit)
        return 0
    if cmd == "start":
        return do_start(argv[-1])
    if cmd == "show":
        unit = next(a for a in argv[1:] if a.endswith((".service",
                                                       ".timer")))
        prop = argv[argv.index("-p") + 1]
        kind = {"Result": "result",
                "ExecMainStatus": "status"}[prop]
        print(read(unit, kind, "success" if kind == "result" else "0"))
        return 0
    if cmd == "list-timers":
        print(read("enabled", "", "") if False else
              "fleet-status-publish.timer")
        return 0
    if cmd == "is-active":
        print("active")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
'''

STUB_GH_FAIL = "#!/bin/sh\nexit 1\n"


class StubAPI:
    """The contents-API stand-in: rate_limit + GET/PUT of repo
    contents, held in memory."""

    def __init__(self):
        self.store = {}   # path -> bytes
        self.puts = []    # paths, in order

    def handler(self):
        outer = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _json(self, code, obj):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                if self.path == "/rate_limit":
                    return self._json(200, {"resources": {"core": {
                        "remaining": 5000, "limit": 5000}}})
                if "/contents/" in self.path:
                    path = self.path.split("/contents/", 1)[1]
                    if path in outer.store:
                        sha = hashlib.sha256(
                            outer.store[path]).hexdigest()
                        return self._json(200, {"sha": sha})
                    return self._json(404, {"message": "Not Found"})
                return self._json(404, {"message": "Not Found"})

            def do_PUT(self):
                if "/contents/" not in self.path:
                    return self._json(404, {"message": "Not Found"})
                path = self.path.split("/contents/", 1)[1]
                length = int(self.headers.get("Content-Length", 0))
                payload = json.loads(
                    self.rfile.read(length).decode("utf-8"))
                outer.store[path] = base64.b64decode(payload["content"])
                outer.puts.append(path)
                sha = hashlib.sha256(outer.store[path]).hexdigest()
                return self._json(200, {"content": {"sha": sha}})

        return H

    def __enter__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                          self.handler())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def write_exe(path, text):
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    os.chmod(path, 0o755)


class Sandbox:
    def __init__(self, api, with_gh_fail=False):
        self.root = tempfile.mkdtemp(prefix="fleetpub-battery-")
        self.etc = os.path.join(self.root, "etc")
        self.units = os.path.join(self.root, "systemd")
        self.stubstate = os.path.join(self.root, "stubstate")
        self.bin = os.path.join(self.root, "bin")
        self.gk = os.path.join(self.root, "gk")
        os.makedirs(self.bin)
        os.makedirs(os.path.join(self.gk, "queue"))
        write_exe(os.path.join(self.bin, "systemctl"), STUB_SYSTEMCTL)
        if with_gh_fail:
            write_exe(os.path.join(self.bin, "gh"), STUB_GH_FAIL)
        job = {"key": "battery-job", "program": "battery",
               "lane": "quick", "state": "submitted",
               "created_at": "2026-11-04T00:00:00Z"}
        with open(os.path.join(self.gk, "queue", "battery-job.json"),
                  "w", encoding="utf-8") as f:
            json.dump(job, f)
        self.api = api

    def env(self, **overrides):
        env = {
            "PATH": self.bin + ":/usr/local/bin:/usr/bin:/bin",
            "HOME": self.root,
            "HTTPS_PROXY": "http://proxy.invalid:3128",
            "NO_PROXY": "127.0.0.1,localhost",
            "no_proxy": "127.0.0.1,localhost",
            "GH_TOKEN": TOKEN,
            "FLEET_STATUS_API_BASE": "http://127.0.0.1:%d" % self.api.port,
            "FLEETPUB_ETC_DIR": self.etc,
            "FLEETPUB_SYSTEMD_DIR": self.units,
            "FLEETPUB_GK_DIR": self.gk,
            "FLEETPUB_SYSTEMCTL": os.path.join(self.bin, "systemctl"),
            "FLEETPUB_STUB_STATE": self.stubstate,
        }
        for key, val in overrides.items():
            if val is None:
                env.pop(key, None)
            else:
                env[key] = val
        return env

    def run(self, args=(), **overrides):
        return subprocess.run(
            ["bash", INSTALLER] + list(args),
            env=self.env(**overrides), capture_output=True, text=True,
            timeout=180)

    def artifact_hashes(self):
        out = {}
        for rel in ("etc/env", "etc/fleet_status.py",
                    "systemd/fleet-status-publish.service",
                    "systemd/fleet-status-publish.timer"):
            path = os.path.join(self.root, rel)
            if os.path.isfile(path):
                with open(path, "rb") as f:
                    out[rel] = hashlib.sha256(f.read()).hexdigest()
        return out


def sha256_file(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def main():
    with StubAPI() as api:
        # --- A: fresh install, default channel -------------------
        sb = Sandbox(api)
        res = sb.run()
        out = res.stdout + res.stderr
        check("A exit 0", res.returncode == 0,
              f"rc={res.returncode}\n{out}")
        check("A OK marker", "FLEETPUB_INSTALL_OK 1" in out)
        check("A gate marker", "FLEETPUB_INSTALL_GATE published" in out)
        check("A secret never printed", TOKEN not in out)
        installed = os.path.join(sb.etc, "fleet_status.py")
        check("A copy installed", os.path.isfile(installed))
        if os.path.isfile(installed):
            check("A copy sha matches source",
                  sha256_file(installed) == sha256_file(SOURCE))
        env_file = os.path.join(sb.etc, "env")
        check("A env file exists", os.path.isfile(env_file))
        if os.path.isfile(env_file):
            mode = stat.S_IMODE(os.stat(env_file).st_mode)
            check("A env file mode 0600", mode == 0o600,
                  oct(mode))
            with open(env_file, encoding="utf-8") as f:
                body = f.read()
            check("A env captured proxy + token names",
                  "HTTPS_PROXY=" in body and "GH_TOKEN=" in body)
        unit = os.path.join(sb.units, "fleet-status-publish.service")
        check("A service unit installed", os.path.isfile(unit))
        check("A timer unit installed", os.path.isfile(
            os.path.join(sb.units, "fleet-status-publish.timer")))
        if os.path.isfile(unit):
            with open(unit, encoding="utf-8") as f:
                body = f.read()
            check("A unit rendered with sandbox gk dir",
                  ("--gk-dir " + sb.gk) in body)
            check("A unit carries the default channel path",
                  "--path channels/syncyard-dev/fleet-status.json"
                  in body)
            check("A unit has no unsubstituted token",
                  "@GK_DIR@" not in body and "@ETC_DIR@" not in body
                  and "@PATH_ARGS@" not in body)
        dev_path = "channels/syncyard-dev/fleet-status.json"
        check("A snapshot landed in stub API", dev_path in api.store)
        if dev_path in api.store:
            snap = json.loads(api.store[dev_path].decode("utf-8"))
            check("A snapshot mode running",
                  snap.get("mode") == "running")
            check("A snapshot waits on the seeded job",
                  [w["key"] for w in snap.get("waiting", [])]
                  == ["battery-job"])
        state_rec = os.path.join(sb.gk, ".fleet-status-publisher.json")
        check("A publisher state record written",
              os.path.isfile(state_rec)
              and bool(json.load(open(state_rec)).get("last_success")))

        hashes_a = sb.artifact_hashes()

        # --- C: idempotent second run ----------------------------
        res2 = sb.run()
        out2 = res2.stdout + res2.stderr
        check("C exit 0", res2.returncode == 0,
              f"rc={res2.returncode}\n{out2}")
        check("C artifacts byte-identical",
              sb.artifact_hashes() == hashes_a)
        check("C copy reported UNCHANGED",
              "FLEETPUB_INSTALL_COPY" in out2
              and "UNCHANGED" in out2)

        # --- B: fresh install, two channels ----------------------
        api2 = StubAPI()
        with api2:
            sb2 = Sandbox(api2)
            resb = sb2.run(args=("--channels",
                                "syncyard-dev,syncyard-laptop"))
            outb = resb.stdout + resb.stderr
            check("B exit 0", resb.returncode == 0,
                  f"rc={resb.returncode}\n{outb}")
            p_dev = "channels/syncyard-dev/fleet-status.json"
            p_lap = "channels/syncyard-laptop/fleet-status.json"
            check("B both channel paths published",
                  p_dev in api2.store and p_lap in api2.store)
            if p_dev in api2.store and p_lap in api2.store:
                check("B bodies byte-identical",
                      api2.store[p_dev] == api2.store[p_lap])

    # Refusals use a fresh API stub so nothing from A leaks in.
    with StubAPI() as api3:
        # --- D: proxy-env missing --------------------------------
        sb3 = Sandbox(api3)
        resd = sb3.run(HTTPS_PROXY=None)
        outd = resd.stdout + resd.stderr
        check("D refused (exit 2)", resd.returncode == 2,
              f"rc={resd.returncode}\n{outd}")
        check("D refusal names proxy-env", "proxy-env" in outd)
        check("D nothing installed",
              not os.path.exists(os.path.join(sb3.etc,
                                              "fleet_status.py"))
              and not os.path.exists(sb3.units))

        # --- E: github-token missing -----------------------------
        sb4 = Sandbox(api3, with_gh_fail=True)
        rese = sb4.run(GH_TOKEN=None, GITHUB_TOKEN=None)
        oute = rese.stdout + rese.stderr
        check("E refused (exit 2)", rese.returncode == 2,
              f"rc={rese.returncode}\n{oute}")
        check("E refusal names github-token", "github-token" in oute)
        check("E nothing installed",
              not os.path.exists(os.path.join(sb4.etc,
                                              "fleet_status.py"))
              and not os.path.exists(sb4.units))

    print()
    if fails:
        print("INSTALL BATTERY: %d FAILED: %s"
              % (len(fails), ", ".join(fails)))
        return 1
    print("INSTALL BATTERY: ALL PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
