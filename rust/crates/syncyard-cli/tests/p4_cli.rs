//! P4 CLI tests: the FIX-08 kill-switch verb surface (spec 037
//! FR-037-10, CLI half) and the verb's presence in the schema
//! P5 generates from.

use std::path::PathBuf;

use syncyard_cli::{run_cli, schema};
use syncyard_daemon::state::StateHome;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p4-cli-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn argv(parts: &[&str]) -> Vec<String> {
    parts.iter().map(|s| s.to_string()).collect()
}

// T037-10 repair-kill-switch (CLI half): the switch is settable
// and readable from the CLI, the act is ledgered daemon-side,
// and a bad argument is a usage error that changes nothing.
#[test]
fn t037_10_cli_kill_switch_verb() {
    let root = scratch("t10");
    let home = StateHome::new(root.join("gk-home"));
    let offvm = root.join("offvm");
    std::fs::create_dir_all(home.queue_dir()).unwrap();

    // The verb is on the advertised surface.
    let verbs: Vec<String> = schema()["verbs"]
        .as_array()
        .unwrap()
        .iter()
        .map(|v| v.as_str().unwrap().to_string())
        .collect();
    assert!(verbs.iter().any(|v| v == "kill-switch"), "verbs: {verbs:?}");

    // Read: off by default (file-fallback client; no daemon
    // needs to be running for the switch to be a record).
    let out = run_cli(&argv(&["kill-switch"]), &home, &offvm);
    assert_eq!(out.code, 0, "{}", out.stdout);
    assert!(
        out.stdout.contains("\"kill_switch\": false"),
        "{}",
        out.stdout
    );

    // Set it.
    let out = run_cli(&argv(&["kill-switch", "on"]), &home, &offvm);
    assert_eq!(out.code, 0, "{}", out.stdout);
    assert!(
        out.stdout.contains("\"kill_switch\": true"),
        "{}",
        out.stdout
    );
    let out = run_cli(&argv(&["kill-switch"]), &home, &offvm);
    assert!(
        out.stdout.contains("\"kill_switch\": true"),
        "{}",
        out.stdout
    );

    // The act was ledgered by the daemon side with its source.
    let ledger = std::fs::read_to_string(home.ledger_path()).unwrap();
    assert!(ledger.contains("operator_kill_switch_set"), "{ledger}");
    assert!(ledger.contains("\"source\":\"cli\""), "{ledger}");

    // A bad argument is refused as usage and changes nothing.
    let out = run_cli(&argv(&["kill-switch", "maybe"]), &home, &offvm);
    assert_eq!(out.code, 1, "{}", out.stdout);
    assert!(out.stdout.contains("kill-switch takes"), "{}", out.stdout);
    let out = run_cli(&argv(&["kill-switch"]), &home, &offvm);
    assert!(
        out.stdout.contains("\"kill_switch\": true"),
        "{}",
        out.stdout
    );

    // Clear it.
    let out = run_cli(&argv(&["kill-switch", "off"]), &home, &offvm);
    assert_eq!(out.code, 0, "{}", out.stdout);
    let ledger = std::fs::read_to_string(home.ledger_path()).unwrap();
    assert!(ledger.contains("operator_kill_switch_cleared"), "{ledger}");
}
