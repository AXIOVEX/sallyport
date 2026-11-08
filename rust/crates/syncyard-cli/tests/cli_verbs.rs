//! CLI parity tests (P2): the core verb surface against the
//! direct-file fallback and against a live daemon over its socket.
//! Scratch state homes only; the live gatekeeper is never touched.

use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use syncyard_cli::run_cli;
use syncyard_daemon::dispatch::fake::FakeDispatcher;
use syncyard_daemon::offvm::{acquire_epoch, DirOffVmStore, OffVmStore};
use syncyard_daemon::serve::{request, serve, Engine, ServeConfig};
use syncyard_daemon::state::{DaemonState, StateHome};
use syncyard_daemon::vwrite::FsWriter;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-cli-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn run(home: &StateHome, offvm: &std::path::Path, args: &[&str]) -> (i32, serde_json::Value) {
    let argv: Vec<String> = args.iter().map(|s| s.to_string()).collect();
    let outcome = run_cli(&argv, home, offvm);
    let v: serde_json::Value = serde_json::from_str(&outcome.stdout)
        .unwrap_or_else(|_| panic!("CLI output not JSON: {}", outcome.stdout));
    (outcome.code, v)
}

#[test]
fn fallback_verbs_end_to_end() {
    let root = scratch("fallback");
    let home = StateHome::new(root.join("home"));
    let offvm = root.join("offvm");
    // version + schema need no daemon.
    let (code, v) = run(&home, &offvm, &["version"]);
    assert_eq!(code, 0);
    assert_eq!(v["contract_version"], 1);
    let (code, v) = run(&home, &offvm, &["schema"]);
    assert_eq!(code, 0);
    let verbs: Vec<&str> = v["verbs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|x| x.as_str().unwrap())
        .collect();
    for want in [
        "submit",
        "status",
        "dispatch",
        "pause",
        "start",
        "stop",
        "hold",
        "resume",
        "cancel",
        "outbox",
        "ledger",
        "snapshot",
        "migrate-history",
    ] {
        assert!(verbs.contains(&want), "schema missing verb {want}");
    }
    // The parity gap is explicit in the schema, not silent.
    let not_yet: Vec<&str> = v["not_yet_verbs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|x| x["verb"].as_str().unwrap())
        .collect();
    assert!(not_yet.contains(&"sweep"));
    // submit (script hash computed from the file) -> status.
    let script = root.join("payload.ps1");
    std::fs::write(&script, b"Write-Output hello").unwrap();
    let (code, v) = run(
        &home,
        &offvm,
        &[
            "submit",
            "--key",
            "cli-test-job",
            "--program",
            "test",
            "--workflow",
            "run.yml",
            "--lane",
            "quick",
            "--script",
            script.to_str().unwrap(),
        ],
    );
    assert_eq!(code, 0, "submit: {v}");
    let job_id = v["job_id"].as_str().unwrap().to_string();
    assert!(job_id.contains("cli-test-job"));
    let (code, v) = run(&home, &offvm, &["status", &job_id]);
    assert_eq!(code, 0);
    assert_eq!(v["state"], "submitted");
    assert_eq!(
        v["script_sha256"],
        serde_json::Value::String(syncyard_daemon::vwrite::sha256_hex(b"Write-Output hello"))
    );
    // Duplicate active key refused (gk.py parity).
    let (code, v) = run(
        &home,
        &offvm,
        &[
            "submit",
            "--key",
            "cli-test-job",
            "--program",
            "test",
            "--workflow",
            "run.yml",
        ],
    );
    assert_eq!(code, 1);
    assert!(v["error"].as_str().unwrap().contains("duplicate key"));
    // Dispatch in fallback mode is refused, by name.
    let (code, v) = run(&home, &offvm, &["dispatch", &job_id]);
    assert_eq!(code, 1);
    assert!(
        v["error"].as_str().unwrap().contains("requires the daemon"),
        "dispatch refusal must name the reason: {v}"
    );
    // pause -> snapshot shows it; start clears it.
    let (code, _v) = run(&home, &offvm, &["pause", "--reason", "cli test"]);
    assert_eq!(code, 0);
    let (_code, v) = run(&home, &offvm, &["snapshot"]);
    assert_eq!(v["paused"], true);
    let (code, v) = run(&home, &offvm, &["start"]);
    assert_eq!(code, 0);
    assert_eq!(v["cleared"], serde_json::json!(["pause"]));
    // hold / resume / cancel.
    let (code, _v) = run(&home, &offvm, &["hold", &job_id]);
    assert_eq!(code, 0);
    let (code, _v) = run(&home, &offvm, &["resume", &job_id]);
    assert_eq!(code, 0);
    let (code, _v) = run(&home, &offvm, &["cancel", &job_id]);
    assert_eq!(code, 0);
    let (_code, v) = run(&home, &offvm, &["status", &job_id]);
    assert_eq!(v["state"], "cancelled");
    // ledger shows the trail.
    let (code, v) = run(&home, &offvm, &["ledger", "--tail", "50"]);
    assert_eq!(code, 0);
    let events: Vec<&str> = v["events"]
        .as_array()
        .unwrap()
        .iter()
        .filter_map(|e| e["event"].as_str())
        .collect();
    for want in [
        "submitted",
        "paused",
        "started",
        "operator_hold",
        "operator_cancel",
    ] {
        assert!(events.contains(&want), "ledger missing {want}: {events:?}");
    }
    // Unknown verb is an error, not a panic.
    let (code, _v) = run(&home, &offvm, &["frobnicate"]);
    assert_eq!(code, 1);
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn socket_verbs_against_live_daemon() {
    let root = scratch("socket");
    let home = StateHome::new(root.join("home"));
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let offvm = root.join("offvm");
    let store = Arc::new(DirOffVmStore::new(offvm.clone()));
    let token = acquire_epoch(store.as_ref(), "cli-test-daemon").unwrap();
    let (state, _gaps) = DaemonState::load(home.clone());
    let store_dyn: Arc<dyn OffVmStore> = store;
    let engine = Arc::new(Mutex::new(Engine {
        state,
        store: store_dyn,
        dispatcher: Arc::new(FakeDispatcher::new()),
        token,
        writer: Arc::new(FsWriter::default()),
    }));
    let shutdown = Arc::new(AtomicBool::new(false));
    let home2 = home.clone();
    let sd2 = Arc::clone(&shutdown);
    let thread = std::thread::spawn(move || {
        serve(
            engine,
            &home2,
            ServeConfig {
                flush_interval: Duration::from_millis(50),
            },
            sd2,
        )
    });
    // Wait for the daemon, then drive it purely through the CLI.
    let mut up = false;
    for _ in 0..60 {
        if request(&home, &serde_json::json!({"verb": "ping"})).is_ok() {
            up = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(up, "daemon never came up");
    let (code, v) = run(
        &home,
        &offvm,
        &[
            "submit",
            "--key",
            "sock-job",
            "--program",
            "test",
            "--workflow",
            "run.yml",
            "--lane",
            "quick",
        ],
    );
    assert_eq!(code, 0, "socket submit: {v}");
    let job_id = v["job_id"].as_str().unwrap().to_string();
    // The mirror's lift leg: `dispatch` over the socket.
    let (code, v) = run(&home, &offvm, &["dispatch", &job_id]);
    assert_eq!(code, 0, "socket dispatch: {v}");
    assert!(v["run_id"].as_u64().unwrap() > 0);
    let (code, v) = run(&home, &offvm, &["status", &job_id]);
    assert_eq!(code, 0);
    assert_eq!(v["state"], "dispatched");
    let _ = request(&home, &serde_json::json!({"verb": "shutdown"}));
    thread.join().unwrap().unwrap();
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn migrate_history_verb_round_trip() {
    // A Python-shaped source dir, migrated through the CLI verb.
    let root = scratch("migrate");
    let src = root.join("python-gk");
    std::fs::create_dir_all(src.join("queue")).unwrap();
    let rec = syncyard_daemon::state::new_queue_record(&syncyard_contract::JobRecord {
        id: "mig-job".to_string(),
        key: "mig".to_string(),
        program: "test".to_string(),
        workflow: "run.yml".to_string(),
        lane: Some("quick".to_string()),
        cwd: None,
        expected_min: 1.0,
        priority: 0,
        priority_source: "auto".to_string(),
        flags: vec![],
        maintenance: false,
        adopted: false,
        notify_chat: None,
        venue: "native".to_string(),
        script_path: None,
        script_sha256: None,
        grab_path: None,
        created_at: "2026-11-08T00:00:00Z".to_string(),
    });
    std::fs::write(
        src.join("queue").join("mig-job.json"),
        serde_json::to_vec_pretty(&rec).unwrap(),
    )
    .unwrap();
    std::fs::write(src.join("ledger.jsonl"), b"{\"event\":\"submitted\"}\n").unwrap();
    let home = StateHome::new(root.join("home"));
    let offvm = root.join("offvm");
    let (code, v) = run(
        &home,
        &offvm,
        &["migrate-history", "--from", src.to_str().unwrap()],
    );
    assert_eq!(code, 0, "migrate-history: {v}");
    assert_eq!(v["records"], 1);
    assert_eq!(v["ledger_lines"], 1);
    assert!(home.history_dir().join("manifest.json").exists());
    let _ = std::fs::remove_dir_all(&root);
}
