//! The queue record: the full persisted form of a job, as written by
//! the Python gatekeeper to `gatekeeper/queue/<job-id>.json` and, after
//! cutover, by the Rust daemon (spec 038 section 2).
//!
//! Serde shape contract (verified against a real record by the
//! round-trip test in `tests/roundtrip.rs`):
//!
//! - Fields the Python gatekeeper always writes keep their names and
//!   are always serialized, including explicit `null` for run fields
//!   that are not known yet (`run_id`, `run_status`, `run_conclusion`,
//!   `verdict_source`, `notify_chat`, `script_path`, `script_sha256`,
//!   and `lane`/`cwd` on auto-routed jobs).
//! - Fields added over the job's life (`dispatched_at`, `completed_at`,
//!   log fields, ...) or only present for some job kinds (`grab_path`,
//!   `needs`, `lane_request`, `cwd_auto`) are omitted when unset.
//! - Any field this contract version does not know is preserved
//!   verbatim in `extra`, so a newer writer's record round-trips
//!   losslessly through an older reader.

use serde::{Deserialize, Deserializer, Serialize, Serializer};

use crate::JobRecord;

/// Job lifecycle state. The string forms are the Python gatekeeper's
/// (`gk.py`); an unrecognized state is preserved as
/// [`JobState::Unknown`] so records never fail to load over a state
/// this contract version predates.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub enum JobState {
    /// Submitted, waiting for dispatch.
    Submitted,
    /// Dispatch in flight (two-phase protocol, spec 038 FR-038-08).
    Dispatching,
    /// Dispatched to a GitHub run; not yet terminal.
    Dispatched,
    /// Paused at a payload checkpoint (spec 011 hold files).
    HeldAtCheckpoint,
    /// Terminal: the run finished (verdict lives in the record's
    /// markers / run_conclusion, spec 020).
    Completed,
    /// Terminal: cancelled by an operator act.
    Cancelled,
    /// A state this contract version does not know, preserved verbatim.
    Unknown(String),
}

impl JobState {
    /// The persisted string form.
    pub fn as_str(&self) -> &str {
        match self {
            JobState::Submitted => "submitted",
            JobState::Dispatching => "dispatching",
            JobState::Dispatched => "dispatched",
            JobState::HeldAtCheckpoint => "held_at_checkpoint",
            JobState::Completed => "completed",
            JobState::Cancelled => "cancelled",
            JobState::Unknown(s) => s.as_str(),
        }
    }

    /// Terminal states expect no further transitions.
    pub fn is_terminal(&self) -> bool {
        matches!(self, JobState::Completed | JobState::Cancelled)
    }
}

impl From<&str> for JobState {
    fn from(s: &str) -> Self {
        match s {
            "submitted" => JobState::Submitted,
            "dispatching" => JobState::Dispatching,
            "dispatched" => JobState::Dispatched,
            "held_at_checkpoint" => JobState::HeldAtCheckpoint,
            "completed" => JobState::Completed,
            "cancelled" => JobState::Cancelled,
            other => JobState::Unknown(other.to_string()),
        }
    }
}

impl Serialize for JobState {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        serializer.serialize_str(self.as_str())
    }
}

impl<'de> Deserialize<'de> for JobState {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let s = String::deserialize(deserializer)?;
        Ok(JobState::from(s.as_str()))
    }
}

/// The full persisted queue record for one job.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct QueueRecord {
    // -- identity and execution spec (the job record proper) --
    /// Unique job id.
    pub id: String,
    /// Caller-chosen key.
    pub key: String,
    /// Owning program.
    pub program: String,
    /// Door workflow file (`run.yml`, `wsl.yml`, `grab.yml`).
    pub workflow: String,
    /// Lane name; `null` on an auto-routed job before assignment.
    pub lane: Option<String>,
    /// Working directory on the target host; `null` until resolved.
    pub cwd: Option<String>,
    /// Payload script path (run/wsl jobs); `null` for grab jobs.
    pub script_path: Option<String>,
    /// SHA-256 of the payload script; `null` for grab jobs.
    pub script_sha256: Option<String>,
    /// Desktop file path to return (grab.yml jobs only).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub grab_path: Option<String>,
    /// Expected runtime in minutes.
    pub expected_min: f64,
    /// Dispatch priority.
    pub priority: i64,
    /// `human` or `auto` (spec 013 FR-003).
    pub priority_source: String,
    /// Free-form flags carried from submission.
    #[serde(default)]
    pub flags: Vec<String>,
    /// Maintenance jobs may dispatch while paused.
    pub maintenance: bool,
    /// True when this record adopted an already-running run.
    pub adopted: bool,
    /// Chat id to notify on state changes; `null` when none.
    pub notify_chat: Option<String>,
    /// Venue binding (spec 038 section 3); `auto` until routed.
    pub venue: String,
    /// Declared resource needs (spec 016), when the submitter gave any.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub needs: Option<serde_json::Value>,
    /// `auto` on auto-routed submissions (spec 016).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub lane_request: Option<String>,
    /// True when an auto-routed job's cwd still needs resolution.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub cwd_auto: Option<bool>,

    // -- lifecycle state --
    /// Current lifecycle state.
    pub state: JobState,
    /// ISO-8601 UTC submission time.
    pub created_at: String,
    /// ISO-8601 UTC dispatch time, once dispatched.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub dispatched_at: Option<String>,
    /// ISO-8601 UTC completion time, once terminal.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub completed_at: Option<String>,
    /// Monotonic per-source sequence number (spec 038 section 9).
    /// New in the Rust core; absent in Python-written records, and
    /// omitted from the JSON while unset so records stay readable by
    /// the Python gatekeeper during the cutover window.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub seq: Option<u64>,

    // -- GitHub run linkage --
    /// GitHub Actions run id; `null` until dispatch captures it.
    pub run_id: Option<u64>,
    /// GitHub run status (`queued`, `in_progress`, `completed`, ...);
    /// GitHub's vocabulary, kept as a string.
    pub run_status: Option<String>,
    /// GitHub run conclusion (`success`, `failure`, `cancelled`, ...);
    /// GitHub's vocabulary, kept as a string.
    pub run_conclusion: Option<String>,
    /// ISO-8601 UTC creation time of the GitHub run.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub run_created_at: Option<String>,
    /// How the payload verdict was established (`markers`, ...);
    /// `null` until a verdict exists (spec 020).
    pub verdict_source: Option<String>,
    /// Set when a sweep found the run missing from the run list
    /// (a fetch-window miss is recorded, never treated as a loss).
    #[serde(default)]
    pub not_in_list_noted: bool,

    // -- harvest --
    /// Payload marker lines harvested from the run log.
    #[serde(default)]
    pub markers: Vec<String>,
    /// Watermark escalation state (W1/W2) keyed by classification.
    #[serde(default)]
    pub watermarks: serde_json::Map<String, serde_json::Value>,
    /// Tail of the harvested run log.
    #[serde(default)]
    pub log_tail: Vec<String>,
    /// Where the log came from (e.g. `gh-log`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_source: Option<String>,
    /// ISO-8601 UTC time the log was fetched.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub log_fetched_at: Option<String>,
    /// True while a log fetch is still owed.
    #[serde(default)]
    pub log_pending: bool,

    /// Fields this contract version does not know, preserved verbatim
    /// so records round-trip losslessly across writer versions.
    #[serde(flatten)]
    pub extra: serde_json::Map<String, serde_json::Value>,
}

impl From<&QueueRecord> for JobRecord {
    /// The job-as-submitted view of a queue record.
    fn from(rec: &QueueRecord) -> Self {
        JobRecord {
            id: rec.id.clone(),
            key: rec.key.clone(),
            program: rec.program.clone(),
            workflow: rec.workflow.clone(),
            lane: rec.lane.clone(),
            cwd: rec.cwd.clone(),
            expected_min: rec.expected_min,
            priority: rec.priority,
            priority_source: rec.priority_source.clone(),
            flags: rec.flags.clone(),
            maintenance: rec.maintenance,
            adopted: rec.adopted,
            notify_chat: rec.notify_chat.clone(),
            venue: rec.venue.clone(),
            script_path: rec.script_path.clone(),
            script_sha256: rec.script_sha256.clone(),
            grab_path: rec.grab_path.clone(),
            created_at: rec.created_at.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn job_state_strings_match_python_gatekeeper() {
        for (s, want_terminal) in [
            ("submitted", false),
            ("dispatching", false),
            ("dispatched", false),
            ("held_at_checkpoint", false),
            ("completed", true),
            ("cancelled", true),
        ] {
            let st = JobState::from(s);
            assert_eq!(st.as_str(), s);
            assert_eq!(st.is_terminal(), want_terminal);
        }
    }

    #[test]
    fn job_state_unknown_is_preserved_verbatim() {
        let st = JobState::from("some_future_state");
        assert_eq!(st.as_str(), "some_future_state");
        let json = serde_json::to_string(&st).unwrap();
        assert_eq!(json, "\"some_future_state\"");
        let back: JobState = serde_json::from_str(&json).unwrap();
        assert_eq!(back, st);
    }

    #[test]
    fn job_state_serde_round_trip() {
        for st in [
            JobState::Submitted,
            JobState::Dispatched,
            JobState::HeldAtCheckpoint,
            JobState::Completed,
        ] {
            let json = serde_json::to_string(&st).unwrap();
            let back: JobState = serde_json::from_str(&json).unwrap();
            assert_eq!(back, st);
        }
    }
}
