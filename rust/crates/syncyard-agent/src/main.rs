//! Agent entry point.
//!
//! The binary is windowless by construction: on Windows it is
//! built for the `windows` subsystem, so starting it never
//! allocates a console window (spec 038 FR-038-54).
//!
//! Usage:
//!   syncyard-agent --daemon-home DIR [--install-path DIR]
//!                  [--work-root DIR] [--venue native]
//!                  [--agent-id ID] [--once]
//!
//! The install path defaults to the directory containing the
//! executable (that is what makes it an install path). The agent
//! is started by the host's supervisor (Task Scheduler /
//! systemd), never by a gatekeeper job — a gatekeeper job may
//! not leave a persistent process running.

// On Windows, build for the windows subsystem: no console
// window is ever allocated for this process.
#![cfg_attr(windows, windows_subsystem = "windows")]

use std::path::PathBuf;
use std::time::Duration;

use syncyard_agent::agent::Agent;
use syncyard_agent::runner::AgentConfig;
use syncyard_agent::socket::SocketChannel;

fn flag(args: &[String], name: &str) -> Option<String> {
    let mut it = args.iter();
    while let Some(a) = it.next() {
        if a == name {
            return it.next().cloned();
        }
        if let Some(v) = a.strip_prefix(&format!("{name}=")) {
            return Some(v.to_string());
        }
    }
    None
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(daemon_home) = flag(&args, "--daemon-home") else {
        eprintln!("syncyard-agent: --daemon-home DIR is required");
        std::process::exit(2);
    };
    let install_path = flag(&args, "--install-path")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::current_exe()
                .ok()
                .and_then(|e| e.parent().map(|p| p.to_path_buf()))
        })
        .unwrap_or_else(|| PathBuf::from("."));
    let work_root = flag(&args, "--work-root")
        .map(PathBuf::from)
        .unwrap_or_else(|| install_path.join("work"));
    let venue = flag(&args, "--venue").unwrap_or_else(|| "native".to_string());
    let agent_id = flag(&args, "--agent-id")
        .unwrap_or_else(|| std::env::var("HOSTNAME").unwrap_or_else(|_| "host".to_string()));
    let once = args.iter().any(|a| a == "--once");

    let channel = SocketChannel::new(PathBuf::from(daemon_home), &venue, &agent_id);
    let config = AgentConfig::new(work_root, channel.source_id());
    let mut agent = match Agent::new(install_path, config, Box::new(channel)) {
        Ok(a) => a,
        Err(e) => {
            // A second instance for this install path exits
            // quietly by design (the singleton is the guard,
            // not an alarm).
            eprintln!("syncyard-agent: {e}");
            std::process::exit(
                if matches!(e, syncyard_agent::channel::AgentError::AlreadyRunning(_)) {
                    0
                } else {
                    1
                },
            );
        }
    };
    loop {
        match agent.run_cycle() {
            Ok(_) => {}
            Err(e) => {
                // Transport trouble is a degradation, not a
                // death: the next cycle retries (and pending
                // completions ride it).
                eprintln!("syncyard-agent: cycle: {e}");
            }
        }
        if once {
            return;
        }
        std::thread::sleep(Duration::from_secs(5));
    }
}
