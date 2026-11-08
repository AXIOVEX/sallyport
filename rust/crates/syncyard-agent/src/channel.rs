//! The agent's control channel: the venue surface between the
//! backchannel agent on a host and the gatekeeper daemon
//! (spec 034 section 3, spec 038 section 3).
//!
//! The surface is a trait so the agent's execution logic is
//! testable without a live host or daemon: [`crate::fake`] is the
//! in-memory implementation the suite runs against, and
//! [`crate::socket::SocketChannel`] is the real transport (the
//! daemon's Unix socket, newline-JSON, same protocol as the CLI).
//!
//! Verdict discipline (spec 020, FR-034-03): the channel carries
//! progress as display data and completion as a report whose only
//! verdict input is the payload's exit code. Nothing on this
//! surface can turn a wrapper success into a job success.

use std::fmt;
use std::path::PathBuf;

use syncyard_contract::{Assignment, CompletionReport, ProgressAck, ProgressBatch};

/// Everything that can go wrong on the agent side of the channel.
#[derive(Debug)]
pub enum AgentError {
    /// The transport to the daemon failed (socket, wire format).
    Transport(String),
    /// The daemon answered, but refused (ok:false). The message is
    /// the daemon's own error string, verbatim.
    Refused(String),
    /// Another agent instance already holds this install path.
    AlreadyRunning(PathBuf),
    /// The payload could not be spawned (a report with
    /// `payload_started: false` is still owed for the job).
    Spawn(String),
    /// Local configuration or filesystem problem.
    Local(String),
}

impl fmt::Display for AgentError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            AgentError::Transport(m) => write!(f, "transport: {m}"),
            AgentError::Refused(m) => write!(f, "daemon refused: {m}"),
            AgentError::AlreadyRunning(p) => {
                write!(f, "agent already running for install path {}", p.display())
            }
            AgentError::Spawn(m) => write!(f, "spawn: {m}"),
            AgentError::Local(m) => write!(f, "local: {m}"),
        }
    }
}

impl std::error::Error for AgentError {}

/// The daemon surface the agent depends on. Implementations must
/// preserve the distinction the ack carries: transport success is
/// not acceptance (`ProgressAck::Throttled` and `Quarantined` are
/// successful calls with non-applied outcomes).
pub trait ControlChannel {
    /// Ask for the next assignment for this agent's venue.
    /// `Ok(None)` means no work is currently offered.
    fn poll_assignment(&mut self) -> Result<Option<Assignment>, AgentError>;

    /// Forward one batch of progress lines. The ack says what the
    /// daemon did with them; a `Throttled` ack means the sender
    /// must retain the lines and re-send them with the next batch.
    fn send_progress(&mut self, batch: &ProgressBatch) -> Result<ProgressAck, AgentError>;

    /// Deliver the terminal report for an assignment. The report's
    /// verdict input is the payload exit code alone.
    fn send_completion(&mut self, report: &CompletionReport) -> Result<(), AgentError>;
}
