//! Venue lanes and the dispatch scan (spec 038 section 3, spec 030
//! folded in).
//!
//! A lane binds to a **venue**, not a runner label. Occupancy is
//! computed settle-first: a run that is terminal on GitHub no longer
//! holds its lane, even before the harvest lands the completion on
//! the record. The scan is work-conserving: a job blocked by a full
//! lane never stops later jobs (in other lanes, or behind it) from
//! dispatching. Caps bind exactly. Starvation past
//! `starvation_after_s` (default 900 s) ledgers `dispatch_starved`
//! once per episode, and dispatch after a starved episode ledgers
//! `dispatch_unstarved` once.

use std::collections::{BTreeMap, BTreeSet};

use syncyard_contract::{JobState, QueueRecord};

use crate::state::DaemonState;
use crate::time::unix_from_iso;

/// Default starvation threshold, seconds (spec 030).
pub const DEFAULT_STARVATION_AFTER_S: i64 = 900;

/// A lane: a name, the venue it binds to, and an exact cap on
/// concurrently unsettled runs.
#[derive(Debug, Clone, PartialEq)]
pub struct Lane {
    pub name: String,
    pub venue: String,
    pub cap: usize,
}

/// Runs known terminal (settled) from GitHub-side evidence, keyed by
/// run id. Settle-first occupancy consults this set ahead of the
/// records' own states.
#[derive(Debug, Default)]
pub struct SettledRuns {
    pub run_ids: BTreeSet<u64>,
}

impl SettledRuns {
    pub fn mark(&mut self, run_id: u64) {
        self.run_ids.insert(run_id);
    }
    pub fn is_settled(&self, run_id: u64) -> bool {
        self.run_ids.contains(&run_id)
    }
}

/// Does this record currently hold a lane slot?
fn occupies(rec: &QueueRecord, settled: &SettledRuns) -> bool {
    match rec.state {
        JobState::Dispatching | JobState::Dispatched | JobState::HeldAtCheckpoint => {
            // Settle-first: a terminal run frees the slot even if
            // the record has not been harvested yet.
            match rec.run_id {
                Some(id) => !settled.is_settled(id),
                None => true,
            }
        }
        _ => false,
    }
}

/// Current occupancy per lane name.
pub fn occupancy(
    jobs: &BTreeMap<String, QueueRecord>,
    settled: &SettledRuns,
) -> BTreeMap<String, usize> {
    let mut occ: BTreeMap<String, usize> = BTreeMap::new();
    for rec in jobs.values() {
        if occupies(rec, settled) {
            if let Some(lane) = &rec.lane {
                *occ.entry(lane.clone()).or_default() += 1;
            }
        }
    }
    occ
}

/// The work-conserving scan: waiting jobs in dispatch order
/// (priority desc, then submission sequence, then id), each checked
/// against its own lane's cap and the daemon's hold state. A blocked
/// job is skipped, never a scan stopper. Returns the job ids to
/// dispatch now, in order.
pub fn scan(state: &DaemonState, lanes: &[Lane], settled: &SettledRuns) -> Vec<String> {
    let caps: BTreeMap<&str, &Lane> = lanes.iter().map(|l| (l.name.as_str(), l)).collect();
    let mut occ = occupancy(&state.jobs, settled);
    let mut waiting: Vec<&QueueRecord> = state
        .jobs
        .values()
        .filter(|r| r.state == JobState::Submitted)
        .collect();
    waiting.sort_by(|a, b| {
        b.priority
            .cmp(&a.priority)
            .then_with(|| a.seq.unwrap_or(0).cmp(&b.seq.unwrap_or(0)))
            .then_with(|| a.id.cmp(&b.id))
    });
    let mut out = Vec::new();
    for rec in waiting {
        if state.dispatch_held(rec).is_some() {
            continue;
        }
        let Some(lane_name) = &rec.lane else {
            continue; // unrouted jobs wait for routing, not dispatch
        };
        let Some(lane) = caps.get(lane_name.as_str()) else {
            continue; // unknown lane: a named constraint upstream
        };
        // Venue binding: the job's venue must match the lane's
        // venue (`auto` matches any lane until routed).
        if rec.venue != "auto" && rec.venue != lane.venue {
            continue;
        }
        let used = occ.get(lane_name.as_str()).copied().unwrap_or(0);
        if used >= lane.cap {
            continue; // this lane is full; later jobs still scan
        }
        *occ.entry(lane_name.clone()).or_default() += 1;
        out.push(rec.id.clone());
    }
    out
}

/// Starvation tracking (spec 030): ledger `dispatch_starved`
/// exactly once per episode per job, and `dispatch_unstarved` when
/// a starved job finally dispatches.
#[derive(Debug, Default)]
pub struct StarvationTracker {
    starved: BTreeSet<String>,
}

impl StarvationTracker {
    /// Check waiting jobs against the threshold at `now_unix`.
    /// Events are ledgered into `state`.
    pub fn check(&mut self, state: &mut DaemonState, now_unix: i64, starvation_after_s: i64) {
        let mut newly: Vec<(String, i64)> = Vec::new();
        for rec in state.jobs.values() {
            if rec.state != JobState::Submitted || self.starved.contains(&rec.id) {
                continue;
            }
            if state.dispatch_held(rec).is_some() {
                continue; // held jobs are not starving; they are held
            }
            if let Some(created) = unix_from_iso(&rec.created_at) {
                let age = now_unix - created;
                if age >= starvation_after_s {
                    newly.push((rec.id.clone(), age));
                }
            }
        }
        for (id, age) in newly {
            self.starved.insert(id.clone());
            let mut fields = BTreeMap::new();
            fields.insert("job_id".into(), serde_json::Value::String(id));
            fields.insert("age_s".into(), serde_json::Value::Number(age.into()));
            state.ledger("dispatch_starved", fields);
        }
    }

    /// Note that `job_id` dispatched; if it was starved, ledger the
    /// episode's close exactly once.
    pub fn note_dispatched(&mut self, state: &mut DaemonState, job_id: &str) {
        if self.starved.remove(job_id) {
            let mut fields = BTreeMap::new();
            fields.insert(
                "job_id".into(),
                serde_json::Value::String(job_id.to_string()),
            );
            state.ledger("dispatch_unstarved", fields);
        }
    }

    pub fn is_starved(&self, job_id: &str) -> bool {
        self.starved.contains(job_id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::tests::sample_job;
    use crate::state::{new_queue_record, DaemonState, StateHome};

    fn state_with(jobs: Vec<QueueRecord>) -> DaemonState {
        let home =
            StateHome::new(std::env::temp_dir().join(format!("sy-lanes-{}", std::process::id())));
        let mut st = DaemonState::empty(home);
        for j in jobs {
            st.put_job(j);
        }
        st
    }

    fn rec(id: &str, lane: &str, prio: i64) -> QueueRecord {
        let mut j = sample_job(id);
        j.lane = Some(lane.to_string());
        j.priority = prio;
        new_queue_record(&j)
    }

    #[test]
    fn caps_bind_exactly_and_blocked_head_does_not_stop_scan() {
        let mut a = rec("a", "quick", 10);
        a.state = JobState::Dispatched;
        a.run_id = Some(111);
        let b = rec("b", "quick", 9); // head of quick queue, lane full
        let c = rec("c", "long", 1); // later job, other lane free
        let st = state_with(vec![a, b, c]);
        let lanes = vec![
            Lane {
                name: "quick".into(),
                venue: "native".into(),
                cap: 1,
            },
            Lane {
                name: "long".into(),
                venue: "native".into(),
                cap: 1,
            },
        ];
        let out = scan(&st, &lanes, &SettledRuns::default());
        // b is blocked by the full quick lane; c still dispatches.
        assert_eq!(out, vec!["c".to_string()]);
    }

    #[test]
    fn settled_run_frees_its_slot_before_harvest() {
        let mut a = rec("a", "quick", 10);
        a.state = JobState::Dispatched; // record not yet harvested
        a.run_id = Some(111);
        let b = rec("b", "quick", 9);
        let st = state_with(vec![a, b]);
        let lanes = vec![Lane {
            name: "quick".into(),
            venue: "native".into(),
            cap: 1,
        }];
        let mut settled = SettledRuns::default();
        settled.mark(111);
        let out = scan(&st, &lanes, &settled);
        assert_eq!(out, vec!["b".to_string()]);
    }

    #[test]
    fn starvation_ledgers_once_per_episode() {
        let mut j = rec("old", "quick", 0);
        j.created_at = "2026-11-08T00:00:00Z".to_string();
        let mut st = state_with(vec![j]);
        let mut tracker = StarvationTracker::default();
        let t0 = crate::time::unix_from_iso("2026-11-08T01:00:00Z").unwrap();
        tracker.check(&mut st, t0, DEFAULT_STARVATION_AFTER_S);
        tracker.check(&mut st, t0 + 3600, DEFAULT_STARVATION_AFTER_S);
        let starved_events = st
            .ledger_buffer
            .iter()
            .filter(|e| e.event == "dispatch_starved")
            .count();
        assert_eq!(starved_events, 1);
        tracker.note_dispatched(&mut st, "old");
        tracker.note_dispatched(&mut st, "old");
        let un = st
            .ledger_buffer
            .iter()
            .filter(|e| e.event == "dispatch_unstarved")
            .count();
        assert_eq!(un, 1);
    }
}
