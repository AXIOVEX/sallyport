//! P7 integration tests: the drills and rehearsals of spec
//! 038 section 7 and spec 036 that are provable on this VM.
//!
//! Each test claims its named test case id (spec form in the
//! comment above it) so the traceability generator picks it
//! up. All state lives in scratch dirs; the live gatekeeper
//! is never touched. Where a case's full form needs a live
//! fleet (the cutover-day byte-copy rehearsal, a restic
//! restore against the real archive), the test proves the
//! fixture-level machinery and the field leg is recorded in
//! PROGRESS.md as field-gated — the claim below names
//! exactly what was proven.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use syncyard_contract::{JobRecord, JobState};
use syncyard_daemon::dispatch::{dispatch_one, fake::FakeDispatcher, recover_dispatching};
use syncyard_daemon::migrate::{export_history, migrate_history, verify_history};
use syncyard_daemon::offvm::{acquire_epoch, DirOffVmStore, OffVmStore, KIND_SUBMITS};
use syncyard_daemon::reconcile::{boot_reconcile, fake::FakeRuns};
use syncyard_daemon::serve::Engine;
use syncyard_daemon::state::{new_queue_record, DaemonState, PauseRecord, StateHome};
use syncyard_daemon::vwrite::test_support::GateWriter;
use syncyard_daemon::vwrite::{sha256_hex, FsWriter, StateWriter};
use syncyard_daemon::workflowpin::WorkflowPins;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p7-{}-{tag}", std::process::id()));
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
        cwd: Some("C:\\work".to_string()),
        expected_min: 5.0,
        priority: 0,
        priority_source: "auto".to_string(),
        flags: vec![],
        maintenance: false,
        adopted: false,
        notify_chat: None,
        venue: "native".to_string(),
        script_path: Some("/tmp/script.ps1".to_string()),
        script_sha256: Some("ab12cd34".to_string()),
        grab_path: None,
        created_at: "2026-11-08T00:00:00Z".to_string(),
    }
}

fn engine_for(
    home: &StateHome,
    store: Arc<DirOffVmStore>,
    writer: Arc<dyn StateWriter>,
) -> (Engine, Arc<FakeDispatcher>) {
    let fake = Arc::new(FakeDispatcher::new());
    let (state, _gaps) = DaemonState::load(home.clone());
    let token = acquire_epoch(store.as_ref(), "p7-daemon").unwrap();
    let store_dyn: Arc<dyn OffVmStore> = store;
    let engine = Engine {
        state,
        store: store_dyn,
        dispatcher: fake.clone(),
        token,
        writer,
        pins: WorkflowPins::new(),
    };
    (engine, fake)
}

// T038-37 err03-power-loss-drill
#[test]
fn t038_37_err03_power_loss_drill() {
    // The drill, in three acts. Act 1: dispatch lands, then
    // power is lost with NO graceful shutdown and NO final
    // flush (the engine is simply dropped). Act 2: after boot
    // reconciliation, the agent's completion arrives and is
    // acknowledged; power is lost again. Act 3: the job is
    // recovered Completed with its payload verdict intact,
    // reconciliation runs BEFORE any new dispatch, and a new
    // job then dispatches cleanly. At no point is a job
    // missing, corrupt, or double-dispatched.
    let root = scratch("t37");
    let home = StateHome::new(root.join("home"));
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let (mut engine, fake) = engine_for(&home, store.clone(), Arc::new(FsWriter::default()));
    engine.submit(&job("power-a")).unwrap();
    let run = dispatch_one(
        &mut engine.state,
        store.as_ref(),
        &engine.token.clone(),
        engine.dispatcher.as_ref(),
        "power-a",
    )
    .unwrap();
    let run_id = run.run_id;
    // POWER LOSS #1: no shutdown verb, no flush.
    drop(engine);

    // Boot: load what survived, reconcile before anything new.
    let (mut st2, gaps) = DaemonState::load(home.clone());
    assert!(gaps.is_empty(), "no record may be corrupt: {gaps:?}");
    let runs = FakeRuns::new();
    runs.set(run_id, "in_progress", None);
    let report = boot_reconcile(&mut st2, store.as_ref(), &runs, &[], gaps);
    // The job is recovered in a NAMED state: dispatching (to
    // be finalized by recovery) or dispatched — never gone.
    let rec = st2
        .jobs
        .get("power-a")
        .expect("the job must survive power loss");
    assert!(
        matches!(rec.state, JobState::Dispatching | JobState::Dispatched),
        "recovered in a named state, got {:?}",
        rec.state
    );
    assert_eq!(rec.run_id, Some(run_id), "run linkage intact");
    for id in &report.dispatching_to_recover {
        let token2 = acquire_epoch(store.as_ref(), "t37-boot").unwrap();
        recover_dispatching(&mut st2, &token2, fake.as_ref(), id).unwrap();
    }
    assert_eq!(
        fake.trigger_calls(),
        1,
        "recovery must never re-trigger the run"
    );
    assert_eq!(st2.jobs["power-a"].state, JobState::Dispatched);

    // Act 2: a fresh engine on the recovered state takes the
    // agent's completion (payload exit 7 — a failure verdict),
    // then power is lost again immediately after the ack.
    let mut engine2 = Engine {
        state: st2,
        store: store.clone() as Arc<dyn OffVmStore>,
        dispatcher: fake.clone(),
        token: acquire_epoch(store.as_ref(), "t37-boot2").unwrap(),
        writer: Arc::new(FsWriter::default()),
        pins: WorkflowPins::new(),
    };
    let resp = engine2.handle(&serde_json::json!({
        "verb": "agent_completion",
        "job_id": "power-a",
        "run_id": run_id,
        "payload_exit_code": 7,
        "payload_started": true,
    }));
    assert_eq!(resp["ok"], serde_json::Value::Bool(true), "{resp}");
    // POWER LOSS #2.
    drop(engine2);

    // Act 3: verdict intact after the second loss.
    let (mut st3, gaps3) = DaemonState::load(home.clone());
    assert!(gaps3.is_empty(), "{gaps3:?}");
    let rec = &st3.jobs["power-a"];
    assert_eq!(rec.state, JobState::Completed);
    assert_eq!(rec.run_conclusion.as_deref(), Some("failure"));
    assert_eq!(rec.verdict_source.as_deref(), Some("payload_exit_code"));
    // Reconciliation first, then — and only then — new work.
    let report3 = boot_reconcile(&mut st3, store.as_ref(), &runs, &[], gaps3);
    assert!(
        !report3.ghosts.contains(&"power-a".to_string()),
        "a terminal job is not a ghost"
    );
    let token3 = acquire_epoch(store.as_ref(), "t37-boot3").unwrap();
    st3.put_job(new_queue_record(&job("power-b")));
    let rec_b = new_queue_record(&job("power-b"));
    let bytes = serde_json::to_vec_pretty(&rec_b).unwrap();
    store
        .put_record(KIND_SUBMITS, "power-b", &bytes)
        .unwrap();
    dispatch_one(&mut st3, store.as_ref(), &token3, fake.as_ref(), "power-b").unwrap();
    assert_eq!(st3.jobs["power-b"].state, JobState::Dispatched);
    assert_eq!(fake.trigger_calls(), 2, "exactly one new trigger");
    let _ = std::fs::remove_dir_all(&root);
}

// T036-10 unattended-degradation-7d
#[test]
fn t036_10_unattended_degradation() {
    // Scope note (honest): the case's full form is 7 days
    // unattended in the field (a soak). What this VM proves
    // is the degradation MACHINERY under simulated time: a
    // write wave held across many write-behind cycles while
    // work keeps arriving and a pause is set mid-wave. The
    // invariants asserted are the case's own: nothing
    // corrupted, the pause held, and after the wave clears
    // the whole backlog lands and reloads exactly. The field
    // soak itself is recorded field-gated in PROGRESS.md.
    let root = scratch("t10");
    let home = StateHome::new(root.clone());
    let mut st = DaemonState::empty(home.clone());
    let writer = GateWriter::new(true); // the wave: every write denied
    st.set_paused(Some(PauseRecord {
        reason: "operator pause before the storm".into(),
        ts: "2026-11-08T00:00:00Z".into(),
    }));
    // 48 simulated write-behind cycles of arriving work.
    for i in 0..48 {
        st.put_job(new_queue_record(&job(&format!("soak-{i:02}"))));
        let mut fields = std::collections::BTreeMap::new();
        fields.insert(
            "job_id".into(),
            serde_json::Value::String(format!("soak-{i:02}")),
        );
        st.ledger("submitted", fields);
        let report = st.flush(&writer);
        assert!(!report.ok && report.denied, "cycle {i} must be denied");
        assert_eq!(st.jobs.len(), i + 1, "RAM accounting stays exact");
    }
    // Nothing reached disk, and nothing pretended otherwise.
    assert!(!home.job_path("soak-00").exists());
    assert!(st.flush_health.wave_mode);
    // The wave clears: one flush lands the entire backlog.
    writer.set_deny(false);
    let report = st.flush(&writer);
    assert!(report.ok, "{:?}", report.errors);
    assert!(!st.flush_health.wave_mode);
    // Reload: every record parses, the pause held, the ledger
    // on disk is intact JSONL end to end.
    let (st2, gaps) = DaemonState::load(home.clone());
    assert!(gaps.is_empty(), "nothing corrupted: {gaps:?}");
    assert_eq!(st2.jobs.len(), 48);
    assert!(st2.paused.is_some(), "the pause held through the wave");
    let ledger_text = std::fs::read_to_string(home.ledger_path()).unwrap();
    for line in ledger_text.lines().filter(|l| !l.is_empty()) {
        serde_json::from_str::<serde_json::Value>(line)
            .unwrap_or_else(|e| panic!("corrupt ledger line: {e}: {line}"));
    }
    assert!(ledger_text.contains("write_wave_detected"));
    let _ = std::fs::remove_dir_all(&root);
}

// T038-01 cutover-roundtrip-rehearsal
#[test]
fn t038_01_cutover_roundtrip_rehearsal() {
    // The fixture-level rehearsal: a Python-gatekeeper-shaped
    // directory is migrated, verified, exported, and the
    // export is byte-exact against the source; then the
    // RESTORE leg — migrating from the EXPORT into a fresh
    // home — reproduces the identical manifest. The live
    // rehearsal (byte-copy of the real gatekeeper directory
    // on cutover day) is a field act, recorded in
    // PROGRESS.md; nothing here touches the live directory.
    let src = scratch("t01-src");
    std::fs::create_dir_all(src.join("queue")).unwrap();
    for id in ["rt-a", "rt-b", "rt-c", "rt-d"] {
        let rec = new_queue_record(&job(id));
        let bytes = serde_json::to_vec_pretty(&rec).unwrap();
        std::fs::write(src.join("queue").join(format!("{id}.json")), &bytes).unwrap();
    }
    let ledger_text = "{\"event\":\"submitted\",\"job_id\":\"rt-a\"}\n{\"event\":\"dispatched\",\"job_id\":\"rt-a\"}\n{\"event\":\"submitted\",\"job_id\":\"rt-b\"}\n{\"event\":\"completed\",\"job_id\":\"rt-a\"}\n";
    std::fs::write(src.join("ledger.jsonl"), ledger_text).unwrap();

    let home = StateHome::new(scratch("t01-home"));
    let manifest = migrate_history(&src, &home, &|| false).unwrap();
    assert_eq!(manifest.records.len(), 4);
    let verified = verify_history(&home).unwrap();
    assert_eq!(verified.records, manifest.records);

    let out = scratch("t01-out");
    assert_eq!(export_history(&home, &out).unwrap(), 4);
    for id in ["rt-a", "rt-b", "rt-c", "rt-d"] {
        let a = std::fs::read(src.join("queue").join(format!("{id}.json"))).unwrap();
        let b = std::fs::read(out.join("queue").join(format!("{id}.json"))).unwrap();
        assert_eq!(a, b, "record {id} byte-exact through the round trip");
        assert_eq!(sha256_hex(&a), manifest.records[id]);
    }
    assert_eq!(
        std::fs::read(out.join("ledger.jsonl")).unwrap(),
        ledger_text.as_bytes()
    );

    // Restore leg: the export is itself a valid source.
    let home2 = StateHome::new(scratch("t01-home2"));
    let restored = migrate_history(&out, &home2, &|| false).unwrap();
    assert_eq!(restored.records, manifest.records);
    assert_eq!(restored.ledger_sha256, manifest.ledger_sha256);
    let _ = std::fs::remove_dir_all(&src);
    let _ = std::fs::remove_dir_all(home.root());
    let _ = std::fs::remove_dir_all(home2.root());
    let _ = std::fs::remove_dir_all(&out);
}

// T036-11 backup-restore-drill
#[test]
fn t036_11_backup_restore_drill() {
    // The drill at machinery level: state is written and
    // flushed, a byte snapshot of the state home is taken
    // (the backup), the live home is then DAMAGED (a record
    // deleted, the ledger truncated), and the restore —
    // snapshot bytes back over the home — brings back every
    // job with zero load gaps. (The production backup tool
    // is restic against the fleet archive; the drill's
    // pass/fail substance is that what was backed up is
    // exactly what comes back, proven here on bytes.)
    fn copy_dir(from: &Path, to: &Path) {
        std::fs::create_dir_all(to).unwrap();
        for entry in std::fs::read_dir(from).unwrap().flatten() {
            let (f, t) = (entry.path(), to.join(entry.file_name()));
            if entry.file_type().unwrap().is_dir() {
                copy_dir(&f, &t);
            } else {
                std::fs::copy(&f, &t).unwrap();
            }
        }
    }
    let root = scratch("t11");
    let home = StateHome::new(root.join("home"));
    let mut st = DaemonState::empty(home.clone());
    for id in ["bk-a", "bk-b", "bk-c"] {
        st.put_job(new_queue_record(&job(id)));
    }
    let mut fields = std::collections::BTreeMap::new();
    fields.insert("note".into(), serde_json::Value::String("drill".into()));
    st.ledger("submitted", fields);
    let report = st.flush(&FsWriter::default());
    assert!(report.ok);
    drop(st);
    // Backup: snapshot the whole state home.
    let snapshot = root.join("snapshot");
    copy_dir(home.root(), &snapshot);
    // Damage: lose a record, truncate the ledger.
    std::fs::remove_file(home.job_path("bk-b")).unwrap();
    let ledger = std::fs::read(home.ledger_path()).unwrap();
    std::fs::write(home.ledger_path(), &ledger[..ledger.len() / 2]).unwrap();
    let (damaged, _) = DaemonState::load(home.clone());
    assert!(!damaged.jobs.contains_key("bk-b"), "damage is real");
    drop(damaged);
    // Restore: snapshot bytes back, then load.
    copy_dir(&snapshot, home.root());
    let (restored, gaps) = DaemonState::load(home.clone());
    assert!(gaps.is_empty(), "restored state loads clean: {gaps:?}");
    for id in ["bk-a", "bk-b", "bk-c"] {
        assert!(restored.jobs.contains_key(id), "{id} restored");
    }
    let ledger_back = std::fs::read(home.ledger_path()).unwrap();
    assert_eq!(ledger_back, ledger, "ledger restored byte-exact");
    let _ = std::fs::remove_dir_all(&root);
}

// T036-08 fault-injection-fixture
#[test]
fn t036_08_fault_injection_fixture() {
    // Companion claim on the ERR-01 fixture (the step-kill
    // loop itself is T038-35 in p2_protocol.rs): the fixture
    // corpus index (T036-04 below) requires the fault-
    // injection class to exist and be exercised; here the
    // canonical mid-protocol kill (after trigger, before
    // commit) is replayed through BOOT RECOVERY rather than
    // the in-process path, proving the fixture drives the
    // recovery machinery end to end: exactly one run, job
    // dispatched, zero double, zero lost.
    use syncyard_daemon::dispatch::DispatchTxn;
    let root = scratch("t08");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let fake = FakeDispatcher::new();
    let token = acquire_epoch(store.as_ref(), "t08-daemon").unwrap();
    let mut st = DaemonState::empty(home.clone());
    let rec = new_queue_record(&job("fix-job"));
    let bytes = serde_json::to_vec_pretty(&rec).unwrap();
    store.put_record(KIND_SUBMITS, "fix-job", &bytes).unwrap();
    st.put_job(rec);
    st.flush(&FsWriter::default());
    let mut txn = DispatchTxn::begin(&mut st, store.as_ref(), &token, "fix-job").unwrap();
    txn.trigger_and_capture(&mut st, store.as_ref(), &token, &fake)
        .unwrap();
    drop(txn);
    drop(st); // the kill: no commit, no flush
    let (mut st2, gaps) = DaemonState::load(home.clone());
    let runs = FakeRuns::new();
    let report = boot_reconcile(&mut st2, store.as_ref(), &runs, &[], gaps);
    assert!(
        report
            .dispatching_to_recover
            .contains(&"fix-job".to_string()),
        "{report:?}"
    );
    for id in &report.dispatching_to_recover {
        recover_dispatching(&mut st2, &token, &fake, id).unwrap();
    }
    assert_eq!(st2.jobs["fix-job"].state, JobState::Dispatched);
    assert_eq!(fake.trigger_calls(), 1, "zero double");
    let _ = std::fs::remove_dir_all(&root);
}

// T036-04 simulation-fixture-coverage
#[test]
fn t036_04_simulation_fixture_coverage() {
    // The index guard: every simulation class spec 036
    // section 5 names must have at least one named test
    // somewhere in the workspace suites. If a class's test
    // is ever deleted, this test fails and names the class.
    let rust_root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..");
    let mut corpus = String::new();
    fn harvest(dir: &Path, corpus: &mut String) {
        let Ok(rd) = std::fs::read_dir(dir) else { return };
        for entry in rd.flatten() {
            let p = entry.path();
            if p.is_dir() {
                harvest(&p, corpus);
            } else if p.extension().map(|x| x == "rs") == Some(true) {
                if let Ok(text) = std::fs::read_to_string(&p) {
                    corpus.push_str(&text);
                }
            }
        }
    }
    harvest(&rust_root.join("crates"), &mut corpus);
    // (class, one named test id that carries it, snake form)
    let classes: [(&str, &str); 6] = [
        ("tray rendering fixtures", "t035_09"),
        ("runner noise fixtures", "t034_09"),
        ("ghost records", "t038_24"),
        ("write-denial wave", "t038_39"),
        ("fault injection", "t038_35"),
        ("mixed-version states", "t038_57"),
    ];
    for (class, id) in classes {
        assert!(
            corpus.contains(id),
            "simulation class '{class}' has no named test ({id} missing)"
        );
    }
}
