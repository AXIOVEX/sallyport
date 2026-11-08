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
}
