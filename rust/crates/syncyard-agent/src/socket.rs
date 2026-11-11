//! The real control channel: the daemon's Unix socket
//! (newline-delimited JSON, the same protocol the CLI speaks).
//! One connection per call; the daemon is the only writer of
//! queue state, the agent only ever sends requests.

use std::cell::Cell;
#[cfg(unix)]
use std::io::{BufRead, BufReader, Read, Write};
#[cfg(unix)]
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::time::Duration;

use syncyard_contract::{
    Assignment, CompletionReport, ProgressAck, ProgressBatch, CONTRACT_VERSION,
};

use crate::channel::{AgentError, ControlChannel};

/// Maximum bytes in one daemon response line (the same bound the
/// daemon applies to requests).
pub const MAX_SOCKET_LINE_BYTES: usize = 1_048_576;

/// Connect/read/write timeout for one daemon exchange: a daemon
/// that accepts and never answers must stall one call, not the
/// agent's whole cycle, forever.
pub const SOCKET_IO_TIMEOUT: Duration = Duration::from_secs(10);

#[cfg(unix)]
fn read_line_bounded<R: BufRead>(reader: &mut R, bound: usize) -> Result<String, AgentError> {
    let mut line = String::new();
    reader
        .by_ref()
        .take((bound + 1) as u64)
        .read_line(&mut line)
        .map_err(|e| AgentError::Transport(format!("read: {e}")))?;
    if line.len() > bound {
        return Err(AgentError::Transport(format!(
            "response line exceeds the {bound}-byte limit"
        )));
    }
    Ok(line)
}

pub struct SocketChannel {
    /// The daemon's state home (its `gk.sock` lives there).
    pub daemon_home: PathBuf,
    /// Venue this agent serves (the daemon's lane venue name).
    pub venue: String,
    /// This agent's identity, sent with polls and used as the
    /// log-source identity (`agent:<id>`) on progress batches.
    pub agent_id: String,
    /// Whether the contract-version hello has been exchanged on
    /// this channel instance (once per process, before the first
    /// real request).
    hello_sent: Cell<bool>,
}

impl SocketChannel {
    pub fn new(daemon_home: PathBuf, venue: &str, agent_id: &str) -> Self {
        SocketChannel {
            daemon_home,
            venue: venue.to_string(),
            agent_id: agent_id.to_string(),
            hello_sent: Cell::new(false),
        }
    }

    /// The log-source identity this agent's batches carry
    /// (spec 034 FR-034-08 attribution).
    pub fn source_id(&self) -> String {
        format!("agent:{}", self.agent_id)
    }

    /// The socket transport is Unix-only in this build; on
    /// other platforms the channel refuses by name (the
    /// Windows named-pipe transport is not yet wired — a
    /// named gap, never a silent stub).
    #[cfg(not(unix))]
    fn raw_call(&self, _req: &serde_json::Value) -> Result<serde_json::Value, AgentError> {
        Err(AgentError::Transport(
            "the daemon socket transport is Unix-only in this build".to_string(),
        ))
    }

    /// One daemon exchange, preceded (once per channel instance)
    /// by the contract-version hello: the daemon refuses a
    /// mismatched agent by name before any real request is served.
    fn call(&self, req: &serde_json::Value) -> Result<serde_json::Value, AgentError> {
        if !self.hello_sent.get() {
            self.raw_call(&serde_json::json!({
                "verb": "agent_hello",
                "agent": self.agent_id,
                "venue": self.venue,
                "contract_version": CONTRACT_VERSION,
            }))?;
            self.hello_sent.set(true);
        }
        self.raw_call(req)
    }

    #[cfg(unix)]
    fn raw_call(&self, req: &serde_json::Value) -> Result<serde_json::Value, AgentError> {
        let sock = self.daemon_home.join("gk.sock");
        let mut stream = UnixStream::connect(&sock)
            .map_err(|e| AgentError::Transport(format!("connect {}: {e}", sock.display())))?;
        stream
            .set_read_timeout(Some(SOCKET_IO_TIMEOUT))
            .and_then(|_| stream.set_write_timeout(Some(SOCKET_IO_TIMEOUT)))
            .map_err(|e| AgentError::Transport(format!("set timeout: {e}")))?;
        let mut bytes = serde_json::to_vec(req).unwrap_or_default();
        bytes.push(b'\n');
        stream
            .write_all(&bytes)
            .and_then(|_| stream.flush())
            .map_err(|e| AgentError::Transport(format!("write: {e}")))?;
        let mut reader = BufReader::new(stream);
        let line = read_line_bounded(&mut reader, MAX_SOCKET_LINE_BYTES)?;
        let resp: serde_json::Value = serde_json::from_str(line.trim())
            .map_err(|e| AgentError::Transport(format!("response does not parse: {e}")))?;
        if resp.get("ok").and_then(|v| v.as_bool()) == Some(true) {
            Ok(resp)
        } else {
            Err(AgentError::Refused(
                resp.get("error")
                    .and_then(|v| v.as_str())
                    .unwrap_or("daemon error without a message")
                    .to_string(),
            ))
        }
    }

    /// Tell the daemon this job's progress transport has failed
    /// (FR-034-07): the job's progress degrades to "progress
    /// unknown" daemon-side. Best-effort by nature — if the
    /// channel itself is down, this fails too, and nothing else
    /// depends on it.
    pub fn send_progress_failure(&self, job_id: &str, run_id: u64) -> Result<(), AgentError> {
        self.call(&serde_json::json!({
            "verb": "progress",
            "job_id": job_id,
            "run_id": run_id,
            "source": self.source_id(),
            "transport_ok": false,
        }))?;
        Ok(())
    }
}

impl ControlChannel for SocketChannel {
    fn poll_assignment(&mut self) -> Result<Option<Assignment>, AgentError> {
        let resp = self.call(&serde_json::json!({
            "verb": "agent_poll",
            "venue": self.venue,
            "agent": self.agent_id,
        }))?;
        match resp.get("assignment") {
            Some(serde_json::Value::Null) | None => Ok(None),
            Some(v) => serde_json::from_value::<Assignment>(v.clone())
                .map(Some)
                .map_err(|e| AgentError::Transport(format!("assignment does not parse: {e}"))),
        }
    }

    fn send_progress(&mut self, batch: &ProgressBatch) -> Result<ProgressAck, AgentError> {
        let resp = self.call(&serde_json::json!({
            "verb": "progress",
            "job_id": batch.job_id,
            "run_id": batch.run_id,
            "source": batch.source,
            "lines": batch.lines,
        }))?;
        match resp.get("ack") {
            Some(v) => serde_json::from_value::<ProgressAck>(v.clone())
                .map_err(|e| AgentError::Transport(format!("ack does not parse: {e}"))),
            None => Err(AgentError::Transport(
                "progress response carries no ack".into(),
            )),
        }
    }

    fn send_completion(&mut self, report: &CompletionReport) -> Result<(), AgentError> {
        self.call(&serde_json::json!({
            "verb": "agent_completion",
            "job_id": report.job_id,
            "run_id": report.run_id,
            "payload_exit_code": report.payload_exit_code,
            "payload_started": report.payload_started,
            "interruption": report.interruption,
        }))?;
        Ok(())
    }
}
