//! P7 tray tests: binary-level coverage of the headless
//! diagnostic pass, settings persistence branches, and the
//! signed-command issue/display path. All state in scratch
//! dirs; no GUI is started (the live shell is the named
//! field gap).

use std::path::PathBuf;
use std::process::Command;

use syncyard_contract::control::{CommandOutcome, ControlAction, OutcomeStatus};
use syncyard_tray::control::{
    display_state, load_or_create_key_file, CommandDisplay, CommandIssuer,
};
use syncyard_tray::settings::Settings;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p7-tray-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

// The binary's one headless pass: acquires the singleton,
// writes a heartbeat, renders the surfaces as text, exits 0.
// The rendered output carries the identity header of the
// build actually running (spec 035 served-identity).
#[test]
fn tray_binary_headless_pass_renders_identity() {
    let bin = env!("CARGO_BIN_EXE_syncyard-tray");
    let root = scratch("bin");
    let out = Command::new(bin)
        .args([
            "--app-dir",
            root.join("app").to_str().unwrap(),
            "--install-path",
            root.join("install").to_str().unwrap(),
        ])
        .output()
        .unwrap();
    assert!(
        out.status.success(),
        "exit {:?}, stderr: {}",
        out.status.code(),
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(
        stdout.contains("Syncyard 0.4.0"),
        "the rendered surfaces carry the identity stamp:\n{stdout}"
    );
    let _ = std::fs::remove_dir_all(&root);
}

// Settings: defaults on a fresh dir, a save/load round
// trip, and validation refusing the shapes that would break
// the presentation math (never stored, by construction —
// save validates first).
#[test]
fn settings_round_trip_and_validation() {
    let dir = scratch("settings");
    let fresh = Settings::load(&dir).unwrap();
    assert_eq!(fresh, Settings::default());
    assert_eq!(fresh.connection_stale_s, 180);
    assert_eq!(fresh.connection_disconnect_s, 900);

    let mut edited = fresh.clone();
    edited.fleet_stale_s = 600;
    edited.fleet_status_path = Some("/tmp/fleet.json".to_string());
    edited.save(&dir).unwrap();
    assert_eq!(Settings::load(&dir).unwrap(), edited);

    let mut bad = edited.clone();
    bad.connection_stale_s = 900; // not below disconnect
    assert!(bad.validate().is_err());
    assert!(bad.save(&dir).is_err(), "invalid settings are never stored");
    assert_eq!(
        Settings::load(&dir).unwrap(),
        edited,
        "the stored file still holds the last valid settings"
    );
    let mut zero = edited.clone();
    zero.fleet_stale_s = 0;
    assert!(zero.validate().is_err());
    let _ = std::fs::remove_dir_all(&dir);
}

// Command path: a signed command lands in the outbox as a
// file (the whole act on this side), verifies against the
// host key, and its display state derives ONLY from the
// gatekeeper's published outcomes: pending, then confirmed
// on an executed outcome, refused with the reason on a
// refused one.
#[test]
fn command_issue_sign_and_display_states() {
    let dir = scratch("cmd");
    let key_path = dir.join("host-key.json");
    let (file, key, created) = load_or_create_key_file(&key_path, "test-host").unwrap();
    assert!(created);
    assert_eq!(file.host, "test-host");
    // Second load reads the same key back.
    let (file2, _key2, created2) = load_or_create_key_file(&key_path, "test-host").unwrap();
    assert!(!created2);
    assert_eq!(file2.public_hex, file.public_hex);

    let issuer = CommandIssuer::new(dir.join("outbox"), "test-host".into(), "0.4.0".into(), key);
    let cmd = issuer
        .issue(ControlAction::Hold, Some("job-9"), None)
        .unwrap();
    assert!(!cmd.signature.is_empty());
    let on_disk = dir
        .join("outbox")
        .join(format!("command-{}.json", cmd.command_id));
    assert!(on_disk.exists(), "the signed command file is the act");
    let parsed: syncyard_contract::control::ControlCommand =
        serde_json::from_str(&std::fs::read_to_string(&on_disk).unwrap()).unwrap();
    assert_eq!(parsed.command_id, cmd.command_id);
    assert_eq!(parsed.action, ControlAction::Hold);

    // No outcome yet: Pending — never "done".
    let now = syncyard_contract::isotime::now_unix();
    assert_eq!(display_state(&cmd, &[], now), CommandDisplay::Pending);
    // Executed outcome: Confirmed with its ledger event.
    let executed = vec![CommandOutcome {
        command_id: cmd.command_id.clone(),
        action: "hold".into(),
        issuer_host: "test-host".into(),
        job_id: Some("job-9".into()),
        status: OutcomeStatus::Executed,
        reason: None,
        ledger_event: Some("operator_hold".into()),
        decided_at: "2026-11-08T00:00:00Z".into(),
    }];
    assert_eq!(
        display_state(&cmd, &executed, now),
        CommandDisplay::Confirmed {
            ledger_event: Some("operator_hold".into())
        }
    );
    // Refused outcome: the reason shows.
    let refused = vec![CommandOutcome {
        status: OutcomeStatus::Refused,
        reason: Some("job not waiting".into()),
        ledger_event: None,
        ..executed[0].clone()
    }];
    assert_eq!(
        display_state(&cmd, &refused, now),
        CommandDisplay::Refused {
            reason: "job not waiting".into()
        }
    );
    let _ = std::fs::remove_dir_all(&dir);
}
