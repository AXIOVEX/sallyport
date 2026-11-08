//! P7 CLI tests: the schema-drift gate (spec 036 T036-16)
//! against a committed snapshot, binary-level coverage of
//! the entry point, and the run_cli error branches.

use std::path::PathBuf;
use std::process::Command;

use syncyard_cli::run_cli;
use syncyard_daemon::state::StateHome;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p7-cli-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn home(tag: &str) -> (StateHome, PathBuf) {
    let root = scratch(tag);
    let offvm = root.join("offvm");
    (StateHome::new(root.join("home")), offvm)
}

// T036-16 schema-drift-fails-ci: the CLI's schema output is
// compared, field for field, against the committed snapshot
// for this train (tests/fixtures/schema-v0.4.0.json — the
// P5 capture, verified identical to the current output when
// pinned). Any drift — a verb added, a note changed, the
// contract version bumped — fails this test with the first
// differing path named, so the change is a deliberate,
// reviewed act (re-pin the fixture in the same commit).
#[test]
fn t036_16_schema_drift_fails() {
    let (home, offvm) = home("schema");
    let out = run_cli(&["schema".to_string()], &home, &offvm);
    assert_eq!(out.code, 0, "{}", out.stdout);
    let now: serde_json::Value = serde_json::from_str(&out.stdout).unwrap();
    let fixture_text = std::fs::read_to_string(
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join("schema-v0.4.0.json"),
    )
    .unwrap();
    let pinned: serde_json::Value = serde_json::from_str(&fixture_text).unwrap();
    fn first_diff(a: &serde_json::Value, b: &serde_json::Value, path: &str) -> Option<String> {
        match (a, b) {
            (serde_json::Value::Object(x), serde_json::Value::Object(y)) => {
                for k in x.keys().chain(y.keys()) {
                    if x.get(k) != y.get(k) {
                        return first_diff(
                            x.get(k).unwrap_or(&serde_json::Value::Null),
                            y.get(k).unwrap_or(&serde_json::Value::Null),
                            &format!("{path}.{k}"),
                        );
                    }
                }
                None
            }
            (serde_json::Value::Array(x), serde_json::Value::Array(y)) => {
                if x.len() != y.len() {
                    return Some(format!("{path}: array len {} vs {}", x.len(), y.len()));
                }
                for (i, (u, v)) in x.iter().zip(y.iter()).enumerate() {
                    if let Some(d) = first_diff(u, v, &format!("{path}[{i}]")) {
                        return Some(d);
                    }
                }
                None
            }
            _ => Some(format!("{path}: {a} vs {b}")),
        }
    }
    if let Some(d) = first_diff(&now, &pinned, "$") {
        panic!("schema drift at {d} — if deliberate, re-pin tests/fixtures/schema-v0.4.0.json in this commit");
    }
}

// Binary-level entry behavior: version and schema need no
// state home; a stateful verb with no home is a usage error
// (exit 2, by design — no compiled-in default home).
#[test]
fn cli_binary_home_rules() {
    let bin = env!("CARGO_BIN_EXE_syncyard-cli");
    let out = Command::new(bin).arg("version").output().unwrap();
    assert_eq!(out.status.code(), Some(0));
    let v: serde_json::Value =
        serde_json::from_str(&String::from_utf8_lossy(&out.stdout)).unwrap();
    assert_eq!(v["version"], serde_json::Value::String("0.4.0".into()));

    let out = Command::new(bin)
        .arg("status")
        .env_remove("SYNCYARD_HOME")
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(2));

    let root = scratch("bin-home");
    let out = Command::new(bin)
        .args(["--home", root.to_str().unwrap(), "schema"])
        .output()
        .unwrap();
    assert_eq!(out.status.code(), Some(0));
    let _ = std::fs::remove_dir_all(&root);
}

// run_cli error branches answer with the error JSON shape
// and a non-zero code — never a panic, never a fake success.
#[test]
fn run_cli_error_branches() {
    let (home, offvm) = home("errs");
    let out = run_cli(&[], &home, &offvm);
    assert_ne!(out.code, 0);
    let out = run_cli(&["no-such-verb".to_string()], &home, &offvm);
    assert_ne!(out.code, 0, "unknown verb: {}", out.stdout);
    let v: serde_json::Value = serde_json::from_str(&out.stdout).unwrap();
    assert_eq!(v["ok"], serde_json::Value::Bool(false));
    let out = run_cli(&["submit".to_string()], &home, &offvm);
    assert_ne!(out.code, 0, "submit without flags: {}", out.stdout);
    let out = run_cli(&["migrate-history".to_string()], &home, &offvm);
    assert_ne!(out.code, 0, "migrate-history without --from: {}", out.stdout);
    let out = run_cli(
        &["version".to_string()],
        &home,
        &offvm,
    );
    assert_eq!(out.code, 0);
    let v: serde_json::Value = serde_json::from_str(&out.stdout).unwrap();
    assert_eq!(v["contract_version"], serde_json::Value::Number(1.into()));
}
