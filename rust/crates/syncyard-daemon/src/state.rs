//! RAM-authoritative daemon state (spec 032, spec 038 section 2).
//!
//! Live job records are held in RAM as the authority; the files in
//! the state home are a write-behind cache, flushed every 60 s and
//! on every state transition with verified writes. A failed flush is
//! a non-event: state stays dirty in RAM, the flush retries next
//! cycle, and flush health is a snapshot field. Under a write wave,
//! flush fails fast with a named ledger constraint and bounded
//! retries (ERR-08); no flush failure ever panics or loses RAM
//! state.
//!
//! Record classes held in RAM (spec 032 section 1 + spec 038
//! FR-038-06's fifth class): (1) live job records, (2) the ledger
//! buffer, (3) history stays file-backed (migrate-history), (4) the
//! outbox cursor, and (5) control files — PAUSED / STOPPED / hosts
//! / lane locks / per-job holds.
//!
//! State-home separation (FR-038-11): the state home is an explicit
//! path given to the daemon; nothing derives it from the code's
//! install location, so a code update never moves or shadows state.

use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use syncyard_contract::{JobState, QueueRecord};

use crate::time::now_iso;
use crate::vwrite::StateWriter;

/// Default write-behind flush interval: 60 s (spec 032 section 3).
pub const DEFAULT_FLUSH_INTERVAL_S: u64 = 60;

/// The on-disk layout of a state home.
#[derive(Debug, Clone)]
pub struct StateHome {
    root: PathBuf,
}

impl StateHome {
    pub fn new(root: PathBuf) -> Self {
        StateHome { root }
    }

    pub fn root(&self) -> &Path {
        &self.root
    }
    pub fn queue_dir(&self) -> PathBuf {
        self.root.join("queue")
    }
    pub fn job_path(&self, job_id: &str) -> PathBuf {
        self.queue_dir().join(format!("{job_id}.json"))
    }
    pub fn ledger_path(&self) -> PathBuf {
        self.root.join("ledger.jsonl")
    }
    pub fn outbox_path(&self) -> PathBuf {
        self.root.join("outbox.jsonl")
    }
    pub fn outbox_cursor_path(&self) -> PathBuf {
        self.root.join("outbox-cursor.json")
    }
    pub fn pause_path(&self) -> PathBuf {
        self.root.join("PAUSED.json")
    }
    pub fn stop_path(&self) -> PathBuf {
        self.root.join("STOPPED.json")
    }
    pub fn hosts_path(&self) -> PathBuf {
        self.root.join("hosts.json")
    }
    pub fn locks_path(&self) -> PathBuf {
        self.root.join("locks.json")
    }
    pub fn holds_path(&self) -> PathBuf {
        self.root.join("holds.json")
    }
    pub fn seq_path(&self) -> PathBuf {
        self.root.join("seq.json")
    }
    pub fn socket_path(&self) -> PathBuf {
        self.root.join("gk.sock")
    }
    pub fn lock_path(&self) -> PathBuf {
        self.root.join("gk.lock")
    }
    pub fn history_dir(&self) -> PathBuf {
        self.root.join("history")
    }
}

/// The pause record (`PAUSED.json` in gk.py's shape).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct PauseRecord {
    pub reason: String,
    pub ts: String,
}

/// The stop record (`STOPPED.json` in gk.py's shape).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StopRecord {
    pub reason: String,
    pub ts: String,
}

/// One ledger event: `{ts, event, ...fields}` — gk.py's line shape.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LedgerEvent {
    pub ts: String,
    pub event: String,
    #[serde(flatten)]
    pub fields: BTreeMap<String, serde_json::Value>,
}

impl LedgerEvent {
    pub fn new(event: &str, fields: BTreeMap<String, serde_json::Value>) -> Self {
        LedgerEvent {
            ts: now_iso(),
            event: event.to_string(),
            fields,
        }
    }
    pub fn to_line(&self) -> String {
        let mut s = serde_json::to_string(self).unwrap_or_else(|_| "{}".to_string());
        s.push('\n');
        s
    }
}

/// Flush health, surfaced in every snapshot (spec 032 section 3).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct FlushHealth {
    /// Records whose on-disk cache is behind RAM.
    pub state_dirty_records: usize,
    /// ISO time of the last fully successful flush, if any.
    pub last_flush_ok_at: Option<String>,
    /// Consecutive failed flush cycles.
    pub consecutive_failures: u32,
    /// True while writers are in wave fail-fast mode (ERR-08).
    pub wave_mode: bool,
}

/// The born-denied cure tracker (spec 038 FR-038-06, spec 032
/// kickoff decision 3): a daemon whose flush failures span a
/// sampler-reported clear window restarts itself **once**. The
/// tracker decides; the supervisor performs the restart.
#[derive(Debug, Default)]
pub struct CureTracker {
    failures_before_window: u32,
    clear_window_seen: bool,
    restart_spent: bool,
}

impl CureTracker {
    /// A flush failed.
    pub fn note_flush_failure(&mut self) {
        if !self.clear_window_seen {
            self.failures_before_window += 1;
        }
    }
    /// The wave sampler reports writes are being accepted again.
    pub fn note_clear_window(&mut self) {
        if self.failures_before_window > 0 {
            self.clear_window_seen = true;
        }
    }
    /// A flush succeeded; the episode is over (a *future* episode
    /// may earn its own single restart only after this one clears —
    /// the latch is per-episode, tracked by the caller resetting on
    /// success).
    pub fn note_flush_success(&mut self) {
        self.failures_before_window = 0;
        self.clear_window_seen = false;
        self.restart_spent = false;
    }
    /// True exactly once per episode: failures were seen, a clear
    /// window was reported, and flushes *still* fail (the check is
    /// done by the caller after a post-window failed flush).
    pub fn should_self_restart(&mut self, still_failing: bool) -> bool {
        if still_failing && self.clear_window_seen && !self.restart_spent {
            self.restart_spent = true;
            return true;
        }
        false
    }
}

/// The daemon's whole RAM state.
pub struct DaemonState {
    pub home: StateHome,
    /// Live + terminal records by job id (RAM is the authority).
    pub jobs: BTreeMap<String, QueueRecord>,
    /// Ledger events buffered since the last successful flush.
    pub ledger_buffer: Vec<LedgerEvent>,
    /// Ledger events already on disk (loaded at boot; count only —
    /// `ledger_tail` re-reads the file for display).
    pub ledger_on_disk: usize,
    /// Outbox lines (notification groups) not yet flushed.
    pub outbox_buffer: Vec<String>,
    /// Outbox cursor: lines acknowledged by the consumer.
    pub outbox_cursor: u64,
    /// Outbox lines on disk at boot (for cursor accounting).
    pub outbox_on_disk: usize,
    /// Control files, fifth RAM class.
    pub paused: Option<PauseRecord>,
    pub stopped: Option<StopRecord>,
    pub hosts: serde_json::Value,
    pub lane_locks: serde_json::Value,
    /// Per-job operator holds (ghost-reconciliation hold-first,
    /// FR-038-24; also the hold verb's state).
    pub holds: BTreeSet<String>,
    /// Next monotonic per-source sequence (spec 038 section 9).
    pub next_seq: u64,
    /// Streaming-progress feeds by job id (spec 034 section 3).
    /// RAM-only by design: progress is display data, never part of
    /// the persisted record; after a restart a running job reads
    /// "no progress reported" until its next batch lands.
    pub progress: BTreeMap<String, crate::progress::JobProgress>,
    /// Cadence/staleness tunables for progress ingestion.
    pub progress_config: crate::progress::ProgressConfig,
    /// Next daemon-assigned local run id (agent venue; see
    /// `crate::progress::LOCAL_RUN_ID_BASE`).
    pub next_local_run_id: u64,
    /// Job ids whose cache write is owed.
    dirty_jobs: BTreeSet<String>,
    dirty_control: bool,
    dirty_seq: bool,
    pub flush_health: FlushHealth,
    pub cure: CureTracker,
    /// ERR-08: set when a wave is first detected (ledgered once per
    /// episode), cleared on the first successful flush.
    wave_ledgered: bool,
}

impl DaemonState {
    /// A fresh, empty state over `home` (nothing loaded).
    pub fn empty(home: StateHome) -> Self {
        DaemonState {
            home,
            jobs: BTreeMap::new(),
            ledger_buffer: Vec::new(),
            ledger_on_disk: 0,
            outbox_buffer: Vec::new(),
            outbox_cursor: 0,
            outbox_on_disk: 0,
            paused: None,
            stopped: None,
            hosts: serde_json::Value::Null,
            lane_locks: serde_json::Value::Null,
            holds: BTreeSet::new(),
            next_seq: 1,
            progress: BTreeMap::new(),
            progress_config: crate::progress::ProgressConfig::default(),
            next_local_run_id: crate::progress::LOCAL_RUN_ID_BASE + 1,
            dirty_jobs: BTreeSet::new(),
            dirty_control: false,
            dirty_seq: false,
            flush_health: FlushHealth {
                state_dirty_records: 0,
                last_flush_ok_at: None,
                consecutive_failures: 0,
                wave_mode: false,
            },
            cure: CureTracker::default(),
            wave_ledgered: false,
        }
    }

    /// Load disk state into RAM (boot step 1; reconciliation against
    /// GitHub and the off-VM record is a separate, later step —
    /// `crate::reconcile`). Unparseable record files are skipped and
    /// named in the returned gap list, never silently dropped
    /// (ERR-09: gaps are announced by name).
    pub fn load(home: StateHome) -> (Self, Vec<String>) {
        let mut st = DaemonState::empty(home);
        let mut gaps = Vec::new();
        let qd = st.home.queue_dir();
        if let Ok(rd) = std::fs::read_dir(&qd) {
            for e in rd.flatten() {
                let p = e.path();
                if p.extension().map(|x| x == "json") != Some(true) {
                    continue;
                }
                match std::fs::read_to_string(&p) {
                    Ok(text) => match serde_json::from_str::<QueueRecord>(&text) {
                        Ok(rec) => {
                            st.next_seq = st.next_seq.max(rec.seq.unwrap_or(0) + 1);
                            // Local run ids must never be reissued
                            // while a record still carries one.
                            if let Some(r) = rec.run_id {
                                if r >= crate::progress::LOCAL_RUN_ID_BASE {
                                    st.next_local_run_id =
                                        st.next_local_run_id.max(r.saturating_add(1));
                                }
                            }
                            st.jobs.insert(rec.id.clone(), rec);
                        }
                        Err(err) => {
                            gaps.push(format!("queue record {} unparseable: {err}", p.display()))
                        }
                    },
                    Err(err) => {
                        gaps.push(format!("queue record {} unreadable: {err}", p.display()))
                    }
                }
            }
        }
        if let Ok(text) = std::fs::read_to_string(st.home.ledger_path()) {
            st.ledger_on_disk = text.lines().filter(|l| !l.trim().is_empty()).count();
        }
        if let Ok(text) = std::fs::read_to_string(st.home.outbox_path()) {
            let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
            st.outbox_on_disk = lines.len();
            st.outbox_buffer = lines.iter().map(|s| s.to_string()).collect();
        }
        if let Ok(text) = std::fs::read_to_string(st.home.outbox_cursor_path()) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(&text) {
                st.outbox_cursor = v.get("cursor").and_then(|c| c.as_u64()).unwrap_or(0);
            }
        }
        st.paused = read_json(&st.home.pause_path());
        st.stopped = read_json(&st.home.stop_path());
        st.hosts = read_json(&st.home.hosts_path()).unwrap_or(serde_json::Value::Null);
        st.lane_locks = read_json(&st.home.locks_path()).unwrap_or(serde_json::Value::Null);
        if let Some(saved) = read_json::<Vec<String>>(&st.home.holds_path()) {
            st.holds = saved.into_iter().collect();
        }
        if let Some(saved) = read_json::<serde_json::Value>(&st.home.seq_path()) {
            if let Some(n) = saved.get("next_seq").and_then(|v| v.as_u64()) {
                st.next_seq = st.next_seq.max(n);
            }
        }
        (st, gaps)
    }

    /// Allocate a daemon-local run id for an agent-venue
    /// assignment (spec 034 section 3 transport). Ids come from a
    /// reserved high range so they can never be mistaken for
    /// GitHub run ids in a record.
    pub fn alloc_local_run_id(&mut self) -> u64 {
        let id = self.next_local_run_id;
        self.next_local_run_id = self.next_local_run_id.saturating_add(1);
        id
    }

    /// Assign the next sequence number to a record (spec 038 §9).
    pub fn stamp_seq(&mut self, rec: &mut QueueRecord) {
        rec.seq = Some(self.next_seq);
        self.next_seq += 1;
        self.dirty_seq = true;
    }

    /// Insert or replace a record, stamping its sequence and
    /// marking it dirty for the next flush.
    pub fn put_job(&mut self, mut rec: QueueRecord) {
        self.stamp_seq(&mut rec);
        self.dirty_jobs.insert(rec.id.clone());
        self.jobs.insert(rec.id.clone(), rec);
    }

    /// Mutate a record in place; stamps sequence + dirty on success.
    pub fn update_job<F: FnOnce(&mut QueueRecord)>(&mut self, id: &str, f: F) -> bool {
        let Some(mut rec) = self.jobs.remove(id) else {
            return false;
        };
        f(&mut rec);
        self.stamp_seq(&mut rec);
        self.dirty_jobs.insert(id.to_string());
        self.jobs.insert(id.to_string(), rec);
        true
    }

    /// Append a ledger event to the RAM buffer (flushed as one
    /// verified batch; during a wave the buffer holds, nothing is
    /// dropped — spec 032 section 1).
    pub fn ledger(&mut self, event: &str, fields: BTreeMap<String, serde_json::Value>) {
        self.ledger_buffer.push(LedgerEvent::new(event, fields));
    }

    /// Append an outbox line (a serialized notification group).
    pub fn outbox_push(&mut self, line: String) {
        self.outbox_buffer.push(line);
    }

    /// Outbox groups not yet acknowledged: lines past the cursor.
    /// Buffer indexing accounts for lines already on disk: the
    /// buffer holds the disk lines first (loaded at boot) followed
    /// by new ones, which is exactly the cursor's frame.
    pub fn outbox_pending(&self) -> Vec<String> {
        let cur = self.outbox_cursor as usize;
        if cur >= self.outbox_buffer.len() {
            Vec::new()
        } else {
            self.outbox_buffer[cur..].to_vec()
        }
    }

    /// Acknowledge outbox delivery up to `cursor` (the consumer's
    /// ack line, as in gk.py's outbox --ack).
    pub fn outbox_ack(&mut self, cursor: u64) {
        self.outbox_cursor = cursor.max(self.outbox_cursor);
        self.dirty_control = true;
    }

    pub fn set_paused(&mut self, rec: Option<PauseRecord>) {
        self.paused = rec;
        self.dirty_control = true;
    }

    pub fn set_stopped(&mut self, rec: Option<StopRecord>) {
        self.stopped = rec;
        self.dirty_control = true;
    }

    pub fn set_hold(&mut self, job_id: &str, held: bool) {
        if held {
            self.holds.insert(job_id.to_string());
        } else {
            self.holds.remove(job_id);
        }
        self.dirty_control = true;
    }

    /// Is automatic dispatch currently held for this job?
    /// Maintenance jobs pass a pause; nothing passes a stop; a
    /// per-job hold binds regardless (until a named operator act).
    pub fn dispatch_held(&self, job: &QueueRecord) -> Option<&'static str> {
        if self.stopped.is_some() {
            return Some("stopped");
        }
        if self.holds.contains(&job.id) {
            return Some("held");
        }
        if self.paused.is_some() && !job.maintenance {
            return Some("paused");
        }
        None
    }

    /// Flush dirty state to the disk cache with verified writes.
    ///
    /// Returns a report; failure is a value, never a panic, and RAM
    /// state is untouched by failure (records stay dirty for the
    /// next cycle). In wave mode (entered on the first denial) the
    /// flush makes exactly one probe write and returns, instead of
    /// burning retries per record (ERR-08 bounded retries).
    pub fn flush(&mut self, writer: &dyn StateWriter) -> FlushReport {
        // The state home's layout is daemon-managed: the queue dir
        // exists before any record write is attempted. (Publish
        // destinations are NOT managed this way — a missing publish
        // directory must stay a visible failure, ERR-04.)
        let _ = std::fs::create_dir_all(self.home.queue_dir());
        let _ = std::fs::create_dir_all(self.home.root());
        let mut report = FlushReport::default();
        // Snapshot the work so a mid-flush transition re-dirties
        // correctly (the record is removed from the dirty set only
        // after its write verifies).
        let job_ids: Vec<String> = self.dirty_jobs.iter().cloned().collect();
        for id in &job_ids {
            let Some(rec) = self.jobs.get(id) else {
                self.dirty_jobs.remove(id);
                continue;
            };
            let bytes = match serde_json::to_vec_pretty(rec) {
                Ok(b) => b,
                Err(e) => {
                    report.errors.push(format!("{id}: serialize: {e}"));
                    continue;
                }
            };
            let path = self.home.job_path(id);
            match writer.write_verified(&path, &bytes) {
                Ok(()) => {
                    self.dirty_jobs.remove(id);
                    report.jobs_written += 1;
                    if self.flush_health.wave_mode {
                        // First success ends the wave episode.
                        self.flush_health.wave_mode = false;
                    }
                }
                Err(e) => {
                    report.errors.push(format!("{id}: {e}"));
                    if e.is_denial() {
                        return self.finish_failed_flush(report, true);
                    }
                }
            }
            if self.flush_health.wave_mode {
                // In wave mode, probe once per cycle only.
                break;
            }
        }
        // Ledger batch.
        if !self.ledger_buffer.is_empty() {
            let batch: Vec<String> = self.ledger_buffer.iter().map(|e| e.to_line()).collect();
            let mut written = 0usize;
            for line in &batch {
                match writer.append_verified(&self.home.ledger_path(), line.as_bytes()) {
                    Ok(()) => written += 1,
                    Err(e) => {
                        report.errors.push(format!("ledger: {e}"));
                        if e.is_denial() {
                            self.ledger_buffer.drain(..written);
                            self.ledger_on_disk += written;
                            return self.finish_failed_flush(report, true);
                        }
                        break;
                    }
                }
            }
            self.ledger_buffer.drain(..written);
            self.ledger_on_disk += written;
            report.ledger_written = written;
        }
        // Outbox: append only the lines not yet on disk.
        let new_lines = self.outbox_buffer.len().saturating_sub(self.outbox_on_disk);
        if new_lines > 0 {
            let start = self.outbox_on_disk;
            let mut written = 0usize;
            for line in &self.outbox_buffer[start..] {
                let mut l = line.clone();
                if !l.ends_with('\n') {
                    l.push('\n');
                }
                match writer.append_verified(&self.home.outbox_path(), l.as_bytes()) {
                    Ok(()) => written += 1,
                    Err(e) => {
                        report.errors.push(format!("outbox: {e}"));
                        if e.is_denial() {
                            self.outbox_on_disk += written;
                            return self.finish_failed_flush(report, true);
                        }
                        break;
                    }
                }
            }
            self.outbox_on_disk += written;
            report.outbox_written = written;
        }
        // Control files + cursor + seq (fifth RAM class cache).
        if self.dirty_control || self.dirty_seq {
            let ok = self.flush_control(writer, &mut report);
            if !ok && report.denied {
                return self.finish_failed_flush(report, true);
            }
        }
        if report.errors.is_empty() {
            self.flush_health.consecutive_failures = 0;
            self.flush_health.last_flush_ok_at = Some(now_iso());
            self.flush_health.state_dirty_records = self.dirty_jobs.len();
            self.cure.note_flush_success();
            report.ok = true;
        } else {
            let denied = report.denied;
            return self.finish_failed_flush(report, denied);
        }
        report
    }

    fn flush_control(&mut self, writer: &dyn StateWriter, report: &mut FlushReport) -> bool {
        let mut all_ok = true;
        let mut write_opt = |path: PathBuf, value: &Option<String>| -> bool {
            match value {
                Some(text) => match writer.write_verified(&path, text.as_bytes()) {
                    Ok(()) => true,
                    Err(e) => {
                        if e.is_denial() {
                            report.denied = true;
                        }
                        report.errors.push(format!("{}: {e}", path.display()));
                        false
                    }
                },
                None => {
                    // Absence is also state: a cleared pause must
                    // remove the cache file (best effort; a missing
                    // file is already the cleared state).
                    let _ = std::fs::remove_file(&path);
                    true
                }
            }
        };
        let paused = self
            .paused
            .as_ref()
            .map(|p| serde_json::to_string_pretty(p).unwrap());
        let stopped = self
            .stopped
            .as_ref()
            .map(|p| serde_json::to_string_pretty(p).unwrap());
        all_ok &= write_opt(self.home.pause_path(), &paused);
        all_ok &= write_opt(self.home.stop_path(), &stopped);
        if self.hosts != serde_json::Value::Null {
            let text = serde_json::to_string_pretty(&self.hosts).unwrap();
            all_ok &= write_opt(self.home.hosts_path(), &Some(text));
        }
        if self.lane_locks != serde_json::Value::Null {
            let text = serde_json::to_string_pretty(&self.lane_locks).unwrap();
            all_ok &= write_opt(self.home.locks_path(), &Some(text));
        }
        let holds: Vec<String> = self.holds.iter().cloned().collect();
        let text = serde_json::to_string_pretty(&holds).unwrap();
        all_ok &= write_opt(self.home.holds_path(), &Some(text));
        let cursor = serde_json::json!({"cursor": self.outbox_cursor}).to_string();
        all_ok &= write_opt(self.home.outbox_cursor_path(), &Some(cursor));
        let seq = serde_json::json!({"next_seq": self.next_seq}).to_string();
        all_ok &= write_opt(self.home.seq_path(), &Some(seq));
        if all_ok {
            self.dirty_control = false;
            self.dirty_seq = false;
        }
        all_ok
    }

    fn finish_failed_flush(&mut self, mut report: FlushReport, denied: bool) -> FlushReport {
        report.ok = false;
        report.denied = denied;
        self.flush_health.consecutive_failures += 1;
        self.flush_health.state_dirty_records = self.dirty_jobs.len();
        self.cure.note_flush_failure();
        if denied && !self.wave_ledgered {
            // ERR-08: a detected wave switches writers to fail-fast
            // with a named ledger constraint, ledgered once.
            self.flush_health.wave_mode = true;
            self.wave_ledgered = true;
            let mut fields = BTreeMap::new();
            fields.insert(
                "constraint".to_string(),
                serde_json::Value::String("write_wave_detected".to_string()),
            );
            fields.insert(
                "dirty_records".to_string(),
                serde_json::Value::Number(self.dirty_jobs.len().into()),
            );
            // The ledger itself may be unflushable right now; the
            // event rides the RAM buffer and lands with the backlog.
            self.ledger_buffer
                .push(LedgerEvent::new("write_wave_detected", fields));
        }
        if !denied {
            self.wave_ledgered = false;
        }
        report
    }

    /// The status snapshot (spec 032 section 3 + spec 014 shape):
    /// counts by state, flush health, control state, epoch-free.
    pub fn snapshot(&self) -> serde_json::Value {
        let mut by_state: BTreeMap<&str, usize> = BTreeMap::new();
        let mut waiting = 0usize;
        let mut active = 0usize;
        for rec in self.jobs.values() {
            *by_state.entry(rec.state.as_str()).or_default() += 1;
            match rec.state {
                JobState::Submitted => waiting += 1,
                JobState::Dispatching | JobState::Dispatched | JobState::HeldAtCheckpoint => {
                    active += 1
                }
                _ => {}
            }
        }
        // Streaming progress for non-terminal jobs rides the
        // snapshot (spec 034 section 3: watchers see progress
        // without waiting for harvest). Same values as the per-job
        // status view — one source, every surface.
        let now_s = crate::time::now_unix();
        let mut progress = serde_json::Map::new();
        for rec in self.jobs.values() {
            if !rec.state.is_terminal() {
                if let Some(view) = self.progress_view(&rec.id, now_s) {
                    if let Ok(v) = serde_json::to_value(&view) {
                        progress.insert(rec.id.clone(), v);
                    }
                }
            }
        }
        serde_json::json!({
            "generated_at": now_iso(),
            "source": "syncyard-daemon",
            "jobs_total": self.jobs.len(),
            "by_state": by_state,
            "waiting": waiting,
            "active": active,
            "progress": progress,
            "paused": self.paused.is_some(),
            "stopped": self.stopped.is_some(),
            "holds": self.holds.len(),
            "flush": {
                "state_dirty_records": self.dirty_jobs.len(),
                "last_flush_ok_at": self.flush_health.last_flush_ok_at,
                "consecutive_failures": self.flush_health.consecutive_failures,
                "wave_mode": self.flush_health.wave_mode,
            },
            "ledger_buffered": self.ledger_buffer.len(),
            "next_seq": self.next_seq,
        })
    }
}

/// Outcome of one flush cycle.
#[derive(Debug, Default)]
pub struct FlushReport {
    pub ok: bool,
    pub denied: bool,
    pub jobs_written: usize,
    pub ledger_written: usize,
    pub outbox_written: usize,
    pub errors: Vec<String>,
}

fn read_json<T: for<'de> Deserialize<'de>>(path: &Path) -> Option<T> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str(&text).ok()
}

/// Build a queue record at submit time (state `submitted`, no run
/// fields), matching gk.py's submit shape via the contract types.
pub fn new_queue_record(job: &syncyard_contract::JobRecord) -> QueueRecord {
    QueueRecord {
        id: job.id.clone(),
        key: job.key.clone(),
        program: job.program.clone(),
        workflow: job.workflow.clone(),
        lane: job.lane.clone(),
        cwd: job.cwd.clone(),
        script_path: job.script_path.clone(),
        script_sha256: job.script_sha256.clone(),
        grab_path: job.grab_path.clone(),
        expected_min: job.expected_min,
        priority: job.priority,
        priority_source: job.priority_source.clone(),
        flags: job.flags.clone(),
        maintenance: job.maintenance,
        adopted: job.adopted,
        notify_chat: job.notify_chat.clone(),
        venue: job.venue.clone(),
        needs: None,
        lane_request: None,
        cwd_auto: None,
        state: JobState::Submitted,
        created_at: job.created_at.clone(),
        dispatched_at: None,
        completed_at: None,
        seq: None,
        run_id: None,
        run_status: None,
        run_conclusion: None,
        run_created_at: None,
        verdict_source: None,
        not_in_list_noted: false,
        markers: Vec::new(),
        watermarks: serde_json::Map::new(),
        log_tail: Vec::new(),
        log_source: None,
        log_fetched_at: None,
        log_pending: false,
        extra: serde_json::Map::new(),
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::vwrite::FsWriter;

    pub fn scratch(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sy-state-{}-{}", std::process::id(), tag));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    pub fn sample_job(id: &str) -> syncyard_contract::JobRecord {
        syncyard_contract::JobRecord {
            id: id.to_string(),
            key: format!("key-{id}"),
            program: "test".to_string(),
            workflow: "run.yml".to_string(),
            lane: Some("quick".to_string()),
            cwd: Some("C:\\work".to_string()),
            expected_min: 5.0,
            priority: 0,
            priority_source: "auto".to_string(),
            flags: vec![],
            maintenance: false,
            adopted: false,
            notify_chat: None,
            venue: "native".to_string(),
            script_path: Some("/tmp/script.ps1".to_string()),
            script_sha256: Some("ab12".to_string()),
            grab_path: None,
            created_at: "2026-11-08T00:00:00Z".to_string(),
        }
    }

    #[test]
    fn load_flush_reload_round_trip() {
        let home = StateHome::new(scratch("rt"));
        std::fs::create_dir_all(home.queue_dir()).unwrap();
        let mut st = DaemonState::empty(home.clone());
        st.put_job(new_queue_record(&sample_job("job-1")));
        st.ledger("submitted", BTreeMap::new());
        st.set_paused(Some(PauseRecord {
            reason: "test pause".into(),
            ts: now_iso(),
        }));
        let report = st.flush(&FsWriter::default());
        assert!(report.ok, "flush errors: {:?}", report.errors);
        assert_eq!(report.jobs_written, 1);

        let (st2, gaps) = DaemonState::load(home.clone());
        assert!(gaps.is_empty(), "gaps: {gaps:?}");
        assert_eq!(st2.jobs.len(), 1);
        assert_eq!(st2.jobs["job-1"].state, JobState::Submitted);
        assert_eq!(st2.ledger_on_disk, 1);
        assert!(st2.paused.is_some());
        // Sequence continues monotonically across the reload.
        assert!(st2.next_seq > 1);
        let _ = std::fs::remove_dir_all(home.root());
    }

    #[test]
    fn unparseable_record_is_a_named_gap_not_a_drop() {
        let home = StateHome::new(scratch("gap"));
        std::fs::create_dir_all(home.queue_dir()).unwrap();
        std::fs::write(home.job_path("broken"), b"{not json").unwrap();
        let (st, gaps) = DaemonState::load(home.clone());
        assert_eq!(st.jobs.len(), 0);
        assert_eq!(gaps.len(), 1);
        assert!(gaps[0].contains("broken"));
        let _ = std::fs::remove_dir_all(home.root());
    }

    #[test]
    fn pause_holds_non_maintenance_and_stop_holds_all() {
        let home = StateHome::new(scratch("held"));
        let mut st = DaemonState::empty(home.clone());
        let mut rec = new_queue_record(&sample_job("job-1"));
        st.put_job(rec.clone());
        assert_eq!(st.dispatch_held(&rec), None);
        st.set_paused(Some(PauseRecord {
            reason: "r".into(),
            ts: now_iso(),
        }));
        assert_eq!(st.dispatch_held(&rec), Some("paused"));
        rec.maintenance = true;
        assert_eq!(st.dispatch_held(&rec), None);
        st.set_stopped(Some(StopRecord {
            reason: "r".into(),
            ts: now_iso(),
        }));
        assert_eq!(st.dispatch_held(&rec), Some("stopped"));
        let _ = std::fs::remove_dir_all(home.root());
    }

    #[test]
    fn cure_tracker_restarts_once_per_episode() {
        let mut c = CureTracker::default();
        c.note_flush_failure();
        // No clear window yet: no restart.
        assert!(!c.should_self_restart(true));
        c.note_clear_window();
        // Clear window reported but failures continue: restart once.
        assert!(c.should_self_restart(true));
        assert!(!c.should_self_restart(true));
        // A success resets the episode.
        c.note_flush_success();
        c.note_flush_failure();
        c.note_clear_window();
        assert!(c.should_self_restart(true));
    }
}
