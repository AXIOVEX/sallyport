//! P7 daemon binary tests: the entry point's usage gates
//! and a full boot-serve-shutdown cycle against scratch
//! state (boot reconciliation runs, the BOOT line announces
//! the recovered state, the socket answers, shutdown exits
//! cleanly). No persistent service: the child is shut down
//! through its own verb before the test ends.

use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::Duration;

use syncyard_daemon::serve::request;
use syncyard_daemon::state::StateHome;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p7-dbin-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[test]
fn daemon_binary_usage_gates() {
    let bin = env!("CARGO_BIN_EXE_syncyard-daemon");
    // No subcommand.
    let out = Command::new(bin).output().unwrap();
    assert_eq!(out.status.code(), Some(2));
    // serve without --home.
    let out = Command::new(bin).arg("serve").output().unwrap();
    assert_eq!(out.status.code(), Some(2));
    // serve with a home but without the no-transport
    // acknowledgement: refuses by design in this build.
    let root = scratch("gates");
    let out = Command::new(bin)
        .args(["serve", "--home", root.to_str().unwrap()])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("no dispatch transport"),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    // Unknown argument.
    let out = Command::new(bin)
        .args(["serve", "--home", root.to_str().unwrap(), "--bogus"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn daemon_binary_boots_serves_and_shuts_down() {
    let bin = env!("CARGO_BIN_EXE_syncyard-daemon");
    let root = scratch("boot");
    let home_dir = root.join("home");
    let offvm = root.join("offvm");
    let child = Command::new(bin)
        .args([
            "serve",
            "--home",
            home_dir.to_str().unwrap(),
            "--offvm",
            offvm.to_str().unwrap(),
            "--allow-no-transport",
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let home = StateHome::new(home_dir.clone());
    let mut pinged = false;
    for _ in 0..100 {
        if let Ok(resp) = request(&home, &serde_json::json!({"verb": "ping"})) {
            if resp["ok"] == serde_json::Value::Bool(true) {
                pinged = true;
                break;
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(pinged, "the booted daemon must answer ping");
    let resp = request(&home, &serde_json::json!({"verb": "shutdown"})).unwrap();
    assert_eq!(resp["ok"], serde_json::Value::Bool(true));
    let out = child.wait_with_output().unwrap();
    assert!(
        out.status.success(),
        "clean shutdown, got {:?}: {}",
        out.status.code(),
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    assert!(
        stdout.contains("BOOT recovered_submits=[]"),
        "the boot line announces the recovered state (ERR-09):\n{stdout}"
    );
    let _ = std::fs::remove_dir_all(&root);
}

#[test]
fn daemon_binary_second_instance_refused_by_lock() {
    let bin = env!("CARGO_BIN_EXE_syncyard-daemon");
    let root = scratch("lock");
    let home_dir = root.join("home");
    let offvm = root.join("offvm");
    let child = Command::new(bin)
        .args([
            "serve",
            "--home",
            home_dir.to_str().unwrap(),
            "--offvm",
            offvm.to_str().unwrap(),
            "--allow-no-transport",
        ])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let home = StateHome::new(home_dir.clone());
    let mut up = false;
    for _ in 0..100 {
        if request(&home, &serde_json::json!({"verb": "ping"})).is_ok() {
            up = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(up, "first instance must be serving");
    // A second serve on the same home is refused by the
    // state lock — two writers never coexist.
    let out = Command::new(bin)
        .args([
            "serve",
            "--home",
            home_dir.to_str().unwrap(),
            "--offvm",
            offvm.to_str().unwrap(),
            "--allow-no-transport",
        ])
        .output()
        .unwrap();
    assert!(
        !out.status.success(),
        "second instance must refuse, got {:?}",
        out.status.code()
    );
    let stderr = String::from_utf8_lossy(&out.stderr).to_lowercase();
    assert!(
        stderr.contains("lock") || stderr.contains("held") || stderr.contains("another"),
        "the refusal names the lock: {stderr}"
    );
    let resp = request(&home, &serde_json::json!({"verb": "shutdown"})).unwrap();
    assert_eq!(resp["ok"], serde_json::Value::Bool(true));
    let out = child.wait_with_output().unwrap();
    assert!(out.status.success());
    let _ = std::fs::remove_dir_all(&root);
}
