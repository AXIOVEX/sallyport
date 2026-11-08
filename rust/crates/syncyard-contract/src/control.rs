//! Tray job-control command files (spec 037).
//!
//! A control action in the successor app is a **signed command
//! file**: a JSON record carrying command id, target, action,
//! issuer host and identity, a single-use nonce, an issued-at
//! timestamp, and an expiry. The file travels through the
//! backchannel to the gatekeeper host, which verifies the
//! signature against the **per-host public key** in its config
//! before acting (repository write access alone never
//! authenticates a command, red-team H6). Execution and refusal
//! both ledger as operator acts on the gatekeeper side; the
//! [`CommandOutcome`] the gatekeeper records is the only
//! confirmation the app may render (spec 037 sections 2-3).
//!
//! The action set is a closed enum (FR-037-08): cancel, hold,
//! release-hold, reprioritize, lift. Content edits, record
//! deletion, and verdict changes are not expressible — an
//! unknown action string fails deserialization, so no file can
//! smuggle one in.
//!
//! Signatures are Ed25519. The signed bytes are the command's
//! own JSON with the `signature` field emptied, serialized from
//! this struct (field order is fixed by the struct definition,
//! so signer and verifier always agree byte-for-byte).

use serde::{Deserialize, Serialize};

use ed25519_dalek::{Signature, Signer, SigningKey, Verifier, VerifyingKey};

use crate::isotime;

/// Default command expiry: 10 minutes (spec 037 section 1).
pub const DEFAULT_EXPIRY_S: i64 = 600;
/// Maximum command expiry: 60 minutes (spec 037 section 1).
pub const MAX_EXPIRY_S: i64 = 3600;

/// The closed set of tray job-control actions (FR-037-08).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ControlAction {
    /// Cancel a waiting or running job (gatekeeper cancel path).
    Cancel,
    /// Hold a waiting job.
    Hold,
    /// Release a waiting job's hold.
    ReleaseHold,
    /// Reprioritize within the waiting queue.
    Reprioritize,
    /// Lift (priority dispatch) a waiting job.
    Lift,
}

impl ControlAction {
    /// The wire string (matches the serde form).
    pub fn as_str(&self) -> &'static str {
        match self {
            ControlAction::Cancel => "cancel",
            ControlAction::Hold => "hold",
            ControlAction::ReleaseHold => "release-hold",
            ControlAction::Reprioritize => "reprioritize",
            ControlAction::Lift => "lift",
        }
    }

    /// Every action in the closed set (audits iterate this).
    pub const ALL: [ControlAction; 5] = [
        ControlAction::Cancel,
        ControlAction::Hold,
        ControlAction::ReleaseHold,
        ControlAction::Reprioritize,
        ControlAction::Lift,
    ];
}

/// One signed command file (spec 037 section 1).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ControlCommand {
    /// Unique command id (issuer-chosen).
    pub command_id: String,
    /// The act requested (closed set).
    pub action: ControlAction,
    /// Target job id (job-scoped acts).
    pub job_id: Option<String>,
    /// Target lane, where the act is not job-scoped.
    pub lane: Option<String>,
    /// Target host, where the act is not job-scoped.
    pub host: Option<String>,
    /// New priority (reprioritize only).
    pub new_priority: Option<i64>,
    /// Host that issued the command; the gatekeeper's enrolled
    /// public key is looked up under this name.
    pub issuer_host: String,
    /// Issuing identity on that host (the app's identity string).
    pub issuer_identity: String,
    /// Single-use nonce (FR-037-04).
    pub nonce: String,
    /// ISO-8601 UTC issue time.
    pub issued_at: String,
    /// ISO-8601 UTC expiry time.
    pub expires_at: String,
    /// The issuing app's train (version) — UPD-08 surface data.
    pub app_train: String,
    /// The issuing app's contract version — UPD-08 surface data.
    pub contract_version: u32,
    /// Ed25519 signature over [`ControlCommand::signing_bytes`],
    /// hex-encoded; empty while unsigned.
    pub signature: String,
}

impl ControlCommand {
    /// The bytes the signature covers: this command serialized
    /// with the signature field emptied.
    pub fn signing_bytes(&self) -> Vec<u8> {
        let mut unsigned = self.clone();
        unsigned.signature = String::new();
        serde_json::to_vec(&unsigned).unwrap_or_default()
    }

    /// Issue time as Unix seconds (`None` if unparseable).
    pub fn issued_unix(&self) -> Option<i64> {
        isotime::unix_from_iso(&self.issued_at)
    }

    /// Expiry time as Unix seconds (`None` if unparseable).
    pub fn expires_unix(&self) -> Option<i64> {
        isotime::unix_from_iso(&self.expires_at)
    }

    /// The declared expiry window, expires − issued.
    pub fn expiry_window_s(&self) -> Option<i64> {
        match (self.issued_unix(), self.expires_unix()) {
            (Some(i), Some(e)) => Some(e - i),
            _ => None,
        }
    }

    /// Age of the command at `now_unix_s` (issued → now).
    pub fn age_s(&self, now_unix_s: i64) -> Option<i64> {
        self.issued_unix().map(|i| now_unix_s - i)
    }

    /// True once `now_unix_s` is past the expiry. An unparseable
    /// timestamp counts as expired: a command whose times cannot
    /// be read is never executed (spec 037 falsifier: a command
    /// file written yesterday must not execute today).
    pub fn is_expired(&self, now_unix_s: i64) -> bool {
        match self.expires_unix() {
            Some(e) => now_unix_s > e,
            None => true,
        }
    }

    /// Sign in place with the issuer host's secret key.
    pub fn sign(&mut self, key: &HostSecretKey) {
        let sig = key.signing.sign(&self.signing_bytes());
        self.signature = to_hex(&sig.to_bytes());
    }

    /// Verify the signature against an enrolled public key.
    pub fn verify(&self, key: &HostPublicKey) -> bool {
        let Some(sig_bytes) = from_hex(&self.signature) else {
            return false;
        };
        let Ok(sig_array) = <[u8; 64]>::try_from(sig_bytes.as_slice()) else {
            return false;
        };
        let sig = Signature::from_bytes(&sig_array);
        key.verifying
            .verify(&self.signing_bytes(), &sig)
            .is_ok()
    }
}

/// Whether a command was executed or refused (spec 037 section 2).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum OutcomeStatus {
    Executed,
    Refused,
}

/// The gatekeeper's recorded decision on one command — the
/// published state the app reads its confirmation from
/// (spec 037 section 3). An app renders a command as done only
/// when an `Executed` outcome for its id exists here.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CommandOutcome {
    pub command_id: String,
    /// The action as written in the command (wire string).
    pub action: String,
    pub issuer_host: String,
    pub job_id: Option<String>,
    pub status: OutcomeStatus,
    /// Refusal reason (`None` when executed).
    pub reason: Option<String>,
    /// The `operator_*` ledger event the act or refusal wrote.
    pub ledger_event: Option<String>,
    /// ISO-8601 UTC decision time.
    pub decided_at: String,
}

/// A host's secret signing key (the tray side of the per-host
/// key pair). The gatekeeper holds only [`HostPublicKey`].
#[derive(Debug)]
pub struct HostSecretKey {
    signing: SigningKey,
}

impl HostSecretKey {
    /// Generate a fresh random key (enrollment).
    pub fn generate() -> Self {
        HostSecretKey {
            signing: SigningKey::generate(&mut rand_core::OsRng),
        }
    }

    /// Derive the key from a 32-byte seed (tests, provisioning).
    pub fn from_seed(seed: [u8; 32]) -> Self {
        HostSecretKey {
            signing: SigningKey::from_bytes(&seed),
        }
    }

    /// Load from the hex form written by [`HostSecretKey::to_hex`].
    pub fn from_hex(s: &str) -> Option<Self> {
        let bytes = from_hex(s)?;
        let seed = <[u8; 32]>::try_from(bytes.as_slice()).ok()?;
        Some(Self::from_seed(seed))
    }

    /// The secret seed as hex (key-file storage only).
    pub fn to_hex(&self) -> String {
        to_hex(&self.signing.to_bytes())
    }

    /// The public half (enrolled at the gatekeeper).
    pub fn public_key(&self) -> HostPublicKey {
        HostPublicKey {
            verifying: self.signing.verifying_key(),
        }
    }
}

/// A host's public key, as enrolled in the gatekeeper's config.
#[derive(Debug, Clone)]
pub struct HostPublicKey {
    verifying: VerifyingKey,
}

impl HostPublicKey {
    /// Parse the hex form used in the gatekeeper's config.
    pub fn from_hex(s: &str) -> Option<Self> {
        let bytes = from_hex(s)?;
        let array = <[u8; 32]>::try_from(bytes.as_slice()).ok()?;
        VerifyingKey::from_bytes(&array)
            .ok()
            .map(|verifying| HostPublicKey { verifying })
    }

    /// The hex form for config files.
    pub fn to_hex(&self) -> String {
        to_hex(&self.verifying.to_bytes())
    }
}

/// Lowercase hex encode.
pub fn to_hex(bytes: &[u8]) -> String {
    let mut s = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        s.push_str(&format!("{b:02x}"));
    }
    s
}

/// Hex decode; `None` on any malformed input.
pub fn from_hex(s: &str) -> Option<Vec<u8>> {
    let s = s.trim();
    if !s.len().is_multiple_of(2) {
        return None;
    }
    let mut out = Vec::with_capacity(s.len() / 2);
    let bytes = s.as_bytes();
    for pair in bytes.chunks_exact(2) {
        let hi = (pair[0] as char).to_digit(16)?;
        let lo = (pair[1] as char).to_digit(16)?;
        out.push((hi * 16 + lo) as u8);
    }
    Some(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_command(key: &HostSecretKey) -> ControlCommand {
        let mut cmd = ControlCommand {
            command_id: "cmd-test-1".to_string(),
            action: ControlAction::Cancel,
            job_id: Some("job-1".to_string()),
            lane: None,
            host: None,
            new_priority: None,
            issuer_host: "tower-compute".to_string(),
            issuer_identity: "syncyard-tray 0.4.0".to_string(),
            nonce: "nonce-abc".to_string(),
            issued_at: "2026-11-08T10:00:00Z".to_string(),
            expires_at: "2026-11-08T10:10:00Z".to_string(),
            app_train: "0.4.0".to_string(),
            contract_version: crate::CONTRACT_VERSION,
            signature: String::new(),
        };
        cmd.sign(key);
        cmd
    }

    // T037-03 (signature half): verification is key-bound.
    #[test]
    fn sign_verify_round_trip_and_tamper_evidence() {
        let key = HostSecretKey::from_seed([7u8; 32]);
        let cmd = sample_command(&key);
        assert!(cmd.verify(&key.public_key()));
        // Any field change breaks the signature.
        let mut tampered = cmd.clone();
        tampered.job_id = Some("job-2".to_string());
        assert!(!tampered.verify(&key.public_key()));
        // A different host's key does not verify.
        let other = HostSecretKey::from_seed([9u8; 32]);
        assert!(!cmd.verify(&other.public_key()));
        // Unsigned or malformed signatures never verify.
        let mut unsigned = cmd.clone();
        unsigned.signature = String::new();
        assert!(!unsigned.verify(&key.public_key()));
        let mut junk = cmd.clone();
        junk.signature = "zz".to_string();
        assert!(!junk.verify(&key.public_key()));
        // Key hex round trips (key file + config shapes).
        let secret2 = HostSecretKey::from_hex(&key.to_hex()).unwrap();
        assert_eq!(secret2.public_key().to_hex(), key.public_key().to_hex());
        let public2 = HostPublicKey::from_hex(&key.public_key().to_hex()).unwrap();
        assert!(cmd.verify(&public2));
    }

    // T037-08 act-set-is-gatekeeper-only: the action set is
    // closed — acts outside it are not expressible in the
    // schema at all.
    #[test]
    fn action_set_is_closed() {
        for banned in [
            "delete-record",
            "edit-script",
            "set-verdict",
            "purge",
            "dispatch",
            "restart-host",
        ] {
            let parsed: Result<ControlAction, _> =
                serde_json::from_value(serde_json::Value::String(banned.to_string()));
            assert!(parsed.is_err(), "{banned} must not be an action");
        }
        assert_eq!(ControlAction::ALL.len(), 5);
        for a in ControlAction::ALL {
            let v = serde_json::to_value(a).unwrap();
            assert_eq!(v, serde_json::Value::String(a.as_str().to_string()));
        }
    }

    // T037-02 (expiry half) + T037-05 (age math half).
    #[test]
    fn expiry_window_and_age_math() {
        let key = HostSecretKey::from_seed([3u8; 32]);
        let cmd = sample_command(&key);
        assert_eq!(cmd.expiry_window_s(), Some(DEFAULT_EXPIRY_S));
        let issued = cmd.issued_unix().unwrap();
        assert_eq!(cmd.age_s(issued + 42), Some(42));
        assert!(!cmd.is_expired(issued + DEFAULT_EXPIRY_S));
        assert!(cmd.is_expired(issued + DEFAULT_EXPIRY_S + 1));
        // Unparseable times are expired, never executable.
        let mut broken = cmd.clone();
        broken.expires_at = "yesterday".to_string();
        assert!(broken.is_expired(issued));
    }
}
