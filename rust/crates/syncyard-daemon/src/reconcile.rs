//! Boot reconciliation and ghost-record reconciliation
//! (spec 038 sections 2 and 6, ERR-02/03/07/09).
//!
//! Boot reconciliation is mandatory after every start — VM
//! replacement makes it the daemon's most-executed path. It loads
//! disk state plus the off-VM records, reconciles against GitHub
//! run states, resolves disagreements **in GitHub's favor**,
//! ledgers every divergence, and never overwrites a payload
//! verdict: a record that already carries a verdict (markers /
//! verdict_source / a terminal state) keeps it, whatever GitHub
//! says later.
//!
//! Ordering uses the records' monotonic sequence (spec 038 section
//! 9): when the disk copy and the off-VM copy of one job disagree,
//! the higher sequence is the newer lifecycle state — wall clock is
//! display only.
//!
//! A **ghost** is a record whose run is terminal on GitHub while
//! the record still reads dispatched/running. Ghost completion is a
//! built-in function, not a hand edit — and it is hold-first
//! (FR-038-24): completing ghosts frees lanes, so before any ghost
//! is completed, every waiting non-maintenance job must be covered
//! by the pause record or a per-job hold, or completing them would
//! dispatch held work with no human decision (the 2026-11-07
//! geometry: 8 ghosts accidentally enforcing the owner's pause over
//! two waiting jobs).

use std::collections::BTreeMap;

use syncyard_contract::{JobState, QueueRecord};

use crate::offvm::{load_records_with_gaps, OffVmStore, KIND_PREDISPATCH, KIND_SUBMITS};
use crate::state::{DaemonState, PauseRecord};
use crate::time::now_iso;

/// A run's state as GitHub reports it.
#[derive(Debug, Clone, PartialEq)]
pub struct RunInfo {
    pub run_id: u64,
    /// `queued`, `in_progress`, `completed`, ... (GitHub vocabulary)
    pub status: String,
    /// `success`, `failure`, `cancelled`, ... once completed.
    pub conclusion: Option<String>,
}

impl RunInfo {
    pub fn is_terminal(&self) -> bool {
        self.status == "completed"
    }
}

/// GitHub-side run evidence. The live source is the GitHub API at
/// cutover; tests use fakes. Reconciliation trusts only this trait's
/// answers about runs, never local guesses.
pub trait RunStateSource: Send + Sync {
    fn run_info(&self, run_id: u64) -> Result<Option<RunInfo>, String>;
}

/// What boot reconciliation did, by name (ERR-09: recovered state
/// and gaps are announced, never implied).
#[derive(Debug, Default)]
pub struct BootReport {
    /// Jobs recovered from the off-VM submit records (by name).
    pub recovered_submits: Vec<String>,
    /// Jobs recovered from off-VM pre-dispatch records (by name).
    pub recovered_dispatches: Vec<String>,
    /// Jobs stuck in `dispatching`, handed to dispatch recovery.
    pub dispatching_to_recover: Vec<String>,
    /// Ghosts found (terminal on GitHub, live on the record).
    pub ghosts: Vec<String>,
    /// Divergences resolved in GitHub's favor (job ids).
    pub divergences: Vec<String>,
    /// Acknowledged submit ids that exist in no store (ERR-02:
    /// reported by name, never silently absorbed).
    pub unrecorded_submits: Vec<String>,
    /// Load gaps from the disk pass (unparseable records etc.).
    pub gaps: Vec<String>,
}

/// Merge the off-VM copies into RAM state, sequence-first: for the
/// same job, the copy with the higher `seq` supplies the lifecycle
/// fields; a copy carrying a payload verdict is never displaced by
/// one without (ERR-07's verdict rule applies between stores too).
fn merge_offvm(
    state: &mut DaemonState,
    offvm_jobs: BTreeMap<String, QueueRecord>,
    report: &mut BootReport,
    kind: &str,
) {
    for (id, incoming) in offvm_jobs {
        match state.jobs.get(&id) {
            None => {
                if kind == KIND_SUBMITS {
                    report.recovered_submits.push(id.clone());
                } else {
                    report.recovered_dispatches.push(id.clone());
                }
                state.put_job(incoming);
            }
            Some(existing) => {
                let existing_has_verdict =
                    existing.verdict_source.is_some() || !existing.markers.is_empty();
                let incoming_seq = incoming.seq.unwrap_or(0);
                let existing_seq = existing.seq.unwrap_or(0);
                if incoming_seq > existing_seq && !existing_has_verdict {
                    // A pre-dispatch witness upgrading a lesser copy
                    // is a recovered dispatch, counted by name.
                    if kind == KIND_PREDISPATCH
                        && matches!(incoming.state, JobState::Dispatching | JobState::Dispatched)
                        && !report.recovered_dispatches.contains(&id)
                    {
                        report.recovered_dispatches.push(id.clone());
                    }
                    let mut merged = incoming;
                    // Preserve the local verdict fields even when
                    // the newer copy lacks them.
                    if merged.verdict_source.is_none() {
                        merged.verdict_source = existing.verdict_source.clone();
                    }
                    if merged.markers.is_empty() {
                        merged.markers = existing.markers.clone();
                    }
                    state.put_job(merged);
                }
            }
        }
    }
}

/// Run boot reconciliation over already-loaded disk state.
///
/// `acknowledged_submits` is the set of submit ids some submitter
/// was told exist (e.g. from its own records); any that exist in no
/// store are named in the report (ERR-02).
pub fn boot_reconcile(
    state: &mut DaemonState,
    store: &dyn OffVmStore,
    runs: &dyn RunStateSource,
    acknowledged_submits: &[String],
    load_gaps: Vec<String>,
) -> BootReport {
    let mut report = BootReport {
        gaps: load_gaps,
        ..Default::default()
    };
    // Off-VM recovery: submit acknowledgements first, then
    // pre-dispatch witnesses (the stronger record).
    for kind in [KIND_SUBMITS, KIND_PREDISPATCH] {
        match load_records_with_gaps(store, kind) {
            Ok((records, gaps)) => {
                report.gaps.extend(gaps);
                merge_offvm(state, records, &mut report, kind);
            }
            Err(e) => {
                report
                    .gaps
                    .push(format!("off-VM {kind} records unreadable: {e}"));
            }
        }
    }
    // ERR-02: acknowledged submits that exist nowhere are named.
    for id in acknowledged_submits {
        if !state.jobs.contains_key(id) {
            report.unrecorded_submits.push(id.clone());
            let mut fields = BTreeMap::new();
            fields.insert("job_id".into(), serde_json::Value::String(id.clone()));
            fields.insert(
                "constraint".into(),
                serde_json::Value::String("unrecorded_acknowledged_submit".into()),
            );
            state.ledger("reconcile_unrecorded_submit", fields);
        }
    }
    // Per-record reconciliation against GitHub.
    let ids: Vec<String> = state.jobs.keys().cloned().collect();
    for id in ids {
        let rec = state.jobs.get(&id).expect("listed").clone();
        match rec.state {
            JobState::Dispatching => {
                report.dispatching_to_recover.push(id);
            }
            JobState::Dispatched | JobState::HeldAtCheckpoint => {
                let Some(run_id) = rec.run_id else { continue };
                let Ok(Some(info)) = runs.run_info(run_id) else {
                    continue;
                };
                if info.is_terminal() {
                    report.ghosts.push(id.clone());
                } else if rec.run_status.as_deref() != Some(info.status.as_str()) {
                    // GitHub wins on run state; the payload verdict
                    // fields are not touched here (a live record has
                    // none yet by construction).
                    state.update_job(&id, |r| {
                        r.run_status = Some(info.status.clone());
                    });
                    report.divergences.push(id.clone());
                    let mut fields = BTreeMap::new();
                    fields.insert("job_id".into(), serde_json::Value::String(id.clone()));
                    fields.insert(
                        "resolution".into(),
                        serde_json::Value::String("github_wins".into()),
                    );
                    state.ledger("reconcile_divergence", fields);
                }
            }
            _ => {}
        }
    }
    // ERR-09: the boot itself is ledgered with its accounting.
    let mut fields = BTreeMap::new();
    fields.insert(
        "recovered_submits".into(),
        serde_json::Value::Number(report.recovered_submits.len().into()),
    );
    fields.insert(
        "recovered_dispatches".into(),
        serde_json::Value::Number(report.recovered_dispatches.len().into()),
    );
    fields.insert(
        "ghosts".into(),
        serde_json::Value::Number(report.ghosts.len().into()),
    );
    fields.insert(
        "gaps".into(),
        serde_json::Value::Number(report.gaps.len().into()),
    );
    state.ledger("daemon_boot_reconciled", fields);
    report
}

/// Complete ghost records from GitHub truth, hold-first.
///
/// Step 1 (the hold): if any waiting non-maintenance job is not
/// covered by the pause or a per-job hold, a pause record is
/// written first and ledgered — completing ghosts must never free a
/// lane under held work (FR-038-24).
///
/// Step 2: each ghost's record is completed from its run's GitHub
/// state (status + conclusion + completion time), with an
/// `operator_record_repair` ledger event per record (FR-038-23).
/// A record already carrying a payload verdict keeps its markers;
/// only the run linkage is completed.
pub fn reconcile_ghosts(
    state: &mut DaemonState,
    runs: &dyn RunStateSource,
    ghost_ids: &[String],
    hold_reason: &str,
) -> Vec<String> {
    // -- hold-first --
    let mut needs_hold = false;
    for rec in state.jobs.values() {
        if rec.state == JobState::Submitted
            && !rec.maintenance
            && state.paused.is_none()
            && !state.holds.contains(&rec.id)
        {
            needs_hold = true;
            break;
        }
    }
    if needs_hold {
        state.set_paused(Some(PauseRecord {
            reason: hold_reason.to_string(),
            ts: now_iso(),
        }));
        let mut fields = BTreeMap::new();
        fields.insert(
            "reason".into(),
            serde_json::Value::String(hold_reason.to_string()),
        );
        fields.insert(
            "purpose".into(),
            serde_json::Value::String("hold_before_ghost_completion".into()),
        );
        state.ledger("ghost_hold_applied", fields);
    }
    // -- complete each ghost from GitHub truth --
    let mut completed = Vec::new();
    for id in ghost_ids {
        let Some(rec) = state.jobs.get(id).cloned() else {
            continue;
        };
        let Some(run_id) = rec.run_id else { continue };
        let Ok(Some(info)) = runs.run_info(run_id) else {
            continue;
        };
        if !info.is_terminal() {
            continue;
        }
        state.update_job(id, |r| {
            r.state = JobState::Completed;
            r.run_status = Some(info.status.clone());
            r.run_conclusion = info.conclusion.clone();
            r.completed_at = Some(now_iso());
            // Markers and verdict_source are the payload verdict:
            // never overwritten (ERR-07). If the record had none,
            // the verdict source is the GitHub conclusion itself.
            if r.verdict_source.is_none() {
                r.verdict_source = Some("github-conclusion".to_string());
            }
        });
        let mut fields = BTreeMap::new();
        fields.insert("job_id".into(), serde_json::Value::String(id.clone()));
        fields.insert("run_id".into(), serde_json::Value::Number(run_id.into()));
        fields.insert(
            "run_conclusion".into(),
            serde_json::Value::String(info.conclusion.clone().unwrap_or_default()),
        );
        state.ledger("operator_record_repair", fields);
        completed.push(id.clone());
    }
    completed
}

/// A fake run-state source for tests.
#[cfg(any(test, feature = "test-support"))]
pub mod fake {
    use super::*;
    use std::sync::Mutex;

    #[derive(Default)]
    pub struct FakeRuns {
        inner: Mutex<BTreeMap<u64, RunInfo>>,
    }

    impl FakeRuns {
        pub fn new() -> Self {
            FakeRuns::default()
        }
        pub fn set(&self, run_id: u64, status: &str, conclusion: Option<&str>) {
            self.inner.lock().unwrap().insert(
                run_id,
                RunInfo {
                    run_id,
                    status: status.to_string(),
                    conclusion: conclusion.map(|s| s.to_string()),
                },
            );
        }
    }

    impl RunStateSource for FakeRuns {
        fn run_info(&self, run_id: u64) -> Result<Option<RunInfo>, String> {
            Ok(self.inner.lock().unwrap().get(&run_id).cloned())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::offvm::DirOffVmStore;
    use crate::state::tests::sample_job;
    use crate::state::{new_queue_record, StateHome};

    fn home(tag: &str) -> StateHome {
        let dir = std::env::temp_dir().join(format!("sy-recon-{}-{}", std::process::id(), tag));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        StateHome::new(dir)
    }

    #[test]
    fn verdict_is_never_overwritten_by_reconciliation() {
        let h = home("verdict");
        let mut st = DaemonState::empty(h.clone());
        let mut rec = new_queue_record(&sample_job("done-job"));
        rec.state = JobState::Completed;
        rec.markers = vec!["PAYLOAD_VERDICT PASS".to_string()];
        rec.verdict_source = Some("markers".to_string());
        rec.run_id = Some(777);
        st.put_job(rec);
        let store = DirOffVmStore::new(h.root().join("offvm"));
        let runs = fake::FakeRuns::new();
        runs.set(777, "completed", Some("failure"));
        let report = boot_reconcile(&mut st, &store, &runs, &[], vec![]);
        let after = &st.jobs["done-job"];
        assert_eq!(after.markers, vec!["PAYLOAD_VERDICT PASS".to_string()]);
        assert_eq!(after.verdict_source.as_deref(), Some("markers"));
        assert!(report.ghosts.is_empty());
        let _ = std::fs::remove_dir_all(h.root());
    }

    #[test]
    fn one_corrupt_offvm_record_suppresses_only_itself() {
        let h = home("offvm-corrupt-one");
        let mut st = DaemonState::empty(h.clone());
        let store = DirOffVmStore::new(h.root().join("offvm"));
        let rec = new_queue_record(&sample_job("recovered-job"));
        store
            .put_record(
                crate::offvm::KIND_SUBMITS,
                "recovered-job",
                &serde_json::to_vec_pretty(&rec).unwrap(),
            )
            .unwrap();
        store
            .put_record(crate::offvm::KIND_SUBMITS, "broken-job", b"{corrupt")
            .unwrap();
        let runs = fake::FakeRuns::new();

        let report = boot_reconcile(&mut st, &store, &runs, &[], vec![]);
        assert!(st.jobs.contains_key("recovered-job"));
        assert_eq!(report.recovered_submits, vec!["recovered-job".to_string()]);
        assert!(
            report.gaps.iter().any(|g| g.contains("broken-job")),
            "the corrupt record must be named without suppressing its kind: {:?}",
            report.gaps
        );
        let _ = std::fs::remove_dir_all(h.root());
    }

    #[test]
    fn ghost_completion_is_hold_first() {
        let h = home("ghost");
        let mut st = DaemonState::empty(h.clone());
        // A waiting non-maintenance job with no hold coverage.
        st.put_job(new_queue_record(&sample_job("waiting-job")));
        // A ghost: dispatched locally, terminal on GitHub.
        let mut ghost = new_queue_record(&sample_job("ghost-job"));
        ghost.state = JobState::Dispatched;
        ghost.run_id = Some(555);
        st.put_job(ghost);
        let runs = fake::FakeRuns::new();
        runs.set(555, "completed", Some("cancelled"));
        let done = reconcile_ghosts(&mut st, &runs, &["ghost-job".to_string()], "test hold");
        assert_eq!(done, vec!["ghost-job".to_string()]);
        // The hold landed before the completion.
        assert!(st.paused.is_some());
        let events: Vec<&str> = st.ledger_buffer.iter().map(|e| e.event.as_str()).collect();
        let hold_at = events
            .iter()
            .position(|e| *e == "ghost_hold_applied")
            .unwrap();
        let repair_at = events
            .iter()
            .position(|e| *e == "operator_record_repair")
            .unwrap();
        assert!(hold_at < repair_at);
        let g = &st.jobs["ghost-job"];
        assert_eq!(g.state, JobState::Completed);
        assert_eq!(g.run_conclusion.as_deref(), Some("cancelled"));
        let _ = std::fs::remove_dir_all(h.root());
    }
}
