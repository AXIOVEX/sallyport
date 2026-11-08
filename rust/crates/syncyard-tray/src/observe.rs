//! Observation assembly (spec 035 sections 1-2, 6): the one
//! pure pass that turns the tick's inputs (the daemon snapshot,
//! the local fleet-status file, the backchannel heartbeat age,
//! foreign-instance detection) into the tray's displayed state.
//!
//! The app never originates a status value: job states,
//! verdicts, counts and timestamps render **verbatim** from
//! gatekeeper records, and per-job progress is the daemon's
//! own spec 034 progress view from the same snapshot — the
//! tray does not poll anything independently and never
//! reinterprets (FR-035-02/03, T034-14). An interim delivery
//! reads "still running" plus elapsed, never a verdict.

use serde_json::Value;

use syncyard_contract::isotime;

use crate::connection::{evaluate, ConnectionReading, ConnectionSignals, ConnectionThresholds};
use crate::fleet::{FleetStatus, Freshness};
use crate::heartbeat::ForeignInstance;
use crate::identity::BuildIdentity;
use crate::settings::Settings;

/// Reporting-pipeline health as published gatekeeper state
/// carries it (FR-035-19, display half). Absent fields render
/// "unknown" — never invented.
#[derive(Debug, Clone, PartialEq)]
pub struct ReportingHealth {
    pub queue_depth: Option<u64>,
    pub dedupe_rate: Option<f64>,
    pub cap_state: Option<String>,
}

impl ReportingHealth {
    pub fn from_snapshot(snapshot: &Value) -> Self {
        let r = snapshot.get("reporting");
        ReportingHealth {
            queue_depth: r
                .and_then(|x| x.get("queue_depth"))
                .and_then(|x| x.as_u64()),
            dedupe_rate: r
                .and_then(|x| x.get("dedupe_rate"))
                .and_then(|x| x.as_f64()),
            cap_state: r
                .and_then(|x| x.get("cap_state"))
                .and_then(|x| x.as_str())
                .map(|s| s.to_string()),
        }
    }

    /// The display line: each value verbatim, or "unknown".
    pub fn line(&self) -> String {
        let depth = self
            .queue_depth
            .map(|d| d.to_string())
            .unwrap_or_else(|| "unknown".to_string());
        let dedupe = self
            .dedupe_rate
            .map(|d| format!("{d:.2}"))
            .unwrap_or_else(|| "unknown".to_string());
        let cap = self
            .cap_state
            .clone()
            .unwrap_or_else(|| "unknown".to_string());
        format!("reports: queue {depth} · dedupe {dedupe} · daily cap {cap}")
    }
}

/// One job row as displayed. `conclusion`/`progress_*` are the
/// gatekeeper record's and the daemon progress view's values,
/// verbatim.
#[derive(Debug, Clone, PartialEq)]
pub struct JobLine {
    pub id: String,
    pub key: String,
    /// The record's own state word, verbatim.
    pub state: String,
    pub terminal: bool,
    /// Terminal rows only: the record's conclusion, verbatim.
    pub conclusion: Option<String>,
    /// Seconds since dispatch (or submission), for the
    /// "still running" form.
    pub elapsed_s: Option<i64>,
    /// The daemon progress view's display line, verbatim.
    pub progress_display: Option<String>,
    pub progress_age_s: Option<i64>,
    pub progress_fraction: Option<(u64, u64)>,
}

impl JobLine {
    /// The row's display text. Terminal rows show the record's
    /// verdict word; running rows show "still running" plus
    /// elapsed plus the progress view — never a verdict word
    /// (FR-035-03).
    pub fn line(&self) -> String {
        if self.terminal {
            match &self.conclusion {
                Some(c) => format!("{} · {} · {}", self.key, self.state, c),
                None => format!("{} · {}", self.key, self.state),
            }
        } else {
            let mut s = format!("{} · {} · still running", self.key, self.state);
            if let Some(e) = self.elapsed_s {
                s.push_str(&format!(" · {e}s"));
            }
            if let Some(d) = &self.progress_display {
                s.push_str(&format!(" · {d}"));
            }
            s
        }
    }
}

/// Everything one observation tick saw.
pub struct ObservationInputs {
    pub now_unix_s: i64,
    /// The daemon snapshot (one fetch per tick; job list and
    /// progress both come from this single value).
    pub snapshot: Option<Value>,
    pub fleet: Option<FleetStatus>,
    pub heartbeat_age_s: Option<i64>,
    /// A heartbeat has been seen on this host at least once.
    pub heartbeat_ever_seen: bool,
    /// A foreign tray instance from another install path.
    pub foreign: Option<ForeignInstance>,
    /// Whether this host's runner is healthy (drives the
    /// health mark; from the platform layer's own checks).
    pub runner_up: bool,
}

/// The assembled displayed state for one tick.
pub struct TrayObservation {
    pub identity: BuildIdentity,
    pub identity_defect: bool,
    pub connection: ConnectionReading,
    pub fleet_line: String,
    pub fleet_freshness: Option<Freshness>,
    pub jobs: Vec<JobLine>,
    pub waiting: usize,
    pub active: usize,
    pub total: usize,
    pub reporting: ReportingHealth,
    pub kill_switch: bool,
    pub foreign_instance: Option<ForeignInstance>,
    pub runner_up: bool,
}

fn job_line(entry: &Value, progress: Option<&Value>, now_unix_s: i64) -> JobLine {
    let state = entry
        .get("state")
        .and_then(|v| v.as_str())
        .unwrap_or("unknown")
        .to_string();
    let terminal = state == "completed" || state == "cancelled";
    let started = entry
        .get("dispatched_at")
        .and_then(|v| v.as_str())
        .or_else(|| entry.get("created_at").and_then(|v| v.as_str()));
    let elapsed_s = if terminal {
        None
    } else {
        started
            .and_then(isotime::unix_from_iso)
            .map(|t| now_unix_s - t)
    };
    let fraction = progress
        .and_then(|p| p.get("fraction"))
        .and_then(|f| f.as_array())
        .and_then(|a| {
            match (
                a.first().and_then(|x| x.as_u64()),
                a.get(1).and_then(|x| x.as_u64()),
            ) {
                (Some(k), Some(n)) => Some((k, n)),
                _ => None,
            }
        });
    JobLine {
        id: entry
            .get("id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        key: entry
            .get("key")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        state,
        terminal,
        conclusion: entry
            .get("run_conclusion")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()),
        elapsed_s,
        progress_display: progress
            .and_then(|p| p.get("display"))
            .and_then(|v| v.as_str())
            .map(|s| s.to_string()),
        progress_age_s: progress
            .and_then(|p| p.get("age_s"))
            .and_then(|v| v.as_i64()),
        progress_fraction: fraction,
    }
}

/// Assemble one observation. Pure: same inputs, same outputs,
/// and the pass writes nothing anywhere (FR-035-08).
pub fn build_observation(
    identity: BuildIdentity,
    settings: &Settings,
    inputs: &ObservationInputs,
) -> TrayObservation {
    let thresholds = ConnectionThresholds {
        stale_after_s: settings.connection_stale_s,
        disconnect_after_s: settings.connection_disconnect_s,
    };
    let snapshot_age_s = inputs
        .fleet
        .as_ref()
        .and_then(|f| f.age_s(inputs.now_unix_s));
    let signals = ConnectionSignals {
        heartbeat_age_s: if inputs.heartbeat_ever_seen {
            inputs.heartbeat_age_s
        } else {
            None
        },
        snapshot_age_s,
    };
    let connection = evaluate(&signals, &thresholds);

    let (jobs, waiting, active, total, reporting, kill_switch) = match &inputs.snapshot {
        Some(snap) => {
            let progress_map = snap.get("progress");
            let mut lines = Vec::new();
            if let Some(arr) = snap.get("jobs").and_then(|j| j.as_array()) {
                for entry in arr {
                    let id = entry.get("id").and_then(|v| v.as_str()).unwrap_or("");
                    let prog = progress_map.and_then(|m| m.get(id));
                    lines.push(job_line(entry, prog, inputs.now_unix_s));
                }
            }
            (
                lines,
                snap.get("waiting").and_then(|v| v.as_u64()).unwrap_or(0) as usize,
                snap.get("active").and_then(|v| v.as_u64()).unwrap_or(0) as usize,
                snap.get("jobs_total").and_then(|v| v.as_u64()).unwrap_or(0) as usize,
                ReportingHealth::from_snapshot(snap),
                snap.get("kill_switch")
                    .and_then(|v| v.as_bool())
                    .unwrap_or(false),
            )
        }
        None => (
            Vec::new(),
            0,
            0,
            0,
            ReportingHealth {
                queue_depth: None,
                dedupe_rate: None,
                cap_state: None,
            },
            false,
        ),
    };

    let (fleet_line, fleet_freshness) = match &inputs.fleet {
        Some(f) => (
            f.line(inputs.now_unix_s, settings.fleet_stale_s),
            Some(f.freshness(inputs.now_unix_s, settings.fleet_stale_s)),
        ),
        None => ("fleet: unknown".to_string(), None),
    };

    TrayObservation {
        identity_defect: identity.stamp_defect(),
        identity,
        connection,
        fleet_line,
        fleet_freshness,
        jobs,
        waiting,
        active,
        total,
        reporting,
        kill_switch,
        foreign_instance: inputs.foreign.clone(),
        runner_up: inputs.runner_up,
    }
}
