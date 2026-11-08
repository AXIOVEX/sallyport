//! Failure reporting (spec 038 FR-038-39, REP-01..07).
//!
//! Every verdict-reaching failure, crash, or failed repair
//! produces exactly one report per configured channel —
//! nothing fails silently (REP-01). Reports dedupe on a
//! structured key from which run ids, timestamps, paths, and
//! counts are excluded BY CONSTRUCTION (REP-02): the key is
//! built only from component, defect class, and a normalized
//! signature whose fragments are passed through the
//! normalizer that drops id/path/count-shaped fragments.
//! A daily cap batches overflow and below-high severity into
//! one digest, and the cap breach is visible state (REP-03).
//! Bundles are scanned for credential-shaped content; a hit
//! refuses the filing and escalates (REP-06). Severity comes
//! from a published mapping, never from the component
//! (REP-05).

use std::collections::BTreeMap;

use syncyard_contract::scan_for_credentials;

/// A report destination. Reports go only to private
/// destinations (REP-06); the channel set is configuration,
/// and each filed report goes to each channel exactly once.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Channel {
    GithubIssues,
    ClaudeNotify,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum Severity {
    Low,
    Medium,
    High,
    Critical,
}

/// The published severity mapping (REP-05). Mechanical: the
/// defect class decides, listed here in one place.
pub fn severity_for(defect_class: &str) -> Severity {
    match defect_class {
        "verdict_wrong" | "double_dispatch" | "data_loss" | "credential_exposed" => {
            Severity::Critical
        }
        "dispatch_failed" | "repair_failed" | "update_refused_tamper" | "ghost_unreconciled" => {
            Severity::High
        }
        "flush_failed" | "placement_refused" | "job_failed" => Severity::Medium,
        _ => Severity::Low,
    }
}

/// One incident to report. The bundle fields (REP-04) ride
/// along: train versions, identity, ledger tail, payload
/// verdict where one exists, capability record.
#[derive(Debug, Clone)]
pub struct Incident {
    pub component: String,
    pub defect_class: String,
    /// Signature fragments BEFORE normalization. Callers pass
    /// the incident's distinguishing facts; the normalizer —
    /// not the caller — decides which fragments are stable
    /// enough for the dedupe key.
    pub signature_fragments: Vec<String>,
    pub train: String,
    pub identity: String,
    pub ledger_tail: Vec<String>,
    pub payload_verdict: Option<String>,
    pub body: String,
}

/// The dedupe key (REP-02): component, defect class,
/// normalized signature. Run ids, timestamps, paths, and
/// counts cannot appear here — the normalizer drops those
/// fragment shapes.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub struct DedupeKey {
    pub component: String,
    pub defect_class: String,
    pub signature: String,
}

/// Drop fragments that are run ids (long digit runs),
/// timestamps (ISO-shaped), paths (separators), or bare
/// counts (pure numbers) — they differ per occurrence and
/// would defeat dedupe; everything else is lowercased and
/// kept in order.
pub fn normalize_signature(fragments: &[String]) -> String {
    let mut kept = Vec::new();
    for f in fragments {
        let t = f.trim();
        let is_run_id = t.len() >= 6 && t.bytes().all(|b| b.is_ascii_digit());
        let is_count = !t.is_empty() && t.bytes().all(|b| b.is_ascii_digit());
        let is_timestamp = t.contains('T') && (t.ends_with('Z') || t.contains('+'));
        let is_path = t.contains('/') || t.contains('\\');
        if is_run_id || is_count || is_timestamp || is_path {
            continue;
        }
        kept.push(t.to_lowercase());
    }
    kept.join("|")
}

impl Incident {
    pub fn dedupe_key(&self) -> DedupeKey {
        DedupeKey {
            component: self.component.clone(),
            defect_class: self.defect_class.clone(),
            signature: normalize_signature(&self.signature_fragments),
        }
    }
    pub fn severity(&self) -> Severity {
        severity_for(&self.defect_class)
    }
    /// Everything that would be filed, in one text — the
    /// REP-06 scan target.
    fn bundle_text(&self) -> String {
        let mut s = self.body.clone();
        s.push_str(&self.train);
        s.push_str(&self.identity);
        for line in &self.ledger_tail {
            s.push_str(line);
        }
        if let Some(v) = &self.payload_verdict {
            s.push_str(v);
        }
        s
    }
}

/// What filing an incident did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FileOutcome {
    /// Filed once per channel in `channels`.
    Filed { channels: Vec<Channel> },
    /// Same structured key already reported; the repeat was
    /// appended with this running count, no new report.
    Deduped { count: u64 },
    /// Batched into the daily digest (cap overflow or
    /// below-high severity).
    Digested,
    /// REP-06: credential-shaped content in the bundle; the
    /// filing is refused and the incident escalated. The
    /// report is NOT filed anywhere.
    RefusedCredentialScan { kinds: Vec<&'static str> },
}

/// The reporting queue: dedupe counts, the daily cap, the
/// digest, and the cap-breach flag the tray surfaces.
#[derive(Debug)]
pub struct ReportQueue {
    pub channels: Vec<Channel>,
    pub daily_cap: usize,
    counts: BTreeMap<DedupeKey, u64>,
    filed_today: usize,
    digest: Vec<Incident>,
    pub cap_breached: bool,
    /// Channel filings actually made (channel -> reports):
    /// the exactly-one evidence (REP-01/REP-07: reporting
    /// loss never blocks execution — this queue is in-memory
    /// and bounded by the cap + digest).
    pub filings: Vec<(Channel, DedupeKey)>,
}

impl ReportQueue {
    pub fn new(channels: Vec<Channel>, daily_cap: usize) -> Self {
        ReportQueue {
            channels,
            daily_cap,
            counts: BTreeMap::new(),
            filed_today: 0,
            digest: Vec::new(),
            cap_breached: false,
            filings: Vec::new(),
        }
    }

    pub fn digest_len(&self) -> usize {
        self.digest.len()
    }

    pub fn count_for(&self, key: &DedupeKey) -> u64 {
        self.counts.get(key).copied().unwrap_or(0)
    }

    /// File one incident. Order matters: scan first (a
    /// poisoned bundle is never filed and never deduped into
    /// legitimacy), then dedupe, then the cap.
    pub fn file(&mut self, incident: Incident) -> FileOutcome {
        let hits = scan_for_credentials(&incident.bundle_text());
        if !hits.is_empty() {
            return FileOutcome::RefusedCredentialScan {
                kinds: hits.iter().map(|h| h.kind).collect(),
            };
        }
        let key = incident.dedupe_key();
        if let Some(count) = self.counts.get_mut(&key) {
            *count += 1;
            return FileOutcome::Deduped { count: *count };
        }
        self.counts.insert(key.clone(), 1);
        let severity = incident.severity();
        if self.filed_today >= self.daily_cap {
            self.cap_breached = true;
            self.digest.push(incident);
            return FileOutcome::Digested;
        }
        if severity < Severity::High {
            // Below-high severity batches into the digest
            // rather than opening an issue per occurrence.
            self.digest.push(incident);
            return FileOutcome::Digested;
        }
        self.filed_today += 1;
        for ch in &self.channels {
            self.filings.push((*ch, key.clone()));
        }
        FileOutcome::Filed {
            channels: self.channels.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn incident(class: &str, fragments: &[&str]) -> Incident {
        Incident {
            component: "daemon".into(),
            defect_class: class.into(),
            signature_fragments: fragments.iter().map(|s| s.to_string()).collect(),
            train: "0.4.0".into(),
            identity: "tower-compute".into(),
            ledger_tail: vec!["event a".into()],
            payload_verdict: Some("failure".into()),
            body: "a thing broke".into(),
        }
    }

    fn queue() -> ReportQueue {
        ReportQueue::new(vec![Channel::GithubIssues, Channel::ClaudeNotify], 2)
    }

    // T038-43 rep01-exactly-one-report: one incident produces
    // exactly one report per configured channel; a repeat of
    // the same incident produces none.
    #[test]
    fn t038_43_rep01_exactly_one_report() {
        let mut q = queue();
        let inc = incident("dispatch_failed", &["lane-quick"]);
        match q.file(inc.clone()) {
            FileOutcome::Filed { channels } => assert_eq!(channels.len(), 2),
            other => panic!("expected Filed, got {other:?}"),
        }
        assert_eq!(q.filings.len(), 2, "one filing per channel, no more");
        assert_eq!(
            q.file(inc),
            FileOutcome::Deduped { count: 2 },
            "the repeat appends with a count, never a second report"
        );
        assert_eq!(q.filings.len(), 2);
    }

    // T038-44 rep02-structured-dedupe: the key excludes run
    // ids, timestamps, paths, and counts by construction —
    // two incidents differing only in those dedupe together.
    #[test]
    fn t038_44_rep02_structured_dedupe() {
        let a = incident(
            "dispatch_failed",
            &[
                "lane-quick",
                "42473559588",
                "2026-11-08T11:01:15Z",
                "C:\\work\\job.ps1",
                "3",
            ],
        );
        let b = incident(
            "dispatch_failed",
            &[
                "lane-quick",
                "42499999999",
                "2026-11-08T12:44:00Z",
                "D:\\other\\x.ps1",
                "7",
            ],
        );
        assert_eq!(a.dedupe_key(), b.dedupe_key());
        let mut q = queue();
        assert!(matches!(q.file(a), FileOutcome::Filed { .. }));
        assert_eq!(q.file(b), FileOutcome::Deduped { count: 2 });
    }

    // T038-45 rep03-daily-cap-digest: below-high batches to
    // the digest; past the cap, high severity digests too and
    // the breach is visible state.
    #[test]
    fn t038_45_rep03_daily_cap_digest() {
        let mut q = queue();
        // Below-high: digest immediately, no issue.
        assert_eq!(
            q.file(incident("job_failed", &["a"])),
            FileOutcome::Digested
        );
        // Two high-severity filings fit the cap of 2.
        assert!(matches!(
            q.file(incident("dispatch_failed", &["a"])),
            FileOutcome::Filed { .. }
        ));
        assert!(matches!(
            q.file(incident("repair_failed", &["b"])),
            FileOutcome::Filed { .. }
        ));
        assert!(!q.cap_breached);
        // The third distinct high-severity incident overflows
        // into the digest and the breach is visible.
        assert_eq!(
            q.file(incident("ghost_unreconciled", &["c"])),
            FileOutcome::Digested
        );
        assert!(q.cap_breached, "cap breach is visible in the tray surface");
        assert_eq!(q.digest_len(), 2);
    }

    // T038-46 rep06-credential-scan-refusal: a bundle with
    // credential-shaped content is refused and escalated —
    // never filed, never deduped into legitimacy.
    #[test]
    fn t038_46_rep06_credential_scan_refusal() {
        let mut q = queue();
        let mut inc = incident("dispatch_failed", &["lane-quick"]);
        inc.body = "log line: token = ghp_0123456789abcdefghij".into();
        match q.file(inc) {
            FileOutcome::RefusedCredentialScan { kinds } => {
                assert_eq!(kinds, vec!["github_pat_classic"])
            }
            other => panic!("expected refusal, got {other:?}"),
        }
        assert!(q.filings.is_empty(), "nothing was filed");
        assert_eq!(q.digest_len(), 0);
    }

    #[test]
    fn severity_mapping_is_mechanical() {
        assert_eq!(severity_for("verdict_wrong"), Severity::Critical);
        assert_eq!(severity_for("dispatch_failed"), Severity::High);
        assert_eq!(severity_for("job_failed"), Severity::Medium);
        assert_eq!(severity_for("something_new"), Severity::Low);
    }
}
