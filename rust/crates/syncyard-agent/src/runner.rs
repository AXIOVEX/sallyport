//! Payload execution (spec 034 sections 1-3).
//!
//! For one assignment the agent:
//! 1. prepares the per-job directory and the progress file path,
//!    creating the parent directory *before* payload start
//!    (FR-034-01), and launches the payload with
//!    `SYNCYARD_PROGRESS_FILE` set to that path;
//! 2. tails the progress file on the forward cadence and sends
//!    new lines to the daemon as progress batches — display data
//!    only, never verdicts;
//! 3. on payload exit, delivers a completion report whose only
//!    verdict input is the payload's own exit code (spec 020).
//!
//! Failure discipline (FR-034-07): progress transport failures
//! never touch the payload or the verdict path. Lines that could
//! not be delivered (transport error, or a daemon throttle) are
//! retained and retried on the next tick; undelivered progress at
//! payload end is counted as degradation in the outcome, and the
//! completion report still goes out (the agent retries it at the
//! cycle level — see [`crate::agent::Agent`]).

use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use syncyard_contract::{Assignment, CompletionReport, ProgressAck, ProgressBatch};

use crate::channel::{AgentError, ControlChannel};
use crate::spawn::apply_plan;

/// Spec 034 section 3's transport cadence: the progress file's
/// tail is forwarded at most every ~60 s per running job.
pub const DEFAULT_FORWARD_INTERVAL_S: u64 = 60;

/// Agent execution configuration.
#[derive(Debug, Clone)]
pub struct AgentConfig {
    /// Per-job work directories live under `<work_root>/jobs/`.
    pub work_root: PathBuf,
    /// Log-source identity stamped on every progress batch
    /// (spec 034 FR-034-08 attribution), e.g. `agent:<host>`.
    pub source_id: String,
    /// How often the progress file is tailed and forwarded.
    pub forward_interval: Duration,
}

impl AgentConfig {
    pub fn new(work_root: PathBuf, source_id: String) -> Self {
        AgentConfig {
            work_root,
            source_id,
            forward_interval: Duration::from_secs(DEFAULT_FORWARD_INTERVAL_S),
        }
    }
}

/// The per-job progress file path (FR-034-01: per-job, set in
/// the payload's environment before it starts).
pub fn progress_path(work_root: &Path, job_id: &str) -> PathBuf {
    work_root.join("jobs").join(job_id).join("progress.log")
}

/// What one execution produced. The completion report is the
/// terminal record's input; the counters are the agent's own
/// account of its transport health during the run.
#[derive(Debug)]
pub struct RunOutcome {
    pub report: CompletionReport,
    /// True when the completion report was delivered.
    pub completion_delivered: bool,
    pub batches_applied: u64,
    pub batches_throttled: u64,
    pub batches_quarantined: u64,
    /// Progress lines retained but never delivered (transport
    /// down at payload end). Display data lost; verdict intact.
    pub lines_undelivered: u64,
    /// True when any part of progress transport degraded
    /// (FR-034-07's isolation: degradation is reported, never
    /// hidden, and never affects the verdict).
    pub progress_degraded: bool,
}

/// Read the lines appended to `path` since `offset`. A trailing
/// partial line (no newline yet) is held in `partial` unless
/// `final` is set — a half-written line is never forwarded.
fn read_new_lines(
    path: &Path,
    offset: &mut u64,
    partial: &mut String,
    final_flush: bool,
) -> Vec<String> {
    let mut file = match std::fs::File::open(path) {
        Ok(f) => f,
        Err(_) => return Vec::new(),
    };
    if file.seek(SeekFrom::Start(*offset)).is_err() {
        return Vec::new();
    }
    let mut chunk = String::new();
    if file.read_to_string(&mut chunk).is_err() {
        return Vec::new();
    }
    *offset += chunk.len() as u64;
    if chunk.is_empty() && partial.is_empty() {
        return Vec::new();
    }
    let mut text = std::mem::take(partial);
    text.push_str(&chunk);
    if final_flush {
        return text
            .lines()
            .filter(|l| !l.is_empty())
            .map(|l| l.trim_end_matches('\r').to_string())
            .collect();
    }
    let mut lines: Vec<String> = Vec::new();
    let mut rest = text.as_str();
    while let Some(idx) = rest.find('\n') {
        let line = rest[..idx].trim_end_matches('\r');
        if !line.is_empty() {
            lines.push(line.to_string());
        }
        rest = &rest[idx + 1..];
    }
    *partial = rest.to_string();
    lines
}

/// Send retained lines once; returns how the daemon answered.
/// Transport errors leave the lines retained and count as
/// degradation (handled by the caller through the outcome).
fn flush_pending(
    channel: &mut dyn ControlChannel,
    assignment: &Assignment,
    source_id: &str,
    pending: &mut Vec<String>,
    outcome_counts: &mut (u64, u64, u64, bool),
) {
    if pending.is_empty() {
        return;
    }
    let batch = ProgressBatch {
        job_id: assignment.job_id.clone(),
        run_id: Some(assignment.run_id),
        source: source_id.to_string(),
        lines: pending.clone(),
    };
    match channel.send_progress(&batch) {
        Ok(ProgressAck::Applied) => {
            outcome_counts.0 += 1;
            pending.clear();
        }
        Ok(ProgressAck::Throttled) => {
            // The daemon's cadence window: retain and re-send
            // with the next tick. Nothing is lost.
            outcome_counts.1 += 1;
        }
        Ok(ProgressAck::Quarantined) => {
            // Our own lines judged foreign: an attribution
            // defect, counted and dropped (re-sending the same
            // lines would quarantine forever).
            outcome_counts.2 += 1;
            outcome_counts.3 = true;
            pending.clear();
        }
        Ok(ProgressAck::TransportUnknown) => {
            outcome_counts.3 = true;
            pending.clear();
        }
        Err(_) => {
            // Transport down: retain for the next tick; the
            // payload and the verdict path are unaffected.
            outcome_counts.3 = true;
        }
    }
}

/// Execute one assignment end to end.
pub fn run_assignment(
    channel: &mut dyn ControlChannel,
    assignment: &Assignment,
    config: &AgentConfig,
) -> Result<RunOutcome, AgentError> {
    let job_dir = config.work_root.join("jobs").join(&assignment.job_id);
    // FR-034-01: the parent directory of the progress file is
    // created before the payload starts.
    std::fs::create_dir_all(&job_dir).map_err(|e| {
        AgentError::Local(format!("job dir {}: {e}", job_dir.display()))
    })?;
    let pfile = progress_path(&config.work_root, &assignment.job_id);
    // A fresh, empty progress file for this run (truncating any
    // stale file a previous attempt left under the same job dir).
    std::fs::File::create(&pfile)
        .map_err(|e| AgentError::Local(format!("progress file {}: {e}", pfile.display())))?;

    let mut counts: (u64, u64, u64, bool) = (0, 0, 0, false);
    let report;
    if assignment.command.is_empty() {
        // Nothing to execute: the payload never started. The
        // report says so; the derived conclusion is "error",
        // never a manufactured success.
        report = CompletionReport {
            job_id: assignment.job_id.clone(),
            run_id: assignment.run_id,
            payload_exit_code: None,
            payload_started: false,
        };
    } else {
        let stdout_log = std::fs::File::create(job_dir.join("stdout.log"))
            .map_err(|e| AgentError::Local(format!("stdout log: {e}")))?;
        let stderr_log = std::fs::File::create(job_dir.join("stderr.log"))
            .map_err(|e| AgentError::Local(format!("stderr log: {e}")))?;
        let mut cmd = Command::new(&assignment.command[0]);
        cmd.args(&assignment.command[1..])
            .env("SYNCYARD_PROGRESS_FILE", &pfile)
            .stdin(Stdio::null())
            .stdout(Stdio::from(stdout_log))
            .stderr(Stdio::from(stderr_log));
        if let Some(cwd) = &assignment.cwd {
            cmd.current_dir(cwd);
        }
        apply_plan(&mut cmd);
        match cmd.spawn() {
            Err(_) => {
                report = CompletionReport {
                    job_id: assignment.job_id.clone(),
                    run_id: assignment.run_id,
                    payload_exit_code: None,
                    payload_started: false,
                };
            }
            Ok(mut child) => {
                let mut offset: u64 = 0;
                let mut partial = String::new();
                let mut pending: Vec<String> = Vec::new();
                let exit_code;
                loop {
                    pending.extend(read_new_lines(&pfile, &mut offset, &mut partial, false));
                    flush_pending(channel, assignment, &config.source_id, &mut pending, &mut counts);
                    match child.try_wait() {
                        Ok(Some(status)) => {
                            exit_code = status.code();
                            break;
                        }
                        Ok(None) => std::thread::sleep(config.forward_interval),
                        Err(e) => {
                            return Err(AgentError::Local(format!(
                                "waiting on payload: {e}"
                            )));
                        }
                    }
                }
                // Final tail: everything the payload wrote,
                // including a last line without a newline.
                pending.extend(read_new_lines(&pfile, &mut offset, &mut partial, true));
                flush_pending(channel, assignment, &config.source_id, &mut pending, &mut counts);
                report = CompletionReport {
                    job_id: assignment.job_id.clone(),
                    run_id: assignment.run_id,
                    payload_exit_code: exit_code,
                    payload_started: true,
                };
                let undelivered = pending.len() as u64;
                return Ok(finish(channel, report, counts, undelivered));
            }
        }
    }
    Ok(finish(channel, report, counts, 0))
}

fn finish(
    channel: &mut dyn ControlChannel,
    report: CompletionReport,
    counts: (u64, u64, u64, bool),
    lines_undelivered: u64,
) -> RunOutcome {
    let delivered = channel.send_completion(&report).is_ok();
    RunOutcome {
        completion_delivered: delivered,
        report,
        batches_applied: counts.0,
        batches_throttled: counts.1,
        batches_quarantined: counts.2,
        lines_undelivered,
        progress_degraded: counts.3 || lines_undelivered > 0,
    }
}
