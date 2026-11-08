//! Door-workflow pinning (spec 034 FR-034-10).
//!
//! Door workflows are pinned by commit sha at dispatch, and
//! the sha is recorded in the job record. A branch name is a
//! moving target: dispatching `run.yml@main` runs whatever
//! main holds at that moment, unaudited. The pin table is
//! the dispatch-time source: workflow file -> the commit sha
//! of the door repo that dispatch must use. A workflow with
//! no pin is refused by name — dispatch never falls back to
//! an unpinned ref.

use std::collections::BTreeMap;

use syncyard_contract::QueueRecord;

/// workflow file -> pinned commit sha (40 lowercase hex).
#[derive(Debug, Clone, Default)]
pub struct WorkflowPins {
    pins: BTreeMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PinError {
    /// The workflow has no pin in the table.
    Unpinned { workflow: String },
    /// The pin in the table is not a commit sha.
    MalformedPin { workflow: String, value: String },
}

impl std::fmt::Display for PinError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PinError::Unpinned { workflow } => {
                write!(f, "door workflow {workflow} has no commit-sha pin")
            }
            PinError::MalformedPin { workflow, value } => {
                write!(f, "pin for {workflow} is not a commit sha: {value}")
            }
        }
    }
}
impl std::error::Error for PinError {}

fn is_commit_sha(s: &str) -> bool {
    s.len() == 40
        && s.bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

impl WorkflowPins {
    pub fn new() -> Self {
        WorkflowPins::default()
    }

    pub fn is_empty(&self) -> bool {
        self.pins.is_empty()
    }

    pub fn insert(&mut self, workflow: impl Into<String>, sha: impl Into<String>) {
        self.pins.insert(workflow.into(), sha.into());
    }

    pub fn pin_for(&self, workflow: &str) -> Option<&str> {
        self.pins.get(workflow).map(|s| s.as_str())
    }

    /// Resolve the pin dispatch must use for `workflow`.
    pub fn require_pin(&self, workflow: &str) -> Result<String, PinError> {
        match self.pins.get(workflow) {
            None => Err(PinError::Unpinned {
                workflow: workflow.to_string(),
            }),
            Some(sha) if is_commit_sha(sha) => Ok(sha.clone()),
            Some(other) => Err(PinError::MalformedPin {
                workflow: workflow.to_string(),
                value: other.clone(),
            }),
        }
    }

    /// Stamp a job record with its workflow's pin, at
    /// dispatch. The record carries the sha from then on
    /// (FR-034-10: "with the sha recorded in the job record").
    pub fn stamp_record(&self, rec: &mut QueueRecord) -> Result<(), PinError> {
        let sha = self.require_pin(&rec.workflow)?;
        rec.workflow_sha = Some(sha);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::new_queue_record;
    use crate::state::tests::sample_job;
    use syncyard_contract::JobState;

    fn record(workflow: &str) -> QueueRecord {
        let mut job = sample_job("j1");
        job.workflow = workflow.to_string();
        new_queue_record(&job)
    }

    // T034-12 door-workflow-pinned-sha: dispatch stamps the
    // record with the workflow's pinned commit sha; an
    // unpinned workflow is refused by name; a branch name in
    // the pin table is refused as malformed.
    #[test]
    fn t034_12_door_workflow_pinned_sha() {
        let sha = "3d3c42e5aac5ba805825da76410c181273ba90b1";
        let mut pins = WorkflowPins::new();
        pins.insert("run.yml", sha);
        let mut rec = record("run.yml");
        assert_eq!(rec.state, JobState::Submitted);
        assert!(rec.workflow_sha.is_none());
        pins.stamp_record(&mut rec).unwrap();
        assert_eq!(rec.workflow_sha.as_deref(), Some(sha));
        // The sha survives the record's JSON round trip.
        let text = serde_json::to_string(&rec).unwrap();
        assert!(text.contains(sha));
        let back: QueueRecord = serde_json::from_str(&text).unwrap();
        assert_eq!(back.workflow_sha.as_deref(), Some(sha));

        // Unpinned workflow: refused, and the record is NOT
        // stamped with a guess.
        let mut grab = record("grab.yml");
        assert_eq!(
            pins.stamp_record(&mut grab),
            Err(PinError::Unpinned {
                workflow: "grab.yml".into()
            })
        );
        assert!(grab.workflow_sha.is_none());

        // A branch name is not a pin.
        let mut bad = WorkflowPins::new();
        bad.insert("wsl.yml", "main");
        assert!(matches!(
            bad.require_pin("wsl.yml"),
            Err(PinError::MalformedPin { .. })
        ));
    }
}
