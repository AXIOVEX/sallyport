//! Tray-side job control (spec 037): the app issues **signed
//! command files** into the outbox directory the backchannel
//! carries to the gatekeeper host, and reads its confirmation
//! from the gatekeeper's published outcomes — never from its
//! own write succeeding (FR-037-07: PENDING never renders as
//! done). The app holds no path that edits gatekeeper state
//! directly, and issues nothing but command files: this
//! module contains no process spawning at all (FR-037-12's
//! surface here).

#[cfg(unix)]
use std::io::{BufRead, BufReader, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

use syncyard_contract::isotime;
use syncyard_contract::{
    CommandOutcome, ControlAction, ControlCommand, HostSecretKey, OutcomeStatus,
    COMMAND_DEFAULT_EXPIRY_S, CONTRACT_VERSION,
};

/// The host key file the app keeps in its own state dir: the
/// secret half. The public half is what enrollment hands to
/// the gatekeeper's config (spec 037 section 2).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HostKeyFile {
    pub host: String,
    pub secret_hex: String,
    pub public_hex: String,
}

/// Load the host key file, creating (and storing) a fresh key
/// on first run. Returns the file and whether it was created.
pub fn load_or_create_key_file(
    path: &Path,
    host: &str,
) -> std::io::Result<(HostKeyFile, HostSecretKey, bool)> {
    if let Ok(text) = std::fs::read_to_string(path) {
        let file: HostKeyFile = serde_json::from_str(&text)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        let key = HostSecretKey::from_hex(&file.secret_hex).ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "key file secret unparseable",
            )
        })?;
        return Ok((file, key, false));
    }
    let key = HostSecretKey::generate();
    let file = HostKeyFile {
        host: host.to_string(),
        secret_hex: key.to_hex(),
        public_hex: key.public_key().to_hex(),
    };
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let text = serde_json::to_string_pretty(&file)
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, &text)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
    }
    std::fs::rename(&tmp, path)?;
    Ok((file, key, true))
}

/// Issues signed command files into an outbox directory.
pub struct CommandIssuer {
    pub outbox_dir: PathBuf,
    pub host: String,
    /// Issuer identity string (app + train).
    pub identity: String,
    pub train: String,
    key: HostSecretKey,
    counter: AtomicU64,
}

impl CommandIssuer {
    pub fn new(outbox_dir: PathBuf, host: String, train: String, key: HostSecretKey) -> Self {
        CommandIssuer {
            identity: format!("syncyard-tray {train}"),
            outbox_dir,
            host,
            train,
            key,
            counter: AtomicU64::new(1),
        }
    }

    /// Build, sign, and file one command. The file is the
    /// whole act on this side: delivery is the backchannel's,
    /// the decision is the gatekeeper's, and confirmation is
    /// read from published outcomes by the tracker below.
    pub fn issue(
        &self,
        action: ControlAction,
        job_id: Option<&str>,
        new_priority: Option<i64>,
    ) -> std::io::Result<ControlCommand> {
        let now = isotime::now_unix();
        let n = self.counter.fetch_add(1, Ordering::SeqCst);
        let command_id = format!("cmd-{now}-{}-{n}", std::process::id());
        let mut hasher = Sha256::new();
        hasher.update(format!("{}|{}|{}|{}", self.host, command_id, now, n).as_bytes());
        let nonce = syncyard_contract::control::to_hex(&hasher.finalize());
        let mut cmd = ControlCommand {
            command_id: command_id.clone(),
            action,
            job_id: job_id.map(|s| s.to_string()),
            lane: None,
            host: None,
            new_priority,
            issuer_host: self.host.clone(),
            issuer_identity: self.identity.clone(),
            nonce,
            issued_at: isotime::iso_from_unix(now.max(0) as u64),
            expires_at: isotime::iso_from_unix((now + COMMAND_DEFAULT_EXPIRY_S).max(0) as u64),
            app_train: self.train.clone(),
            contract_version: CONTRACT_VERSION,
            signature: String::new(),
        };
        cmd.sign(&self.key);
        std::fs::create_dir_all(&self.outbox_dir)?;
        let text = serde_json::to_string_pretty(&cmd)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        let target = self.outbox_dir.join(format!("command-{command_id}.json"));
        let tmp = self.outbox_dir.join(format!(".command-{command_id}.tmp"));
        std::fs::write(&tmp, text)?;
        std::fs::rename(&tmp, &target)?;
        Ok(cmd)
    }
}

/// How one issued command renders (spec 037 section 3):
/// derived only from the gatekeeper's published outcomes.
#[derive(Debug, Clone, PartialEq)]
pub enum CommandDisplay {
    /// Written; no outcome published yet. Never "done".
    Pending,
    /// The gatekeeper executed it (ledger event named).
    Confirmed { ledger_event: Option<String> },
    /// The gatekeeper refused it, with its reason.
    Refused { reason: String },
    /// Past expiry with no outcome: EXPIRED — not executed.
    ExpiredNotExecuted { age_s: i64 },
}

/// The tracker: pure display-state derivation.
pub fn display_state(
    cmd: &ControlCommand,
    outcomes: &[CommandOutcome],
    now_unix_s: i64,
) -> CommandDisplay {
    if let Some(o) = outcomes
        .iter()
        .rev()
        .find(|o| o.command_id == cmd.command_id)
    {
        return match o.status {
            OutcomeStatus::Executed => CommandDisplay::Confirmed {
                ledger_event: o.ledger_event.clone(),
            },
            OutcomeStatus::Refused => CommandDisplay::Refused {
                reason: o.reason.clone().unwrap_or_else(|| "refused".to_string()),
            },
        };
    }
    if cmd.is_expired(now_unix_s) {
        return CommandDisplay::ExpiredNotExecuted {
            age_s: cmd.age_s(now_unix_s).unwrap_or(0),
        };
    }
    CommandDisplay::Pending
}

/// Maximum bytes in one daemon response line (the same bound
/// the daemon applies to requests).
pub const MAX_SOCKET_LINE_BYTES: usize = 1_048_576;

/// Read/write timeout for one daemon socket exchange.
pub const SOCKET_IO_TIMEOUT: Duration = Duration::from_secs(10);

/// The gatekeeper channel the app reads switch state and
/// outcomes through (the daemon socket on this host; the
/// backchannel relays where the daemon is remote).
pub trait GatekeeperChannel {
    fn call(&self, req: &serde_json::Value) -> Result<serde_json::Value, String>;
}

/// The daemon's Unix socket as a channel.
pub struct SocketGatekeeper {
    pub socket_path: PathBuf,
}

#[cfg(unix)]
impl GatekeeperChannel for SocketGatekeeper {
    fn call(&self, req: &serde_json::Value) -> Result<serde_json::Value, String> {
        use std::os::unix::net::UnixStream;
        let mut stream = UnixStream::connect(&self.socket_path)
            .map_err(|e| format!("connect {}: {e}", self.socket_path.display()))?;
        stream
            .set_read_timeout(Some(SOCKET_IO_TIMEOUT))
            .and_then(|_| stream.set_write_timeout(Some(SOCKET_IO_TIMEOUT)))
            .map_err(|e| e.to_string())?;
        let mut line = serde_json::to_string(req).map_err(|e| e.to_string())?;
        line.push('\n');
        stream
            .write_all(line.as_bytes())
            .map_err(|e| e.to_string())?;
        let mut reader = BufReader::new(stream);
        let mut resp = String::new();
        reader
            .by_ref()
            .take((MAX_SOCKET_LINE_BYTES + 1) as u64)
            .read_line(&mut resp)
            .map_err(|e| e.to_string())?;
        if resp.len() > MAX_SOCKET_LINE_BYTES {
            return Err(format!(
                "response line exceeds the {MAX_SOCKET_LINE_BYTES}-byte limit"
            ));
        }
        serde_json::from_str(&resp).map_err(|e| format!("bad reply: {e}"))
    }
}

#[cfg(not(unix))]
impl GatekeeperChannel for SocketGatekeeper {
    fn call(&self, _req: &serde_json::Value) -> Result<serde_json::Value, String> {
        Err("daemon socket channel is unix-only in this build".to_string())
    }
}

/// Read the FIX-08 kill-switch state as the gatekeeper
/// publishes it (a record, displayed — spec 037 FR-037-10).
pub fn read_kill_switch(ch: &dyn GatekeeperChannel) -> Result<bool, String> {
    let resp = ch.call(&serde_json::json!({"verb": "kill_switch"}))?;
    resp.get("kill_switch")
        .and_then(|v| v.as_bool())
        .ok_or_else(|| "reply carries no kill_switch".to_string())
}

/// Set or clear the kill switch through the gatekeeper (the
/// act itself is ledgered there, naming this app as source).
pub fn set_kill_switch(ch: &dyn GatekeeperChannel, on: bool) -> Result<bool, String> {
    let resp = ch.call(&serde_json::json!({
        "verb": "kill_switch",
        "on": on,
        "source": "tray-app",
    }))?;
    resp.get("kill_switch")
        .and_then(|v| v.as_bool())
        .ok_or_else(|| "reply carries no kill_switch".to_string())
}

/// Fetch the gatekeeper's published command outcomes.
pub fn fetch_outcomes(ch: &dyn GatekeeperChannel) -> Result<Vec<CommandOutcome>, String> {
    let resp = ch.call(&serde_json::json!({"verb": "control_outcomes"}))?;
    let arr = resp
        .get("outcomes")
        .cloned()
        .unwrap_or(serde_json::Value::Null);
    serde_json::from_value(arr).map_err(|e| format!("outcomes do not parse: {e}"))
}
