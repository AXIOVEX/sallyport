//! Streaming-progress consumption (spec 034 sections 3-5).
//!
//! The daemon holds each running job's latest progress snapshot in
//! RAM on the job's record (spec 034 section 3), fed by batches
//! from the backchannel agent or the runner's log harvest. Three
//! disciplines govern everything here:
//!
//! - **Attribution** (FR-034-08): a batch folds into a job's
//!   snapshot only when its run id and log-source identity match
//!   the job's run. Foreign lines are quarantined and counted,
//!   never displayed as the payload's.
//! - **Cadence** (FR-034-04): a job's snapshot refreshes at most
//!   every ~60 s. Early batches are throttled, not applied — and
//!   not lost: the sender retains throttled lines and re-sends
//!   them with the next batch (see `ProgressAck::Throttled`).
//!   A snapshot older than 180 s renders as "progress stale".
//! - **Verdict hygiene** (FR-034-03): progress is display data.
//!   Ingesting progress never changes a job's state, run fields,
//!   or verdict fields, and never writes a ledger event. The
//!   verdict remains the payload exit code from the terminal
//!   record (spec 020).
//!
//! Progress is RAM-only: after a daemon restart a running job
//! shows "no progress reported" until the next batch (at most one
//! transport interval later) refreshes it. That degradation is the
//! spec's own shape — unknown progress, never a fabricated value.

use serde::Serialize;
use syncyard_contract::{ProgressAck, ProgressBatch, ProgressSnapshot};

use crate::state::DaemonState;
use crate::time::unix_from_iso;

/// Refresh window: a job's snapshot accepts at most one batch per
/// this many seconds (FR-034-04's "~60 s").
pub const DEFAULT_MIN_REFRESH_S: i64 = 60;
/// A snapshot older than this renders "progress stale" (FR-034-04).
pub const DEFAULT_STALE_AFTER_S: i64 = 180;

/// Base for daemon-assigned local run ids (the agent venue). Real
/// GitHub run ids are far below this; local ids are visibly
/// synthetic in any record that carries one, and the assignment is
/// ledgered with `local: true`.
pub const LOCAL_RUN_ID_BASE: u64 = 1 << 62;

/// Tunables for progress ingestion (spec 034 section 3/4 numbers
/// by default; tests set their own).
#[derive(Debug, Clone, Copy)]
pub struct ProgressConfig {
    pub min_refresh_s: i64,
    pub stale_after_s: i64,
}

impl Default for ProgressConfig {
    fn default() -> Self {
        ProgressConfig {
            min_refresh_s: DEFAULT_MIN_REFRESH_S,
            stale_after_s: DEFAULT_STALE_AFTER_S,
        }
    }
}

/// Whether the job's progress feed is currently delivering.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FeedHealth {
    /// Batches are arriving and being attributed.
    Live,
    /// The transport reported failure: progress reads "progress
    /// unknown" until the next attributed batch (FR-034-07).
    Unknown,
}

/// One job's progress feed state (RAM only).
#[derive(Debug, Clone)]
pub struct JobProgress {
    /// The folded snapshot of every attributed line so far.
    pub snapshot: ProgressSnapshot,
    /// Run identity this feed is bound to (established by the
    /// record's run id, or by the first attributed batch when the
    /// record has none yet).
    pub run_id: Option<u64>,
    /// Log-source identity established for the run.
    pub source: Option<String>,
    /// Unix seconds of the last *accepted* refresh.
    pub last_refresh_unix: Option<i64>,
    pub health: FeedHealth,
    /// Foreign lines quarantined for this job (FR-034-08).
    pub quarantined_lines: u64,
    /// Whole batches quarantined (attribution failure on a
    /// transport-failure report carries no lines but still counts).
    pub quarantined_batches: u64,
    /// Batches refused by the cadence window (sender retains and
    /// re-sends the lines; nothing is lost).
    pub throttled_batches: u64,
}

impl Default for JobProgress {
    fn default() -> Self {
        JobProgress {
            snapshot: ProgressSnapshot::new(),
            run_id: None,
            source: None,
            last_refresh_unix: None,
            health: FeedHealth::Live,
            quarantined_lines: 0,
            quarantined_batches: 0,
            throttled_batches: 0,
        }
    }
}

/// The watcher-facing view of one job's progress: exactly what the
/// status/CLI/MCP surfaces render. `display` is one of a closed
/// set of strings; no field here is a verdict (FR-034-03).
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ProgressView {
    pub job_id: String,
    /// "no progress reported" | "progress unknown" |
    /// "payload reports completed, verdict pending harvest" |
    /// "progress stale" | "in progress"
    pub display: String,
    /// Seconds since the last accepted refresh, when one exists.
    pub age_s: Option<i64>,
    /// Seconds since the job started running (dispatch time, or
    /// submit time when not yet dispatched). Shown with the
    /// "no progress reported" display (FR-034-05).
    pub elapsed_s: Option<i64>,
    pub started: bool,
    pub last_marker: Option<String>,
    pub heartbeats: u64,
    /// Latest (k, n) from a well-formed PROGRESS line.
    pub fraction: Option<(u64, u64)>,
    pub label: Option<String>,
    /// The payload's advisory COMPLETED word. Display data only.
    pub completed_word: Option<String>,
    /// PROGRESS regressions observed (FR-034-06).
    pub regressions: u64,
    /// Present when regressions > 0: the regression is surfaced,
    /// never silently clamped (FR-034-06).
    pub regression_note: Option<String>,
    pub malformed_progress_count: u64,
    pub unknown_line_count: u64,
    pub quarantined_lines: u64,
    pub source: Option<String>,
    pub run_id: Option<u64>,
}

impl DaemonState {
    /// The run identity a batch must carry to be attributed to
    /// this job: the record's run id when captured, else the
    /// feed's bound run id.
    fn expected_run(&self, job_id: &str) -> Option<u64> {
        if let Some(rec) = self.jobs.get(job_id) {
            if rec.run_id.is_some() {
                return rec.run_id;
            }
        }
        self.progress.get(job_id).and_then(|p| p.run_id)
    }

    /// Attribution check (FR-034-08). Returns true when the batch
    /// may fold into the job's snapshot.
    fn attributed(&self, batch: &ProgressBatch) -> bool {
        match self.expected_run(&batch.job_id) {
            // Both sides know the run: they must agree.
            Some(expected) => batch.run_id == Some(expected),
            // No run identity anywhere: unattributable.
            None => batch.run_id.is_some(),
        }
        // (source agreement is checked by the caller against the
        // feed's established source)
    }

    /// Ingest one progress batch. Purely a display-data update:
    /// no job record changes, no ledger events (FR-034-03).
    pub fn ingest_progress(&mut self, batch: &ProgressBatch, now_unix_s: i64) -> ProgressAck {
        if !self.jobs.contains_key(&batch.job_id) {
            // Nowhere to attribute to; the caller (serve) turns an
            // unknown job into an error before this point, but the
            // state-level answer stays total.
            return ProgressAck::Quarantined;
        }
        let cfg = self.progress_config;
        // Attribution: run identity, then source identity (both
        // checked on immutable borrows before the feed is taken
        // mutably).
        let run_ok = self.attributed(batch);
        let source_ok = match self
            .progress
            .get(&batch.job_id)
            .and_then(|f| f.source.as_ref())
        {
            Some(established) => established == &batch.source,
            None => true,
        };
        let feed = self.progress.entry(batch.job_id.clone()).or_default();
        if !run_ok || !source_ok {
            feed.quarantined_lines += batch.lines.len() as u64;
            feed.quarantined_batches += 1;
            return ProgressAck::Quarantined;
        }
        // Cadence: at most one accepted refresh per window.
        if let Some(last) = feed.last_refresh_unix {
            if now_unix_s - last < cfg.min_refresh_s {
                feed.throttled_batches += 1;
                return ProgressAck::Throttled;
            }
        }
        // Bind the run/source on first attribution.
        if feed.run_id.is_none() {
            feed.run_id = batch.run_id;
        }
        if feed.source.is_none() {
            feed.source = Some(batch.source.clone());
        }
        for line in &batch.lines {
            let event = syncyard_contract::parse_line(line);
            feed.snapshot.observe(&event);
        }
        feed.last_refresh_unix = Some(now_unix_s);
        feed.health = FeedHealth::Live;
        ProgressAck::Applied
    }

    /// Record a progress-transport failure for a job (FR-034-07):
    /// its progress degrades to "progress unknown". Dispatch,
    /// harvest, and the verdict path are untouched. A failure
    /// report from a foreign source is quarantined like any other
    /// foreign traffic.
    pub fn note_progress_transport_failure(
        &mut self,
        job_id: &str,
        run_id: Option<u64>,
        source: &str,
    ) -> ProgressAck {
        if !self.jobs.contains_key(job_id) {
            return ProgressAck::Quarantined;
        }
        let batch = ProgressBatch {
            job_id: job_id.to_string(),
            run_id,
            source: source.to_string(),
            lines: Vec::new(),
        };
        let run_ok = self.attributed(&batch);
        let source_ok = match self.progress.get(job_id).and_then(|f| f.source.as_ref()) {
            Some(established) => established == source,
            None => true,
        };
        let feed = self.progress.entry(job_id.to_string()).or_default();
        if !run_ok || !source_ok {
            feed.quarantined_batches += 1;
            return ProgressAck::Quarantined;
        }
        feed.health = FeedHealth::Unknown;
        ProgressAck::TransportUnknown
    }

    /// The watcher-facing progress view for one job.
    pub fn progress_view(&self, job_id: &str, now_unix_s: i64) -> Option<ProgressView> {
        let rec = self.jobs.get(job_id)?;
        let feed = self.progress.get(job_id);
        let snap = feed.map(|f| &f.snapshot);
        let age_s = feed
            .and_then(|f| f.last_refresh_unix)
            .map(|t| now_unix_s - t);
        let run_start = rec
            .dispatched_at
            .as_deref()
            .and_then(unix_from_iso)
            .or_else(|| unix_from_iso(&rec.created_at));
        let elapsed_s = run_start.map(|t| now_unix_s - t);
        let silent = snap.map(|s| s.is_silent()).unwrap_or(true);
        let display = if feed.map(|f| f.health) == Some(FeedHealth::Unknown) {
            "progress unknown"
        } else if silent {
            "no progress reported"
        } else if snap.and_then(|s| s.completed_word.as_ref()).is_some() {
            // Spec 034 section 4's exact display for a COMPLETED
            // line with no terminal record yet.
            "payload reports completed, verdict pending harvest"
        } else if age_s.map(|a| a > self.progress_config.stale_after_s) == Some(true) {
            "progress stale"
        } else {
            "in progress"
        };
        let regressions = snap.map(|s| s.regressions).unwrap_or(0);
        Some(ProgressView {
            job_id: job_id.to_string(),
            display: display.to_string(),
            age_s,
            elapsed_s,
            started: snap.map(|s| s.started).unwrap_or(false),
            last_marker: snap.and_then(|s| s.last_marker.clone()),
            heartbeats: snap.map(|s| s.heartbeats).unwrap_or(0),
            fraction: snap.and_then(|s| s.fraction),
            label: snap.and_then(|s| s.label.clone()),
            completed_word: snap.and_then(|s| s.completed_word.clone()),
            regressions,
            regression_note: (regressions > 0).then(|| {
                format!("progress regressed: k decreased {regressions} time(s); latest value shown")
            }),
            malformed_progress_count: snap.map(|s| s.malformed_progress_count).unwrap_or(0),
            unknown_line_count: snap.map(|s| s.unknown_line_count).unwrap_or(0),
            quarantined_lines: feed.map(|f| f.quarantined_lines).unwrap_or(0),
            source: feed.and_then(|f| f.source.clone()),
            run_id: self.expected_run(job_id),
        })
    }
}
