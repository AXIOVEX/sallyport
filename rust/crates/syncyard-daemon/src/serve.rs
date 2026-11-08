//! The daemon server: RAM state behind a Unix domain socket
//! (spec 032 section 2, spec 038 FR-038-03).
//!
//! CLI verbs are thin socket clients; the daemon is the only
//! writer of queue state while it runs, enforced by the state lock
//! (see [`crate::lock`]). A background thread flushes dirty state
//! every [`crate::state::DEFAULT_FLUSH_INTERVAL_S`] seconds (and
//! verbs flush on transitions through the same path).
//!
//! Requests and responses are newline-delimited JSON:
//! `{"verb": "submit", "job": {...}}` → `{"ok": true, ...}`.
//! Sockets pass during write waves (spec 032's measured shape), so
//! every verb works at full speed mid-wave against RAM state.

use std::collections::BTreeMap;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use syncyard_contract::{Assignment, CompletionReport, JobRecord, JobState, ProgressBatch};

use crate::dispatch::{dispatch_one, Dispatcher};
use crate::offvm::{FencingToken, OffVmStore, KIND_SUBMITS};
use crate::state::{new_queue_record, DaemonState, PauseRecord, StateHome, StopRecord};
use crate::time::{now_iso, now_unix};
use crate::vwrite::StateWriter;

/// Everything a verb handler needs, behind one lock.
pub struct Engine {
    pub state: DaemonState,
    pub store: Arc<dyn OffVmStore>,
    pub dispatcher: Arc<dyn Dispatcher>,
    pub token: FencingToken,
    pub writer: Arc<dyn StateWriter>,
}

impl Engine {
    /// Submit a job: RAM first, then the off-VM acknowledgement
    /// record; the ack is given only after both (spec 032 section
    /// 4 — a submitter always learns whether its job exists).
    /// Duplicate keys among non-terminal jobs are refused, as in
    /// gk.py.
    pub fn submit(&mut self, job: &JobRecord) -> Result<String, String> {
        for existing in self.state.jobs.values() {
            if existing.key == job.key && !existing.state.is_terminal() {
                return Err(format!(
                    "duplicate key {} already active as {}",
                    job.key, existing.id
                ));
            }
        }
        let rec = new_queue_record(job);
        let bytes = serde_json::to_vec_pretty(&rec).map_err(|e| e.to_string())?;
        self.store
            .put_record(KIND_SUBMITS, &job.id, &bytes)
            .map_err(|e| format!("submit acknowledgement failed: {e}"))?;
        self.state.put_job(rec);
        let mut fields = BTreeMap::new();
        fields.insert("job_id".into(), serde_json::Value::String(job.id.clone()));
        fields.insert("key".into(), serde_json::Value::String(job.key.clone()));
        fields.insert(
            "program".into(),
            serde_json::Value::String(job.program.clone()),
        );
        self.state.ledger("submitted", fields);
        // Flush on transition; failure is a non-event (RAM + the
        // off-VM record hold the truth).
        let _ = self.state.flush(self.writer.as_ref());
        Ok(job.id.clone())
    }

    /// Handle one request object; returns the response object.
    pub fn handle(&mut self, req: &serde_json::Value) -> serde_json::Value {
        let verb = req.get("verb").and_then(|v| v.as_str()).unwrap_or("");
        match verb {
            "ping" => ok(serde_json::json!({
                "version": env!("CARGO_PKG_VERSION"),
                "contract_version": syncyard_contract::CONTRACT_VERSION,
                "epoch": self.token.epoch,
            })),
            "submit" => {
                let Some(job_v) = req.get("job") else {
                    return err("submit requires a job object");
                };
                match serde_json::from_value::<JobRecord>(job_v.clone()) {
                    Ok(job) => match self.submit(&job) {
                        Ok(id) => ok(serde_json::json!({"job_id": id})),
                        Err(e) => err(&e),
                    },
                    Err(e) => err(&format!("job does not parse: {e}")),
                }
            }
            "status" => match req.get("job_id").and_then(|v| v.as_str()) {
                Some(id) => match self.state.jobs.get(id) {
                    Some(rec) => {
                        let mut v = serde_json::to_value(rec).unwrap_or_default();
                        // The job's streaming-progress view rides
                        // the status response (spec 034 section 3);
                        // the record itself is untouched — progress
                        // is display data, never record state.
                        if let Some(view) = self.state.progress_view(id, now_unix()) {
                            if let Ok(pv) = serde_json::to_value(&view) {
                                v["progress"] = pv;
                            }
                        }
                        ok(v)
                    }
                    None => err(&format!("unknown job {id}")),
                },
                None => ok(self.state.snapshot()),
            },
            "progress" => {
                // Spec 034 transport + view. Three shapes:
                //  {"job_id", "lines": [...], "run_id", "source"}
                //      ingest one batch (agent / harvest sender)
                //  {"job_id", "transport_ok": false, ...}
                //      report a transport failure (FR-034-07)
                //  {"job_id"}
                //      read the watcher-facing view (CLI/watchers)
                let Some(id) = req.get("job_id").and_then(|v| v.as_str()) else {
                    return err("progress requires job_id");
                };
                if !self.state.jobs.contains_key(id) {
                    return err(&format!("unknown job {id}"));
                }
                let id_owned = id.to_string();
                if req.get("transport_ok").and_then(|v| v.as_bool()) == Some(false) {
                    let ack = self.state.note_progress_transport_failure(
                        &id_owned,
                        req.get("run_id").and_then(|v| v.as_u64()),
                        req.get("source").and_then(|v| v.as_str()).unwrap_or(""),
                    );
                    return ok(serde_json::json!({
                        "ack": ack,
                        "progress": serde_json::to_value(
                            self.state.progress_view(&id_owned, now_unix())
                        ).unwrap_or_default(),
                    }));
                }
                if let Some(lines_v) = req.get("lines") {
                    let lines: Vec<String> = match serde_json::from_value(lines_v.clone()) {
                        Ok(l) => l,
                        Err(e) => return err(&format!("lines does not parse: {e}")),
                    };
                    let batch = ProgressBatch {
                        job_id: id_owned.clone(),
                        run_id: req.get("run_id").and_then(|v| v.as_u64()),
                        source: req
                            .get("source")
                            .and_then(|v| v.as_str())
                            .unwrap_or("")
                            .to_string(),
                        lines,
                    };
                    let ack = self.state.ingest_progress(&batch, now_unix());
                    return ok(serde_json::json!({
                        "ack": ack,
                        "progress": serde_json::to_value(
                            self.state.progress_view(&id_owned, now_unix())
                        ).unwrap_or_default(),
                    }));
                }
                match self.state.progress_view(&id_owned, now_unix()) {
                    Some(view) => ok(serde_json::to_value(&view).unwrap_or_default()),
                    None => err(&format!("unknown job {id}")),
                }
            }
            "agent_poll" => {
                // The backchannel agent's assignment surface
                // (native venue): the oldest submitted, unheld job
                // for the venue is assigned a daemon-local run id
                // and marked dispatched. The agent echoes the run
                // id in every progress batch and in its completion
                // report, which is what makes attribution checkable.
                let venue = req
                    .get("venue")
                    .and_then(|v| v.as_str())
                    .unwrap_or("native")
                    .to_string();
                let agent = req
                    .get("agent")
                    .and_then(|v| v.as_str())
                    .unwrap_or("agent")
                    .to_string();
                let candidate = self
                    .state
                    .jobs
                    .values()
                    .filter(|r| {
                        r.state == JobState::Submitted
                            && r.venue == venue
                            && self.state.dispatch_held(r).is_none()
                    })
                    .min_by(|a, b| {
                        a.created_at
                            .cmp(&b.created_at)
                            .then(a.seq.unwrap_or(0).cmp(&b.seq.unwrap_or(0)))
                    })
                    .map(|r| r.id.clone());
                match candidate {
                    None => ok(serde_json::json!({"assignment": null})),
                    Some(job_id) => {
                        let run_id = self.state.alloc_local_run_id();
                        let mut assignment = None;
                        self.state.update_job(&job_id, |rec| {
                            rec.state = JobState::Dispatched;
                            rec.dispatched_at = Some(now_iso());
                            rec.run_id = Some(run_id);
                            rec.run_status = Some("in_progress".to_string());
                            assignment = Some(Assignment {
                                job_id: rec.id.clone(),
                                run_id,
                                command: rec
                                    .script_path
                                    .clone()
                                    .map(|p| vec![p])
                                    .unwrap_or_default(),
                                cwd: rec.cwd.clone(),
                            });
                        });
                        let mut fields = BTreeMap::new();
                        fields.insert("job_id".into(), serde_json::Value::String(job_id.clone()));
                        fields.insert("run_id".into(), serde_json::Value::Number(run_id.into()));
                        fields.insert("agent".into(), serde_json::Value::String(agent));
                        fields.insert("local".into(), serde_json::Value::Bool(true));
                        self.state.ledger("agent_dispatched", fields);
                        let _ = self.state.flush(self.writer.as_ref());
                        ok(serde_json::json!({"assignment": assignment}))
                    }
                }
            }
            "agent_completion" => {
                // The agent's terminal report. The verdict comes
                // from the payload exit code alone (spec 020,
                // FR-034-03): a progress COMPLETED line the payload
                // wrote earlier is advisory and never consulted
                // here.
                let (Some(id), Some(run_id)) = (
                    req.get("job_id").and_then(|v| v.as_str()),
                    req.get("run_id").and_then(|v| v.as_u64()),
                ) else {
                    return err("agent_completion requires job_id and run_id");
                };
                let report = CompletionReport {
                    job_id: id.to_string(),
                    run_id,
                    payload_exit_code: req
                        .get("payload_exit_code")
                        .and_then(|v| v.as_i64())
                        .map(|c| c as i32),
                    payload_started: req
                        .get("payload_started")
                        .and_then(|v| v.as_bool())
                        .unwrap_or(false),
                };
                let Some(rec) = self.state.jobs.get(id) else {
                    return err(&format!("unknown job {id}"));
                };
                if rec.state != JobState::Dispatched || rec.run_id != Some(run_id) {
                    return err(&format!(
                        "completion does not match the dispatched run for {id}"
                    ));
                }
                let conclusion = report.derived_conclusion();
                let id_owned = id.to_string();
                self.state.update_job(&id_owned, |rec| {
                    rec.state = JobState::Completed;
                    rec.completed_at = Some(now_iso());
                    rec.run_status = Some("completed".to_string());
                    rec.run_conclusion = Some(conclusion.to_string());
                    rec.verdict_source = Some("payload_exit_code".to_string());
                });
                let mut fields = BTreeMap::new();
                fields.insert("job_id".into(), serde_json::Value::String(id_owned.clone()));
                fields.insert("run_id".into(), serde_json::Value::Number(run_id.into()));
                fields.insert(
                    "payload_exit_code".into(),
                    report
                        .payload_exit_code
                        .map(|c| serde_json::Value::Number(c.into()))
                        .unwrap_or(serde_json::Value::Null),
                );
                fields.insert(
                    "conclusion".into(),
                    serde_json::Value::String(conclusion.to_string()),
                );
                self.state.ledger("agent_completion", fields);
                let _ = self.state.flush(self.writer.as_ref());
                ok(serde_json::json!({
                    "completed": id_owned,
                    "run_conclusion": conclusion,
                }))
            }
            "snapshot" => ok(self.state.snapshot()),
            "outbox" => ok(serde_json::json!({
                "pending": self.state.outbox_pending(),
                "cursor": self.state.outbox_cursor,
            })),
            "outbox_ack" => {
                let cursor = req.get("cursor").and_then(|v| v.as_u64()).unwrap_or(0);
                self.state.outbox_ack(cursor);
                let _ = self.state.flush(self.writer.as_ref());
                ok(serde_json::json!({"cursor": self.state.outbox_cursor}))
            }
            "pause" => {
                let reason = req
                    .get("reason")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                self.state.set_paused(Some(PauseRecord {
                    reason: reason.clone(),
                    ts: now_iso(),
                }));
                let mut fields = BTreeMap::new();
                fields.insert("reason".into(), serde_json::Value::String(reason));
                self.state.ledger("paused", fields);
                let _ = self.state.flush(self.writer.as_ref());
                ok(serde_json::json!({"paused": true}))
            }
            "stop" => {
                let reason = req
                    .get("reason")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_string();
                self.state.set_stopped(Some(StopRecord {
                    reason: reason.clone(),
                    ts: now_iso(),
                }));
                let mut fields = BTreeMap::new();
                fields.insert("reason".into(), serde_json::Value::String(reason));
                self.state.ledger("stopped", fields);
                let _ = self.state.flush(self.writer.as_ref());
                ok(serde_json::json!({"stopped": true}))
            }
            "start" => {
                // gk.py's start: clear any stop and any pause.
                let mut cleared = Vec::new();
                if self.state.stopped.take().is_some() {
                    cleared.push("stop");
                }
                if self.state.paused.take().is_some() {
                    cleared.push("pause");
                }
                self.state.set_paused(None);
                self.state.set_stopped(None);
                let mut fields = BTreeMap::new();
                fields.insert(
                    "cleared".into(),
                    serde_json::Value::Array(
                        cleared
                            .iter()
                            .map(|c| serde_json::Value::String(c.to_string()))
                            .collect(),
                    ),
                );
                self.state.ledger("started", fields);
                let _ = self.state.flush(self.writer.as_ref());
                ok(serde_json::json!({"cleared": cleared}))
            }
            "hold" => {
                let Some(id) = req.get("job_id").and_then(|v| v.as_str()) else {
                    return err("hold requires job_id");
                };
                if !self.state.jobs.contains_key(id) {
                    return err(&format!("unknown job {id}"));
                }
                self.state.set_hold(id, true);
                let mut fields = BTreeMap::new();
                fields.insert("job_id".into(), serde_json::Value::String(id.into()));
                self.state.ledger("operator_hold", fields);
                let _ = self.state.flush(self.writer.as_ref());
                ok(serde_json::json!({"held": id}))
            }
            "resume" => {
                // Release a per-job hold (the daemon side of the
                // hold/resume pair; payload checkpoint signaling is
                // a later phase — see PROGRESS.md gaps).
                let Some(id) = req.get("job_id").and_then(|v| v.as_str()) else {
                    return err("resume requires job_id");
                };
                if !self.state.jobs.contains_key(id) {
                    return err(&format!("unknown job {id}"));
                }
                self.state.set_hold(id, false);
                let mut fields = BTreeMap::new();
                fields.insert("job_id".into(), serde_json::Value::String(id.into()));
                self.state.ledger("operator_hold_released", fields);
                let _ = self.state.flush(self.writer.as_ref());
                ok(serde_json::json!({"resumed": id}))
            }
            "cancel" => {
                let Some(id) = req.get("job_id").and_then(|v| v.as_str()) else {
                    return err("cancel requires job_id");
                };
                let id_owned = id.to_string();
                if !self.apply_cancel(&id_owned, BTreeMap::new()) {
                    return err(&format!("unknown job {id}"));
                }
                let _ = self.state.flush(self.writer.as_ref());
                ok(serde_json::json!({"cancelled": id}))
            }
            // Spec 037: a signed tray command file, delivered by
            // the backchannel. Verification + execution live in
            // crate::control; the recorded outcome is the only
            // reply an app may treat as confirmation.
            "control_command" => {
                let Some(raw) = req.get("command") else {
                    return err("control_command requires a command");
                };
                let cmd: syncyard_contract::ControlCommand =
                    match serde_json::from_value(raw.clone()) {
                        Ok(c) => c,
                        Err(e) => return err(&format!("command does not parse: {e}")),
                    };
                let outcome = self.execute_control_command(&cmd);
                let _ = self.state.flush(self.writer.as_ref());
                match serde_json::to_value(&outcome) {
                    Ok(v) => ok(serde_json::json!({"outcome": v})),
                    Err(e) => err(&format!("outcome does not serialize: {e}")),
                }
            }
            "control_outcomes" => {
                ok(serde_json::json!({"outcomes": self.state.control.outcomes}))
            }
            // FIX-08 repair kill switch (spec 037 FR-037-10):
            // read it, or set/clear it as a ledgered operator act.
            "kill_switch" => {
                if let Some(on) = req.get("on").and_then(|v| v.as_bool()) {
                    let source = req
                        .get("source")
                        .and_then(|v| v.as_str())
                        .unwrap_or("socket")
                        .to_string();
                    let changed = self.set_kill_switch(on, &source);
                    let _ = self.state.flush(self.writer.as_ref());
                    ok(serde_json::json!({"kill_switch": on, "changed": changed}))
                } else {
                    ok(serde_json::json!({"kill_switch": self.state.control.kill_switch}))
                }
            }
            "dispatch" => {
                // The `gk dispatch` verb (spec 038 FR-038-06: the
                // mirror's lift leg migrates to this verb).
                let Some(id) = req.get("job_id").and_then(|v| v.as_str()) else {
                    return err("dispatch requires job_id");
                };
                let id_owned = id.to_string();
                match dispatch_one(
                    &mut self.state,
                    self.store.as_ref(),
                    &self.token,
                    self.dispatcher.as_ref(),
                    &id_owned,
                ) {
                    Ok(run) => {
                        let _ = self.state.flush(self.writer.as_ref());
                        ok(serde_json::json!({
                            "run_id": run.run_id,
                            "run_status": run.status,
                        }))
                    }
                    Err(e) => err(&e.to_string()),
                }
            }
            "ledger" => {
                let tail = req.get("tail").and_then(|v| v.as_u64()).unwrap_or(20) as usize;
                ok(serde_json::json!({
                    "on_disk": self.state.ledger_on_disk,
                    "buffered": self.state.ledger_buffer.len(),
                    "events": ledger_tail(&self.state, tail),
                }))
            }
            "flush" => {
                let report = self.state.flush(self.writer.as_ref());
                ok(serde_json::json!({
                    "ok": report.ok,
                    "jobs_written": report.jobs_written,
                    "ledger_written": report.ledger_written,
                    "errors": report.errors,
                }))
            }
            other => err(&format!("unknown verb {other}")),
        }
    }
}

fn ok(mut v: serde_json::Value) -> serde_json::Value {
    if let Some(obj) = v.as_object_mut() {
        obj.insert("ok".into(), serde_json::Value::Bool(true));
    }
    v
}

fn err(msg: &str) -> serde_json::Value {
    serde_json::json!({"ok": false, "error": msg})
}

fn ledger_tail(state: &DaemonState, tail: usize) -> Vec<serde_json::Value> {
    let mut out: Vec<serde_json::Value> = Vec::new();
    if let Ok(text) = std::fs::read_to_string(state.home.ledger_path()) {
        let lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
        for line in lines.iter().rev().take(tail) {
            if let Ok(v) = serde_json::from_str::<serde_json::Value>(line) {
                out.push(v);
            }
        }
    }
    for ev in state.ledger_buffer.iter().rev().take(tail) {
        if out.len() >= tail {
            break;
        }
        if let Ok(v) = serde_json::to_value(ev) {
            out.push(v);
        }
    }
    out.reverse();
    // Keep chronological order within each half; exact interleaving
    // of disk vs buffer is not needed for a tail read.
    out
}

/// Server configuration.
pub struct ServeConfig {
    pub flush_interval: Duration,
}

impl Default for ServeConfig {
    fn default() -> Self {
        ServeConfig {
            flush_interval: Duration::from_secs(crate::state::DEFAULT_FLUSH_INTERVAL_S),
        }
    }
}

/// Run the server until `shutdown` is set or a `shutdown` request
/// arrives on a connection. The caller must already hold the state
/// lock and have run boot reconciliation.
pub fn serve(
    engine: Arc<Mutex<Engine>>,
    home: &StateHome,
    config: ServeConfig,
    shutdown: Arc<AtomicBool>,
) -> std::io::Result<()> {
    let sock = home.socket_path();
    let _ = std::fs::remove_file(&sock);
    let listener = UnixListener::bind(&sock)?;
    listener.set_nonblocking(true)?;

    // Write-behind flush thread.
    let flush_engine = Arc::clone(&engine);
    let flush_shutdown = Arc::clone(&shutdown);
    let flush_thread = std::thread::spawn(move || {
        while !flush_shutdown.load(Ordering::SeqCst) {
            std::thread::sleep(config.flush_interval);
            if flush_shutdown.load(Ordering::SeqCst) {
                break;
            }
            if let Ok(mut eng) = flush_engine.lock() {
                let writer = Arc::clone(&eng.writer);
                let _ = eng.state.flush(writer.as_ref());
            }
        }
    });

    while !shutdown.load(Ordering::SeqCst) {
        match listener.accept() {
            Ok((stream, _)) => {
                let eng = Arc::clone(&engine);
                let sd = Arc::clone(&shutdown);
                std::thread::spawn(move || {
                    let _ = handle_conn(stream, eng, sd);
                });
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(25));
            }
            Err(e) => return Err(e),
        }
    }
    let _ = flush_thread.join();
    // Final flush on the way down (a failure is a non-event: the
    // witness records and RAM-to-disk backlog reconcile at boot).
    if let Ok(mut eng) = engine.lock() {
        let writer = Arc::clone(&eng.writer);
        let _ = eng.state.flush(writer.as_ref());
    }
    let _ = std::fs::remove_file(&sock);
    Ok(())
}

fn handle_conn(
    stream: UnixStream,
    engine: Arc<Mutex<Engine>>,
    shutdown: Arc<AtomicBool>,
) -> std::io::Result<()> {
    let mut reader = BufReader::new(stream.try_clone()?);
    let mut writer = stream;
    let mut line = String::new();
    loop {
        line.clear();
        let n = reader.read_line(&mut line)?;
        if n == 0 {
            return Ok(());
        }
        let req: serde_json::Value =
            serde_json::from_str(line.trim()).unwrap_or(serde_json::Value::Null);
        let resp = if req.get("verb").and_then(|v| v.as_str()) == Some("shutdown") {
            shutdown.store(true, Ordering::SeqCst);
            ok(serde_json::json!({"shutdown": true}))
        } else {
            match engine.lock() {
                Ok(mut eng) => eng.handle(&req),
                Err(_) => err("engine lock poisoned"),
            }
        };
        let mut bytes = serde_json::to_vec(&resp).unwrap_or_default();
        bytes.push(b'\n');
        writer.write_all(&bytes)?;
        writer.flush()?;
    }
}

/// Send one request to a serving daemon and read one response.
/// Used by the CLI's socket client and by tests.
pub fn request(home: &StateHome, req: &serde_json::Value) -> std::io::Result<serde_json::Value> {
    let mut stream = UnixStream::connect(home.socket_path())?;
    let mut bytes = serde_json::to_vec(req).unwrap_or_default();
    bytes.push(b'\n');
    stream.write_all(&bytes)?;
    stream.flush()?;
    let mut reader = BufReader::new(stream);
    let mut line = String::new();
    reader.read_line(&mut line)?;
    serde_json::from_str(line.trim())
        .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
}
