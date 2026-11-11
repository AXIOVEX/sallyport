//! P3 CLI tests: the spec 034 progress verb surface and the
//! CLI/daemon/contract version agreement (T034-13).

// Unix-only as a whole: both tests drive the daemon's live
// socket transport, which refuses by name on non-Unix
// platforms in this build (serve.rs — the Windows
// named-pipe transport is not yet wired).
#![cfg(unix)]

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use syncyard_cli::{run_cli, schema, Client, FileClient, SocketClient};
use syncyard_daemon::dispatch::fake::FakeDispatcher;
use syncyard_daemon::offvm::{acquire_epoch, DirOffVmStore};
use syncyard_daemon::serve::{serve, Engine, ServeConfig};
use syncyard_daemon::state::{DaemonState, StateHome};
use syncyard_daemon::vwrite::FsWriter;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p3-cli-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn engine_for(home: &StateHome, offvm: &std::path::Path) -> Engine {
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = Arc::new(DirOffVmStore::new(offvm.to_path_buf()));
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

fn wait_up(home: &StateHome) {
    let mut client = SocketClient { home: home.clone() };
    for _ in 0..60 {
        if let Ok(r) = client.call(&serde_json::json!({"verb": "ping"})) {
            if r["ok"] == serde_json::Value::Bool(true) {
                return;
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    panic!("daemon never came up");
}

fn sample_job(id: &str) -> syncyard_contract::JobRecord {
    syncyard_contract::JobRecord {
        id: id.to_string(),
        key: format!("k-{id}"),
        program: "cli-test".to_string(),
        workflow: "run.yml".to_string(),
        lane: Some("quick".to_string()),
        cwd: None,
        expected_min: 5.0,
        priority: 0,
        priority_source: "auto".to_string(),
        flags: vec![],
        maintenance: false,
        adopted: false,
        notify_chat: None,
        venue: "native".to_string(),
        script_path: Some("/bin/true".to_string()),
        script_sha256: Some("ab12".to_string()),
        grab_path: None,
        created_at: "2026-11-08T05:00:00Z".to_string(),
    }
}

// T034-13 cli-daemon-contract-compat (spec 034 section 7): the
// CLI's own contract version (what `schema` prints), the
// contract crate's version, and the version a live daemon
// reports on ping must all agree — that agreement is what lets
// a newer agent talk to an older daemon and vice versa within
// the version's compatibility promise.
#[test]
fn t034_13_cli_daemon_contract_compat() {
    let root = scratch("t13");
    let home = StateHome::new(root.join("gk-home"));
    let offvm = root.join("offvm");

    let schema_doc = schema();
    let cli_version = schema_doc["contract_version"].as_u64().unwrap();
    assert_eq!(cli_version, u64::from(syncyard_contract::CONTRACT_VERSION));
    // The progress verb is part of the surface the schema
    // advertises (it is how watchers read spec 034 progress).
    let verbs: Vec<String> = schema_doc["verbs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    assert!(verbs.iter().any(|v| v == "progress"), "verbs: {verbs:?}");

    // File-fallback client agrees.
    {
        let mut fc = FileClient::open(&home, &offvm).unwrap();
        let ping = fc.call(&serde_json::json!({"verb": "ping"})).unwrap();
        assert_eq!(
            ping["contract_version"].as_u64().unwrap(),
            u64::from(syncyard_contract::CONTRACT_VERSION)
        );
    }

    // A live daemon on the socket agrees.
    let engine = Arc::new(Mutex::new(engine_for(&home, &offvm)));
    let shutdown = Arc::new(AtomicBool::new(false));
    let (home2, sd) = (home.clone(), Arc::clone(&shutdown));
    let thread = std::thread::spawn(move || {
        serve(
            engine,
            &home2,
            ServeConfig {
                flush_interval: Duration::from_millis(50),
            },
            sd,
        )
    });
    wait_up(&home);
    let mut sc = SocketClient { home: home.clone() };
    let ping = sc.call(&serde_json::json!({"verb": "ping"})).unwrap();
    assert_eq!(
        ping["contract_version"].as_u64().unwrap(),
        u64::from(syncyard_contract::CONTRACT_VERSION)
    );
    let _ = sc.call(&serde_json::json!({"verb": "shutdown"}));
    shutdown.store(true, Ordering::SeqCst);
    let _ = thread.join();
    let _ = std::fs::remove_dir_all(&root);
}

// The progress verb end to end through run_cli in socket mode:
// submit, stream lines in, read the view back — a watcher sees
// fraction and markers without waiting for harvest (spec 034
// FR-034-08's CLI surface).
#[test]
fn progress_verb_surfaces_streamed_progress() {
    let root = scratch("verb");
    let home = StateHome::new(root.join("gk-home"));
    let offvm = root.join("offvm");
    let engine = Arc::new(Mutex::new(engine_for(&home, &offvm)));
    let shutdown = Arc::new(AtomicBool::new(false));
    let (home2, sd) = (home.clone(), Arc::clone(&shutdown));
    let thread = std::thread::spawn(move || {
        serve(
            engine,
            &home2,
            ServeConfig {
                flush_interval: Duration::from_millis(50),
            },
            sd,
        )
    });
    wait_up(&home);
    let mut sc = SocketClient { home: home.clone() };
    sc.call(&serde_json::json!({
        "verb": "submit",
        "job": serde_json::to_value(sample_job("job-cli-1")).unwrap(),
    }))
    .unwrap();
    let ack = sc
        .call(&serde_json::json!({
            "verb": "progress",
            "job_id": "job-cli-1",
            "run_id": 4242,
            "source": "agent:cli-test",
            "lines": ["STARTED", "PROGRESS 3/4 rows"],
        }))
        .unwrap();
    assert_eq!(ack["ack"], serde_json::json!("applied"));

    let args: Vec<String> = vec!["progress".into(), "job-cli-1".into()];
    let outcome = run_cli(&args, &home, &offvm);
    assert_eq!(outcome.code, 0, "stdout: {}", outcome.stdout);
    let v: serde_json::Value = serde_json::from_str(&outcome.stdout).unwrap();
    // The progress verb returns the view flat (the status verb
    // nests the same view under its "progress" key).
    assert_eq!(v["fraction"], serde_json::json!([3, 4]));
    assert_eq!(v["started"], serde_json::json!(true));
    assert_eq!(v["display"], serde_json::json!("in progress"));

    // A job with no stream reads the spec's silence wording
    // through the same verb.
    sc.call(&serde_json::json!({
        "verb": "submit",
        "job": serde_json::to_value(sample_job("job-cli-2")).unwrap(),
    }))
    .unwrap();
    let args2: Vec<String> = vec!["progress".into(), "job-cli-2".into()];
    let outcome2 = run_cli(&args2, &home, &offvm);
    assert_eq!(outcome2.code, 0);
    let v2: serde_json::Value = serde_json::from_str(&outcome2.stdout).unwrap();
    assert_eq!(v2["display"], serde_json::json!("no progress reported"));

    let mut sc2 = SocketClient { home: home.clone() };
    let _ = sc2.call(&serde_json::json!({"verb": "shutdown"}));
    shutdown.store(true, Ordering::SeqCst);
    let _ = thread.join();
    let _ = std::fs::remove_dir_all(&root);
}
