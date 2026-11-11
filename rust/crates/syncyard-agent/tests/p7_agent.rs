//! P7 agent tests: the macOS native-venue verdict harvest
//! (spec 039 FR-MAC-03a) on POSIX shells, plus binary-level
//! coverage of the agent entry point. Payloads are real
//! shell scripts executed by the real runner; all state
//! lives in scratch dirs.

#[cfg(unix)]
use std::path::Path;
use std::path::PathBuf;
use std::process::Command;
use std::time::Duration;

use syncyard_agent::fake::FakeChannel;
use syncyard_agent::runner::{run_assignment, AgentConfig};
use syncyard_contract::Assignment;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p7-agent-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

#[cfg(unix)]
fn payload(dir: &Path, name: &str, shebang: &str, body: &str) -> String {
    let path = dir.join(name);
    std::fs::write(&path, format!("#!{shebang}\n{body}\n")).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    path.display().to_string()
}

fn config(work_root: PathBuf) -> AgentConfig {
    let mut cfg = AgentConfig::new(work_root, "agent:air-inference".to_string());
    cfg.forward_interval = Duration::from_millis(5);
    cfg
}

// T039-05 native-job-verdict-harvest (machinery half): a
// native POSIX payload — zsh, the macOS default shell,
// and bash — runs through the real runner, and the payload's
// OWN exit code is what the completion report carries as
// the verdict: 41 in, failure out; 0 in, success out. The
// live leg (gatekeeper dispatch to air-inference itself) is
// an owner-gated field act, recorded in PROGRESS.md.
// Unix-only: the payloads under test are POSIX shells by
// name (/bin/zsh, /bin/bash, /bin/sh below); Windows has no
// such interpreters and no shebang execution, so the spawns
// fail there before the harvest semantics engage.
#[cfg(unix)]
#[test]
fn t039_05_native_job_verdict_harvest() {
    let root = scratch("t05");
    let cfg = config(root.join("work"));
    let mut ran = 0;
    for shell in ["/bin/zsh", "/bin/bash"] {
        if !Path::new(shell).exists() {
            continue;
        }
        ran += 1;
        let script = payload(
            &root,
            &format!("p-{ran}.sh"),
            shell,
            "echo harvesting\nexit 41",
        );
        let mut fake = FakeChannel::default();
        let asg = Assignment {
            job_id: format!("mac-job-{ran}"),
            run_id: 7,
            command: vec![script],
            cwd: None,
        };
        let outcome = run_assignment(&mut fake, &asg, &cfg).unwrap();
        assert_eq!(
            outcome.report.payload_exit_code,
            Some(41),
            "{shell}: the payload's own exit code is the verdict source"
        );
        assert!(outcome.report.payload_started);
        assert_eq!(outcome.report.derived_conclusion(), "failure");
        assert!(outcome.completion_delivered);
    }
    assert!(ran >= 1, "at least one POSIX shell must be present");
    // And the zero case, same path: success is the payload's
    // own 0, not the machinery's.
    let script = payload(&root, "ok.sh", "/bin/sh", "exit 0");
    let mut fake = FakeChannel::default();
    let asg = Assignment {
        job_id: "mac-job-ok".into(),
        run_id: 8,
        command: vec![script],
        cwd: None,
    };
    let outcome = run_assignment(&mut fake, &asg, &cfg).unwrap();
    assert_eq!(outcome.report.payload_exit_code, Some(0));
    assert_eq!(outcome.report.derived_conclusion(), "success");
    let _ = std::fs::remove_dir_all(&root);
}

// Runner honesty edges (coverage with substance): a payload
// that cannot start reports not-started with NO exit code —
// derived conclusion "error", never a manufactured success;
// an empty command is the same.
#[test]
fn runner_reports_unstartable_payload_as_error_never_success() {
    let root = scratch("edges");
    let cfg = config(root.join("work"));
    let mut fake = FakeChannel::default();
    let asg = Assignment {
        job_id: "ghost-job".into(),
        run_id: 9,
        command: vec![root.join("does-not-exist.sh").display().to_string()],
        cwd: None,
    };
    let outcome = run_assignment(&mut fake, &asg, &cfg).unwrap();
    assert!(!outcome.report.payload_started);
    assert_eq!(outcome.report.payload_exit_code, None);
    assert_eq!(outcome.report.derived_conclusion(), "error");

    let mut fake2 = FakeChannel::default();
    let asg2 = Assignment {
        job_id: "empty-job".into(),
        run_id: 10,
        command: vec![],
        cwd: None,
    };
    let outcome2 = run_assignment(&mut fake2, &asg2, &cfg).unwrap();
    assert!(!outcome2.report.payload_started);
    assert_eq!(outcome2.report.derived_conclusion(), "error");
    let _ = std::fs::remove_dir_all(&root);
}

// Binary-level: no --daemon-home is a usage error (exit 2);
// --once against a home with no daemon socket runs one
// degraded cycle and exits 0 (transport trouble is a
// degradation, not a death — main's documented behavior).
// The identity is pinned explicitly: startup without any
// identity fails closed (see the test below), and this test
// exercises the socket cycle, not identity resolution.
#[test]
fn agent_binary_usage_and_dead_socket_cycle() {
    let bin = env!("CARGO_BIN_EXE_syncyard-agent");
    let out = Command::new(bin).output().unwrap();
    assert_eq!(out.status.code(), Some(2));
    let root = scratch("bin");
    let home = root.join("no-daemon-here");
    std::fs::create_dir_all(&home).unwrap();
    let out = Command::new(bin)
        .args([
            "--daemon-home",
            home.to_str().unwrap(),
            "--install-path",
            root.join("install").to_str().unwrap(),
            "--agent-id",
            "agent-bin",
            "--once",
        ])
        .output()
        .unwrap();
    assert_eq!(
        out.status.code(),
        Some(0),
        "stderr: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let _ = std::fs::remove_dir_all(&root);
}

// Agent identity fails closed: with no --agent-id and no usable
// HOSTNAME (the launchd/supervisor shape), startup is a clear
// error — never the placeholder identity "host".
#[test]
fn agent_binary_without_identity_fails_closed() {
    let bin = env!("CARGO_BIN_EXE_syncyard-agent");
    let root = scratch("no-identity");
    let home = root.join("daemon-home");
    std::fs::create_dir_all(&home).unwrap();
    let out = Command::new(bin)
        .args([
            "--daemon-home",
            home.to_str().unwrap(),
            "--install-path",
            root.join("install").to_str().unwrap(),
            "--once",
        ])
        .env_remove("HOSTNAME")
        .output()
        .unwrap();
    assert_eq!(
        out.status.code(),
        Some(2),
        "no identity must be a usage error, stderr: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stderr = String::from_utf8_lossy(&out.stderr);
    assert!(
        stderr.contains("--agent-id") && stderr.contains("HOSTNAME"),
        "the error must name both identity sources: {stderr}"
    );
    let _ = std::fs::remove_dir_all(&root);
}
