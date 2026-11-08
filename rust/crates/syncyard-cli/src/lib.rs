//! Syncyard CLI (spec 038 sections 2 and 9, spec 032 section 2).
//!
//! Thin client of the gatekeeper daemon: when the daemon answers
//! on its Unix socket, every verb goes through it (the daemon is
//! the only writer). When it does not, verbs fall back to direct
//! file mode — the state engine in-process, under the same flock
//! the daemon holds, so fallback and daemon can never interleave.
//! Fallback is never worse than the Python gatekeeper's behavior;
//! its one refusal is `dispatch`, whose transport lives daemon-side
//! (a named P2 gap, reported as such, never faked).
//!
//! Verb parity with `gk.py` for the core surface: submit, status,
//! snapshot, outbox (+ack), pause, stop, start, hold, resume,
//! cancel, ledger, dispatch, migrate-history, version, schema.
//! The `schema` verb emits the verb surface as JSON: the
//! `gh syncyard` extension shim is generated from the CLI's own
//! schema (spec 038 section 9), and P5 builds the drift test on it.
//! Verbs whose behavior belongs to later phases (sweep/harvest,
//! lane priority and locks, focus, drain, probe-host, adopt) are
//! listed in [`NOT_YET_VERBS`] so the gap is explicit in the schema
//! output rather than silent.

use std::path::{Path, PathBuf};

use syncyard_contract::JobRecord;
use syncyard_daemon::migrate::{migrate_history, verify_history};
use syncyard_daemon::offvm::{DirOffVmStore, FencingToken};
use syncyard_daemon::serve::{request, Engine};
use syncyard_daemon::state::{DaemonState, StateHome};
use syncyard_daemon::vwrite::{sha256_hex, FsWriter};

/// Role marker for diagnostics and the future identity stamp (UPD-03).
pub const CRATE_ROLE: &str = "cli";

/// The verbs this build implements.
pub const VERBS: &[&str] = &[
    "submit",
    "status",
    "snapshot",
    "outbox",
    "pause",
    "stop",
    "start",
    "hold",
    "resume",
    "cancel",
    "ledger",
    "dispatch",
    "migrate-history",
    "flush",
    "version",
    "schema",
];

/// gk.py verbs not yet implemented here, with their phase. Named in
/// the schema output so parity gaps are machine-visible.
pub const NOT_YET_VERBS: &[(&str, &str)] = &[
    ("sweep", "harvest/reconcile loop lands with the GitHub transport"),
    ("priority", "lane priority surface, later phase"),
    ("lock", "lane locks, later phase"),
    ("unlock", "lane locks, later phase"),
    ("focus", "dispatch focus, later phase"),
    ("drain", "host drain, later phase"),
    ("probe-host", "capability probe, later phase"),
    ("adopt", "run adoption, later phase"),
    ("purge", "destructive; requires its own operator design"),
];

/// The CLI schema (the generation source for the gh extension shim).
pub fn schema() -> serde_json::Value {
    serde_json::json!({
        "contract_version": syncyard_contract::CONTRACT_VERSION,
        "version": env!("CARGO_PKG_VERSION"),
        "verbs": VERBS,
        "not_yet_verbs": NOT_YET_VERBS
            .iter()
            .map(|(v, note)| serde_json::json!({"verb": v, "note": note}))
            .collect::<Vec<_>>(),
    })
}

/// One CLI invocation's result.
#[derive(Debug)]
pub struct CliOutcome {
    pub code: i32,
    pub stdout: String,
}

fn out(code: i32, v: &serde_json::Value) -> CliOutcome {
    CliOutcome {
        code,
        stdout: serde_json::to_string_pretty(v).unwrap_or_else(|_| "{}".to_string()),
    }
}

fn err_out(msg: &str) -> CliOutcome {
    out(1, &serde_json::json!({"ok": false, "error": msg}))
}

/// A request/response channel to the gatekeeper, socket or file.
pub trait Client {
    fn call(&mut self, req: &serde_json::Value) -> Result<serde_json::Value, String>;
}

/// Socket client: the daemon is the writer.
pub struct SocketClient {
    pub home: StateHome,
}

impl Client for SocketClient {
    fn call(&mut self, req: &serde_json::Value) -> Result<serde_json::Value, String> {
        request(&self.home, req).map_err(|e| format!("daemon socket: {e}"))
    }
}

/// A dispatcher placeholder for fallback mode: dispatch is refused,
/// by name (see module docs).
struct RefuseDispatch;
impl syncyard_daemon::dispatch::Dispatcher for RefuseDispatch {
    fn trigger(
        &self,
        _job: &syncyard_contract::QueueRecord,
        _key: &str,
    ) -> Result<syncyard_daemon::dispatch::RunRef, String> {
        Err("dispatch requires the daemon (transport is daemon-side)".to_string())
    }
    fn find_by_key(
        &self,
        _key: &str,
    ) -> Result<Option<syncyard_daemon::dispatch::RunRef>, String> {
        Ok(None)
    }
}

/// Direct-file fallback client: takes the state lock (refused while
/// the daemon holds it), loads disk state, and serves verbs through
/// the same engine the daemon uses.
pub struct FileClient {
    engine: Engine,
    _lock: syncyard_daemon::lock::StateLock,
}

impl FileClient {
    pub fn open(home: &StateHome, offvm_root: &Path) -> Result<FileClient, String> {
        std::fs::create_dir_all(home.queue_dir()).map_err(|e| e.to_string())?;
        let lock = syncyard_daemon::lock::try_state_lock(&home.lock_path())
            .map_err(|e| format!("state lock: {e}"))?
            .ok_or_else(|| {
                "state lock is held (the daemon is running); use the socket".to_string()
            })?;
        let (state, _gaps) = DaemonState::load(home.clone());
        let engine = Engine {
            state,
            store: std::sync::Arc::new(DirOffVmStore::new(offvm_root.to_path_buf())),
            dispatcher: std::sync::Arc::new(RefuseDispatch),
            // Fallback mode never dispatches, so it never needs the
            // fencing epoch; epoch 0 makes any accidental dispatch
            // attempt fail the epoch assertion rather than proceed.
            token: FencingToken {
                epoch: 0,
                holder: "cli-fallback".to_string(),
            },
            writer: std::sync::Arc::new(FsWriter::default()),
        };
        Ok(FileClient {
            engine,
            _lock: lock,
        })
    }
}

impl Client for FileClient {
    fn call(&mut self, req: &serde_json::Value) -> Result<serde_json::Value, String> {
        // Dispatch is refused in fallback mode, by name: the
        // transport lives daemon-side. (Without this intercept the
        // request would die on the epoch assertion with a
        // misleading fencing error.)
        if req.get("verb").and_then(|v| v.as_str()) == Some("dispatch") {
            return Ok(serde_json::json!({
                "ok": false,
                "error": "dispatch requires the daemon (transport is daemon-side); \
                          start syncyard-daemon serve, or use another verb in fallback mode",
            }));
        }
        Ok(self.engine.handle(req))
    }
}

/// Pick a client: socket when the daemon answers a ping, else the
/// direct-file fallback.
pub fn auto_client(home: &StateHome, offvm_root: &Path) -> Result<Box<dyn Client>, String> {
    let mut socket = SocketClient { home: home.clone() };
    let ping = serde_json::json!({"verb": "ping"});
    if let Ok(resp) = socket.call(&ping) {
        if resp.get("ok").and_then(|v| v.as_bool()) == Some(true) {
            return Ok(Box::new(socket));
        }
    }
    Ok(Box::new(FileClient::open(home, offvm_root)?))
}

/// Generate a job id in gk.py's shape:
/// `YYYYMMDDTHHMMSSZ-<key>-<6 hex>`.
pub fn new_job_id(key: &str) -> String {
    let iso = syncyard_daemon::time::now_iso();
    let stamp: String = iso.chars().filter(|c| c.is_ascii_alphanumeric()).collect();
    let nanos = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.subsec_nanos())
        .unwrap_or(0);
    let suffix = format!("{:06x}", (nanos ^ (std::process::id() << 8)) & 0x00ff_ffff);
    format!("{stamp}-{key}-{suffix}")
}

struct Args {
    rest: Vec<String>,
}

impl Args {
    fn flag(&self, name: &str) -> Option<String> {
        let mut it = self.rest.iter();
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
    fn has(&self, name: &str) -> bool {
        self.rest.iter().any(|a| a == name)
    }
    fn positional(&self, idx: usize) -> Option<String> {
        self.rest
            .iter()
            .filter(|a| !a.starts_with("--"))
            .nth(idx)
            .cloned()
    }
}

/// Run one CLI invocation. `args` excludes the program name.
/// The state home is never defaulted to the live gatekeeper: the
/// caller (main) resolves it from --home or SYNCYARD_HOME and this
/// function takes it explicitly.
pub fn run_cli(args: &[String], home: &StateHome, offvm_root: &Path) -> CliOutcome {
    let Some(verb) = args.first() else {
        return err_out("no verb given (see: schema)");
    };
    let a = Args {
        rest: args[1..].to_vec(),
    };
    match verb.as_str() {
        "version" => out(
            0,
            &serde_json::json!({
                "ok": true,
                "version": env!("CARGO_PKG_VERSION"),
                "contract_version": syncyard_contract::CONTRACT_VERSION,
            }),
        ),
        "schema" => out(0, &schema()),
        "migrate-history" => {
            let Some(from) = a.flag("--from") else {
                return err_out("migrate-history requires --from DIR");
            };
            // Wave probe: a scratch write inside the state home
            // (created first — migrate-history is an offline op
            // that manages this home; a nonexistent home is not a
            // wave).
            let _ = std::fs::create_dir_all(home.root());
            let probe_path = home.root().join(".wave-probe");
            let wave = || {
                let p = probe_path.clone();
                std::fs::write(&p, b"probe")
                    .map(|_| {
                        let _ = std::fs::remove_file(&p);
                        false
                    })
                    .unwrap_or(true)
            };
            match migrate_history(Path::new(&from), home, &wave) {
                Ok(manifest) => {
                    let _ = verify_history(home);
                    out(
                        0,
                        &serde_json::json!({
                            "ok": true,
                            "records": manifest.records.len(),
                            "ledger_lines": manifest.ledger_lines,
                            "ledger_chain": manifest.ledger_chain,
                        }),
                    )
                }
                Err(e) => err_out(&e.to_string()),
            }
        }
        "submit" => {
            let (Some(key), Some(program), Some(workflow)) = (
                a.flag("--key"),
                a.flag("--program"),
                a.flag("--workflow"),
            ) else {
                return err_out("submit requires --key, --program, --workflow");
            };
            let script = a.flag("--script");
            let script_sha = match &script {
                Some(p) => match std::fs::read(p) {
                    Ok(bytes) => Some(sha256_hex(&bytes)),
                    Err(e) => return err_out(&format!("cannot read --script {p}: {e}")),
                },
                None => None,
            };
            let job = JobRecord {
                id: new_job_id(&key),
                key,
                program,
                workflow,
                lane: a.flag("--lane"),
                cwd: a.flag("--cwd"),
                expected_min: a
                    .flag("--expected-min")
                    .and_then(|v| v.parse().ok())
                    .unwrap_or(30.0),
                priority: a.flag("--priority").and_then(|v| v.parse().ok()).unwrap_or(0),
                priority_source: if a.has("--priority") {
                    "human".to_string()
                } else {
                    "auto".to_string()
                },
                flags: vec![],
                maintenance: a.has("--maintenance"),
                adopted: false,
                notify_chat: a.flag("--notify"),
                venue: a.flag("--venue").unwrap_or_else(|| "auto".to_string()),
                script_path: script.map(|p| {
                    PathBuf::from(&p)
                        .canonicalize()
                        .map(|c| c.display().to_string())
                        .unwrap_or(p)
                }),
                script_sha256: script_sha,
                grab_path: a.flag("--grab-path"),
                created_at: syncyard_daemon::time::now_iso(),
            };
            let req = serde_json::json!({"verb": "submit", "job": job});
            dispatch_request(&req, home, offvm_root)
        }
        "status" => {
            let mut req = serde_json::json!({"verb": "status"});
            if let Some(id) = a.positional(0) {
                req["job_id"] = serde_json::Value::String(id);
            }
            dispatch_request(&req, home, offvm_root)
        }
        "snapshot" => dispatch_request(&serde_json::json!({"verb": "snapshot"}), home, offvm_root),
        "flush" => dispatch_request(&serde_json::json!({"verb": "flush"}), home, offvm_root),
        "outbox" => {
            if let Some(ack) = a.flag("--ack") {
                let cursor: u64 = ack.parse().unwrap_or(0);
                dispatch_request(
                    &serde_json::json!({"verb": "outbox_ack", "cursor": cursor}),
                    home,
                    offvm_root,
                )
            } else {
                dispatch_request(&serde_json::json!({"verb": "outbox"}), home, offvm_root)
            }
        }
        "pause" => dispatch_request(
            &serde_json::json!({"verb": "pause", "reason": a.flag("--reason").unwrap_or_default()}),
            home,
            offvm_root,
        ),
        "stop" => dispatch_request(
            &serde_json::json!({"verb": "stop", "reason": a.flag("--reason").unwrap_or_default()}),
            home,
            offvm_root,
        ),
        "start" => dispatch_request(&serde_json::json!({"verb": "start"}), home, offvm_root),
        "hold" | "resume" | "cancel" | "dispatch" => {
            let Some(id) = a.positional(0) else {
                return err_out(&format!("{verb} requires a job id"));
            };
            dispatch_request(
                &serde_json::json!({"verb": verb, "job_id": id}),
                home,
                offvm_root,
            )
        }
        "ledger" => {
            let tail: u64 = a.flag("--tail").and_then(|v| v.parse().ok()).unwrap_or(20);
            dispatch_request(
                &serde_json::json!({"verb": "ledger", "tail": tail}),
                home,
                offvm_root,
            )
        }
        other => err_out(&format!("unknown verb {other} (see: schema)")),
    }
}

fn dispatch_request(
    req: &serde_json::Value,
    home: &StateHome,
    offvm_root: &Path,
) -> CliOutcome {
    match auto_client(home, offvm_root) {
        Ok(mut client) => match client.call(req) {
            Ok(resp) => {
                let code = if resp.get("ok").and_then(|v| v.as_bool()) == Some(true) {
                    0
                } else {
                    1
                };
                out(code, &resp)
            }
            Err(e) => err_out(&e),
        },
        Err(e) => err_out(&e),
    }
}
