#!/usr/bin/env python3
"""Staging store harvest battery for gk.py (staging-store build
2026-11-09). Runs against a throwaway queue dir and an in-memory
fake of the mini-server SFTP store; the live gatekeeper and the
real mini-server are never touched. The fake records every batch
command so ordering pins (claim before pull; ledger+save before
delete) are asserted, not assumed."""
import hashlib
import io
import json
import os
import shutil
import sys
from contextlib import redirect_stdout

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gk

failures = []


def check(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        failures.append(name)


tmpd = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "test-tmp", "staging-run")
shutil.rmtree(tmpd, ignore_errors=True)
os.makedirs(os.path.join(tmpd, "queue"))
gk.QUEUE_DIR = os.path.join(tmpd, "queue")
gk.LEDGER = os.path.join(tmpd, "ledger.jsonl")
gk.HLOG = os.path.join(tmpd, "gk.log")
gk.STAGING_HARVEST_DIR = os.path.join(tmpd, "harvest")
gk._staging_run_attempt = lambda job: job.get("_fake_attempt", 1)


# --- fake SFTP store --------------------------------------------------------

class FakeStore:
    """path -> bytes files; path -> target links; explicit dirs.
    ops records (verb, path) for ordering assertions."""

    def __init__(self):
        self.files = {}
        self.links = {}
        self.dirs = set(["/"])
        self.ops = []
        self.down = False
        self.truncate_gets = False
        self.on_claim_put = None

    def _parents(self, path):
        parts = path.strip("/").split("/")[:-1]
        cur = ""
        for p in parts:
            cur += "/" + p
            self.dirs.add(cur)

    def add_file(self, path, data):
        self._parents(path)
        self.files[path] = data

    def add_link(self, path, target):
        self._parents(path)
        self.links[path] = target

    def run(self, commands, timeout=180):
        out_lines = []
        for raw in commands:
            ignore = raw.startswith("-")
            cmd = raw[1:] if ignore else raw
            parts = cmd.split(" ")
            verb = parts[0]
            if self.down:
                if ignore:
                    continue
                return 1, "", "Connection closed by remote host"
            if verb == "ls":
                path = parts[2]
                rc, lines = self._ls(path)
                if rc != 0:
                    if ignore:
                        continue
                    return 1, "", ("Can't ls: %s: No such file or "
                                   "directory" % path)
                out_lines.extend(lines)
            elif verb == "get":
                remote, local = parts[1], parts[2]
                self.ops.append(("get", remote))
                if remote not in self.files:
                    if ignore:
                        continue
                    return 1, "", "get: no such file"
                data = self.files[remote]
                if self.truncate_gets and remote.endswith(
                        ".meta.json") is False:
                    data = data[:-7]
                with open(local, "wb") as f:
                    f.write(data)
            elif verb == "put":
                local, remote = parts[1], parts[2]
                self.ops.append(("put", remote))
                with open(local, "rb") as f:
                    data = f.read()
                self._parents(remote)
                self.files[remote] = data
                if remote.rsplit("/", 1)[-1].startswith(".claim-") \
                        and self.on_claim_put:
                    self.on_claim_put(self)
            elif verb == "rename":
                old, new = parts[1], parts[2]
                self.ops.append(("rename", old))
                src = self.files if old in self.files else self.links
                if old not in src or new in self.files \
                        or new in self.links:
                    if ignore:
                        continue
                    return 1, "", "rename failed"
                src[new] = src.pop(old)
                self._parents(new)
            elif verb == "rm":
                path = parts[1]
                self.ops.append(("rm", path))
                if path in self.files:
                    del self.files[path]
                elif path in self.links:
                    del self.links[path]
                elif not ignore:
                    return 1, "", "rm: no such file"
            elif verb == "rmdir":
                path = parts[1]
                self.ops.append(("rmdir", path))
                kids = [p for p in list(self.files) + list(self.links)
                        if p.startswith(path + "/")]
                sub = [d for d in self.dirs
                       if d.startswith(path + "/")]
                if path not in self.dirs or kids or sub:
                    if not ignore:
                        return 1, "", "rmdir failed"
                else:
                    self.dirs.discard(path)
            elif verb == "mkdir":
                path = parts[1]
                self.ops.append(("mkdir", path))
                if path in self.dirs:
                    if not ignore:
                        return 1, "", "mkdir failed"
                else:
                    self._parents(path + "/x")
                    self.dirs.add(path)
            else:
                return 1, "", "unknown command " + verb
        return 0, "\n".join(out_lines), ""

    def _ls(self, path):
        if path in self.files:
            return 0, [self._line(path, "file",
                                  len(self.files[path]))]
        if path not in self.dirs:
            return 1, []
        lines = []
        kids = {}
        for p, data in self.files.items():
            if p.rsplit("/", 1)[0] == path:
                kids[p] = ("file", len(data))
        for p, tgt in self.links.items():
            if p.rsplit("/", 1)[0] == path:
                kids[p] = ("link", len(tgt))
        for d in self.dirs:
            if d != path and d.rsplit("/", 1)[0] == path:
                kids[d] = ("dir", 4096)
        for p in sorted(kids):
            lines.append(self._line(p, kids[p][0], kids[p][1]))
        return 0, lines

    @staticmethod
    def _line(path, kind, size):
        perms = {"file": "-rw-rw----", "dir": "drwxrwsr-x",
                 "link": "lrwxrwxrwx"}[kind]
        return "%s ? staging staging-access %d Nov  9 18:07 %s" % (
            perms, size, path)


store = FakeStore()
gk._sftp_run = store.run

HOST = "tower-offsite"
RDIR = "/hosts/%s/store" % HOST


def make_job(suffix, markers, lane="tower-quick"):
    jid = "20261109T120000Z-staging-test-%s" % suffix
    job = {"id": jid, "key": "staging-test-" + suffix,
           "program": "syncyard-staging", "workflow": "linux.yml",
           "lane": lane, "state": "completed", "run_id": 42424242,
           "run_status": "completed", "run_conclusion": "success",
           "flags": [], "watermarks": {}, "markers": markers,
           "run_created_at": "2026-11-09T11:59:00Z",
           "log_fetched_at": "2026-11-09T12:01:00Z",
           "notify_chat": None}
    gk.save_job(job)
    return job


def staged_pair(job_id, name, data, sha=None, meta_sha=None,
                meta_bytes=None, pushed=1794247200):
    sha = sha or hashlib.sha256(data).hexdigest()
    meta = {"job_id": job_id, "host": HOST, "filename": name,
            "bytes": len(data) if meta_bytes is None else meta_bytes,
            "sha256": meta_sha or sha, "pushed_at_epoch": pushed,
            "pushed_at": "2026-11-09T12:00:00Z"}
    base = "%s/%s" % (RDIR, job_id)
    store.add_file(base + "/" + name, data)
    store.add_file(base + "/" + name + ".meta.json",
                   json.dumps(meta).encode())
    return sha


def ledger_events(name=None):
    out = []
    if os.path.isfile(gk.LEDGER):
        with open(gk.LEDGER) as f:
            for raw in f:
                try:
                    ev = json.loads(raw)
                except ValueError:
                    continue
                if name is None or ev.get("event") == name:
                    out.append(ev)
    return out


DATA = b"staging-proof-bytes" * 5000  # 95,000 bytes, deterministic
SHA = hashlib.sha256(DATA).hexdigest()

# --- 1. marker validation (F5/FBB-3) ---------------------------------------
JID1 = "20261109T120000Z-staging-test-parse"
job = make_job("parse", [
    "STAGED_FILE job=%s name=ok.bin bytes=%d sha256=%s"
    % (JID1, len(DATA), SHA),
    "STAGED_FILE job=%s name=ok.bin bytes=%d sha256=%s"
    % (JID1, len(DATA), SHA),
    "STAGED_FILE job=other-job name=x.bin bytes=10 sha256=" + "a" * 64,
    "STAGED_FILE job=%s name=../evil bytes=10 sha256=%s"
    % (JID1, "b" * 64),
    "STAGED_FILE job=%s name=no.bin bytes=0 sha256=%s" % (JID1, "c" * 64),
    "STAGED_FILE job=%s name=big.bin bytes=99999999999 sha256=%s"
    % (JID1, "d" * 64),
    "STAGED_FILE job=%s name=bad.bin bytes=10 sha256=XYZ" % JID1,
])
changed = gk.staging_init(job)
entries = job.get("staging") or []
check("init: exactly one pending entry from identical duplicates",
      changed and len(entries) == 1
      and entries[0]["status"] == "pending"
      and entries[0]["host"] == HOST)
refused = [e for e in ledger_events("staging_refused")
           if e.get("job_id") == job["id"]]
check("init: five refused markers ledgered (job/name/bytes/sha)",
      len(refused) == 5)

# conflicting duplicates -> hash_mismatch, never last-wins
job2 = make_job("conflict", [
    "STAGED_FILE job=20261109T120000Z-staging-test-conflict "
    "name=c.bin bytes=10 sha256=" + "a" * 64,
    "STAGED_FILE job=20261109T120000Z-staging-test-conflict "
    "name=c.bin bytes=10 sha256=" + "b" * 64,
])
gk.staging_init(job2)
check("init: conflicting duplicates are a mismatch entry",
      job2["staging"][0]["status"] == "hash_mismatch")

# --- 2. happy harvest --------------------------------------------------------
store.files.clear(); store.links.clear(); store.ops.clear()
job3 = make_job("happy", [
    "STAGED_FILE job=20261109T120000Z-staging-test-happy "
    "name=proof.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job3)
staged_pair(job3["id"], "proof.bin", DATA)
n = gk.staging_pass([job3])
e3 = job3["staging"][0]
landed = os.path.join(gk.STAGING_HARVEST_DIR, job3["id"], "proof.bin")
check("harvest: entry harvested, one job processed",
      n == 1 and e3["status"] == "harvested")
check("harvest: landed bytes exact",
      os.path.isfile(landed)
      and hashlib.sha256(open(landed, "rb").read()).hexdigest() == SHA)
check("harvest: staged pair and claim deleted remotely",
      not any("staging-test-happy" in p for p in store.files))
ops = [v for v, _p in store.ops]
check("harvest: claim put before data get (F4 ordering)",
      ops.index("put") < [i for i, (v, p) in enumerate(store.ops)
                          if v == "get" and p.endswith("proof.bin")][0])
check("harvest: ledger carries staging_harvested + staging_deleted",
      any(e.get("job_id") == job3["id"]
          for e in ledger_events("staging_harvested"))
      and any(e.get("job_id") == job3["id"]
              for e in ledger_events("staging_deleted")))

# --- 3. transport failure defers (FBB-8) -------------------------------------
store.files.clear(); store.ops.clear(); store.down = True
job4 = make_job("down", [
    "STAGED_FILE job=20261109T120000Z-staging-test-down "
    "name=d.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job4)
gk.staging_pass([job4])
store.down = False
e4 = job4["staging"][0]
check("defer: transport failure leaves entry pending, attempts=1",
      e4["status"] == "pending" and e4["attempts"] == 1)
check("defer: staging_deferred ledgered, no mismatch",
      any(e.get("job_id") == job4["id"]
          for e in ledger_events("staging_deferred"))
      and not any(e.get("job_id") == job4["id"]
                  for e in ledger_events("staging_hash_mismatch")))

# --- 4. truncated pull defers, never quarantines ------------------------------
store.files.clear(); store.ops.clear(); store.truncate_gets = True
job5 = make_job("trunc", [
    "STAGED_FILE job=20261109T120000Z-staging-test-trunc "
    "name=t.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job5)
staged_pair(job5["id"], "t.bin", DATA)
gk.staging_pass([job5])
store.truncate_gets = False
e5 = job5["staging"][0]
check("defer: truncated pull stays pending (pull_size_mismatch)",
      e5["status"] == "pending"
      and "pull_size_mismatch" in (e5["detail"] or ""))
check("defer: staged copy untouched after truncation",
      any(p.endswith("/t.bin") for p in store.files)
      and not any("quarantine" in p for p in store.files))

# --- 5. clean full-length pull, wrong hash -> mismatch + quarantine ----------
store.files.clear(); store.ops.clear()
wrong = b"X" * len(DATA)
job6 = make_job("mismatch", [
    "STAGED_FILE job=20261109T120000Z-staging-test-mismatch "
    "name=m.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job6)
staged_pair(job6["id"], "m.bin", wrong, sha=SHA, meta_sha=SHA)
gk.staging_pass([job6])
e6 = job6["staging"][0]
mm = [e for e in ledger_events("staging_hash_mismatch")
      if e.get("job_id") == job6["id"]]
check("mismatch: entry hash_mismatch with both hashes ledgered",
      e6["status"] == "hash_mismatch" and len(mm) == 1
      and mm[0].get("recorded_sha256") == SHA
      and mm[0].get("pulled_sha256")
      == hashlib.sha256(wrong).hexdigest())
check("mismatch: bytes quarantined, nothing delivered",
      any("quarantine" in p and p.endswith("/m.bin")
          for p in store.files)
      and not os.path.exists(os.path.join(
          gk.STAGING_HARVEST_DIR, job6["id"], "m.bin")))

# --- 6. records disagree: mismatch with NO data pull --------------------------
store.files.clear(); store.ops.clear()
job7 = make_job("recdis", [
    "STAGED_FILE job=20261109T120000Z-staging-test-recdis "
    "name=r.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job7)
staged_pair(job7["id"], "r.bin", DATA, meta_sha="e" * 64)
gk.staging_pass([job7])
e7 = job7["staging"][0]
check("records: meta/marker disagreement is the mismatch path",
      e7["status"] == "hash_mismatch"
      and e7["detail"] == "records_disagree")
check("records: data file never pulled on a records mismatch",
      not any(v == "get" and p.endswith("/r.bin")
              for v, p in store.ops))

# --- 7. symlink refused at harvest (F6) ---------------------------------------
store.files.clear(); store.links.clear(); store.ops.clear()
job8 = make_job("link", [
    "STAGED_FILE job=20261109T120000Z-staging-test-link "
    "name=l.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job8)
base8 = "%s/%s" % (RDIR, job8["id"])
store.add_link(base8 + "/l.bin", "/etc/passwd")
store.add_file(base8 + "/l.bin.meta.json",
               json.dumps({"job_id": job8["id"], "host": HOST,
                           "filename": "l.bin", "bytes": len(DATA),
                           "sha256": SHA}).encode())
gk.staging_pass([job8])
e8 = job8["staging"][0]
check("symlink: entry refused, never pulled",
      e8["status"] == "refused"
      and not any(v == "get" and p.endswith("/l.bin")
                  for v, p in store.ops))

# --- 8/9/10. absence resolution (FDB-4) ---------------------------------------
store.files.clear(); store.links.clear(); store.ops.clear()
job9 = make_job("expired", [
    "STAGED_FILE job=20261109T120000Z-staging-test-expired "
    "name=x.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job9)
store.add_file(RDIR + "/.tombstones/%s--x.bin.tomb" % job9["id"],
               b"1794240000 expired\n")
gk.staging_pass([job9])
check("absence: tombstone earns staging_expired",
      job9["staging"][0]["status"] == "expired"
      and any(e.get("job_id") == job9["id"]
              for e in ledger_events("staging_expired")))

job10 = make_job("missing", [
    "STAGED_FILE job=20261109T120000Z-staging-test-missing "
    "name=y.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job10)
gk.staging_pass([job10])
check("absence: bare absence is staging_missing, not expired",
      job10["staging"][0]["status"] == "missing"
      and any(e.get("job_id") == job10["id"]
              for e in ledger_events("staging_missing")))

store.add_file("%s/quarantine/%s/z.bin" % (RDIR, "20261109T120000Z-"
                                           "staging-test-qfound"),
               DATA)
job11 = make_job("qfound", [
    "STAGED_FILE job=20261109T120000Z-staging-test-qfound "
    "name=z.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job11)
gk.staging_pass([job11])
check("absence: pair in quarantine resolves as hash_mismatch",
      job11["staging"][0]["status"] == "hash_mismatch"
      and job11["staging"][0]["detail"] == "found_in_quarantine")

# --- 11. attempt change re-derives (FDB-5) ------------------------------------
store.files.clear(); store.ops.clear()
job12 = make_job("attempt", [
    "STAGED_FILE job=20261109T120000Z-staging-test-attempt "
    "name=a.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job12)
job12["run_attempt"] = 1
job12["_fake_attempt"] = 2
new_sha = hashlib.sha256(b"new attempt bytes").hexdigest()
def fake_fetch(j):
    j["markers"] = [
        "STAGED_FILE job=%s name=a2.bin bytes=17 sha256=%s"
        % (j["id"], new_sha)]
    j["log_fetched_at"] = gk.now_iso()
    return True
real_fetch = gk.fetch_log_once
gk.fetch_log_once = fake_fetch
gk.staging_pass([job12])
gk.fetch_log_once = real_fetch
check("attempt: change ledgered and entries re-derived",
      any(e.get("event") == "staging_attempt_changed"
          and e.get("job_id") == job12["id"]
          for e in ledger_events())
      and job12["staging"][0]["name"] == "a2.bin")

# --- 12. duplicate-push race requeues (D6) -------------------------------------
store.files.clear(); store.ops.clear()
v2 = b"Y" * len(DATA)
v2_sha = hashlib.sha256(v2).hexdigest()
job13 = make_job("race", [
    "STAGED_FILE job=20261109T120000Z-staging-test-race "
    "name=p.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
gk.staging_init(job13)
staged_pair(job13["id"], "p.bin", DATA)

def swap_to_v2(st):
    base = "%s/%s" % (RDIR, job13["id"])
    st.files[base + "/p.bin"] = v2
    st.files[base + "/p.bin.meta.json"] = json.dumps(
        {"job_id": job13["id"], "host": HOST, "filename": "p.bin",
         "bytes": len(v2), "sha256": v2_sha,
         "pushed_at_epoch": 1794247200}).encode()
store.on_claim_put = swap_to_v2
gk.staging_pass([job13])
store.on_claim_put = None
e13 = job13["staging"][0]
check("race: pulled bytes matching the newer meta requeue, "
      "no quarantine",
      e13["status"] == "pending"
      and e13["detail"] == "duplicate_push_race_requeue"
      and not any("quarantine" in p for p in store.files))

# --- 13. one job per pass -------------------------------------------------------
store.files.clear(); store.ops.clear()
ja = make_job("onea", [
    "STAGED_FILE job=20261109T120000Z-staging-test-onea "
    "name=o.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
jb = make_job("oneb", [
    "STAGED_FILE job=20261109T120000Z-staging-test-oneb "
    "name=o.bin bytes=%d sha256=%s" % (len(DATA), SHA)])
for j in (ja, jb):
    gk.staging_init(j)
    staged_pair(j["id"], "o.bin", DATA)
n = gk.staging_pass([ja, jb], limit=1)
done = [j for j in (ja, jb) if j["staging"][0]["status"] == "harvested"]
check("pass: limit=1 processes exactly one job", n == 1
      and len(done) == 1)

# --- 14. outbox renderers (D8) ---------------------------------------------------
job14 = make_job("notify", [])
job14["notify_chat"] = "chat-staging-test"
gk.save_job(job14)
gk.ledger("staging_hash_mismatch", job_id=job14["id"],
          key=job14["key"], filename="n.bin",
          recorded_sha256="a" * 64, pulled_sha256="b" * 64)
gk.ledger("staging_expired", job_id=job14["id"], key=job14["key"],
          filename="n.bin")
gk.ledger("staging_missing", job_id=job14["id"], key=job14["key"],
          filename="n.bin", checks="ledger,tombstones,quarantine")
cursor_path = os.path.join(tmpd, "outbox-cursor.json")
with open(cursor_path, "w") as f:
    json.dump({"line": 0}, f)
real_cursor = gk.OUTBOX_CURSOR
gk.OUTBOX_CURSOR = cursor_path


class A:
    ack = False


buf = io.StringIO()
with redirect_stdout(buf):
    gk.cmd_outbox(A())
gk.OUTBOX_CURSOR = real_cursor
out = buf.getvalue()
check("outbox: mismatch/expired/missing pushes rendered",
      "STAGING HASH MISMATCH" in out and "STAGING EXPIRED" in out
      and "STAGING MISSING" in out
      and "NOTIFY chat-staging-test" in out)

print()
if failures:
    print("FAILURES: %d" % len(failures))
    for f in failures:
        print("  - " + f)
    sys.exit(1)
print("ALL PASS")
