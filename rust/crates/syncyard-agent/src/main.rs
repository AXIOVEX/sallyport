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

/// Resolve this agent's identity, fail-closed: an explicit
/// `--agent-id` wins; otherwise a non-empty `HOSTNAME` is used.
/// With neither, startup is an error — never the placeholder
/// string "host", which would let every supervisor-launched
/// agent on an unnamed host present as the same identity.
fn resolve_agent_id(explicit: Option<String>, hostname: Option<String>) -> Result<String, String> {
    if let Some(id) = explicit {
        if id.trim().is_empty() {
            return Err("--agent-id must not be empty".to_string());
        }
        return Ok(id);
    }
    match hostname {
        Some(name) if !name.trim().is_empty() => Ok(name),
        _ => Err(
            "agent identity unavailable: pass --agent-id ID or set a non-empty HOSTNAME; \
             refusing to start under a placeholder identity"
                .to_string(),
        ),
    }
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
    let agent_id = match resolve_agent_id(flag(&args, "--agent-id"), std::env::var("HOSTNAME").ok())
    {
        Ok(id) => id,
        Err(e) => {
            eprintln!("syncyard-agent: {e}");
            std::process::exit(2);
        }
    };
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn agent_id_resolution_fails_closed() {
        assert_eq!(
            resolve_agent_id(Some("pinned-id".into()), None).unwrap(),
            "pinned-id"
        );
        assert_eq!(
            resolve_agent_id(None, Some("real-host".into())).unwrap(),
            "real-host"
        );
        let err = resolve_agent_id(None, None).unwrap_err();
        assert!(err.contains("--agent-id") && err.contains("HOSTNAME"));
        assert!(resolve_agent_id(None, Some("  ".into())).is_err());
        assert!(resolve_agent_id(Some(" ".into()), Some("real-host".into())).is_err());
    }
}
