//! Spec 037 job control, daemon side: verification and
//! execution of signed tray command files, plus the FIX-08
//! repair kill switch.
//!
//! The gatekeeper is the only actor here. A command file that
//! merely exists changes nothing (spec 037 section 2): this
//! module authenticates it against the **enrolled per-host
//! public key** (config, below), checks the version combination
//! (UPD-08), the expiry window, and the single-use nonce — and
//! only then performs the act through the same paths the CLI
//! verbs use. Every execution ledgers an `operator_*` event
//! naming the command id and issuing host; every refusal
//! ledgers `operator_command_refused` with its reason. The
//! [`CommandOutcome`] is recorded in control state and served
//! back; it is the only confirmation an app may render.
//!
//! Verification order is deliberate: authentication and
//! legality (signature, version, expiry shape) run **before**
//! the nonce is burned, so a forged or malformed file cannot
//! deny service to the legitimate command carrying the same
//! nonce. Target-level refusals (unknown job, wrong state)
//! happen after the burn: they are authenticated requests and
//! refusing them without burning would invite probing loops.
//!
//! Enrollment: `host_keys` maps issuer host name to its Ed25519
//! public key (hex). It is part of control state
//! (`control.json` in the state home) and is provisioned by the
//! operator writing that file; the daemon never invents a key.

use std::collections::BTreeMap;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use syncyard_contract::{
    CommandOutcome, ControlAction, ControlCommand, HostPublicKey, OutcomeStatus,
    COMMAND_MAX_EXPIRY_S, CONTRACT_VERSION,
};

use crate::dispatch::DispatchError;
use crate::serve::Engine;
use crate::state::DaemonState;
use crate::time::{iso_from_unix, now_iso, now_unix};

/// Outcome history kept in control state (oldest dropped).
pub const OUTCOME_CAP: usize = 256;
/// Nonce history kept in control state (oldest dropped).
pub const NONCE_CAP: usize = 4096;

/// Durable control-plane state for spec 037 + FIX-08.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ControlState {
    /// Enrolled tray host public keys: issuer host -> public key hex.
    #[serde(default)]
    pub host_keys: BTreeMap<String, String>,
    /// FIX-08 repair kill switch: while set, all self-healing
    /// is halted (job execution is not). Stays set until an
    /// explicit clear act.
    #[serde(default)]
    pub kill_switch: bool,
    /// Single-use nonces already burned (FR-037-04).
    #[serde(default)]
    pub seen_nonces: Vec<String>,
    /// Recorded command outcomes, oldest first.
    #[serde(default)]
    pub outcomes: Vec<CommandOutcome>,
}

impl ControlState {
    fn nonce_seen(&self, nonce: &str) -> bool {
        self.seen_nonces.iter().any(|n| n == nonce)
    }

    fn burn_nonce(&mut self, nonce: &str) {
        self.seen_nonces.push(nonce.to_string());
        if self.seen_nonces.len() > NONCE_CAP {
            let excess = self.seen_nonces.len() - NONCE_CAP;
            self.seen_nonces.drain(0..excess);
        }
    }

    fn record_outcome(&mut self, outcome: CommandOutcome) {
        self.outcomes.push(outcome);
        if self.outcomes.len() > OUTCOME_CAP {
            let excess = self.outcomes.len() - OUTCOME_CAP;
            self.outcomes.drain(0..excess);
        }
    }

    /// The recorded outcome for one command id, if decided.
    pub fn outcome_for(&self, command_id: &str) -> Option<&CommandOutcome> {
        self.outcomes
            .iter()
            .rev()
            .find(|o| o.command_id == command_id)
    }
}

impl DaemonState {
    /// Whether the born-denied cure's self-restart may be acted
    /// on. The FIX-08 kill switch halts self-healing outright
    /// and does **not** spend the cure's one restart: while the
    /// switch is set no decision is consumed, so clearing the
    /// switch resumes the normal rule.
    pub fn repair_restart_decision(&mut self, still_failing: bool) -> bool {
        if self.control_load_failed() {
            // The kill switch state is unknown; fail closed and
            // do not spend the cure's one restart.
            return false;
        }
        if self.control.kill_switch {
            return false;
        }
        self.cure.should_self_restart(still_failing)
    }
}

/// Ledger fields every control act shares.
fn command_fields(cmd: &ControlCommand) -> BTreeMap<String, Value> {
    let mut fields = BTreeMap::new();
    fields.insert("command_id".into(), json!(cmd.command_id));
    fields.insert("issuer_host".into(), json!(cmd.issuer_host));
    fields.insert("issuer_identity".into(), json!(cmd.issuer_identity));
    fields.insert("issuer".into(), json!("tray-app"));
    fields.insert("action".into(), json!(cmd.action.as_str()));
    if let Some(j) = &cmd.job_id {
        fields.insert("job_id".into(), json!(j));
    }
    fields
}

impl Engine {
    /// The gatekeeper's normal cancel path (FR-037-09), shared
    /// by the `cancel` verb and by control commands so both
    /// ledger the same `operator_cancel` event. Returns whether
    /// the job exists.
    pub fn apply_cancel(&mut self, id: &str, extra_fields: BTreeMap<String, Value>) -> bool {
        let changed = self.state.update_job(id, |rec| {
            if !rec.state.is_terminal() {
                rec.state = syncyard_contract::JobState::Cancelled;
                rec.completed_at = Some(now_iso());
            }
        });
        if changed {
            let mut fields = BTreeMap::new();
            fields.insert("job_id".into(), json!(id));
            fields.extend(extra_fields);
            self.state.ledger("operator_cancel", fields);
        }
        changed
    }

    /// Verify and (when legal) execute one signed command file.
    /// Always produces and records a [`CommandOutcome`].
    pub fn execute_control_command(&mut self, cmd: &ControlCommand) -> CommandOutcome {
        if self.state.control_load_failed() {
            // Fail closed without recording or touching control
            // state: the corrupt file's kill switch, nonces, and
            // outcomes are unknown and must not be reset.
            return CommandOutcome {
                command_id: cmd.command_id.clone(),
                action: cmd.action.as_str().to_string(),
                issuer_host: cmd.issuer_host.clone(),
                job_id: cmd.job_id.clone(),
                status: OutcomeStatus::Refused,
                reason: Some(
                    "control.json is corrupt or unreadable; control commands are refused \
                     until the file is repaired"
                        .to_string(),
                ),
                ledger_event: None,
                decided_at: iso_from_unix(now_unix().max(0) as u64),
            };
        }
        let now = now_unix();
        // 1. Enrolled issuer host with a parseable key.
        let public = match self.state.control.host_keys.get(&cmd.issuer_host) {
            Some(hex) => match HostPublicKey::from_hex(hex) {
                Some(k) => k,
                None => {
                    return self.refuse(
                        cmd,
                        format!(
                            "enrolled key for issuer host {} is unparseable",
                            cmd.issuer_host
                        ),
                    )
                }
            },
            None => {
                return self.refuse(
                    cmd,
                    format!(
                        "unknown issuer host {}: no enrolled public key",
                        cmd.issuer_host
                    ),
                )
            }
        };
        // 2. Signature. Repository write access is not identity:
        // only this check authenticates the command.
        if !cmd.verify(&public) {
            return self.refuse(cmd, "signature verification failed".to_string());
        }
        // 3. Version legality (UPD-08): refuse naming the
        // required version; never corrupt state over it.
        if cmd.contract_version != CONTRACT_VERSION {
            return self.refuse(
                cmd,
                format!(
                    "illegal version combination: app train {} (contract {}) cannot command \
                     gatekeeper train {} (contract {}); requires an app on contract {}",
                    cmd.app_train,
                    cmd.contract_version,
                    env!("CARGO_PKG_VERSION"),
                    CONTRACT_VERSION,
                    CONTRACT_VERSION
                ),
            );
        }
        // 4. Expiry shape: window must exist, be positive, and
        // stay within the spec's maximum.
        let window = match cmd.expiry_window_s() {
            Some(w) => w,
            None => return self.refuse(cmd, "command timestamps are unparseable".to_string()),
        };
        if window <= 0 {
            return self.refuse(cmd, "expiry is not after the issue time".to_string());
        }
        if window > COMMAND_MAX_EXPIRY_S {
            return self.refuse(
                cmd,
                format!("expiry window {window}s exceeds the {COMMAND_MAX_EXPIRY_S}s maximum"),
            );
        }
        // 5. Not yet expired at execution time.
        if cmd.is_expired(now) {
            let age = cmd.age_s(now).unwrap_or(-1);
            return self.refuse(
                cmd,
                format!("expired: command age {age}s is past its expiry; not executed"),
            );
        }
        // 6. Single-use nonce (burned only now that the command
        // is authenticated and in-window).
        if self.state.control.nonce_seen(&cmd.nonce) {
            return self.refuse(
                cmd,
                "nonce replay: this nonce was already used; refused".to_string(),
            );
        }
        self.state.control.burn_nonce(&cmd.nonce);
        self.state.touch_control();

        // 7. The act itself, through the normal paths.
        let job_id = match &cmd.job_id {
            Some(j) => j.clone(),
            None => {
                return self.refuse(
                    cmd,
                    format!("action {} requires a job target", cmd.action.as_str()),
                )
            }
        };
        let rec = match self.state.jobs.get(&job_id) {
            Some(r) => r.clone(),
            None => return self.refuse(cmd, format!("unknown job {job_id}")),
        };
        let fields = command_fields(cmd);
        let ledger_event: &str = match cmd.action {
            ControlAction::Cancel => {
                if rec.state.is_terminal() {
                    return self.refuse(
                        cmd,
                        format!("job {job_id} is already {} (terminal)", rec.state.as_str()),
                    );
                }
                self.apply_cancel(&job_id, fields);
                "operator_cancel"
            }
            ControlAction::Hold => {
                if rec.state != syncyard_contract::JobState::Submitted {
                    return self.refuse(
                        cmd,
                        format!(
                            "hold applies to waiting jobs; job {job_id} is {}",
                            rec.state.as_str()
                        ),
                    );
                }
                if self.state.holds.contains(&job_id) {
                    return self.refuse(cmd, format!("job {job_id} is already held"));
                }
                self.state.set_hold(&job_id, true);
                self.state.ledger("operator_hold", fields);
                "operator_hold"
            }
            ControlAction::ReleaseHold => {
                if !self.state.holds.contains(&job_id) {
                    return self.refuse(cmd, format!("job {job_id} is not held"));
                }
                self.state.set_hold(&job_id, false);
                self.state.ledger("operator_hold_released", fields);
                "operator_hold_released"
            }
            ControlAction::Reprioritize => {
                if rec.state != syncyard_contract::JobState::Submitted {
                    return self.refuse(
                        cmd,
                        format!(
                            "reprioritize applies to waiting jobs; job {job_id} is {}",
                            rec.state.as_str()
                        ),
                    );
                }
                let new_priority = match cmd.new_priority {
                    Some(p) => p,
                    None => {
                        return self.refuse(cmd, "reprioritize requires new_priority".to_string())
                    }
                };
                let mut f = fields;
                f.insert("new_priority".into(), json!(new_priority));
                f.insert("old_priority".into(), json!(rec.priority));
                self.state.update_job(&job_id, |r| {
                    r.priority = new_priority;
                    r.priority_source = "human".to_string();
                });
                self.state.ledger("operator_reprioritize", f);
                "operator_reprioritize"
            }
            ControlAction::Lift => {
                if rec.state != syncyard_contract::JobState::Submitted {
                    return self.refuse(
                        cmd,
                        format!(
                            "lift applies to waiting jobs; job {job_id} is {}",
                            rec.state.as_str()
                        ),
                    );
                }
                match crate::dispatch::dispatch_one(
                    &mut self.state,
                    self.store.as_ref(),
                    &self.token,
                    self.dispatcher.as_ref(),
                    &job_id,
                ) {
                    Ok(run_ref) => {
                        let mut f = fields;
                        f.insert("run_id".into(), json!(run_ref.run_id));
                        self.state.ledger("operator_lift", f);
                        "operator_lift"
                    }
                    Err(DispatchError::NotDispatchable(msg)) => return self.refuse(cmd, msg),
                    Err(e) => return self.refuse(cmd, format!("lift failed: {e}")),
                }
            }
        };
        let outcome = CommandOutcome {
            command_id: cmd.command_id.clone(),
            action: cmd.action.as_str().to_string(),
            issuer_host: cmd.issuer_host.clone(),
            job_id: cmd.job_id.clone(),
            status: OutcomeStatus::Executed,
            reason: None,
            ledger_event: Some(ledger_event.to_string()),
            decided_at: iso_from_unix(now.max(0) as u64),
        };
        self.state.control.record_outcome(outcome.clone());
        self.state.touch_control();
        outcome
    }

    /// Record a refusal: ledgered with its reason, outcome kept.
    fn refuse(&mut self, cmd: &ControlCommand, reason: String) -> CommandOutcome {
        let mut fields = command_fields(cmd);
        fields.insert("reason".into(), json!(reason));
        self.state.ledger("operator_command_refused", fields);
        let outcome = CommandOutcome {
            command_id: cmd.command_id.clone(),
            action: cmd.action.as_str().to_string(),
            issuer_host: cmd.issuer_host.clone(),
            job_id: cmd.job_id.clone(),
            status: OutcomeStatus::Refused,
            reason: Some(reason),
            ledger_event: Some("operator_command_refused".to_string()),
            decided_at: iso_from_unix(now_unix().max(0) as u64),
        };
        self.state.control.record_outcome(outcome.clone());
        self.state.touch_control();
        outcome
    }

    /// Set or clear the FIX-08 kill switch as an operator act,
    /// ledgered on every change (FR-037-10). `source` names the
    /// surface that asked ("tray-app", "cli", ...).
    pub fn set_kill_switch(&mut self, on: bool, source: &str) -> bool {
        if self.state.control_load_failed() {
            return false;
        }
        let changed = self.state.control.kill_switch != on;
        self.state.control.kill_switch = on;
        let mut fields = BTreeMap::new();
        fields.insert("source".into(), json!(source));
        fields.insert("kill_switch".into(), json!(on));
        self.state.ledger(
            if on {
                "operator_kill_switch_set"
            } else {
                "operator_kill_switch_cleared"
            },
            fields,
        );
        self.state.touch_control();
        changed
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn control_state_bounds_its_histories() {
        let mut cs = ControlState::default();
        for i in 0..(NONCE_CAP + 10) {
            cs.burn_nonce(&format!("n{i}"));
        }
        assert_eq!(cs.seen_nonces.len(), NONCE_CAP);
        assert!(!cs.nonce_seen("n0"));
        assert!(cs.nonce_seen(&format!("n{}", NONCE_CAP + 9)));
        for i in 0..(OUTCOME_CAP + 5) {
            cs.record_outcome(CommandOutcome {
                command_id: format!("c{i}"),
                action: "hold".into(),
                issuer_host: "h".into(),
                job_id: None,
                status: OutcomeStatus::Executed,
                reason: None,
                ledger_event: None,
                decided_at: "2026-11-08T00:00:00Z".into(),
            });
        }
        assert_eq!(cs.outcomes.len(), OUTCOME_CAP);
    }
}
