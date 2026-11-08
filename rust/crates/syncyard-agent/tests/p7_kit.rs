//! The conformance-kit test (spec 034 section 7): the kit's
//! payloads run through the REAL runner, and their harvested
//! progress files must parse to the identical event stream
//! in every language.

use std::path::{Path, PathBuf};
use std::time::Duration;

use syncyard_agent::fake::FakeChannel;
use syncyard_agent::runner::{progress_path, run_assignment, AgentConfig};
use syncyard_contract::progress::{parse_lines, ProgressEvent};
use syncyard_contract::Assignment;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p7-kit-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn kit_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("conformance-kit")
}

fn expected_stream() -> Vec<ProgressEvent> {
    vec![
        ProgressEvent::Started,
        ProgressEvent::Marker {
            text: "kit-start".into(),
        },
        ProgressEvent::Progress {
            k: 1,
            n: 2,
            label: "first-half".into(),
        },
        ProgressEvent::Heartbeat,
        ProgressEvent::Progress {
            k: 2,
            n: 2,
            label: "second-half".into(),
        },
        ProgressEvent::Completed {
            verdict_word: "done".into(),
        },
    ]
}

fn run_kit_payload(cfg: &AgentConfig, job_id: &str, command: Vec<String>) -> Vec<ProgressEvent> {
    let mut fake = FakeChannel::default();
    let asg = Assignment {
        job_id: job_id.to_string(),
        run_id: 11,
        command,
        cwd: None,
    };
    let outcome = run_assignment(&mut fake, &asg, cfg).unwrap();
    assert_eq!(
        outcome.report.payload_exit_code,
        Some(0),
        "the kit payload's own exit code is the verdict"
    );
    let pfile = progress_path(&cfg.work_root, job_id);
    let contents = std::fs::read_to_string(&pfile).unwrap();
    parse_lines(&contents)
}

// T034-11 conformance-kit-three-languages: bash and Python
// payloads execute here and harvest IDENTICALLY; the
// PowerShell payload is present with the correct shape and
// also executes when a pwsh is available (its Windows field
// leg is recorded in PROGRESS.md).
#[test]
fn t034_11_conformance_kit_three_languages() {
    let root = scratch("kit");
    let mut cfg = AgentConfig::new(root.join("work"), "agent:kit".to_string());
    cfg.forward_interval = Duration::from_millis(5);
    let kit = kit_dir();

    let bash = run_kit_payload(
        &cfg,
        "kit-bash",
        vec![
            "/bin/bash".to_string(),
            kit.join("payload.sh").to_string_lossy().to_string(),
        ],
    );
    assert_eq!(bash, expected_stream(), "bash event stream");

    let python = run_kit_payload(
        &cfg,
        "kit-python",
        vec![
            "python3".to_string(),
            kit.join("payload.py").to_string_lossy().to_string(),
        ],
    );
    assert_eq!(python, expected_stream(), "python event stream");
    assert_eq!(bash, python, "harvested identically across languages");

    // PowerShell: the payload exists and carries every
    // grammar line literally (shape), and executes too when
    // a PowerShell is on PATH.
    let ps1 = std::fs::read_to_string(kit.join("payload.ps1")).unwrap();
    for needle in [
        "STARTED",
        "MARKER kit-start",
        "PROGRESS 1/2 first-half",
        "HEARTBEAT",
        "PROGRESS 2/2 second-half",
        "COMPLETED done",
        "SYNCYARD_PROGRESS_FILE",
    ] {
        assert!(ps1.contains(needle), "payload.ps1 missing: {needle}");
    }
    if Path::new("/usr/bin/pwsh").exists() || which_pwsh() {
        let ps = run_kit_payload(
            &cfg,
            "kit-ps",
            vec![
                "pwsh".to_string(),
                "-NoProfile".to_string(),
                "-File".to_string(),
                kit.join("payload.ps1").to_string_lossy().to_string(),
            ],
        );
        assert_eq!(ps, expected_stream(), "powershell event stream");
    }
    let _ = std::fs::remove_dir_all(&root);
}

fn which_pwsh() -> bool {
    std::process::Command::new("which")
        .arg("pwsh")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}
