//! The job record: a job as submitted (identity + execution spec),
//! before any lifecycle state accrues. Field names and null behavior
//! match the Python gatekeeper's submit dict in `gk.py` exactly.

use serde::{Deserialize, Serialize};

/// A job as submitted to the gatekeeper.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct JobRecord {
    /// Unique job id, e.g. `20261108T094635Z-fleet-mirror-...-e0c9ae`.
    pub id: String,
    /// Caller-chosen key; duplicates among active jobs are refused.
    pub key: String,
    /// Owning program chat / component (e.g. `desktop-access`).
    pub program: String,
    /// Door workflow file (e.g. `run.yml`, `wsl.yml`, `grab.yml`).
    pub workflow: String,
    /// Lane name, or `None` while an auto-routed job awaits assignment.
    pub lane: Option<String>,
    /// Working directory on the target host; `None` until resolved for
    /// auto-routed jobs.
    pub cwd: Option<String>,
    /// Expected runtime in minutes (drives watermark thresholds).
    pub expected_min: f64,
    /// Dispatch priority; higher dispatches sooner within a lane.
    pub priority: i64,
    /// `human` when a person pinned the priority, else `auto`
    /// (spec 013 FR-003).
    pub priority_source: String,
    /// Free-form flags carried from submission.
    #[serde(default)]
    pub flags: Vec<String>,
    /// Maintenance jobs may dispatch while the fleet is paused.
    pub maintenance: bool,
    /// True when this record adopted an already-running GitHub run.
    pub adopted: bool,
    /// Chat id to notify on state changes, if any.
    pub notify_chat: Option<String>,
    /// Venue the job is bound to (spec 038 section 3: lanes bind to
    /// venues, not runner labels). `auto` until routed.
    pub venue: String,
    /// Payload script path on this machine (run/wsl workflows).
    pub script_path: Option<String>,
    /// SHA-256 of the payload script, recorded at submission so the
    /// dispatched payload is verifiable against the record.
    pub script_sha256: Option<String>,
    /// Desktop file path to return (grab.yml jobs only).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub grab_path: Option<String>,
    /// ISO-8601 UTC submission time.
    pub created_at: String,
}
