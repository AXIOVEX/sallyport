//! P4 end-to-end: spec 037 tray control against the real
//! daemon over its real socket. The command travels as a file
//! (written by the tray's issuer, read back, delivered as the
//! backchannel would deliver it); confirmation is read from
//! the gatekeeper's published outcomes, never from the write.

use std::path::PathBuf;
#[cfg(unix)]
use std::sync::atomic::{AtomicBool, Ordering};
#[cfg(unix)]
use std::sync::{Arc, Mutex};
#[cfg(unix)]
use std::time::Duration;

#[cfg(unix)]
use syncyard_contract::JobRecord;
use syncyard_contract::{ControlAction, ControlCommand, HostSecretKey};
#[cfg(unix)]
use syncyard_daemon::dispatch::fake::FakeDispatcher;
#[cfg(unix)]
use syncyard_daemon::offvm::{acquire_epoch, DirOffVmStore};
#[cfg(unix)]
use syncyard_daemon::serve::{serve, Engine, ServeConfig};
#[cfg(unix)]
use syncyard_daemon::state::{new_queue_record, DaemonState, StateHome};
#[cfg(unix)]
use syncyard_daemon::vwrite::FsWriter;
use syncyard_tray::control::{display_state, CommandDisplay, CommandIssuer};
#[cfg(unix)]
use syncyard_tray::control::{fetch_outcomes, read_kill_switch, set_kill_switch};
#[cfg(unix)]
use syncyard_tray::control::{GatekeeperChannel, SocketGatekeeper};

const HOST: &str = "test-host";

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p4-e2e-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn tray_key() -> HostSecretKey {
    HostSecretKey::from_seed([42u8; 32])
}

#[cfg(unix)]
struct Daemon {
    ch: SocketGatekeeper,
    shutdown: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<Result<(), std::io::Error>>>,
    _root: PathBuf,
}

#[cfg(unix)]
fn start_daemon(tag: &str) -> Daemon {
    let root = scratch(tag);
    let home = StateHome::new(root.join("gk-home"));
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let (mut state, _gaps) = DaemonState::load(home.clone());
    state
        .control
        .host_keys
        .insert(HOST.to_string(), tray_key().public_key().to_hex());
    let rec = JobRecord {
        id: "job-1".to_string(),
        key: "k-job-1".to_string(),
        program: "e2e".to_string(),
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
        created_at: "2026-11-08T09:00:00Z".to_string(),
    };
    state.put_job(new_queue_record(&rec));
    let token = acquire_epoch(store.as_ref(), "test-daemon").unwrap();
    let engine = Engine {
        state,
        store,
        dispatcher: Arc::new(FakeDispatcher::new()),
        token,
        writer: Arc::new(FsWriter::default()),
        pins: syncyard_daemon::workflowpin::WorkflowPins::new(),
    };
    let shutdown = Arc::new(AtomicBool::new(false));
    let sd = Arc::clone(&shutdown);
    let home2 = home.clone();
    let thread = std::thread::spawn(move || {
        serve(
            Arc::new(Mutex::new(engine)),
            &home2,
            ServeConfig {
                flush_interval: Duration::from_millis(50),
            },
            sd,
        )
    });
    let ch = SocketGatekeeper {
        socket_path: home.socket_path(),
    };
    for _ in 0..80 {
        if let Ok(r) = ch.call(&serde_json::json!({"verb": "ping"})) {
            if r["ok"] == serde_json::Value::Bool(true) {
                break;
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    Daemon {
        ch,
        shutdown,
        thread: Some(thread),
        _root: root,
    }
}

#[cfg(unix)]
impl Drop for Daemon {
    fn drop(&mut self) {
        self.shutdown.store(true, Ordering::SeqCst);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}

// T037-07 confirmation-from-state + T037-09 cancel path, end
// to end: PENDING until the gatekeeper's outcome is published;
// the tray's own write confirms nothing.
// Unix-only: start_daemon serves over the daemon's socket,
// and the socket transport refuses by name on non-Unix
// platforms in this build (serve.rs — the Windows
// named-pipe transport is not yet wired).
#[cfg(unix)]
#[test]
fn t037_07_and_t037_09_command_round_trip() {
    let daemon = start_daemon("roundtrip");
    let outbox = daemon._root.join("outbox");
    let issuer = CommandIssuer::new(
        outbox.clone(),
        HOST.to_string(),
        "0.4.0".to_string(),
        tray_key(),
    );

    // The app issues: one signed file lands in the outbox.
    let cmd = issuer
        .issue(ControlAction::Cancel, Some("job-1"), None)
        .unwrap();
    let files: Vec<_> = std::fs::read_dir(&outbox).unwrap().flatten().collect();
    assert_eq!(files.len(), 1, "exactly one command file");

    // Before any gatekeeper decision: PENDING, never done.
    let outcomes = fetch_outcomes(&daemon.ch).unwrap();
    assert!(outcomes.is_empty());
    let now = syncyard_contract::isotime::now_unix();
    assert_eq!(display_state(&cmd, &outcomes, now), CommandDisplay::Pending);

    // The backchannel delivers the file's content to the
    // gatekeeper (here: straight over the daemon socket).
    let text = std::fs::read_to_string(files[0].path()).unwrap();
    let delivered: ControlCommand = serde_json::from_str(&text).unwrap();
    assert_eq!(delivered, cmd);
    let resp = daemon
        .ch
        .call(&serde_json::json!({"verb": "control_command", "command": delivered}))
        .unwrap();
    assert_eq!(resp["ok"], serde_json::Value::Bool(true));
    assert_eq!(resp["outcome"]["status"], "executed");
    assert_eq!(resp["outcome"]["ledger_event"], "operator_cancel");

    // Now — and only now — the app may render it confirmed,
    // reading the gatekeeper's published outcomes.
    let outcomes = fetch_outcomes(&daemon.ch).unwrap();
    assert_eq!(outcomes.len(), 1);
    assert_eq!(
        display_state(&cmd, &outcomes, now),
        CommandDisplay::Confirmed {
            ledger_event: Some("operator_cancel".to_string())
        }
    );
    // And the job really is cancelled in gatekeeper state.
    let snap = daemon
        .ch
        .call(&serde_json::json!({"verb": "snapshot"}))
        .unwrap();
    let job = snap["jobs"]
        .as_array()
        .unwrap()
        .iter()
        .find(|j| j["id"] == "job-1")
        .unwrap();
    assert_eq!(job["state"], "cancelled");
}

// T037-05 expiry-enforcement (tray half): an expired command
// with no published outcome renders EXPIRED — not executed,
// with its age.
#[test]
fn t037_05_expired_unconfirmed_renders_expired_not_executed() {
    let now = syncyard_contract::isotime::now_unix();
    let mut cmd = ControlCommand {
        command_id: "cmd-old".to_string(),
        action: ControlAction::Hold,
        job_id: Some("job-1".to_string()),
        lane: None,
        host: None,
        new_priority: None,
        issuer_host: HOST.to_string(),
        issuer_identity: "syncyard-tray 0.4.0".to_string(),
        nonce: "nonce-old".to_string(),
        issued_at: syncyard_contract::isotime::iso_from_unix((now - 7200) as u64),
        expires_at: syncyard_contract::isotime::iso_from_unix((now - 3600) as u64),
        app_train: "0.4.0".to_string(),
        contract_version: syncyard_contract::CONTRACT_VERSION,
        signature: String::new(),
    };
    cmd.sign(&tray_key());
    match display_state(&cmd, &[], now) {
        CommandDisplay::ExpiredNotExecuted { age_s } => {
            assert!(age_s >= 7200, "age reported: {age_s}");
        }
        other => panic!("expected ExpiredNotExecuted, got {other:?}"),
    }
}

// T037-10 repair-kill-switch (tray half): set and read through
// the gatekeeper channel; the state the app shows is the
// gatekeeper's record.
// Unix-only: same start_daemon socket-transport reason as
// t037_07_and_t037_09 above.
#[cfg(unix)]
#[test]
fn t037_10_tray_kill_switch_round_trip() {
    let daemon = start_daemon("killswitch");
    assert!(!read_kill_switch(&daemon.ch).unwrap());
    assert!(set_kill_switch(&daemon.ch, true).unwrap());
    assert!(read_kill_switch(&daemon.ch).unwrap());
    let snap = daemon
        .ch
        .call(&serde_json::json!({"verb": "snapshot"}))
        .unwrap();
    assert_eq!(snap["kill_switch"], serde_json::Value::Bool(true));
    assert!(!set_kill_switch(&daemon.ch, false).unwrap());
    assert!(!read_kill_switch(&daemon.ch).unwrap());
}

// T037-12 windowless-control-path: the tray's control module
// spawns nothing (no process API on the control path at all),
// and issuing creates exactly one file.
#[test]
fn t037_12_control_path_spawns_nothing_and_files_exactly_one_command() {
    let src = std::fs::read_to_string(
        std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("src/control.rs"),
    )
    .unwrap();
    assert!(
        !src.contains("process::Command"),
        "no process spawning on the control path"
    );
    assert!(!src.contains(".spawn("), "no spawned children");
    let outbox = scratch("t12-files").join("outbox");
    let issuer = CommandIssuer::new(
        outbox.clone(),
        HOST.to_string(),
        "0.4.0".to_string(),
        tray_key(),
    );
    let cmd = issuer
        .issue(ControlAction::Hold, Some("job-9"), None)
        .unwrap();
    // The issued file parses and verifies against the host key.
    let files: Vec<_> = std::fs::read_dir(&outbox).unwrap().flatten().collect();
    assert_eq!(files.len(), 1);
    let text = std::fs::read_to_string(files[0].path()).unwrap();
    let filed: ControlCommand = serde_json::from_str(&text).unwrap();
    assert_eq!(filed.command_id, cmd.command_id);
    assert!(filed.verify(&tray_key().public_key()));
}

#[cfg(unix)]
#[test]
fn socket_gatekeeper_bounds_an_oversized_response() {
    use std::io::{BufRead, BufReader, Write};
    use std::os::unix::net::UnixListener;

    let root = scratch("oversized-response");
    let sock = root.join("gk.sock");
    let listener = UnixListener::bind(&sock).unwrap();
    let _server = std::thread::spawn(move || {
        if let Some(Ok(mut stream)) = listener.incoming().next() {
            let mut reader = BufReader::new(stream.try_clone().unwrap());
            let mut line = String::new();
            let _ = reader.read_line(&mut line);
            let mut resp = vec![b'x'; 1_048_577];
            resp.push(b'\n');
            let _ = stream.write_all(&resp);
        }
    });
    let ch = SocketGatekeeper { socket_path: sock };
    let err = ch.call(&serde_json::json!({"verb": "ping"})).unwrap_err();
    assert!(
        err.contains("exceeds"),
        "an oversized response must fail on the named bound, got: {err}"
    );
    let _ = std::fs::remove_dir_all(&root);
}
