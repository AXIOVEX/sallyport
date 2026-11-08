//! The two-phase ledgered dispatch protocol (spec 038 FR-038-08,
//! ERR-01).
//!
//! Dispatch is the one operation with an outside witness, so it is
//! staged so that a crash at any step is recoverable with zero
//! double dispatches and zero lost dispatches:
//!
//!   1. `intent`    — the job record goes to `dispatching`, the
//!      idempotency key is computed, and a `dispatching` ledger
//!      event is buffered. Nothing external has happened yet.
//!   2. `witness`   — the pre-dispatch record (carrying the
//!      idempotency key) is written to the off-VM store and
//!      confirmed by read-back. From here the dispatch is
//!      recoverable after any crash, including VM loss.
//!   3. `trigger`   — the dispatcher is asked for a run under the
//!      idempotency key. A dispatcher that already has a run under
//!      the key returns it instead of starting a second one.
//!   4. `capture`   — the run id is recorded on the job and in the
//!      witness record.
//!   5. `commit`    — the job goes to `dispatched`, a `dispatched`
//!      ledger event is buffered, and state flushes (a flush
//!      failure here is a non-event: RAM + the witness record hold
//!      the truth).
//!
//! The idempotency key is derived from the job id, its submission
//! sequence, the payload script hash, and the fencing epoch, and is
//! stored on the record (`extra["idempotency_key"]`), in the ledger
//! event, and in the witness record — verifiable from GitHub-side
//! evidence alone (the witness record) without trusting local disk.
//!
//! The GitHub transport sits behind the [`Dispatcher`] trait. P2
//! implements and fault-tests the protocol against an in-memory
//! fake; the live transport is cutover wiring (named gap).

use std::collections::BTreeMap;

use syncyard_contract::JobState;

use crate::offvm::{assert_epoch, FencingToken, OffVmError, OffVmStore, KIND_PREDISPATCH};
use crate::state::DaemonState;
use crate::vwrite::sha256_hex;

/// A triggered workflow run, as the dispatcher reports it.
#[derive(Debug, Clone, PartialEq)]
pub struct RunRef {
    pub run_id: u64,
    pub status: String,
    pub created_at: String,
}

/// The dispatch transport. Implementations must honor the
/// idempotency key: triggering twice under one key yields one run.
pub trait Dispatcher: Send + Sync {
    /// Start (or find) the run for `job` under `idempotency_key`.
    fn trigger(
        &self,
        job: &syncyard_contract::QueueRecord,
        idempotency_key: &str,
    ) -> Result<RunRef, String>;
    /// Find the run previously triggered under `idempotency_key`.
    fn find_by_key(&self, idempotency_key: &str) -> Result<Option<RunRef>, String>;
}

/// Compute the idempotency key for a dispatch of `job` under
/// `epoch`. Deterministic: the same job, sequence, payload and
/// epoch always produce the same key, so a retry after a crash
/// re-derives it instead of inventing a second dispatch.
pub fn idempotency_key(job: &syncyard_contract::QueueRecord, epoch: u64) -> String {
    let material = format!(
        "{}|{}|{}|{}",
        job.id,
        job.seq.unwrap_or(0),
        job.script_sha256.as_deref().unwrap_or(""),
        epoch
    );
    sha256_hex(material.as_bytes())
}

/// Why a dispatch did not complete.
#[derive(Debug)]
pub enum DispatchError {
    /// The job is not in a dispatchable state.
    NotDispatchable(String),
    /// Fencing lost: another daemon holds the current epoch.
    Fenced,
    /// The off-VM witness write failed.
    OffVm(OffVmError),
    /// The transport failed (retryable; the record stays in
    /// `dispatching` with its witness, ready for recovery).
    Transport(String),
}

impl std::fmt::Display for DispatchError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DispatchError::NotDispatchable(s) => write!(f, "not dispatchable: {s}"),
            DispatchError::Fenced => write!(f, "fencing epoch not held"),
            DispatchError::OffVm(e) => write!(f, "off-VM witness failed: {e}"),
            DispatchError::Transport(s) => write!(f, "dispatch transport failed: {s}"),
        }
    }
}

impl std::error::Error for DispatchError {}

fn ledger_fields(
    job_id: &str,
    key: &str,
    idem: &str,
) -> BTreeMap<String, serde_json::Value> {
    let mut m = BTreeMap::new();
    m.insert("job_id".into(), serde_json::Value::String(job_id.into()));
    m.insert("key".into(), serde_json::Value::String(key.into()));
    m.insert(
        "idempotency_key".into(),
        serde_json::Value::String(idem.into()),
    );
    m
}

/// A dispatch transaction, step by step. The server runs it to
/// completion; fault-injection tests stop it between steps to prove
/// ERR-01 recovery at every boundary.
pub struct DispatchTxn<'a> {
    pub job_id: String,
    pub idem_key: String,
    pub step: u8,
    _marker: std::marker::PhantomData<&'a ()>,
}

impl<'a> DispatchTxn<'a> {
    /// Step 1 (intent) + step 2 (witness). After this returns, the
    /// dispatch survives any crash.
    pub fn begin(
        state: &mut DaemonState,
        store: &dyn OffVmStore,
        token: &FencingToken,
        job_id: &str,
    ) -> Result<DispatchTxn<'a>, DispatchError> {
        assert_epoch(store, token).map_err(|_| DispatchError::Fenced)?;
        let job = state
            .jobs
            .get(job_id)
            .ok_or_else(|| DispatchError::NotDispatchable(format!("unknown job {job_id}")))?;
        if job.state != JobState::Submitted {
            return Err(DispatchError::NotDispatchable(format!(
                "job {job_id} is {}",
                job.state.as_str()
            )));
        }
        if let Some(held) = state.dispatch_held(job) {
            return Err(DispatchError::NotDispatchable(format!(
                "job {job_id} dispatch held: {held}"
            )));
        }
        let idem = idempotency_key(job, token.epoch);
        // Step 1: intent.
        state.update_job(job_id, |rec| {
            rec.state = JobState::Dispatching;
            rec.extra.insert(
                "idempotency_key".to_string(),
                serde_json::Value::String(idem.clone()),
            );
        });
        let job = state.jobs.get(job_id).expect("job present").clone();
        state.ledger("dispatching", ledger_fields(job_id, &job.key, &idem));
        // Step 2: the off-VM witness, confirmed before dispatch.
        let bytes = serde_json::to_vec_pretty(&job).expect("record serializes");
        store
            .put_record(KIND_PREDISPATCH, job_id, &bytes)
            .map_err(DispatchError::OffVm)?;
        let confirmed = store
            .get_record(KIND_PREDISPATCH, job_id)
            .map_err(DispatchError::OffVm)?;
        if confirmed.as_deref() != Some(bytes.as_slice()) {
            return Err(DispatchError::OffVm(OffVmError::Corrupt(
                "pre-dispatch witness read-back mismatch".into(),
            )));
        }
        Ok(DispatchTxn {
            job_id: job_id.to_string(),
            idem_key: idem,
            step: 2,
            _marker: std::marker::PhantomData,
        })
    }

    /// Steps 3-5: trigger (idempotent), capture, commit.
    pub fn finish(
        self,
        state: &mut DaemonState,
        store: &dyn OffVmStore,
        token: &FencingToken,
        dispatcher: &dyn Dispatcher,
    ) -> Result<RunRef, DispatchError> {
        assert_epoch(store, token).map_err(|_| DispatchError::Fenced)?;
        let job = state
            .jobs
            .get(&self.job_id)
            .cloned()
            .ok_or_else(|| DispatchError::NotDispatchable(format!("unknown job {}", self.job_id)))?;
        // Step 3: trigger under the idempotency key. If a run
        // already exists under the key (a prior attempt crashed
        // after triggering), adopt it — never trigger twice.
        let run = match dispatcher
            .find_by_key(&self.idem_key)
            .map_err(DispatchError::Transport)?
        {
            Some(existing) => existing,
            None => dispatcher
                .trigger(&job, &self.idem_key)
                .map_err(DispatchError::Transport)?,
        };
        // Step 4: capture the run id on the record + witness.
        state.update_job(&self.job_id, |rec| {
            rec.run_id = Some(run.run_id);
            rec.run_status = Some(run.status.clone());
            rec.run_created_at = Some(run.created_at.clone());
        });
        let job = state.jobs.get(&self.job_id).expect("job present").clone();
        let bytes = serde_json::to_vec_pretty(&job).expect("record serializes");
        store
            .put_record(KIND_PREDISPATCH, &self.job_id, &bytes)
            .map_err(DispatchError::OffVm)?;
        // Step 5: commit.
        state.update_job(&self.job_id, |rec| {
            rec.state = JobState::Dispatched;
            rec.dispatched_at = Some(crate::time::now_iso());
        });
        let mut fields = ledger_fields(&self.job_id, &job.key, &self.idem_key);
        fields.insert(
            "run_id".into(),
            serde_json::Value::Number(run.run_id.into()),
        );
        state.ledger("dispatched", fields);
        Ok(run)
    }
}

/// Run a full dispatch (the normal path).
pub fn dispatch_one(
    state: &mut DaemonState,
    store: &dyn OffVmStore,
    token: &FencingToken,
    dispatcher: &dyn Dispatcher,
    job_id: &str,
) -> Result<RunRef, DispatchError> {
    let txn = DispatchTxn::begin(state, store, token, job_id)?;
    txn.finish(state, store, token, dispatcher)
}

/// Recover a job stuck in `dispatching` after a crash (called by
/// boot reconciliation). Zero double: if the trigger already
/// happened, its run is found by key and adopted. Zero lost: if it
/// never happened, the job returns to `submitted` with its witness
/// intact, ready to dispatch again under the same key.
pub fn recover_dispatching(
    state: &mut DaemonState,
    token: &FencingToken,
    dispatcher: &dyn Dispatcher,
    job_id: &str,
) -> Result<String, DispatchError> {
    let job = state
        .jobs
        .get(job_id)
        .cloned()
        .ok_or_else(|| DispatchError::NotDispatchable(format!("unknown job {job_id}")))?;
    if job.state != JobState::Dispatching {
        return Ok("not-dispatching".to_string());
    }
    let idem = job
        .extra
        .get("idempotency_key")
        .and_then(|v| v.as_str())
        .map(|s| s.to_string())
        .unwrap_or_else(|| idempotency_key(&job, token.epoch));
    match dispatcher
        .find_by_key(&idem)
        .map_err(DispatchError::Transport)?
    {
        Some(run) => {
            state.update_job(job_id, |rec| {
                rec.run_id = Some(run.run_id);
                rec.run_status = Some(run.status.clone());
                rec.run_created_at = Some(run.created_at.clone());
                rec.state = JobState::Dispatched;
                rec.dispatched_at = Some(crate::time::now_iso());
            });
            let mut fields = ledger_fields(job_id, &job.key, &idem);
            fields.insert("run_id".into(), serde_json::Value::Number(run.run_id.into()));
            fields.insert("recovered".into(), serde_json::Value::Bool(true));
            state.ledger("dispatched", fields);
            Ok(format!("adopted run {}", run.run_id))
        }
        None => {
            state.update_job(job_id, |rec| {
                rec.state = JobState::Submitted;
            });
            let mut fields = ledger_fields(job_id, &job.key, &idem);
            fields.insert("recovered".into(), serde_json::Value::Bool(true));
            state.ledger("dispatch_recovered_untriggered", fields);
            Ok("returned to submitted (never triggered)".to_string())
        }
    }
}

/// An in-memory dispatcher for tests and protocol development.
/// Honors the idempotency contract exactly: one run per key.
#[cfg(any(test, feature = "test-support"))]
pub mod fake {
    use super::*;
    use std::sync::Mutex;

    #[derive(Default)]
    pub struct FakeDispatcher {
        inner: Mutex<FakeInner>,
    }

    #[derive(Default)]
    struct FakeInner {
        by_key: BTreeMap<String, RunRef>,
        next_run: u64,
        trigger_calls: usize,
    }

    impl FakeDispatcher {
        pub fn new() -> Self {
            FakeDispatcher {
                inner: Mutex::new(FakeInner {
                    by_key: BTreeMap::new(),
                    next_run: 424_000_000,
                    trigger_calls: 0,
                }),
            }
        }
        pub fn trigger_calls(&self) -> usize {
            self.inner.lock().unwrap().trigger_calls
        }
        pub fn runs(&self) -> usize {
            self.inner.lock().unwrap().by_key.len()
        }
    }

    impl Dispatcher for FakeDispatcher {
        fn trigger(
            &self,
            _job: &syncyard_contract::QueueRecord,
            idempotency_key: &str,
        ) -> Result<RunRef, String> {
            let mut inner = self.inner.lock().unwrap();
            inner.trigger_calls += 1;
            if let Some(existing) = inner.by_key.get(idempotency_key) {
                return Ok(existing.clone());
            }
            inner.next_run += 1;
            let run = RunRef {
                run_id: inner.next_run,
                status: "queued".to_string(),
                created_at: crate::time::now_iso(),
            };
            inner.by_key.insert(idempotency_key.to_string(), run.clone());
            Ok(run)
        }

        fn find_by_key(&self, idempotency_key: &str) -> Result<Option<RunRef>, String> {
            Ok(self
                .inner
                .lock()
                .unwrap()
                .by_key
                .get(idempotency_key)
                .cloned())
        }
    }
}
