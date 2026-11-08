//! The streaming-progress protocol (spec 034).
//!
//! A payload may write line-oriented progress to the file named by the
//! `SYNCYARD_PROGRESS_FILE` environment variable. Grammar, exactly as
//! spec 034 section 2 defines it:
//!
//! - `STARTED` — the payload has begun executing.
//! - `MARKER <text>` — a free-text milestone, verbatim.
//! - `HEARTBEAT` — the payload is alive at write time.
//! - `PROGRESS <k>/<n> <label>` — k of n units complete, 0 <= k <= n,
//!   n >= 1.
//! - `COMPLETED <verdict-word>` — the payload's own final line.
//!
//! Unknown lines are preserved and ignored by consumers; malformed
//! `PROGRESS` lines are ignored and counted (FR-034-02).
//!
//! No progress line is ever a verdict (FR-034-03): the verdict is the
//! payload exit code from the terminal record (spec 020). In
//! particular, [`ProgressEvent::Completed`] is advisory display data
//! only — see [`ProgressSnapshot::completed_word`].

use serde::{Deserialize, Serialize};

/// One parsed line of a progress file.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ProgressEvent {
    /// `STARTED`
    Started,
    /// `MARKER <text>`
    Marker {
        /// The milestone text, verbatim from the payload.
        text: String,
    },
    /// `HEARTBEAT`
    Heartbeat,
    /// `PROGRESS <k>/<n> <label>` with 0 <= k <= n and n >= 1.
    Progress {
        /// Units complete.
        k: u64,
        /// Units total.
        n: u64,
        /// Free-text label for the unit series.
        label: String,
    },
    /// `COMPLETED <verdict-word>` — advisory only, never a verdict
    /// (FR-034-03).
    Completed {
        /// The payload's own verdict word, uninterpreted.
        verdict_word: String,
    },
    /// A line that starts as a `PROGRESS` line but does not parse.
    /// Consumers must count these and otherwise ignore them
    /// (FR-034-02); a malformed line never fails the job.
    MalformedProgress {
        /// The offending line, verbatim.
        line: String,
    },
    /// Any other line: preserved in the harvest, ignored by consumers
    /// (spec 034 section 2).
    Unknown {
        /// The line, verbatim.
        line: String,
    },
}

/// Parse one progress-file line.
///
/// Trailing CR/LF is stripped first (the file may be written from
/// PowerShell, bash, or Python callers; spec 034 FR-034-09). The parse
/// never fails: unrecognized input yields [`ProgressEvent::Unknown`]
/// or [`ProgressEvent::MalformedProgress`].
pub fn parse_line(raw: &str) -> ProgressEvent {
    let line = raw.trim_end_matches(['\r', '\n']);
    if line == "STARTED" {
        return ProgressEvent::Started;
    }
    if line == "HEARTBEAT" {
        return ProgressEvent::Heartbeat;
    }
    if let Some(text) = line.strip_prefix("MARKER ") {
        return ProgressEvent::Marker {
            text: text.to_string(),
        };
    }
    if let Some(word) = line.strip_prefix("COMPLETED ") {
        let word = word.trim();
        if !word.is_empty() {
            return ProgressEvent::Completed {
                verdict_word: word.to_string(),
            };
        }
        return ProgressEvent::Unknown {
            line: line.to_string(),
        };
    }
    if line == "PROGRESS" || line.starts_with("PROGRESS ") {
        return parse_progress(line);
    }
    ProgressEvent::Unknown {
        line: line.to_string(),
    }
}

/// Parse a `PROGRESS <k>/<n> <label>` line, or report it malformed.
fn parse_progress(line: &str) -> ProgressEvent {
    let malformed = || ProgressEvent::MalformedProgress {
        line: line.to_string(),
    };
    let rest = match line.strip_prefix("PROGRESS ") {
        Some(r) => r,
        None => return malformed(),
    };
    let (fraction, label) = match rest.split_once(' ') {
        Some((f, l)) if !l.trim().is_empty() => (f, l),
        _ => return malformed(),
    };
    let (k, n) = match fraction.split_once('/') {
        Some((k, n)) => match (k.parse::<u64>(), n.parse::<u64>()) {
            (Ok(k), Ok(n)) => (k, n),
            _ => return malformed(),
        },
        None => return malformed(),
    };
    if n < 1 || k > n {
        return malformed();
    }
    ProgressEvent::Progress {
        k,
        n,
        label: label.to_string(),
    }
}

/// Parse every line of a progress file's contents, in order.
pub fn parse_lines(contents: &str) -> Vec<ProgressEvent> {
    contents.lines().map(parse_line).collect()
}

/// One transport batch of raw progress lines for one job (spec 034
/// section 3): the backchannel agent (or the runner's log harvest)
/// copies the progress file's tail to the gatekeeper at most every
/// ~60 s per running job.
///
/// Attribution (FR-034-08): a batch belongs to a job only when its
/// run identity and log-source identity match the job's record.
/// `run_id` is the gatekeeper-side run identity for the job (a
/// GitHub run id on the door venues, a daemon-assigned local id on
/// the agent venue); batches that cannot be attributed are
/// quarantined by the consumer, never folded into the snapshot.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProgressBatch {
    /// The job these lines claim to belong to.
    pub job_id: String,
    /// Run identity the lines were observed under, if the source
    /// knows it.
    pub run_id: Option<u64>,
    /// Log-source identity (e.g. `agent:<host>`, `harvest:gh-log`).
    /// A run's source is established by its first attributed batch;
    /// later batches under a different source are foreign.
    pub source: String,
    /// Raw progress-file lines, verbatim, in file order.
    pub lines: Vec<String>,
}

/// The daemon's answer to a progress batch. Transport success is
/// not acceptance: a throttled batch's lines are retained by the
/// sender and re-sent with the next batch, so cadence enforcement
/// never loses progress.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ProgressAck {
    /// Lines folded into the job's snapshot.
    Applied,
    /// Arrived inside the ~60 s refresh window; not applied.
    Throttled,
    /// Attribution failed (run id or source mismatch); lines
    /// quarantined and counted, never displayed as the payload's.
    Quarantined,
    /// The batch reported a transport failure; the job's progress
    /// now reads "progress unknown" (FR-034-07).
    TransportUnknown,
}

/// A job assignment handed to a backchannel agent (the native
/// venue's runner surface, spec 038 section 3).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Assignment {
    /// The job to execute.
    pub job_id: String,
    /// Daemon-assigned local run identity for this execution; the
    /// agent echoes it in every progress batch and in the
    /// completion report so attribution can be checked.
    pub run_id: u64,
    /// Payload argv (the job's script path as a single element on
    /// the current venues).
    pub command: Vec<String>,
    /// Working directory for the payload, if the job named one.
    pub cwd: Option<String>,
}

/// The agent's terminal report for one assignment.
///
/// The verdict source is the payload's own exit code and nothing
/// else (spec 020; FR-034-03): the agent machinery succeeding at
/// delivering this report never makes the job a success, and a
/// `COMPLETED` progress line never overrides the exit code.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct CompletionReport {
    /// The job that was executed.
    pub job_id: String,
    /// The run identity from the assignment, echoed back.
    pub run_id: u64,
    /// The payload's exit code; `None` when the payload was killed
    /// by a signal or could not be started at all.
    pub payload_exit_code: Option<i32>,
    /// True when the payload process actually started.
    pub payload_started: bool,
    /// A named interruption that ended the run without the
    /// payload reaching a verdict of its own. The one value
    /// the fleet uses today is `sleep` (spec 039 FR-MAC-05):
    /// a job interrupted by host sleep is reported
    /// interrupted-by-sleep, NEVER as failed or passed —
    /// the spec's falsifier is structural here, because the
    /// interruption check runs before the exit code is read.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub interruption: Option<String>,
}

impl CompletionReport {
    /// The run conclusion derived from the payload exit code
    /// alone: 0 is `success`, any other code is `failure`, and a
    /// payload that never produced an exit code is `error`
    /// (it never ran to a verdict of its own). A named
    /// interruption outranks the exit code entirely: sleep
    /// interruption is its own outcome, never failed or passed
    /// (spec 039 FR-MAC-05).
    pub fn derived_conclusion(&self) -> &'static str {
        if self.interruption.as_deref() == Some("sleep") {
            return "interrupted-by-sleep";
        }
        match self.payload_exit_code {
            Some(0) => "success",
            Some(_) => "failure",
            None => "error",
        }
    }
}

/// The gatekeeper's latest progress snapshot for one running job
/// (spec 034 section 3): a fold of the job's progress events, in the
/// order they were observed.
///
/// Deliberately carries no verdict: [`ProgressSnapshot::completed_word`]
/// is the payload's advisory word only, and no field here may be
/// rendered, forwarded, or ledgered as a payload verdict (FR-034-03).
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProgressSnapshot {
    /// The payload has written `STARTED`.
    pub started: bool,
    /// Most recent `MARKER` text, if any.
    pub last_marker: Option<String>,
    /// Number of `HEARTBEAT` lines observed.
    pub heartbeats: u64,
    /// Latest `(k, n)` from a well-formed `PROGRESS` line.
    pub fraction: Option<(u64, u64)>,
    /// Label of the latest `PROGRESS` line.
    pub label: Option<String>,
    /// The payload's advisory `COMPLETED` word, if it wrote one.
    /// Never a verdict (FR-034-03).
    pub completed_word: Option<String>,
    /// Malformed `PROGRESS` lines seen (counted, never fatal).
    pub malformed_progress_count: u64,
    /// Unknown lines seen (preserved in harvest, ignored here).
    pub unknown_line_count: u64,
    /// Number of times a later snapshot's k was smaller than the
    /// greatest k previously seen for the same unit series
    /// (FR-034-06: regressions are surfaced, never silently clamped).
    pub regressions: u64,
    /// Total events folded into this snapshot.
    pub events_seen: u64,
}

impl ProgressSnapshot {
    /// An empty snapshot ("no progress reported", FR-034-05).
    pub fn new() -> Self {
        Self::default()
    }

    /// Fold one event into the snapshot.
    pub fn observe(&mut self, event: &ProgressEvent) {
        self.events_seen += 1;
        match event {
            ProgressEvent::Started => self.started = true,
            ProgressEvent::Marker { text } => self.last_marker = Some(text.clone()),
            ProgressEvent::Heartbeat => self.heartbeats += 1,
            ProgressEvent::Progress { k, n, label } => {
                if let Some((prev_k, prev_n)) = self.fraction {
                    if prev_n == *n && *k < prev_k {
                        self.regressions += 1;
                    }
                }
                self.fraction = Some((*k, *n));
                self.label = Some(label.clone());
            }
            ProgressEvent::Completed { verdict_word } => {
                self.completed_word = Some(verdict_word.clone());
            }
            ProgressEvent::MalformedProgress { .. } => self.malformed_progress_count += 1,
            ProgressEvent::Unknown { .. } => self.unknown_line_count += 1,
        }
    }

    /// Fold a whole progress file's contents into a snapshot.
    pub fn from_contents(contents: &str) -> Self {
        let mut snap = ProgressSnapshot::new();
        for event in parse_lines(contents) {
            snap.observe(&event);
        }
        snap
    }

    /// True when the payload has written nothing at all: the job
    /// displays as "no progress reported" (FR-034-05) and no component
    /// may synthesize progress on its behalf.
    pub fn is_silent(&self) -> bool {
        self.events_seen == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // T034-02 grammar-accept-and-ignore (spec 034 FR-034-02):
    // each grammar line parses to its event; unknown lines are
    // preserved as Unknown and never error.
    #[test]
    fn t034_02_grammar_accept_and_ignore() {
        assert_eq!(parse_line("STARTED"), ProgressEvent::Started);
        assert_eq!(parse_line("HEARTBEAT"), ProgressEvent::Heartbeat);
        assert_eq!(
            parse_line("MARKER phase 2 of 5 done"),
            ProgressEvent::Marker {
                text: "phase 2 of 5 done".to_string()
            }
        );
        assert_eq!(
            parse_line("PROGRESS 3/10 widgets"),
            ProgressEvent::Progress {
                k: 3,
                n: 10,
                label: "widgets".to_string()
            }
        );
        assert_eq!(
            parse_line("COMPLETED PASS"),
            ProgressEvent::Completed {
                verdict_word: "PASS".to_string()
            }
        );
        // CRLF from a PowerShell writer parses the same.
        assert_eq!(parse_line("STARTED\r\n"), ProgressEvent::Started);
        // Unknown lines are preserved verbatim and ignored.
        assert_eq!(
            parse_line("some runner chatter"),
            ProgressEvent::Unknown {
                line: "some runner chatter".to_string()
            }
        );
        // Boundary values of the PROGRESS grammar.
        assert_eq!(
            parse_line("PROGRESS 0/1 boot"),
            ProgressEvent::Progress {
                k: 0,
                n: 1,
                label: "boot".to_string()
            }
        );
        assert_eq!(
            parse_line("PROGRESS 7/7 done"),
            ProgressEvent::Progress {
                k: 7,
                n: 7,
                label: "done".to_string()
            }
        );
    }

    // T034-03 malformed-progress-counted (spec 034 FR-034-02):
    // malformed PROGRESS lines parse to MalformedProgress and are
    // counted by the snapshot without failing anything.
    #[test]
    fn t034_03_malformed_progress_counted() {
        for bad in [
            "PROGRESS",
            "PROGRESS x/y label",
            "PROGRESS 1/0 label", // n must be >= 1
            "PROGRESS 5/4 label", // k must be <= n
            "PROGRESS 1/2",       // label is part of the grammar
            "PROGRESS 1-2 label", // fraction must be k/n
            "PROGRESS / label",
        ] {
            match parse_line(bad) {
                ProgressEvent::MalformedProgress { line } => assert_eq!(line, bad),
                other => panic!("{bad:?} parsed as {other:?}, want MalformedProgress"),
            }
        }
        let snap = ProgressSnapshot::from_contents(
            "STARTED\nPROGRESS x/y label\nPROGRESS 1/4 units\nPROGRESS 9/4 units\n",
        );
        assert_eq!(snap.malformed_progress_count, 2);
        assert_eq!(snap.fraction, Some((1, 4)));
        assert!(snap.started);
    }

    #[test]
    fn snapshot_tracks_marker_heartbeat_and_completion_word() {
        let snap = ProgressSnapshot::from_contents(
            "STARTED\nMARKER warmup\nHEARTBEAT\nHEARTBEAT\nMARKER main phase\nCOMPLETED DONE\n",
        );
        assert!(snap.started);
        assert_eq!(snap.last_marker.as_deref(), Some("main phase"));
        assert_eq!(snap.heartbeats, 2);
        // The COMPLETED word is carried as advisory data only.
        assert_eq!(snap.completed_word.as_deref(), Some("DONE"));
        assert_eq!(snap.events_seen, 6);
        assert!(!snap.is_silent());
    }

    #[test]
    fn snapshot_regression_is_counted_not_clamped() {
        // FR-034-06: a later smaller k for the same series is a
        // regression, recorded as such; the displayed fraction still
        // follows the latest snapshot.
        let snap = ProgressSnapshot::from_contents("PROGRESS 5/8 units\nPROGRESS 3/8 units\n");
        assert_eq!(snap.regressions, 1);
        assert_eq!(snap.fraction, Some((3, 8)));
    }

    #[test]
    fn silent_job_snapshot_is_silent() {
        // FR-034-05: no writes at all means "no progress reported".
        let snap = ProgressSnapshot::from_contents("");
        assert!(snap.is_silent());
        assert_eq!(snap.fraction, None);
        assert_eq!(snap.completed_word, None);
    }

    #[test]
    fn wire_types_round_trip() {
        // The agent<->daemon wire types (spec 034 section 3) keep
        // their serde shape across the socket.
        let batch = ProgressBatch {
            job_id: "job-1".into(),
            run_id: Some(42),
            source: "agent:test-host".into(),
            lines: vec!["STARTED".into(), "PROGRESS 1/2 units".into()],
        };
        let text = serde_json::to_string(&batch).unwrap();
        assert_eq!(serde_json::from_str::<ProgressBatch>(&text).unwrap(), batch);

        let asg = Assignment {
            job_id: "job-1".into(),
            run_id: 42,
            command: vec!["/tmp/payload.sh".into()],
            cwd: None,
        };
        let text = serde_json::to_string(&asg).unwrap();
        assert_eq!(serde_json::from_str::<Assignment>(&text).unwrap(), asg);

        let ack = serde_json::to_string(&ProgressAck::Throttled).unwrap();
        assert_eq!(ack, "\"throttled\"");
    }

    #[test]
    fn completion_conclusion_comes_from_exit_code_only() {
        // Spec 020 / FR-034-03: the derived conclusion reads the
        // payload exit code and nothing else.
        let rep = |code, started| CompletionReport {
            job_id: "j".into(),
            run_id: 1,
            payload_exit_code: code,
            payload_started: started,
            interruption: None,
        };
        assert_eq!(rep(Some(0), true).derived_conclusion(), "success");
        assert_eq!(rep(Some(1), true).derived_conclusion(), "failure");
        assert_eq!(rep(Some(127), true).derived_conclusion(), "failure");
        // Killed by a signal, or never started: no exit code, so
        // no success is derivable — the report is an error, never
        // a pass manufactured by the wrapper.
        assert_eq!(rep(None, true).derived_conclusion(), "error");
        assert_eq!(rep(None, false).derived_conclusion(), "error");
    }

    #[test]
    fn t039_10_sleep_interruption_named_not_failed() {
        // T039-10 sleep-interruption-named-not-failed (spec
        // 039 FR-MAC-05): a sleep interruption outranks every
        // exit code — even 0 (never "passed") and even a
        // signal kill (never "failed"/"error").
        let mut rep = CompletionReport {
            job_id: "j".into(),
            run_id: 1,
            payload_exit_code: Some(0),
            payload_started: true,
            interruption: Some("sleep".into()),
        };
        assert_eq!(rep.derived_conclusion(), "interrupted-by-sleep");
        rep.payload_exit_code = None;
        assert_eq!(rep.derived_conclusion(), "interrupted-by-sleep");
        // Wire shape: an uninterrupted report does not carry
        // the field at all (Python-side readers unaffected).
        rep.interruption = None;
        rep.payload_exit_code = Some(0);
        let json = serde_json::to_string(&rep).unwrap();
        assert!(!json.contains("interruption"), "{json}");
    }
}
