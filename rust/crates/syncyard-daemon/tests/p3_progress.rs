//! P3 streaming-progress integration tests (spec 034, daemon side;
//! spec 038 harvest attribution).
//!
//! Each test claims its named test case id (spec form in the
//! comment above it) so the traceability generator picks it up.
//! All state lives in scratch dirs under the system temp dir; the
//! live gatekeeper is never touched.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use syncyard_contract::{JobRecord, JobState, ProgressAck, ProgressBatch};
use syncyard_daemon::dispatch::fake::FakeDispatcher;
use syncyard_daemon::offvm::{acquire_epoch, DirOffVmStore};
use syncyard_daemon::serve::Engine;
use syncyard_daemon::state::{new_queue_record, DaemonState, StateHome};
use syncyard_daemon::time::iso_from_unix;
use syncyard_daemon::vwrite::FsWriter;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p3-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn job(id: &str) -> JobRecord {
    JobRecord {
        id: id.to_string(),
        key: format!("key-{id}"),
        program: "test".to_string(),
        workflow: "run.yml".to_string(),
        lane: Some("quick".to_string()),
        cwd: Some("/tmp".to_string()),
        expected_min: 5.0,
        priority: 0,
        priority_source: "auto".to_string(),
        flags: vec![],
        maintenance: false,
        adopted: false,
        notify_chat: None,
        venue: "native".to_string(),
        script_path: Some("/bin/true".to_string()),
        script_sha256: Some("ab12cd34".to_string()),
        grab_path: None,
        created_at: "2026-11-08T00:00:00Z".to_string(),
    }
}

fn engine_for(home: &StateHome, root: &Path) -> Engine {
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let (state, _gaps) = DaemonState::load(home.clone());
    let token = acquire_epoch(store.as_ref(), "test-daemon").unwrap();
    Engine {
        state,
        store,
        dispatcher: Arc::new(FakeDispatcher::new()),
        token,
        writer: Arc::new(FsWriter::default()),
        pins: syncyard_daemon::workflowpin::WorkflowPins::new(),
    }
}

/// A state holding one dispatched job with the given run id.
fn state_with_dispatched(tag: &str, id: &str, run_id: u64) -> DaemonState {
    let home = StateHome::new(scratch(tag));
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let mut st = DaemonState::empty(home);
    let mut rec = new_queue_record(&job(id));
    rec.state = JobState::Dispatched;
    rec.run_id = Some(run_id);
    rec.dispatched_at = Some(iso_from_unix(1000));
    st.put_job(rec);
    st
}

fn batch(job_id: &str, run_id: u64, source: &str, lines: &[&str]) -> ProgressBatch {
    ProgressBatch {
        job_id: job_id.to_string(),
        run_id: Some(run_id),
        source: source.to_string(),
        lines: lines.iter().map(|s| s.to_string()).collect(),
    }
}

// T034-04 completed-line-is-not-a-verdict (spec 034 FR-034-03):
// a payload's COMPLETED line is advisory display data. Only the
// payload exit code in the terminal record is a verdict.
#[test]
fn t034_04_completed_line_is_not_a_verdict() {
    let root = scratch("t04");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let mut eng = engine_for(&home, &root);
    eng.state.progress_config.min_refresh_s = 0;

    eng.submit(&job("job-a")).unwrap();
    let resp = eng.handle(&serde_json::json!({"verb": "agent_poll", "venue": "native"}));
    let run_id = resp["assignment"]["run_id"].as_u64().unwrap();

    // The payload claims PASS in its progress file.
    let resp = eng.handle(&serde_json::json!({
        "verb": "progress", "job_id": "job-a", "run_id": run_id,
        "source": "agent:test-host",
        "lines": ["STARTED", "COMPLETED PASS"],
    }));
    assert_eq!(resp["ack"], serde_json::json!("applied"));

    // Nothing verdict-shaped moved: record still dispatched, no
    // conclusion, no verdict source. The display uses spec 034
    // section 4's exact pending-harvest wording.
    let status = eng.handle(&serde_json::json!({"verb": "status", "job_id": "job-a"}));
    assert_eq!(status["state"], serde_json::json!("dispatched"));
    assert_eq!(status["run_conclusion"], serde_json::Value::Null);
    assert_eq!(status["verdict_source"], serde_json::Value::Null);
    assert_eq!(
        status["progress"]["completed_word"],
        serde_json::json!("PASS")
    );
    assert_eq!(
        status["progress"]["display"],
        serde_json::json!("payload reports completed, verdict pending harvest")
    );

    // The payload then exits 1. The exit code governs: failure,
    // despite the COMPLETED PASS line.
    let resp = eng.handle(&serde_json::json!({
        "verb": "agent_completion", "job_id": "job-a", "run_id": run_id,
        "payload_exit_code": 1, "payload_started": true,
    }));
    assert_eq!(resp["run_conclusion"], serde_json::json!("failure"));
    let status = eng.handle(&serde_json::json!({"verb": "status", "job_id": "job-a"}));
    assert_eq!(status["run_conclusion"], serde_json::json!("failure"));
    assert_eq!(
        status["verdict_source"],
        serde_json::json!("payload_exit_code")
    );
    let _ = std::fs::remove_dir_all(&root);
}

// T034-05 progress-never-ledgers-verdict (spec 034 FR-034-03):
// ingesting progress writes no ledger events at all, and leaves
// the job record byte-identical.
#[test]
fn t034_05_progress_never_ledgers_verdict() {
    let mut st = state_with_dispatched("t05", "job-a", 777);
    st.progress_config.min_refresh_s = 0;
    let before = serde_json::to_value(st.jobs["job-a"].clone()).unwrap();
    let ledger_before = st.ledger_buffer.len();

    let ack = st.ingest_progress(
        &batch(
            "job-a",
            777,
            "agent:test-host",
            &[
                "STARTED",
                "PROGRESS 3/4 units",
                "COMPLETED FAIL",
                "PROGRESS nonsense here",
            ],
        ),
        2000,
    );
    assert_eq!(ack, ProgressAck::Applied);

    assert_eq!(
        st.ledger_buffer.len(),
        ledger_before,
        "progress ingest must not ledger anything, verdict or otherwise"
    );
    let after = serde_json::to_value(st.jobs["job-a"].clone()).unwrap();
    assert_eq!(before, after, "the job record is untouched by progress");
    let view = st.progress_view("job-a", 2000).unwrap();
    assert_eq!(view.completed_word.as_deref(), Some("FAIL"));
    assert_eq!(view.malformed_progress_count, 1);
}

// T034-06 snapshot-cadence-and-staleness (spec 034 FR-034-04):
// at most one accepted refresh per ~60 s window; a snapshot older
// than 180 s renders "progress stale", never as current.
#[test]
fn t034_06_snapshot_cadence_and_staleness() {
    let mut st = state_with_dispatched("t06", "job-a", 777);
    // Defaults are the spec numbers: 60 s window, 180 s stale.
    assert_eq!(st.progress_config.min_refresh_s, 60);
    assert_eq!(st.progress_config.stale_after_s, 180);

    let ack = st.ingest_progress(
        &batch("job-a", 777, "agent:h", &["PROGRESS 1/4 units"]),
        1000,
    );
    assert_eq!(ack, ProgressAck::Applied);

    // 30 s later: inside the window. Throttled, not applied — the
    // snapshot still shows the older value.
    let ack = st.ingest_progress(
        &batch("job-a", 777, "agent:h", &["PROGRESS 2/4 units"]),
        1030,
    );
    assert_eq!(ack, ProgressAck::Throttled);
    let view = st.progress_view("job-a", 1030).unwrap();
    assert_eq!(view.fraction, Some((1, 4)));
    assert_eq!(st.progress["job-a"].throttled_batches, 1);

    // At exactly 60 s the next refresh is accepted.
    let ack = st.ingest_progress(
        &batch("job-a", 777, "agent:h", &["PROGRESS 2/4 units"]),
        1060,
    );
    assert_eq!(ack, ProgressAck::Applied);
    let view = st.progress_view("job-a", 1060).unwrap();
    assert_eq!(view.fraction, Some((2, 4)));

    // Age exactly 180 s is not yet stale; 181 s is.
    let view = st.progress_view("job-a", 1240).unwrap();
    assert_eq!(view.age_s, Some(180));
    assert_eq!(view.display, "in progress");
    let view = st.progress_view("job-a", 1241).unwrap();
    assert_eq!(view.age_s, Some(181));
    assert_eq!(view.display, "progress stale");
}

// T034-07 silent-job-stays-silent (spec 034 FR-034-05): a job
// that writes no progress displays "no progress reported" with
// elapsed time; nothing is synthesized on its behalf.
#[test]
fn t034_07_silent_job_stays_silent() {
    let st = state_with_dispatched("t07", "job-a", 777);
    let view = st.progress_view("job-a", 1600).unwrap();
    assert_eq!(view.display, "no progress reported");
    // Dispatched at unix 1000 (see state_with_dispatched).
    assert_eq!(view.elapsed_s, Some(600));
    assert_eq!(view.age_s, None);
    assert!(!view.started);
    assert_eq!(view.fraction, None);
    assert_eq!(view.last_marker, None);
    assert_eq!(view.heartbeats, 0);
}

// T034-08 progress-regression-surfaced (spec 034 FR-034-06):
// a later smaller k is shown with a regression note, never
// silently clamped to the earlier value.
#[test]
fn t034_08_progress_regression_surfaced() {
    let mut st = state_with_dispatched("t08", "job-a", 777);
    st.progress_config.min_refresh_s = 0;
    assert_eq!(
        st.ingest_progress(
            &batch("job-a", 777, "agent:h", &["PROGRESS 5/8 units"]),
            1000
        ),
        ProgressAck::Applied
    );
    assert_eq!(
        st.ingest_progress(
            &batch("job-a", 777, "agent:h", &["PROGRESS 3/8 units"]),
            1001
        ),
        ProgressAck::Applied
    );
    let view = st.progress_view("job-a", 1002).unwrap();
    // The displayed fraction follows the latest snapshot (3/8) —
    // not clamped back to 5/8 — and the regression is surfaced.
    assert_eq!(view.fraction, Some((3, 8)));
    assert_eq!(view.regressions, 1);
    let note = view.regression_note.expect("regression must be surfaced");
    assert!(note.contains("regressed"), "note: {note}");
}

// T034-09 transport-failure-isolated (spec 034 FR-034-07):
// a transport failure degrades that job's progress to "progress
// unknown" and touches nothing else — not the job's record, not
// other jobs' progress, not dispatch or the verdict path.
#[test]
fn t034_09_transport_failure_isolated() {
    let root = scratch("t09");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let mut eng = engine_for(&home, &root);
    eng.state.progress_config.min_refresh_s = 0;

    eng.submit(&job("job-a")).unwrap();
    eng.submit(&job("job-b")).unwrap();
    let pa = eng.handle(&serde_json::json!({"verb": "agent_poll", "venue": "native"}));
    let run_a = pa["assignment"]["run_id"].as_u64().unwrap();
    let pb = eng.handle(&serde_json::json!({"verb": "agent_poll", "venue": "native"}));
    let run_b = pb["assignment"]["run_id"].as_u64().unwrap();
    assert_eq!(pa["assignment"]["job_id"], serde_json::json!("job-a"));
    assert_eq!(pb["assignment"]["job_id"], serde_json::json!("job-b"));

    for (id, run) in [("job-a", run_a), ("job-b", run_b)] {
        let resp = eng.handle(&serde_json::json!({
            "verb": "progress", "job_id": id, "run_id": run,
            "source": "agent:test-host", "lines": ["STARTED", "PROGRESS 1/2 units"],
        }));
        assert_eq!(resp["ack"], serde_json::json!("applied"));
    }

    // Job A's transport fails.
    let resp = eng.handle(&serde_json::json!({
        "verb": "progress", "job_id": "job-a", "run_id": run_a,
        "source": "agent:test-host", "transport_ok": false,
    }));
    assert_eq!(resp["ack"], serde_json::json!("transport_unknown"));

    let sa = eng.handle(&serde_json::json!({"verb": "status", "job_id": "job-a"}));
    assert_eq!(
        sa["progress"]["display"],
        serde_json::json!("progress unknown")
    );
    // Job A's record and verdict path are untouched.
    assert_eq!(sa["state"], serde_json::json!("dispatched"));
    assert_eq!(sa["run_conclusion"], serde_json::Value::Null);
    // Job B's progress is unaffected.
    let sb = eng.handle(&serde_json::json!({"verb": "status", "job_id": "job-b"}));
    assert_eq!(sb["progress"]["display"], serde_json::json!("in progress"));
    assert_eq!(sb["progress"]["fraction"], serde_json::json!([1, 2]));

    // Dispatch still works, and job A still completes normally
    // through the verdict path (payload exit code).
    eng.submit(&job("job-c")).unwrap();
    let resp = eng.handle(&serde_json::json!({
        "verb": "agent_completion", "job_id": "job-a", "run_id": run_a,
        "payload_exit_code": 0, "payload_started": true,
    }));
    assert_eq!(resp["run_conclusion"], serde_json::json!("success"));
    let _ = std::fs::remove_dir_all(&root);
}

// T034-10 foreign-marker-quarantine (spec 034 FR-034-08) and
// T038-34 harvest-foreign-line-quarantine (spec 038 FR-038-31):
// lines are attributed to a job only when run id and log-source
// identity match its record; foreign lines are quarantined and
// counted, never displayed as the payload's. The stray checkout
// line from the 2026-11-06 field defect is preserved as unknown
// chatter under correct attribution, never as a marker.
#[test]
fn t034_10_foreign_marker_quarantine() {
    let mut st = state_with_dispatched("t10", "job-a", 4242);
    st.progress_config.min_refresh_s = 0;

    // Foreign run id: quarantined wholesale.
    let ack = st.ingest_progress(
        &batch(
            "job-a",
            9999,
            "harvest:gh-log",
            &["MARKER foreign milestone"],
        ),
        2000,
    );
    assert_eq!(ack, ProgressAck::Quarantined);
    let view = st.progress_view("job-a", 2000).unwrap();
    assert_eq!(view.quarantined_lines, 1);
    assert_eq!(view.last_marker, None);
    assert_eq!(view.display, "no progress reported");

    // Correct run + source. The stray frun line (defect:
    // stray-frun-marker-lines-2026-11-06) parses as unknown
    // chatter: preserved in the count, never a marker.
    let ack = st.ingest_progress(
        &batch(
            "job-a",
            4242,
            "harvest:gh-log",
            &[
                "STARTED",
                "HEAD is now at cf35db2 staged: stage frun driver + current-job (frun)",
            ],
        ),
        2001,
    );
    assert_eq!(ack, ProgressAck::Applied);
    let view = st.progress_view("job-a", 2001).unwrap();
    assert!(view.started);
    assert_eq!(view.unknown_line_count, 1);
    assert_eq!(view.last_marker, None);

    // Same run, different source: an impostor feed is foreign.
    let ack = st.ingest_progress(
        &batch("job-a", 4242, "agent:other-host", &["MARKER impostor"]),
        2002,
    );
    assert_eq!(ack, ProgressAck::Quarantined);
    let view = st.progress_view("job-a", 2002).unwrap();
    assert_eq!(view.quarantined_lines, 2);
    assert_eq!(view.last_marker, None);

    // The established source still applies cleanly.
    let ack = st.ingest_progress(
        &batch("job-a", 4242, "harvest:gh-log", &["MARKER real milestone"]),
        2003,
    );
    assert_eq!(ack, ProgressAck::Applied);
    let view = st.progress_view("job-a", 2003).unwrap();
    assert_eq!(view.last_marker.as_deref(), Some("real milestone"));
}

// Agent-venue mechanics (no named case; the assignment/completion
// surface the T034-04/T034-09 engine tests ride on): poll assigns
// the oldest submitted job for the venue, honors the pause, and
// completion requires the exact run id.
#[test]
fn agent_poll_and_completion_matching() {
    let root = scratch("poll");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let mut eng = engine_for(&home, &root);

    eng.submit(&job("job-a")).unwrap();
    let mut other = job("job-b");
    other.venue = "docker".to_string();
    eng.submit(&other).unwrap();

    // Pause holds assignment (dispatch_held covers the poll).
    eng.handle(&serde_json::json!({"verb": "pause", "reason": "test"}));
    let resp = eng.handle(&serde_json::json!({"verb": "agent_poll", "venue": "native"}));
    assert_eq!(resp["assignment"], serde_json::Value::Null);
    eng.handle(&serde_json::json!({"verb": "start"}));

    // Only the native job is offered; the docker job is not.
    let resp = eng.handle(&serde_json::json!({"verb": "agent_poll", "venue": "native"}));
    assert_eq!(resp["assignment"]["job_id"], serde_json::json!("job-a"));
    let run_id = resp["assignment"]["run_id"].as_u64().unwrap();
    assert!(run_id >= syncyard_daemon::progress::LOCAL_RUN_ID_BASE);
    let resp = eng.handle(&serde_json::json!({"verb": "agent_poll", "venue": "native"}));
    assert_eq!(resp["assignment"], serde_json::Value::Null);

    // Completion under a wrong run id is refused, record intact.
    let resp = eng.handle(&serde_json::json!({
        "verb": "agent_completion", "job_id": "job-a", "run_id": run_id + 1,
        "payload_exit_code": 0, "payload_started": true,
    }));
    assert_eq!(resp["ok"], serde_json::json!(false));
    let status = eng.handle(&serde_json::json!({"verb": "status", "job_id": "job-a"}));
    assert_eq!(status["state"], serde_json::json!("dispatched"));

    // A payload that never started yields "error", never success.
    let resp = eng.handle(&serde_json::json!({
        "verb": "agent_completion", "job_id": "job-a", "run_id": run_id,
        "payload_exit_code": null, "payload_started": false,
    }));
    assert_eq!(resp["run_conclusion"], serde_json::json!("error"));
    let _ = std::fs::remove_dir_all(&root);
}
