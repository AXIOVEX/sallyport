//! launchd service definitions for the macOS host (spec
//! 039 FR-MAC-02), generated as data and rendered to plist
//! XML by code — never hand-written on the host.
//!
//! The agent runs as a LaunchAgent in the owner's LOGIN
//! session (gui domain): jobs needing the GUI session,
//! Keychain, or user files run as the user, never as root.
//! KeepAlive puts it under launchd supervision, and it is
//! windowless by construction (a background agent with no
//! UI elements; spec 035 surfaces come from the separate
//! menu-bar app). Logs go to ~/Library/Logs/Syncyard plus
//! the gatekeeper harvest path. A system LaunchDaemon is
//! generated ONLY for a named job class that needs
//! pre-login availability — never assumed, and this module
//! refuses to build one without the class named.

/// Where the service lives.
#[derive(Debug, Clone, PartialEq)]
pub enum LaunchTarget {
    /// LaunchAgent in the owner's login session.
    Agent { uid: u32 },
    /// System LaunchDaemon, only for a named job class.
    Daemon { job_class: String },
}

#[derive(Debug, Clone, PartialEq)]
pub struct LaunchdService {
    pub label: String,
    pub program_arguments: Vec<String>,
    pub target: LaunchTarget,
    pub keep_alive: bool,
    pub run_at_load: bool,
    /// Absolute log directory (also asserted by the tests to
    /// be the fleet-standard ~/Library/Logs/Syncyard shape).
    pub log_dir: String,
    pub harvest_dir: String,
}

pub const AGENT_LABEL: &str = "ai.axiovex.syncyard.agent";

impl LaunchdService {
    /// The standard agent service for one Mac host.
    pub fn agent(uid: u32, home: &str, program_path: &str, harvest_dir: &str) -> LaunchdService {
        LaunchdService {
            label: AGENT_LABEL.to_string(),
            program_arguments: vec![program_path.to_string()],
            target: LaunchTarget::Agent { uid },
            keep_alive: true,
            run_at_load: true,
            log_dir: format!("{home}/Library/Logs/Syncyard"),
            harvest_dir: harvest_dir.to_string(),
        }
    }

    /// A LaunchDaemon for a named pre-login job class. An
    /// empty class name is a construction error, by design:
    /// daemons are specced per class, never assumed.
    pub fn daemon_for_class(
        job_class: &str,
        program_path: &str,
        log_dir: &str,
    ) -> Result<LaunchdService, String> {
        if job_class.trim().is_empty() {
            return Err(
                "a LaunchDaemon requires a named job class needing pre-login availability"
                    .to_string(),
            );
        }
        Ok(LaunchdService {
            label: format!("ai.axiovex.syncyard.daemon.{job_class}"),
            program_arguments: vec![program_path.to_string()],
            target: LaunchTarget::Daemon {
                job_class: job_class.to_string(),
            },
            keep_alive: true,
            run_at_load: true,
            log_dir: log_dir.to_string(),
            harvest_dir: String::new(),
        })
    }

    /// The launchctl domain target: gui/<uid> for agents
    /// (the login session), system for daemons.
    pub fn domain_target(&self) -> String {
        match &self.target {
            LaunchTarget::Agent { uid } => format!("gui/{uid}"),
            LaunchTarget::Daemon { .. } => "system".to_string(),
        }
    }

    /// The plist install path for this service.
    pub fn plist_path(&self, home: &str) -> String {
        match &self.target {
            LaunchTarget::Agent { .. } => {
                format!("{home}/Library/LaunchAgents/{}.plist", self.label)
            }
            LaunchTarget::Daemon { .. } => {
                format!("/Library/LaunchDaemons/{}.plist", self.label)
            }
        }
    }

    pub fn stdout_path(&self) -> String {
        format!("{}/agent.out.log", self.log_dir)
    }
    pub fn stderr_path(&self) -> String {
        format!("{}/agent.err.log", self.log_dir)
    }

    /// Render the plist XML. Deterministic key order so the
    /// install can diff what it is about to load.
    pub fn to_plist(&self) -> String {
        let args = self
            .program_arguments
            .iter()
            .map(|a| format!("        <string>{a}</string>"))
            .collect::<Vec<_>>()
            .join("\n");
        format!(
            "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
             <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
             <plist version=\"1.0\">\n\
             <dict>\n\
             \u{20}\u{20}<key>Label</key>\n\
             \u{20}\u{20}<string>{label}</string>\n\
             \u{20}\u{20}<key>ProgramArguments</key>\n\
             \u{20}\u{20}<array>\n\
             {args}\n\
             \u{20}\u{20}</array>\n\
             \u{20}\u{20}<key>RunAtLoad</key>\n\
             \u{20}\u{20}<{run_at_load}/>\n\
             \u{20}\u{20}<key>KeepAlive</key>\n\
             \u{20}\u{20}<{keep_alive}/>\n\
             \u{20}\u{20}<key>LimitToSessionType</key>\n\
             \u{20}\u{20}<string>Aqua</string>\n\
             \u{20}\u{20}<key>StandardOutPath</key>\n\
             \u{20}\u{20}<string>{stdout}</string>\n\
             \u{20}\u{20}<key>StandardErrorPath</key>\n\
             \u{20}\u{20}<string>{stderr}</string>\n\
             </dict>\n\
             </plist>\n",
            label = self.label,
            args = args,
            run_at_load = if self.run_at_load { "true" } else { "false" },
            keep_alive = if self.keep_alive { "true" } else { "false" },
            stdout = self.stdout_path(),
            stderr = self.stderr_path(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn svc() -> LaunchdService {
        LaunchdService::agent(
            501,
            "/Users/owner",
            "/Users/owner/Library/Syncyard/syncyard-agent",
            "/Users/owner/Library/Syncyard/harvest",
        )
    }

    // T039-03 launchagent-session-and-keepalive: the agent
    // service targets the owner's login session (gui/501),
    // never root's system domain; KeepAlive supervision is
    // on; the plist carries no UI/window elements
    // (windowless by construction).
    #[test]
    fn t039_03_launchagent_session_and_keepalive() {
        let s = svc();
        assert_eq!(s.domain_target(), "gui/501");
        assert!(s.keep_alive && s.run_at_load);
        assert!(matches!(s.target, LaunchTarget::Agent { uid: 501 }));
        assert_eq!(
            s.plist_path("/Users/owner"),
            "/Users/owner/Library/LaunchAgents/ai.axiovex.syncyard.agent.plist"
        );
        let plist = s.to_plist();
        assert!(plist.contains("<key>KeepAlive</key>"), "{plist}");
        assert!(plist.contains("<true/>"), "{plist}");
        assert!(plist.contains("LimitToSessionType"), "{plist}");
        assert!(plist.contains("Aqua"), "the GUI login session");
        // Windowless: the service definition names no window
        // or UI program at all — it is a background agent.
        assert!(!plist.to_lowercase().contains("window"), "{plist}");
        // A daemon without a named job class cannot be built.
        assert!(LaunchdService::daemon_for_class("", "/bin/x", "/tmp").is_err());
        let d = LaunchdService::daemon_for_class("prelogin-sync", "/bin/x", "/tmp").unwrap();
        assert_eq!(d.domain_target(), "system");
    }

    // T039-04 agent-logs-fleet-standard: stdout/stderr land
    // in ~/Library/Logs/Syncyard, and the harvest path the
    // gatekeeper collects from is part of the service
    // definition, not an afterthought.
    #[test]
    fn t039_04_agent_logs_fleet_standard() {
        let s = svc();
        assert_eq!(s.log_dir, "/Users/owner/Library/Logs/Syncyard");
        assert_eq!(
            s.stdout_path(),
            "/Users/owner/Library/Logs/Syncyard/agent.out.log"
        );
        assert_eq!(
            s.stderr_path(),
            "/Users/owner/Library/Logs/Syncyard/agent.err.log"
        );
        assert!(!s.harvest_dir.is_empty());
        let plist = s.to_plist();
        assert!(plist.contains("/Users/owner/Library/Logs/Syncyard/agent.out.log"));
        assert!(plist.contains("/Users/owner/Library/Logs/Syncyard/agent.err.log"));
    }
}
