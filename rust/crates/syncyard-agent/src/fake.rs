//! The in-memory control channel: the suite's stand-in for a
//! live daemon, with failure knobs for the isolation tests
//! (FR-034-07). It records everything it is given, verbatim, so
//! tests assert on what the agent actually sent — never on what
//! the agent says it sent.

use std::collections::VecDeque;

use syncyard_contract::{Assignment, CompletionReport, ProgressAck, ProgressBatch};

use crate::channel::{AgentError, ControlChannel};

/// How the fake answers progress batches.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AckMode {
    /// Every batch is applied.
    Applied,
    /// Every batch is throttled (the agent must retain its lines).
    Throttled,
    /// Every batch is quarantined (attribution failure upstream).
    Quarantined,
}

#[derive(Debug, Default)]
pub struct FakeChannel {
    /// Assignments handed out, in order.
    pub assignments: VecDeque<Assignment>,
    /// Every progress batch received, with the ack returned.
    pub progress: Vec<(ProgressBatch, ProgressAck)>,
    /// Every completion report received.
    pub completions: Vec<CompletionReport>,
    /// The ack mode for progress batches.
    pub ack_mode: Option<AckMode>,
    /// When true, every progress send fails at the transport.
    pub fail_progress: bool,
    /// Fail this many completion sends (transport), then succeed.
    pub fail_completion_times: usize,
    /// Fail this many assignment polls (transport), then succeed.
    pub fail_poll_times: usize,
    pub poll_calls: usize,
}

impl ControlChannel for FakeChannel {
    fn poll_assignment(&mut self) -> Result<Option<Assignment>, AgentError> {
        self.poll_calls += 1;
        if self.fail_poll_times > 0 {
            self.fail_poll_times -= 1;
            return Err(AgentError::Transport("fake poll failure".into()));
        }
        Ok(self.assignments.pop_front())
    }

    fn send_progress(&mut self, batch: &ProgressBatch) -> Result<ProgressAck, AgentError> {
        if self.fail_progress {
            return Err(AgentError::Transport("fake progress failure".into()));
        }
        let ack = match self.ack_mode {
            Some(AckMode::Throttled) => ProgressAck::Throttled,
            Some(AckMode::Quarantined) => ProgressAck::Quarantined,
            _ => ProgressAck::Applied,
        };
        self.progress.push((batch.clone(), ack));
        Ok(ack)
    }

    fn send_completion(&mut self, report: &CompletionReport) -> Result<(), AgentError> {
        if self.fail_completion_times > 0 {
            self.fail_completion_times -= 1;
            return Err(AgentError::Transport("fake completion failure".into()));
        }
        self.completions.push(report.clone());
        Ok(())
    }
}
