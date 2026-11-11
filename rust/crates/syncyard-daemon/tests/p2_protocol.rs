//! P2 protocol integration tests for spec 038 / spec 036.
//!
//! Each test claims its named test case id (spec form in the
//! comment above it) so the traceability generator picks it up.
//! All state lives in scratch dirs under the system temp dir; the
//! live gatekeeper is never touched.

use std::path::PathBuf;
#[cfg(unix)]
use std::sync::atomic::AtomicBool;
use std::sync::atomic::Ordering;
use std::sync::Arc;
#[cfg(unix)]
use std::sync::Mutex;
#[cfg(unix)]
use std::time::Duration;

use syncyard_contract::{JobRecord, JobState};
use syncyard_daemon::dispatch::{
    dispatch_one, fake::FakeDispatcher, idempotency_key, recover_dispatching, DispatchTxn,
};
use syncyard_daemon::lanes::{scan, Lane, SettledRuns, StarvationTracker};
#[cfg(unix)]
use syncyard_daemon::lock::{acquire_owner_lock, OwnerLock, Takeover};
use syncyard_daemon::migrate::{export_history, migrate_history, verify_history, MigrateError};
use syncyard_daemon::offvm::{
    acquire_epoch, DirOffVmStore, OffVmStore, KIND_PREDISPATCH, KIND_SUBMITS,
};
use syncyard_daemon::publish::publish_verified;
use syncyard_daemon::reconcile::{boot_reconcile, fake::FakeRuns, reconcile_ghosts};
use syncyard_daemon::serve::Engine;
#[cfg(unix)]
use syncyard_daemon::serve::{request, serve, ServeConfig};
use syncyard_daemon::state::{new_queue_record, DaemonState, StateHome};
use syncyard_daemon::time::unix_from_iso;
use syncyard_daemon::vwrite::test_support::GateWriter;
use syncyard_daemon::vwrite::{sha256_hex, FsWriter, StateWriter};

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p2-{}-{tag}", std::process::id()));
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
    let token = acquire_epoch(store.as_ref(), "test-daemon").unwrap();
    let store_dyn: Arc<dyn OffVmStore> = store;
    let engine = Engine {
        state,
        store: store_dyn,
        dispatcher: fake.clone(),
        token,
        writer,
        pins: syncyard_daemon::workflowpin::WorkflowPins::new(),
    };
    (engine, fake)
}

// T038-03 daemon-single-writer-socket
// Unix-only: the daemon's socket transport refuses by name
// on non-Unix platforms in this build (serve.rs — the
// Windows named-pipe transport is not yet wired), so there
// is no serving daemon to drive there.
#[cfg(unix)]
#[test]
fn t038_03_daemon_single_writer_socket() {
    let root = scratch("t03");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    // The serve path holds the state lock for its lifetime.
    let lock = syncyard_daemon::lock::try_state_lock(&home.lock_path())
        .unwrap()
        .expect("first writer takes the lock");
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let (engine, _fake) = engine_for(&home, store, Arc::new(FsWriter::default()));
    let engine = Arc::new(Mutex::new(engine));
    let shutdown = Arc::new(AtomicBool::new(false));
    let home2 = home.clone();
    let eng2 = Arc::clone(&engine);
    let sd2 = Arc::clone(&shutdown);
    let thread = std::thread::spawn(move || {
        serve(
            eng2,
            &home2,
            ServeConfig {
                flush_interval: Duration::from_millis(50),
            },
            sd2,
        )
    });
    // Wait for the socket to answer.
    let mut pinged = false;
    for _ in 0..60 {
        if let Ok(resp) = request(&home, &serde_json::json!({"verb": "ping"})) {
            if resp["ok"] == serde_json::Value::Bool(true) {
                pinged = true;
                break;
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(pinged, "daemon socket never answered ping");
    // While the daemon holds the lock, a second writer is refused.
    assert!(
        syncyard_daemon::lock::try_state_lock(&home.lock_path())
            .unwrap()
            .is_none(),
        "fallback writer must be refused while the daemon holds the lock"
    );
    // Verbs work over the socket.
    let j = job("sock-job");
    let resp = request(
        &home,
        &serde_json::json!({"verb": "submit", "job": serde_json::to_value(&j).unwrap()}),
    )
    .unwrap();
    assert_eq!(resp["ok"], serde_json::Value::Bool(true), "submit: {resp}");
    let resp = request(
        &home,
        &serde_json::json!({"verb": "status", "job_id": "sock-job"}),
    )
    .unwrap();
    assert_eq!(resp["state"], serde_json::Value::String("submitted".into()));
    // Shut down and release: the fallback lock is free again.
    let resp = request(&home, &serde_json::json!({"verb": "shutdown"})).unwrap();
    assert_eq!(resp["ok"], serde_json::Value::Bool(true));
    thread.join().unwrap().unwrap();
    drop(lock);
    assert!(
        syncyard_daemon::lock::try_state_lock(&home.lock_path())
            .unwrap()
            .is_some(),
        "fallback writer must be able to take the lock once the daemon stops"
    );
    let _ = std::fs::remove_dir_all(&root);
}

// T038-04 write-behind-flush-60s
// Unix-only: same live-daemon socket-transport reason as
// t038_03 above.
#[cfg(unix)]
#[test]
fn t038_04_write_behind_flush_60s() {
    assert_eq!(syncyard_daemon::state::DEFAULT_FLUSH_INTERVAL_S, 60);
    let root = scratch("t04");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let (engine, _fake) = engine_for(&home, store, Arc::new(FsWriter::default()));
    let engine = Arc::new(Mutex::new(engine));
    let shutdown = Arc::new(AtomicBool::new(false));
    let home2 = home.clone();
    let eng2 = Arc::clone(&engine);
    let sd2 = Arc::clone(&shutdown);
    let thread = std::thread::spawn(move || {
        serve(
            eng2,
            &home2,
            ServeConfig {
                flush_interval: Duration::from_millis(50),
            },
            sd2,
        )
    });
    // Dirty a record directly in RAM: no verb, no manual flush.
    {
        let mut eng = engine.lock().unwrap();
        eng.state.put_job(new_queue_record(&job("wb-job")));
        assert_eq!(eng.state.snapshot()["flush"]["state_dirty_records"], 1);
    }
    assert!(
        !home.job_path("wb-job").exists(),
        "nothing written before the flush cycle"
    );
    // The write-behind cycle lands it, verified by content.
    let mut landed = false;
    for _ in 0..40 {
        if home.job_path("wb-job").exists() {
            landed = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(landed, "write-behind flush never landed the record");
    let on_disk: syncyard_contract::QueueRecord =
        serde_json::from_str(&std::fs::read_to_string(home.job_path("wb-job")).unwrap()).unwrap();
    assert_eq!(on_disk.id, "wb-job");
    let snap = request(&home, &serde_json::json!({"verb": "snapshot"})).unwrap();
    assert_eq!(snap["flush"]["state_dirty_records"], 0);
    assert!(snap["flush"]["last_flush_ok_at"].is_string());
    let _ = request(&home, &serde_json::json!({"verb": "shutdown"}));
    thread.join().unwrap().unwrap();
    let _ = std::fs::remove_dir_all(&root);
}

// T038-05 dispatch-mid-wave
#[test]
fn t038_05_dispatch_mid_wave() {
    let root = scratch("t05");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    // The local disk writer is inside a wave: every write denied.
    let gate = Arc::new(GateWriter::new(true));
    let (mut engine, _fake) =
        engine_for(&home, store.clone(), gate.clone() as Arc<dyn StateWriter>);
    // Submit still acknowledges (RAM + off-VM record).
    engine
        .submit(&job("wave-job"))
        .expect("submit acks mid-wave");
    assert!(
        engine.state.flush_health.wave_mode,
        "the failed flush must put writers in wave mode"
    );
    // Dispatch works mid-wave: the witness is off-VM, the trigger
    // is not a local file write.
    let run = dispatch_one(
        &mut engine.state,
        store.as_ref(),
        &engine.token.clone(),
        engine.dispatcher.as_ref(),
        "wave-job",
    )
    .expect("dispatch must work mid-wave");
    let rec = &engine.state.jobs["wave-job"];
    assert_eq!(rec.state, JobState::Dispatched);
    assert_eq!(rec.run_id, Some(run.run_id));
    assert!(
        store
            .get_record(KIND_PREDISPATCH, "wave-job")
            .unwrap()
            .is_some(),
        "pre-dispatch witness must exist off-VM"
    );
    // When the wave clears, the backlog flush lands everything.
    gate.set_deny(false);
    let report = engine.state.flush(&FsWriter::default());
    assert!(report.ok, "post-wave flush: {:?}", report.errors);
    let on_disk: syncyard_contract::QueueRecord =
        serde_json::from_str(&std::fs::read_to_string(home.job_path("wave-job")).unwrap()).unwrap();
    assert_eq!(on_disk.state, JobState::Dispatched);
    let _ = std::fs::remove_dir_all(&root);
}

// T038-06 fifth-ram-class-and-cure-rule
#[test]
fn t038_06_fifth_ram_class_and_cure_rule() {
    let root = scratch("t06");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    // Control files live in RAM (fifth class) and flush like records.
    let mut st = DaemonState::empty(home.clone());
    st.set_paused(Some(syncyard_daemon::state::PauseRecord {
        reason: "fifth class".into(),
        ts: "2026-11-08T00:00:00Z".into(),
    }));
    st.set_hold("held-job", true);
    st.hosts = serde_json::json!({"tower-compute": {"venues": ["native"]}});
    st.lane_locks = serde_json::json!({"quick": {"holder": "ops"}});
    let report = st.flush(&FsWriter::default());
    assert!(report.ok, "{:?}", report.errors);
    let (st2, gaps) = DaemonState::load(home.clone());
    assert!(gaps.is_empty());
    assert_eq!(st2.paused.as_ref().unwrap().reason, "fifth class");
    assert!(st2.holds.contains("held-job"));
    assert_eq!(st2.hosts["tower-compute"]["venues"][0], "native");
    assert_eq!(st2.lane_locks["quick"]["holder"], "ops");
    // Cure rule: failures, then a sampler clear window, then still
    // failing => self-restart exactly once for the episode.
    let gate = GateWriter::new(true);
    let mut st3 = DaemonState::empty(home.clone());
    st3.put_job(new_queue_record(&job("cure-job")));
    let report = st3.flush(&gate);
    assert!(!report.ok);
    st3.cure.note_clear_window();
    assert!(
        st3.cure.should_self_restart(true),
        "cure rule must fire once"
    );
    assert!(!st3.cure.should_self_restart(true), "and only once");
    let _ = std::fs::remove_dir_all(&root);
}

// T038-08 dispatch-idempotency-key
#[test]
fn t038_08_dispatch_idempotency_key() {
    // The key is deterministic per job+sequence+payload+epoch.
    let mut rec = new_queue_record(&job("idem-job"));
    rec.seq = Some(7);
    let k1 = idempotency_key(&rec, 3);
    assert_eq!(k1, idempotency_key(&rec, 3));
    assert_ne!(k1, idempotency_key(&rec, 4));
    // A crashed attempt that already triggered is adopted by key,
    // never re-triggered.
    let root = scratch("t08");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let (mut engine, _fake) = engine_for(&home, store.clone(), Arc::new(FsWriter::default()));
    engine.submit(&job("first-job")).unwrap();
    let txn = DispatchTxn::begin(
        &mut engine.state,
        store.as_ref(),
        &engine.token.clone(),
        "first-job",
    )
    .unwrap();
    let key = txn.idem_key.clone();
    let run = txn
        .finish(
            &mut engine.state,
            store.as_ref(),
            &engine.token.clone(),
            engine.dispatcher.as_ref(),
        )
        .unwrap();
    // Simulate a second job record whose crashed dispatch carried
    // the same key (the retry-after-crash geometry): recovery finds
    // the existing run by key.
    engine.submit(&job("second-job")).unwrap();
    engine.state.update_job("second-job", |r| {
        r.state = JobState::Dispatching;
        r.extra.insert(
            "idempotency_key".to_string(),
            serde_json::Value::String(key.clone()),
        );
    });
    let outcome = recover_dispatching(
        &mut engine.state,
        &engine.token.clone(),
        engine.dispatcher.as_ref(),
        "second-job",
    )
    .unwrap();
    assert!(outcome.contains(&run.run_id.to_string()), "{outcome}");
    assert_eq!(
        engine.state.jobs["second-job"].run_id,
        Some(run.run_id),
        "the same run must be adopted, not a second one triggered"
    );
    let _ = std::fs::remove_dir_all(&root);
}

// T038-09 two-daemons-one-dispatches
#[test]
fn t038_09_two_daemons_one_dispatches() {
    let root = scratch("t09");
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let home_a = StateHome::new(root.join("home-a"));
    let home_b = StateHome::new(root.join("home-b"));
    std::fs::create_dir_all(home_a.queue_dir()).unwrap();
    std::fs::create_dir_all(home_b.queue_dir()).unwrap();
    let (mut a, _fa) = engine_for(&home_a, store.clone(), Arc::new(FsWriter::default()));
    let (mut b, _fb) = engine_for(&home_b, store.clone(), Arc::new(FsWriter::default()));
    // Construction order: a took epoch 1, then b took epoch 2.
    // a is fenced out; its dispatch must be refused.
    a.submit(&job("job-a")).unwrap();
    let err = dispatch_one(
        &mut a.state,
        store.as_ref(),
        &a.token.clone(),
        a.dispatcher.as_ref(),
        "job-a",
    );
    assert!(err.is_err(), "stale-epoch daemon must not dispatch");
    // b holds the current epoch and dispatches.
    b.submit(&job("job-b")).unwrap();
    let run = dispatch_one(
        &mut b.state,
        store.as_ref(),
        &b.token.clone(),
        b.dispatcher.as_ref(),
        "job-b",
    )
    .expect("current-epoch daemon dispatches");
    assert!(run.run_id > 0);
    assert_eq!(b.state.jobs["job-b"].state, JobState::Dispatched);
    let _ = std::fs::remove_dir_all(&root);
}

// T038-10 boot-reconciliation-github-wins
#[test]
fn t038_10_boot_reconciliation_github_wins() {
    let root = scratch("t10");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = DirOffVmStore::new(root.join("offvm"));
    let mut st = DaemonState::empty(home.clone());
    let mut live = new_queue_record(&job("live-job"));
    live.state = JobState::Dispatched;
    live.run_id = Some(4242);
    live.run_status = Some("queued".to_string());
    st.put_job(live);
    let mut done = new_queue_record(&job("done-job"));
    done.state = JobState::Completed;
    done.run_id = Some(4243);
    done.markers = vec!["PAYLOAD_OK".to_string()];
    done.verdict_source = Some("markers".to_string());
    st.put_job(done);
    st.flush(&FsWriter::default());
    // Boot on the flushed state.
    let (mut st2, gaps) = DaemonState::load(home.clone());
    let runs = FakeRuns::new();
    runs.set(4242, "in_progress", None);
    runs.set(4243, "completed", Some("failure"));
    let report = boot_reconcile(&mut st2, &store, &runs, &[], gaps);
    // GitHub wins on run state for the live record.
    assert_eq!(
        st2.jobs["live-job"].run_status.as_deref(),
        Some("in_progress")
    );
    assert_eq!(report.divergences, vec!["live-job".to_string()]);
    // The payload verdict on the completed record is untouched.
    assert_eq!(st2.jobs["done-job"].markers, vec!["PAYLOAD_OK".to_string()]);
    assert_eq!(
        st2.jobs["done-job"].verdict_source.as_deref(),
        Some("markers")
    );
    let _ = std::fs::remove_dir_all(&root);
}

// T038-11 state-home-separation
#[test]
fn t038_11_state_home_separation() {
    let code_dir = scratch("t11-code");
    std::fs::write(code_dir.join("binary-placeholder"), b"x").unwrap();
    let root = scratch("t11");
    let home = StateHome::new(root.join("state-home"));
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let (mut engine, _fake) = engine_for(&home, store, Arc::new(FsWriter::default()));
    engine.submit(&job("sep-job")).unwrap();
    // State landed only under the state home.
    assert!(home.job_path("sep-job").exists());
    assert!(!code_dir.join("queue").exists());
    let entries: Vec<_> = std::fs::read_dir(&code_dir).unwrap().collect();
    assert_eq!(entries.len(), 1, "code dir must stay untouched by state");
    let _ = std::fs::remove_dir_all(&root);
    let _ = std::fs::remove_dir_all(&code_dir);
}

// T038-12 venue-lane-settle-first-scan
#[test]
fn t038_12_venue_lane_settle_first_scan() {
    let root = scratch("t12");
    let home = StateHome::new(root);
    let mut st = DaemonState::empty(home);
    let lanes = vec![
        Lane {
            name: "quick".into(),
            venue: "native".into(),
            cap: 2,
        },
        Lane {
            name: "dock".into(),
            venue: "docker".into(),
            cap: 1,
        },
    ];
    let mk = |id: &str, lane: &str, venue: &str| {
        let mut j = job(id);
        j.lane = Some(lane.to_string());
        j.venue = venue.to_string();
        // Fresh submissions: only the dedicated old job below is
        // old enough to starve.
        j.created_at = "2026-11-08T01:59:00Z".to_string();
        new_queue_record(&j)
    };
    st.put_job(mk("wrong-venue", "quick", "docker")); // venue mismatch
    st.put_job(mk("auto-venue", "quick", "auto")); // auto matches
    st.put_job(mk("docker-job", "dock", "docker")); // matches docker lane
    let out = scan(&st, &lanes, &SettledRuns::default());
    assert!(
        !out.contains(&"wrong-venue".to_string()),
        "venue mismatch must not place: {out:?}"
    );
    assert!(out.contains(&"auto-venue".to_string()));
    assert!(out.contains(&"docker-job".to_string()));
    // Starvation: an old waiting job ledgers dispatch_starved once.
    let mut old = mk("old-job", "quick", "native");
    old.created_at = "2026-11-08T00:00:00Z".to_string();
    st.put_job(old);
    let mut tracker = StarvationTracker::default();
    let now = unix_from_iso("2026-11-08T02:00:00Z").unwrap();
    tracker.check(&mut st, now, 900);
    tracker.check(&mut st, now + 60, 900);
    let n = st
        .ledger_buffer
        .iter()
        .filter(|e| e.event == "dispatch_starved")
        .count();
    assert_eq!(n, 1, "exactly one starved event per episode");
}

// T038-25 ghost-reconcile-from-github-truth
#[test]
fn t038_25_ghost_reconcile_from_github_truth() {
    let root = scratch("t25");
    let home = StateHome::new(root.clone());
    let store = DirOffVmStore::new(root.join("offvm"));
    let mut st = DaemonState::empty(home);
    let mut ghost = new_queue_record(&job("ghost-job"));
    ghost.state = JobState::Dispatched;
    ghost.run_id = Some(987);
    st.put_job(ghost);
    let runs = FakeRuns::new();
    runs.set(987, "completed", Some("success"));
    let report = boot_reconcile(&mut st, &store, &runs, &[], vec![]);
    assert_eq!(report.ghosts, vec!["ghost-job".to_string()]);
    let done = reconcile_ghosts(&mut st, &runs, &report.ghosts, "ghost hold");
    assert_eq!(done, vec!["ghost-job".to_string()]);
    let g = &st.jobs["ghost-job"];
    assert_eq!(g.state, JobState::Completed);
    assert_eq!(g.run_conclusion.as_deref(), Some("success"));
    assert_eq!(g.verdict_source.as_deref(), Some("github-conclusion"));
    assert!(st
        .ledger_buffer
        .iter()
        .any(|e| e.event == "operator_record_repair"));
    let _ = std::fs::remove_dir_all(&root);
}

// T038-26 hold-queue-before-ghost-completion
#[test]
fn t038_26_hold_queue_before_ghost_completion() {
    let root = scratch("t26");
    let home = StateHome::new(root.clone());
    let mut st = DaemonState::empty(home.clone());
    st.put_job(new_queue_record(&job("waiting-job")));
    let mut maint = new_queue_record(&job("maint-job"));
    maint.maintenance = true;
    st.put_job(maint);
    let mut ghost = new_queue_record(&job("ghost-job"));
    ghost.state = JobState::Dispatched;
    ghost.run_id = Some(654);
    st.put_job(ghost);
    let runs = FakeRuns::new();
    runs.set(654, "completed", Some("cancelled"));
    let done = reconcile_ghosts(
        &mut st,
        &runs,
        &["ghost-job".to_string()],
        "hold-first test",
    );
    assert_eq!(done.len(), 1);
    // The hold was applied before completion freed the lane...
    assert!(st.paused.is_some());
    let events: Vec<&str> = st.ledger_buffer.iter().map(|e| e.event.as_str()).collect();
    assert!(
        events.iter().position(|e| *e == "ghost_hold_applied")
            < events.iter().position(|e| *e == "operator_record_repair"),
        "hold must be ledgered before the repair: {events:?}"
    );
    // ...and the scan now dispatches the maintenance job only:
    // completing the ghost must not release held program work.
    let lanes = vec![Lane {
        name: "quick".into(),
        venue: "native".into(),
        cap: 3,
    }];
    let out = scan(&st, &lanes, &SettledRuns::default());
    assert_eq!(out, vec!["maint-job".to_string()], "scan: {out:?}");
    let _ = std::fs::remove_dir_all(&root);
}

// T038-27 publish-verifies-every-destination
// T038-52 single-path-publisher-defect-2026-11-06
#[test]
fn t038_27_publish_verifies_every_destination() {
    let root = scratch("t27");
    let a = root.join("dest-a.json");
    let b = root.join("dest-b.json");
    let bytes = b"{\"snapshot\":true}";
    let report = publish_verified(&FsWriter::default(), bytes, &[a.clone(), b.clone()]);
    assert!(report.all_verified());
    // The 2026-11-06 defect wrote only one of two paths while
    // reporting success: both files must exist, byte-identical.
    assert_eq!(std::fs::read(&a).unwrap(), bytes);
    assert_eq!(std::fs::read(&b).unwrap(), bytes);
    // One bad destination fails the whole publish, by name.
    let bad = root.join("no-such-dir").join("c.json");
    let report = publish_verified(&FsWriter::default(), bytes, &[a.clone(), bad.clone()]);
    assert!(!report.all_verified());
    assert!(report.results[0].ok);
    assert!(!report.results[1].ok);
    assert_eq!(report.results[1].path, bad);
    let _ = std::fs::remove_dir_all(&root);
}

// T038-28 dead-owner-lock-takeover
// T038-53 stranded-lock-defect-2026-11-06
// Unix-only: the stranded-owner branch requires a liveness
// probe, and off-Unix pid_alive conservatively reports every
// owner alive (lock.rs, by design), so the takeover branch
// is unreachable there.
#[cfg(unix)]
#[test]
fn t038_28_dead_owner_lock_takeover() {
    let root = scratch("t28");
    let p = root.join("owner.lock");
    let w = FsWriter::default();
    // The 2026-11-06 geometry: owner dead, lock old. The old cure
    // waited 600 s of age; the rule now is death, not age.
    let stranded = OwnerLock {
        pid: 99_999_999,
        holder: "dead-daemon".into(),
        ts: "2026-11-06T00:00:00Z".into(),
    };
    w.write_verified(&p, &serde_json::to_vec(&stranded).unwrap())
        .unwrap();
    match acquire_owner_lock(&p, "us", &w).unwrap() {
        Takeover::Acquired => {}
        other => panic!("stranded lock must be taken over, got {other:?}"),
    }
    // A young lock with a dead owner is taken too (age irrelevant).
    let young = OwnerLock {
        pid: 99_999_998,
        holder: "just-died".into(),
        ts: syncyard_daemon::time::now_iso(),
    };
    w.write_verified(&p, &serde_json::to_vec(&young).unwrap())
        .unwrap();
    assert!(matches!(
        acquire_owner_lock(&p, "us", &w).unwrap(),
        Takeover::Acquired
    ));
    // A live owner is never taken from.
    let live = OwnerLock {
        pid: std::process::id(),
        holder: "alive".into(),
        ts: syncyard_daemon::time::now_iso(),
    };
    w.write_verified(&p, &serde_json::to_vec(&live).unwrap())
        .unwrap();
    assert!(matches!(
        acquire_owner_lock(&p, "us", &w).unwrap(),
        Takeover::OwnerAlive(_)
    ));
    let _ = std::fs::remove_dir_all(&root);
}

// T038-35 err01-fault-injection
#[test]
fn t038_35_err01_fault_injection() {
    // Kill the daemon at every dispatch-protocol boundary; after
    // boot reconciliation the job must end dispatched exactly
    // once: zero double, zero lost.
    for kill_after in 0u8..=4 {
        let root = scratch(&format!("t35-{kill_after}"));
        let home = StateHome::new(root.clone());
        std::fs::create_dir_all(home.queue_dir()).unwrap();
        let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
        let fake = FakeDispatcher::new();
        let token = acquire_epoch(store.as_ref(), "t35-daemon").unwrap();
        let writer = FsWriter::default();
        // Submit (acked: RAM + off-VM + disk cache).
        let mut st = DaemonState::empty(home.clone());
        let rec = new_queue_record(&job("fi-job"));
        let bytes = serde_json::to_vec_pretty(&rec).unwrap();
        store.put_record(KIND_SUBMITS, "fi-job", &bytes).unwrap();
        st.put_job(rec);
        st.flush(&writer);
        // Drive the protocol to the kill point.
        match kill_after {
            0 => { /* crash before begin */ }
            1 => {
                let _txn = DispatchTxn::begin(&mut st, store.as_ref(), &token, "fi-job").unwrap();
                // crash: txn + RAM dropped without flushing
            }
            2 => {
                let mut txn =
                    DispatchTxn::begin(&mut st, store.as_ref(), &token, "fi-job").unwrap();
                txn.trigger_and_capture(&mut st, store.as_ref(), &token, &fake)
                    .unwrap();
                // crash before commit
            }
            3 => {
                dispatch_one(&mut st, store.as_ref(), &token, &fake, "fi-job").unwrap();
                // crash after commit, before flush
            }
            _ => {
                dispatch_one(&mut st, store.as_ref(), &token, &fake, "fi-job").unwrap();
                st.flush(&writer);
                // crash after flush
            }
        }
        drop(st);
        // Boot: load disk, reconcile, recover, then dispatch if the
        // job is still waiting (the normal post-boot flow).
        let (mut st2, gaps) = DaemonState::load(home.clone());
        let runs = FakeRuns::new();
        let report = boot_reconcile(
            &mut st2,
            store.as_ref(),
            &runs,
            &["fi-job".to_string()],
            gaps,
        );
        for id in &report.dispatching_to_recover {
            recover_dispatching(&mut st2, &token, &fake, id).unwrap();
        }
        if st2.jobs["fi-job"].state == JobState::Submitted {
            dispatch_one(&mut st2, store.as_ref(), &token, &fake, "fi-job").unwrap();
        }
        assert_eq!(
            fake.trigger_calls(),
            1,
            "kill_after={kill_after}: exactly one trigger"
        );
        assert_eq!(fake.runs(), 1, "kill_after={kill_after}: exactly one run");
        let final_rec = &st2.jobs["fi-job"];
        assert_eq!(
            final_rec.state,
            JobState::Dispatched,
            "kill_after={kill_after}: job must end dispatched"
        );
        assert!(final_rec.run_id.is_some());
        let _ = std::fs::remove_dir_all(&root);
    }
}

// T038-36 err02-vm-loss-accounting
#[test]
fn t038_36_err02_vm_loss_accounting() {
    let root = scratch("t36");
    let home = StateHome::new(root.join("home"));
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let (mut engine, fake) = engine_for(&home, store.clone(), Arc::new(FsWriter::default()));
    engine.submit(&job("survivor-job")).unwrap();
    engine.submit(&job("dispatched-job")).unwrap();
    dispatch_one(
        &mut engine.state,
        store.as_ref(),
        &engine.token.clone(),
        engine.dispatcher.as_ref(),
        "dispatched-job",
    )
    .unwrap();
    drop(engine);
    // VM loss: the whole state home is gone.
    std::fs::remove_dir_all(home.root()).unwrap();
    let home2 = StateHome::new(root.join("home"));
    let (mut st2, gaps) = DaemonState::load(home2);
    assert!(st2.jobs.is_empty(), "disk state is gone after VM loss");
    let runs = FakeRuns::new();
    let report = boot_reconcile(
        &mut st2,
        store.as_ref(),
        &runs,
        &[
            "survivor-job".to_string(),
            "dispatched-job".to_string(),
            "never-recorded-job".to_string(),
        ],
        gaps,
    );
    // The acknowledged submit is recovered by name from off-VM.
    assert!(report
        .recovered_submits
        .contains(&"survivor-job".to_string()));
    assert!(st2.jobs.contains_key("survivor-job"));
    // The dispatched job is recovered from its witness. The
    // witness holds the step-4 state (dispatching + run id): the
    // dispatch provably happened, and post-boot dispatch recovery
    // finalizes the commit from GitHub-side evidence (the fake's
    // registry here) — no lost dispatch, no second trigger.
    assert!(report
        .recovered_dispatches
        .contains(&"dispatched-job".to_string()));
    assert!(report
        .dispatching_to_recover
        .contains(&"dispatched-job".to_string()));
    let rec = &st2.jobs["dispatched-job"];
    assert_eq!(rec.state, JobState::Dispatching);
    assert!(rec.run_id.is_some());
    let token2 = acquire_epoch(store.as_ref(), "t36-boot").unwrap();
    for id in &report.dispatching_to_recover {
        recover_dispatching(&mut st2, &token2, fake.as_ref(), id).unwrap();
    }
    let rec = &st2.jobs["dispatched-job"];
    assert_eq!(rec.state, JobState::Dispatched);
    assert!(rec.run_id.is_some());
    assert_eq!(fake.trigger_calls(), 1, "recovery must not re-trigger");
    // An acknowledged submit that exists nowhere is named, never
    // silently absorbed.
    assert_eq!(
        report.unrecorded_submits,
        vec!["never-recorded-job".to_string()]
    );
    let _ = std::fs::remove_dir_all(&root);
}

// T038-38 err06-migration-manifest
// T036-14 compaction-hash-chain-verified
#[test]
fn t038_38_err06_migration_manifest() {
    // A Python-gatekeeper-shaped source: queue/*.json + ledger.jsonl.
    let src = scratch("t38-src");
    std::fs::create_dir_all(src.join("queue")).unwrap();
    let mut want_hashes = std::collections::BTreeMap::new();
    for id in ["hist-a", "hist-b", "hist-c"] {
        let rec = new_queue_record(&job(id));
        let bytes = serde_json::to_vec_pretty(&rec).unwrap();
        want_hashes.insert(id.to_string(), sha256_hex(&bytes));
        std::fs::write(src.join("queue").join(format!("{id}.json")), &bytes).unwrap();
    }
    let ledger_text = "{\"event\":\"submitted\",\"job_id\":\"hist-a\"}\n{\"event\":\"dispatched\",\"job_id\":\"hist-a\"}\n{\"event\":\"submitted\",\"job_id\":\"hist-b\"}\n";
    std::fs::write(src.join("ledger.jsonl"), ledger_text).unwrap();
    let home = StateHome::new(scratch("t38-home"));
    let manifest = migrate_history(&src, &home, &|| false).unwrap();
    assert_eq!(manifest.records, want_hashes, "per-record SHA-256 manifest");
    assert_eq!(manifest.ledger_lines, 3);
    let verified = verify_history(&home).unwrap();
    assert_eq!(verified.ledger_chain, manifest.ledger_chain);
    // Round trip: export reproduces the source bytes exactly.
    let out = scratch("t38-out");
    let n = export_history(&home, &out).unwrap();
    assert_eq!(n, 3);
    for id in ["hist-a", "hist-b", "hist-c"] {
        let a = std::fs::read(src.join("queue").join(format!("{id}.json"))).unwrap();
        let b = std::fs::read(out.join("queue").join(format!("{id}.json"))).unwrap();
        assert_eq!(a, b, "record {id} must round-trip byte-exact");
    }
    assert_eq!(
        std::fs::read(out.join("ledger.jsonl")).unwrap(),
        ledger_text.as_bytes()
    );
    // The hash chain is the guard: tamper with the archived ledger
    // and repair only the plain file hash in the manifest — the
    // chain must still catch it.
    let hist = home.history_dir();
    let mut ledger = std::fs::read(hist.join("ledger.jsonl")).unwrap();
    ledger[10] ^= 0x01;
    std::fs::write(hist.join("ledger.jsonl"), &ledger).unwrap();
    let mut manifest_v: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(hist.join("manifest.json")).unwrap())
            .unwrap();
    manifest_v["ledger_sha256"] = serde_json::Value::String(sha256_hex(&ledger));
    std::fs::write(
        hist.join("manifest.json"),
        serde_json::to_vec_pretty(&manifest_v).unwrap(),
    )
    .unwrap();
    match verify_history(&home) {
        Err(MigrateError::Mismatch(what)) => {
            assert!(
                what.contains("chain"),
                "chain must be the failing check, got: {what}"
            )
        }
        other => panic!("expected a chain mismatch, got {other:?}"),
    }
    let _ = std::fs::remove_dir_all(&src);
    let _ = std::fs::remove_dir_all(home.root());
    let _ = std::fs::remove_dir_all(&out);
}

// T036-15 compaction-blocked-in-wave
#[test]
fn t036_15_compaction_blocked_in_wave() {
    let src = scratch("t36-15-src");
    std::fs::create_dir_all(src.join("queue")).unwrap();
    let home = StateHome::new(scratch("t36-15-home"));
    match migrate_history(&src, &home, &|| true) {
        Err(MigrateError::WaveDetected) => {}
        other => panic!("expected WaveDetected, got {other:?}"),
    }
    assert!(
        !home.history_dir().join("manifest.json").exists(),
        "no archive may be written during a wave"
    );
    let _ = std::fs::remove_dir_all(&src);
    let _ = std::fs::remove_dir_all(home.root());
}

// T038-39 err08-wave-fail-fast
// T036-18 wave-fixture-all-writers
#[test]
fn t038_39_err08_wave_fail_fast() {
    use std::sync::atomic::AtomicUsize;
    // Count write attempts to prove fail-fast: the pre-ERR-08
    // behavior burned 8 retries per record per pass.
    struct CountingWriter {
        gate: GateWriter,
        attempts: AtomicUsize,
    }
    impl StateWriter for CountingWriter {
        fn write_verified(
            &self,
            path: &std::path::Path,
            bytes: &[u8],
        ) -> Result<(), syncyard_daemon::vwrite::WriteFailure> {
            self.attempts.fetch_add(1, Ordering::SeqCst);
            self.gate.write_verified(path, bytes)
        }
        fn append_verified(
            &self,
            path: &std::path::Path,
            line: &[u8],
        ) -> Result<(), syncyard_daemon::vwrite::WriteFailure> {
            self.attempts.fetch_add(1, Ordering::SeqCst);
            self.gate.append_verified(path, line)
        }
    }
    let root = scratch("t39");
    let home = StateHome::new(root.clone());
    let mut st = DaemonState::empty(home.clone());
    // Dirty every writer class: records, ledger, outbox, control.
    st.put_job(new_queue_record(&job("w1")));
    st.put_job(new_queue_record(&job("w2")));
    st.put_job(new_queue_record(&job("w3")));
    st.ledger("submitted", std::collections::BTreeMap::new());
    st.outbox_push("{\"group\":1}".to_string());
    st.set_paused(Some(syncyard_daemon::state::PauseRecord {
        reason: "wave test".into(),
        ts: "2026-11-08T00:00:00Z".into(),
    }));
    let writer = CountingWriter {
        gate: GateWriter::new(true),
        attempts: AtomicUsize::new(0),
    };
    let report = st.flush(&writer);
    assert!(!report.ok && report.denied);
    assert!(
        writer.attempts.load(Ordering::SeqCst) <= 2,
        "fail-fast: one denied probe, not retries per record (got {})",
        writer.attempts.load(Ordering::SeqCst)
    );
    assert!(st.flush_health.wave_mode);
    // The wave is a named ledger constraint, ledgered once.
    let wave_events: Vec<_> = st
        .ledger_buffer
        .iter()
        .filter(|e| e.event == "write_wave_detected")
        .collect();
    assert_eq!(wave_events.len(), 1);
    assert_eq!(
        wave_events[0].fields.get("constraint"),
        Some(&serde_json::Value::String(
            "write_wave_detected".to_string()
        ))
    );
    // Nothing was lost: after the wave clears, the whole backlog
    // lands in one flush (the spec 032 falsifier).
    writer.gate.set_deny(false);
    let report = st.flush(&writer);
    assert!(report.ok, "{:?}", report.errors);
    assert!(!st.flush_health.wave_mode);
    for id in ["w1", "w2", "w3"] {
        assert!(home.job_path(id).exists(), "{id} must land after the wave");
    }
    let ledger_on_disk = std::fs::read_to_string(home.ledger_path()).unwrap();
    assert!(ledger_on_disk.contains("write_wave_detected"));
    assert!(home.outbox_path().exists());
    assert!(home.pause_path().exists());
    let _ = std::fs::remove_dir_all(&root);
}

// T038-40 err09-self-start-announce
#[test]
fn t038_40_err09_self_start_announce() {
    let root = scratch("t40");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    // One good record on disk, one broken file (a named gap), and
    // one off-VM submit with no disk record (a named recovery).
    let mut st = DaemonState::empty(home.clone());
    st.put_job(new_queue_record(&job("disk-job")));
    st.flush(&FsWriter::default());
    std::fs::write(home.job_path("broken-record"), b"{not json at all").unwrap();
    let store = DirOffVmStore::new(root.join("offvm"));
    let orphan = new_queue_record(&job("offvm-only-job"));
    store
        .put_record(
            KIND_SUBMITS,
            "offvm-only-job",
            &serde_json::to_vec_pretty(&orphan).unwrap(),
        )
        .unwrap();
    let (mut st2, gaps) = DaemonState::load(home.clone());
    let runs = FakeRuns::new();
    let report = boot_reconcile(&mut st2, &store, &runs, &[], gaps);
    assert_eq!(report.gaps.len(), 1);
    assert!(
        report.gaps[0].contains("broken-record"),
        "gap named: {:?}",
        report.gaps
    );
    assert_eq!(report.recovered_submits, vec!["offvm-only-job".to_string()]);
    // The boot is announced in the ledger with its accounting.
    let boot = st2
        .ledger_buffer
        .iter()
        .find(|e| e.event == "daemon_boot_reconciled")
        .expect("boot must be ledgered");
    assert_eq!(boot.fields.get("gaps"), Some(&serde_json::json!(1)));
    let _ = std::fs::remove_dir_all(&root);
}

// T038-60 sequence-orders-reconciliation
#[test]
fn t038_60_sequence_orders_reconciliation() {
    let root = scratch("t60");
    let home = StateHome::new(root.clone());
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    // Disk copy: LOWER sequence but a LATER wall clock (a restored
    // backup written by a clock-skewed writer).
    let mut disk = new_queue_record(&job("seq-job"));
    disk.state = JobState::Dispatched;
    disk.seq = Some(5);
    disk.completed_at = None;
    std::fs::write(
        home.job_path("seq-job"),
        serde_json::to_vec_pretty(&disk).unwrap(),
    )
    .unwrap();
    // Off-VM witness: HIGHER sequence, earlier wall clock, terminal.
    let mut witness = disk.clone();
    witness.state = JobState::Completed;
    witness.seq = Some(9);
    witness.completed_at = Some("2026-11-08T01:00:00Z".to_string());
    let store = DirOffVmStore::new(root.join("offvm"));
    store
        .put_record(
            KIND_PREDISPATCH,
            "seq-job",
            &serde_json::to_vec_pretty(&witness).unwrap(),
        )
        .unwrap();
    let (mut st, gaps) = DaemonState::load(home.clone());
    let runs = FakeRuns::new();
    let _report = boot_reconcile(&mut st, &store, &runs, &[], gaps);
    // Sequence wins: the record is the seq-9 terminal state, not
    // the seq-5 state the wall clock would have picked.
    assert_eq!(st.jobs["seq-job"].state, JobState::Completed);
    let _ = std::fs::remove_dir_all(&root);
}
