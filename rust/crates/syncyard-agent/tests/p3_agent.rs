//! P3 agent integration tests (spec 034 agent side, spec 038
//! windowless construction rides in the crate's unit tests).
//!
//! Each test claims its named test case id where one applies
//! (spec form in the comment above it). Payloads are real shell
//! scripts executed by the real runner; all state lives in
//! scratch dirs under the system temp dir. The end-to-end test
//! serves the real daemon in-process — the live gatekeeper and
//! the fleet are never touched.

use std::path::{Path, PathBuf};
use std::time::Duration;

use syncyard_agent::agent::Agent;
use syncyard_agent::fake::{AckMode, FakeChannel};
use syncyard_agent::runner::{progress_path, run_assignment, AgentConfig};
use syncyard_agent::socket::SocketChannel;
use syncyard_contract::Assignment;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p3-agent-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

/// Write an executable shell payload and return its path.
fn payload(dir: &Path, name: &str, body: &str) -> String {
    let path = dir.join(name);
    std::fs::write(&path, format!("#!/bin/sh\n{body}\n")).unwrap();
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
    }
    path.display().to_string()
}

fn config(work_root: PathBuf) -> AgentConfig {
    let mut cfg = AgentConfig::new(work_root, "agent:test-host".to_string());
    cfg.forward_interval = Duration::from_millis(5);
    cfg
}

fn assignment(job_id: &str, run_id: u64, script: String) -> Assignment {
    Assignment {
        job_id: job_id.to_string(),
        run_id,
        command: vec![script],
        cwd: None,
    }
}

// T034-01 progress-env-set-on-all-venues (spec 034 FR-034-01),
// agent half: every payload is launched with SYNCYARD_PROGRESS_FILE
// set to a per-job path whose parent directory exists before the
// payload starts (the payload appends to the file as its first
// act — a missing directory would fail the run).
#[test]
fn t034_01_progress_env_set_on_all_venues() {
    let root = scratch("t01");
    let cfg = config(root.join("work"));
    let env_out = root.join("env.txt");
    let script = payload(
        &root,
        "payload.sh",
        &format!(
            "printf '%s' \"$SYNCYARD_PROGRESS_FILE\" > '{}'\n\
             printf 'STARTED\\nPROGRESS 1/2 units\\n' >> \"$SYNCYARD_PROGRESS_FILE\"\n\
             exit 0",
            env_out.display()
        ),
    );

    let mut fake = FakeChannel::default();
    let asg = assignment("job-1", 42, script.clone());
    let outcome = run_assignment(&mut fake, &asg, &cfg).unwrap();
    assert!(outcome.completion_delivered);
    assert_eq!(outcome.report.payload_exit_code, Some(0));

    // The env var named the per-job path, and the payload's
    // appends landed (parent pre-created by the agent).
    let seen = std::fs::read_to_string(&env_out).unwrap();
    let expected = progress_path(&cfg.work_root, "job-1");
    assert_eq!(seen, expected.display().to_string());
    assert!(expected.exists(), "payload progress writes landed");

    // Progress lines were forwarded as batches for this run.
    assert!(!fake.progress.is_empty());
    let lines: Vec<&str> = fake
        .progress
        .iter()
        .flat_map(|(b, _)| b.lines.iter().map(|s| s.as_str()))
        .collect();
    assert!(lines.contains(&"STARTED"), "lines: {lines:?}");
    assert!(lines.contains(&"PROGRESS 1/2 units"), "lines: {lines:?}");
    assert!(fake.progress.iter().all(|(b, _)| b.run_id == Some(42)));
    assert!(fake
        .progress
        .iter()
        .all(|(b, _)| b.source == "agent:test-host"));

    // A second job gets a different per-job path.
    let env_out2 = root.join("env2.txt");
    let script2 = payload(
        &root,
        "payload2.sh",
        &format!(
            "printf '%s' \"$SYNCYARD_PROGRESS_FILE\" > '{}'\nexit 0",
            env_out2.display()
        ),
    );
    let asg2 = assignment("job-2", 43, script2);
    run_assignment(&mut fake, &asg2, &cfg).unwrap();
    let seen2 = std::fs::read_to_string(&env_out2).unwrap();
    assert_eq!(
        seen2,
        progress_path(&cfg.work_root, "job-2").display().to_string()
    );
    assert_ne!(seen, seen2);
    let _ = std::fs::remove_dir_all(&root);
}

// T034-04 completed-line-is-not-a-verdict (spec 034 FR-034-03),
// agent half: a payload that writes COMPLETED PASS and then
// exits 1 produces a completion report whose derived conclusion
// is failure — the exit code governs, the line is forwarded as
// display data only.
#[test]
fn t034_04_completed_line_is_not_a_verdict_agent() {
    let root = scratch("t04");
    let cfg = config(root.join("work"));
    let script = payload(
        &root,
        "liar.sh",
        "printf 'COMPLETED PASS\\n' >> \"$SYNCYARD_PROGRESS_FILE\"\nexit 1",
    );
    let mut fake = FakeChannel::default();
    let outcome = run_assignment(&mut fake, &assignment("job-x", 7, script), &cfg).unwrap();

    assert_eq!(outcome.report.payload_exit_code, Some(1));
    assert!(outcome.report.payload_started);
    assert_eq!(outcome.report.derived_conclusion(), "failure");
    assert_eq!(fake.completions.len(), 1);
    assert_eq!(fake.completions[0].derived_conclusion(), "failure");
    // The COMPLETED line traveled as progress data.
    let lines: Vec<&str> = fake
        .progress
        .iter()
        .flat_map(|(b, _)| b.lines.iter().map(|s| s.as_str()))
        .collect();
    assert!(lines.contains(&"COMPLETED PASS"), "lines: {lines:?}");
    let _ = std::fs::remove_dir_all(&root);
}

// T034-09 transport-failure-isolated (spec 034 FR-034-07),
// agent half: with the progress transport down, the payload
// still runs to completion, the completion report still goes
// out, and the degradation is counted in the outcome — never
// hidden, never fatal, never verdict-relevant.
#[test]
fn t034_09_transport_failure_isolated_agent() {
    let root = scratch("t09");
    let cfg = config(root.join("work"));
    let script = payload(
        &root,
        "worker.sh",
        "printf 'STARTED\\nPROGRESS 1/1 units\\n' >> \"$SYNCYARD_PROGRESS_FILE\"\nexit 0",
    );
    let mut fake = FakeChannel {
        fail_progress: true,
        ..FakeChannel::default()
    };
    let outcome = run_assignment(&mut fake, &assignment("job-y", 9, script), &cfg).unwrap();

    assert!(outcome.progress_degraded, "degradation must be reported");
    assert_eq!(fake.progress.len(), 0, "no batch got through");
    assert_eq!(outcome.lines_undelivered, 2, "lines retained, then counted");
    assert!(outcome.completion_delivered);
    assert_eq!(fake.completions.len(), 1);
    assert_eq!(fake.completions[0].payload_exit_code, Some(0));
    assert_eq!(fake.completions[0].derived_conclusion(), "success");
    let _ = std::fs::remove_dir_all(&root);
}

// Throttled lines are retained, not dropped (spec 034 section 3
// cadence design): with the daemon answering Throttled to every
// batch, the run's lines are all still held at payload end and
// counted as undelivered, and completion is unaffected.
#[test]
fn throttled_progress_lines_are_retained() {
    let root = scratch("throttle");
    let cfg = config(root.join("work"));
    let script = payload(
        &root,
        "worker.sh",
        "printf 'STARTED\\nMARKER halfway\\n' >> \"$SYNCYARD_PROGRESS_FILE\"\nexit 0",
    );
    let mut fake = FakeChannel {
        ack_mode: Some(AckMode::Throttled),
        ..FakeChannel::default()
    };
    let outcome = run_assignment(&mut fake, &assignment("job-z", 11, script), &cfg).unwrap();
    assert!(outcome.batches_throttled >= 1);
    assert_eq!(outcome.batches_applied, 0);
    assert_eq!(outcome.lines_undelivered, 2);
    assert!(outcome.completion_delivered);
    let _ = std::fs::remove_dir_all(&root);
}

// A completion report the channel refuses at payload end is not
// dropped: the agent holds it and the next cycle's retry
// delivers it before any new work.
#[test]
fn undelivered_completion_is_retried_next_cycle() {
    let root = scratch("retry");
    let script = payload(&root, "ok.sh", "exit 0");
    let fake = FakeChannel {
        fail_completion_times: 1,
        assignments: std::collections::VecDeque::from([assignment("job-r", 21, script)]),
        ..FakeChannel::default()
    };
    let mut agent = Agent::new(
        root.join("install"),
        config(root.join("work")),
        Box::new(fake),
    )
    .unwrap();

    let first = agent.run_cycle().unwrap();
    assert!(first.run.is_some());
    assert_eq!(agent.pending_completions.len(), 1);
    // The fake is inside the agent; read its record through a
    // second cycle.
    let second = agent.run_cycle().unwrap();
    assert_eq!(second.retried_delivered, 1);
    assert!(second.run.is_none(), "no new assignment was queued");
    assert_eq!(agent.pending_completions.len(), 0);
    let _ = std::fs::remove_dir_all(&root);
}

// End to end against the real daemon (no named case; this is
// the P3 wiring proof for the daemon verbs the claimed cases
// ride on): submit -> agent poll -> real payload -> streamed
// progress -> completion with the verdict from the exit code.
#[test]
fn agent_end_to_end_against_real_daemon() {
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};
    use syncyard_daemon::dispatch::fake::FakeDispatcher;
    use syncyard_daemon::offvm::{acquire_epoch, DirOffVmStore};
    use syncyard_daemon::serve::{request, serve, Engine, ServeConfig};
    use syncyard_daemon::state::{DaemonState, StateHome};
    use syncyard_daemon::vwrite::FsWriter;

    let root = scratch("e2e");
    let home = StateHome::new(root.join("gk-home"));
    std::fs::create_dir_all(home.queue_dir()).unwrap();
    let store = Arc::new(DirOffVmStore::new(root.join("offvm")));
    let (state, _gaps) = DaemonState::load(home.clone());
    let token = acquire_epoch(store.as_ref(), "e2e-daemon").unwrap();
    let engine = Engine {
        state,
        store,
        dispatcher: Arc::new(FakeDispatcher::new()),
        token,
        writer: Arc::new(FsWriter::default()),
    };
    let engine = Arc::new(Mutex::new(engine));
    let shutdown = Arc::new(AtomicBool::new(false));
    let (home2, eng2, sd2) = (home.clone(), Arc::clone(&engine), Arc::clone(&shutdown));
    let thread = std::thread::spawn(move || {
        serve(
            eng2,
            &home2,
            ServeConfig {
                flush_interval: Duration::from_millis(50),
            },
            sd2,
        )
    });
    // Wait for the socket.
    let mut up = false;
    for _ in 0..60 {
        if let Ok(r) = request(&home, &serde_json::json!({"verb": "ping"})) {
            if r["ok"] == serde_json::Value::Bool(true) {
                up = true;
                break;
            }
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    assert!(up, "daemon never came up");

    // Submit a native-venue job whose payload exits 3.
    let script = payload(
        &root,
        "job.sh",
        "printf 'STARTED\\nPROGRESS 2/3 widgets\\n' >> \"$SYNCYARD_PROGRESS_FILE\"\nexit 3",
    );
    let job = syncyard_contract::JobRecord {
        id: "e2e-job".to_string(),
        key: "e2e-key".to_string(),
        program: "test".to_string(),
        workflow: "run.yml".to_string(),
        lane: Some("quick".to_string()),
        cwd: Some(root.display().to_string()),
        expected_min: 5.0,
        priority: 0,
        priority_source: "auto".to_string(),
        flags: vec![],
        maintenance: false,
        adopted: false,
        notify_chat: None,
        venue: "native".to_string(),
        script_path: Some(script),
        script_sha256: Some("ab12".to_string()),
        grab_path: None,
        created_at: "2026-11-08T00:00:00Z".to_string(),
    };
    let resp = request(
        &home,
        &serde_json::json!({"verb": "submit", "job": serde_json::to_value(&job).unwrap()}),
    )
    .unwrap();
    assert_eq!(resp["ok"], serde_json::Value::Bool(true));

    // The real agent, over the real socket channel.
    let channel = SocketChannel::new(home.root().to_path_buf(), "native", "e2e-host");
    let mut cfg = AgentConfig::new(root.join("agent-work"), channel.source_id());
    cfg.forward_interval = Duration::from_millis(5);
    let mut agent = Agent::new(root.join("install"), cfg, Box::new(channel)).unwrap();
    let cycle = agent.run_cycle().unwrap();
    let run = cycle.run.expect("the agent ran the assignment");
    assert_eq!(run.report.payload_exit_code, Some(3));
    assert!(run.completion_delivered);

    // The daemon's record: verdict from the exit code (failure),
    // and the streamed progress is on the status surface.
    let status = request(
        &home,
        &serde_json::json!({"verb": "status", "job_id": "e2e-job"}),
    )
    .unwrap();
    assert_eq!(status["state"], serde_json::json!("completed"));
    assert_eq!(status["run_conclusion"], serde_json::json!("failure"));
    assert_eq!(
        status["verdict_source"],
        serde_json::json!("payload_exit_code")
    );
    assert_eq!(status["progress"]["fraction"], serde_json::json!([2, 3]));
    assert_eq!(status["progress"]["started"], serde_json::json!(true));

    let _ = request(&home, &serde_json::json!({"verb": "shutdown"}));
    shutdown.store(true, Ordering::SeqCst);
    let _ = thread.join();
    let _ = std::fs::remove_dir_all(&root);
}
