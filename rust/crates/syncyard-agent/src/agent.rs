//! The agent as a supervised unit: singleton guard, work
//! cycles, and completion-delivery retry.
//!
//! One cycle: (1) retry any undelivered completion reports —
//! a terminal report is never dropped because the channel
//! hiccuped at payload end; (2) poll for an assignment;
//! (3) execute it. The agent runs one job at a time per
//! install path (the singleton's scope); host parallelism is
//! a venue property of the daemon's lanes, not of one agent.

use std::path::PathBuf;

use syncyard_contract::CompletionReport;

use crate::channel::{AgentError, ControlChannel};
use crate::runner::{run_assignment, AgentConfig, RunOutcome};
use crate::singleton::{acquire, InstanceGuard};

pub struct Agent {
    #[allow(dead_code)]
    guard: InstanceGuard,
    pub config: AgentConfig,
    pub channel: Box<dyn ControlChannel>,
    /// Completion reports whose delivery failed; retried at the
    /// head of every cycle until the daemon has them.
    pub pending_completions: Vec<CompletionReport>,
}

/// What one cycle did.
#[derive(Debug)]
pub struct CycleOutcome {
    /// Pending completions delivered by this cycle's retry.
    pub retried_delivered: usize,
    /// The assignment executed this cycle, if any.
    pub assignment_job: Option<String>,
    pub run: Option<RunOutcome>,
}

impl Agent {
    /// Build an agent and take the singleton guard for
    /// `install_path` (refused by name if one is already held).
    pub fn new(
        install_path: PathBuf,
        config: AgentConfig,
        channel: Box<dyn ControlChannel>,
    ) -> Result<Agent, AgentError> {
        let guard = acquire(&install_path)?;
        Ok(Agent {
            guard,
            config,
            channel,
            pending_completions: Vec::new(),
        })
    }

    /// Run one work cycle.
    pub fn run_cycle(&mut self) -> Result<CycleOutcome, AgentError> {
        // (1) Completion retry first: terminal truth outranks
        // new work.
        let mut delivered = 0usize;
        let mut still_pending: Vec<CompletionReport> = Vec::new();
        for report in std::mem::take(&mut self.pending_completions) {
            match self.channel.send_completion(&report) {
                Ok(()) => delivered += 1,
                Err(_) => still_pending.push(report),
            }
        }
        self.pending_completions = still_pending;

        // (2) Poll.
        let assignment = self.channel.poll_assignment()?;

        // (3) Execute.
        let mut outcome = CycleOutcome {
            retried_delivered: delivered,
            assignment_job: None,
            run: None,
        };
        if let Some(asg) = assignment {
            outcome.assignment_job = Some(asg.job_id.clone());
            let run = run_assignment(self.channel.as_mut(), &asg, &self.config)?;
            if !run.completion_delivered {
                self.pending_completions.push(run.report.clone());
            }
            outcome.run = Some(run);
        }
        Ok(outcome)
    }
}
