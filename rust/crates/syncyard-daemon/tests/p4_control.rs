//! P4 daemon tests: spec 037 tray job control — signed command
//! verification and execution, the operator ledger trail, and
//! the FIX-08 repair kill switch.
//!
//! Payload verdicts come from these tests' own output; the
//! engine under test is the real daemon over a real socket-free
//! in-process harness (same pattern as the P2/P3 suites).

use std::path::PathBuf;
use std::sync::Arc;

use syncyard_contract::{
    ControlAction, ControlCommand, HostSecretKey, JobRecord, OutcomeStatus, CONTRACT_VERSION,
};
use syncyard_daemon::dispatch::fake::FakeDispatcher;
use syncyard_daemon::offvm::{acquire_epoch, DirOffVmStore};
use syncyard_daemon::serve::Engine;
use syncyard_daemon::state::{new_queue_record, DaemonState, StateHome};
use syncyard_daemon::time::{iso_from_unix, now_unix};
use syncyard_daemon::vwrite::FsWriter;

const HOST: &str = "test-host";

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p4-ctl-{}-{tag}", std::process::id()));
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
    }
}

fn host_key() -> HostSecretKey {
    HostSecretKey::from_seed([42u8; 32])
}

fn engine_with_job(tag: &str) -> (Engine, PathBuf) {
    let root = scratch(tag);
    let home = StateHome::new(root.join("home"));
    let mut engine = engine_for(&home, &root.join("offvm"));
    engine
        .state
        .control
        .host_keys
        .insert(HOST.to_string(), host_key().public_key().to_hex());
    let rec = JobRecord {
        id: "job-1".to_string(),
        key: "k-job-1".to_string(),
        program: "ctl-test".to_string(),
        workflow: "run.yml".to_string(),
        lane: Some("quick".to_string()),
        cwd: None,
        expected_min: 5.0,
        priority: 3,
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
    };
    engine.state.put_job(new_queue_record(&rec));
    (engine, root)
}

static NONCE: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);

/// A signed command from the enrolled host, issued now.
fn command(action: ControlAction, job_id: &str) -> ControlCommand {
    let n = NONCE.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
    let now = now_unix();
    let mut cmd = ControlCommand {
        command_id: format!("cmd-t-{n}"),
        action,
        job_id: Some(job_id.to_string()),
        lane: None,
        host: None,
        new_priority: None,
        issuer_host: HOST.to_string(),
        issuer_identity: "syncyard-tray 0.4.0".to_string(),
        nonce: format!("nonce-{n}"),
        issued_at: iso_from_unix(now as u64),
        expires_at: iso_from_unix((now + 600) as u64),
        app_train: "0.4.0".to_string(),
        contract_version: CONTRACT_VERSION,
        signature: String::new(),
    };
    cmd.sign(&host_key());
    cmd
}

fn ledger_events(engine: &Engine) -> Vec<(String, serde_json::Value)> {
    engine
        .state
        .ledger_buffer
        .iter()
        .map(|e| {
            (
                e.event.clone(),
                serde_json::to_value(&e.fields).unwrap_or_default(),
            )
        })
        .collect()
}

// T037-02 command-schema
#[test]
fn t037_02_command_schema_and_expiry_bounds() {
    let cmd = command(ControlAction::Hold, "job-1");
    let v = serde_json::to_value(&cmd).unwrap();
    for field in [
        "command_id",
        "action",
        "job_id",
        "issuer_host",
        "issuer_identity",
        "nonce",
        "issued_at",
        "expires_at",
        "signature",
    ] {
        assert!(v.get(field).is_some(), "schema field {field} present");
    }
    assert_eq!(cmd.expiry_window_s(), Some(600));
    // A window past the 3600s maximum is refused, never executed.
    let (mut engine, _root) = engine_with_job("t02");
    let now = now_unix();
    let mut wide = command(ControlAction::Hold, "job-1");
    wide.expires_at = iso_from_unix((now + 7200) as u64);
    wide.sign(&host_key());
    let outcome = engine.execute_control_command(&wide);
    assert_eq!(outcome.status, OutcomeStatus::Refused);
    assert!(outcome.reason.unwrap().contains("exceeds"));
    assert!(engine.state.holds.is_empty());
}

// T037-03 key-bound-authentication
#[test]
fn t037_03_wrong_key_and_unenrolled_host_refused_file_alone_changes_nothing() {
    let (mut engine, _root) = engine_with_job("t03");
    // The file merely existing changes nothing: no execution was
    // invoked, so no outcome, no ledger line, no state change.
    let cmd = command(ControlAction::Cancel, "job-1");
    assert!(engine.state.control.outcome_for(&cmd.command_id).is_none());
    assert!(engine.state.ledger_buffer.is_empty());
    // A signature from a key that is not the enrolled one.
    let mut forged = cmd.clone();
    forged.signature = String::new();
    forged.sign(&HostSecretKey::from_seed([1u8; 32]));
    let outcome = engine.execute_control_command(&forged);
    assert_eq!(outcome.status, OutcomeStatus::Refused);
    assert!(outcome.reason.unwrap().contains("signature"));
    // A host with no enrolled key at all.
    let mut alien = command(ControlAction::Cancel, "job-1");
    alien.issuer_host = "someone-elses-laptop".to_string();
    alien.sign(&host_key());
    let outcome = engine.execute_control_command(&alien);
    assert_eq!(outcome.status, OutcomeStatus::Refused);
    assert!(outcome.reason.unwrap().contains("unknown issuer host"));
    // The job was never touched.
    assert_eq!(
        engine.state.jobs["job-1"].state,
        syncyard_contract::JobState::Submitted
    );
}

// T037-04 nonce-single-use
#[test]
fn t037_04_nonce_replay_refused_and_ledgered() {
    let (mut engine, _root) = engine_with_job("t04");
    let cmd = command(ControlAction::Hold, "job-1");
    let first = engine.execute_control_command(&cmd);
    assert_eq!(first.status, OutcomeStatus::Executed);
    assert!(engine.state.holds.contains("job-1"));
    // The very same file delivered a second time must not act twice.
    let second = engine.execute_control_command(&cmd);
    assert_eq!(second.status, OutcomeStatus::Refused);
    assert!(second.reason.unwrap().contains("nonce replay"));
    let refusals: Vec<_> = ledger_events(&engine)
        .into_iter()
        .filter(|(e, f)| {
            e == "operator_command_refused"
                && f.get("command_id").and_then(|v| v.as_str()) == Some(cmd.command_id.as_str())
        })
        .collect();
    assert_eq!(refusals.len(), 1);
}

// T037-05 expiry-enforcement (daemon half: expired files refused)
#[test]
fn t037_05_expired_command_refused_never_executed() {
    let (mut engine, _root) = engine_with_job("t05");
    let now = now_unix();
    let mut old = command(ControlAction::Cancel, "job-1");
    old.issued_at = iso_from_unix((now - 7200) as u64);
    old.expires_at = iso_from_unix((now - 3600) as u64);
    old.sign(&host_key());
    let outcome = engine.execute_control_command(&old);
    assert_eq!(outcome.status, OutcomeStatus::Refused);
    assert!(outcome.reason.unwrap().contains("expired"));
    assert_eq!(
        engine.state.jobs["job-1"].state,
        syncyard_contract::JobState::Submitted
    );
}

// T037-06 every-act-ledgered
#[test]
fn t037_06_acts_and_refusals_ledgered_as_operator_events() {
    let (mut engine, _root) = engine_with_job("t06");
    let hold = command(ControlAction::Hold, "job-1");
    engine.execute_control_command(&hold);
    let release = command(ControlAction::ReleaseHold, "job-1");
    engine.execute_control_command(&release);
    let bad = command(ControlAction::ReleaseHold, "job-1"); // not held now
    let refused = engine.execute_control_command(&bad);
    assert_eq!(refused.status, OutcomeStatus::Refused);
    let events = ledger_events(&engine);
    let hold_ev = events
        .iter()
        .find(|(e, _)| e == "operator_hold")
        .expect("hold ledgered");
    assert_eq!(
        hold_ev.1.get("command_id").and_then(|v| v.as_str()),
        Some(hold.command_id.as_str())
    );
    assert_eq!(
        hold_ev.1.get("issuer_host").and_then(|v| v.as_str()),
        Some(HOST)
    );
    assert_eq!(
        hold_ev.1.get("action").and_then(|v| v.as_str()),
        Some("hold")
    );
    assert!(events.iter().any(|(e, _)| e == "operator_hold_released"));
    let refusal = events
        .iter()
        .find(|(e, _)| e == "operator_command_refused")
        .expect("refusal ledgered");
    assert!(refusal
        .1
        .get("reason")
        .and_then(|v| v.as_str())
        .unwrap()
        .contains("not held"));
}

// T037-09 cancel-via-gatekeeper-path
#[test]
fn t037_09_command_cancel_uses_the_gatekeeper_cancel_path() {
    let (mut engine, _root) = engine_with_job("t09");
    let cmd = command(ControlAction::Cancel, "job-1");
    let outcome = engine.execute_control_command(&cmd);
    assert_eq!(outcome.status, OutcomeStatus::Executed);
    assert_eq!(outcome.ledger_event.as_deref(), Some("operator_cancel"));
    assert_eq!(
        engine.state.jobs["job-1"].state,
        syncyard_contract::JobState::Cancelled
    );
    // Same event name the cancel verb writes — one cancel path.
    let cancels: Vec<_> = ledger_events(&engine)
        .into_iter()
        .filter(|(e, _)| e == "operator_cancel")
        .collect();
    assert_eq!(cancels.len(), 1);
    assert_eq!(
        cancels[0].1.get("command_id").and_then(|v| v.as_str()),
        Some(cmd.command_id.as_str())
    );
    // Terminal jobs refuse further cancels by command, too.
    let again = command(ControlAction::Cancel, "job-1");
    let outcome = engine.execute_control_command(&again);
    assert_eq!(outcome.status, OutcomeStatus::Refused);
    assert!(outcome.reason.unwrap().contains("terminal"));
}

// T037-10 repair-kill-switch (daemon half)
#[test]
fn t037_10_kill_switch_halts_self_healing_not_execution_and_persists() {
    let root = scratch("t10");
    let home = StateHome::new(root.join("home"));
    let mut engine = engine_for(&home, &root.join("offvm"));
    engine
        .state
        .control
        .host_keys
        .insert(HOST.to_string(), host_key().public_key().to_hex());
    let rec = JobRecord {
        id: "job-ks".to_string(),
        key: "k-job-ks".to_string(),
        program: "ctl-test".to_string(),
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
        venue: "agent".to_string(),
        script_path: Some("/tmp/s.ps1".to_string()),
        script_sha256: Some("ab".to_string()),
        grab_path: None,
        created_at: "2026-11-08T00:00:00Z".to_string(),
    };
    engine.state.put_job(new_queue_record(&rec));
    assert!(engine.set_kill_switch(true, "test"));
    // Job execution is NOT halted: the agent venue still assigns.
    let resp = engine.handle(&serde_json::json!({"verb": "agent_poll", "venue": "agent"}));
    assert_eq!(resp["ok"], serde_json::Value::Bool(true));
    assert!(resp.get("assignment").is_some());
    // The switch survives a flush + reload (it is a record).
    let _ = engine.state.flush(&FsWriter::default());
    let (reloaded, _gaps) = DaemonState::load(home.clone());
    assert!(reloaded.control.kill_switch);
    // Snapshot carries it for the app (FR-037-10 display half).
    assert_eq!(
        engine.state.snapshot()["kill_switch"],
        serde_json::Value::Bool(true)
    );
    // Prime a born-denied episode (flush failures, then a
    // sampler clear window). While the switch is set the cure's
    // decision is refused WITHOUT being spent...
    engine.state.cure.note_flush_failure();
    engine.state.cure.note_clear_window();
    assert!(!engine.state.repair_restart_decision(true));
    // ...and only an explicit clear act clears the switch,
    // after which the same episode still earns its restart.
    engine.set_kill_switch(false, "test");
    assert!(!engine.state.control.kill_switch);
    assert!(engine.state.repair_restart_decision(true));
    // The set event rode an earlier flush to the ledger file;
    // the clear event is still buffered. Both must exist.
    let mut events: Vec<String> = engine
        .state
        .ledger_buffer
        .iter()
        .map(|e| e.event.clone())
        .collect();
    if let Ok(text) = std::fs::read_to_string(home.ledger_path()) {
        for line in text.lines() {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                if let Some(e) = v.get("event").and_then(|x| x.as_str()) {
                    events.push(e.to_string());
                }
            }
        }
    }
    assert!(events.contains(&"operator_kill_switch_set".to_string()));
    assert!(events.contains(&"operator_kill_switch_cleared".to_string()));
}

// T037-11 mixed-version-refusal (UPD-08)
#[test]
fn t037_11_illegal_version_combination_refused_naming_required_version() {
    let (mut engine, _root) = engine_with_job("t11");
    let mut future = command(ControlAction::Hold, "job-1");
    future.contract_version = CONTRACT_VERSION + 41;
    future.app_train = "9.9.9".to_string();
    future.sign(&host_key());
    let outcome = engine.execute_control_command(&future);
    assert_eq!(outcome.status, OutcomeStatus::Refused);
    let reason = outcome.reason.unwrap();
    assert!(
        reason.contains(&format!("requires an app on contract {CONTRACT_VERSION}")),
        "refusal names the required version: {reason}"
    );
    // State is untouched: never corrupt state over a version row.
    assert!(engine.state.holds.is_empty());
    assert_eq!(
        engine.state.jobs["job-1"].state,
        syncyard_contract::JobState::Submitted
    );
}

// Reprioritize + lift execute through the normal paths.
#[test]
fn control_reprioritize_and_lift_execute() {
    let (mut engine, _root) = engine_with_job("tacts");
    let mut rp = command(ControlAction::Reprioritize, "job-1");
    rp.new_priority = Some(-50);
    rp.sign(&host_key());
    let outcome = engine.execute_control_command(&rp);
    assert_eq!(outcome.status, OutcomeStatus::Executed);
    assert_eq!(engine.state.jobs["job-1"].priority, -50);
    assert_eq!(engine.state.jobs["job-1"].priority_source, "human");
    let lift = command(ControlAction::Lift, "job-1");
    let outcome = engine.execute_control_command(&lift);
    assert_eq!(outcome.status, OutcomeStatus::Executed);
    assert_eq!(outcome.ledger_event.as_deref(), Some("operator_lift"));
    assert_eq!(
        engine.state.jobs["job-1"].state,
        syncyard_contract::JobState::Dispatched
    );
    assert!(engine.state.jobs["job-1"].run_id.is_some());
    // A held job's lift refuses with the hold named (dispatch_one
    // is the gate; the command layer surfaces its reason).
    let (mut engine2, _r2) = engine_with_job("tacts2");
    let hold = command(ControlAction::Hold, "job-1");
    engine2.execute_control_command(&hold);
    let lift = command(ControlAction::Lift, "job-1");
    let outcome = engine2.execute_control_command(&lift);
    assert_eq!(outcome.status, OutcomeStatus::Refused);
    assert!(outcome.reason.unwrap().contains("held"));
}
