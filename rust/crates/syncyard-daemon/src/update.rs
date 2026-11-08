//! Update discipline (spec 038 FR-038-43 / FR-038-44,
//! UPD-01..09).
//!
//! The rules this module makes mechanical:
//! - Artifacts are accepted only when their sha256 matches a
//!   pin read from the off-VM state record; comparison is
//!   mechanical, byte against byte from the source records —
//!   never an eyeballed string (UPD-07, FR-038-28). A mismatch
//!   is refused, and the refusal is high severity.
//! - Rollout is canary-first with a soak; the fleet is
//!   blocked until the canary passes the health gate (UPD-02).
//! - The health gate is identity: the served build stamp, a
//!   capability-probe refresh, and one synthetic job's
//!   PAYLOAD verdict. Liveness alone never satisfies it
//!   (UPD-03, red-team C3).
//! - Mixed-version legality comes from the release's
//!   compatibility matrix; illegal combinations are refused
//!   with a message naming the required version, and a
//!   refusal never corrupts state (UPD-08) — every function
//!   here is pure, so there is no state to corrupt.
//! - Until signing is live, a host with neither signing nor
//!   dual-channel pinning does not auto-update (FR-038-44).
//! - No update interrupts a running job: hosts with running
//!   jobs defer and report the deferral (UPD-06).

use std::collections::BTreeSet;

/// A candidate artifact and the pin it must match. Both
/// halves come from source records (the artifact's computed
/// hash; the off-VM state record's pin), never from rendered
/// strings a human compared by eye.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArtifactPin {
    pub version: String,
    pub sha256: String,
}

/// Why an update step was refused. Every variant is a named,
/// ledgerable refusal.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UpdateRefusal {
    /// sha256 mismatch against the pin (UPD-07).
    TamperedArtifact { expected: String, actual: String },
    /// The artifact's version is not the pinned train.
    VersionNotPinned { pinned: String, offered: String },
    /// The mixed-version pair is illegal; names the required
    /// version (UPD-08).
    MixedVersion { current: String, offered: String, required: String },
    /// The canary has not passed its health gate + soak.
    CanaryGateNotPassed,
    /// A job is running; the update defers (UPD-06).
    DeferredRunningJob,
}

impl UpdateRefusal {
    /// REP-05 severity for reporting this refusal: a tamper
    /// refusal is high severity (UPD-07).
    pub fn severity(&self) -> &'static str {
        match self {
            UpdateRefusal::TamperedArtifact { .. } => "high",
            _ => "medium",
        }
    }
}

/// Verify a candidate artifact against its pin (UPD-07).
/// The comparison is exact and mechanical on both fields.
pub fn verify_artifact(
    artifact: &ArtifactPin,
    pin: &ArtifactPin,
) -> Result<(), UpdateRefusal> {
    if artifact.version != pin.version {
        return Err(UpdateRefusal::VersionNotPinned {
            pinned: pin.version.clone(),
            offered: artifact.version.clone(),
        });
    }
    if artifact.sha256 != pin.sha256 {
        return Err(UpdateRefusal::TamperedArtifact {
            expected: pin.sha256.clone(),
            actual: artifact.sha256.clone(),
        });
    }
    Ok(())
}

/// Is a (current, offered) train pair a legal mixed-version
/// combination? Legal pairs are named in the release's
/// compatibility matrix (same train, or adjacent trains the
/// matrix lists).
pub fn mixed_version_legal(
    matrix: &BTreeSet<(String, String)>,
    current: &str,
    offered: &str,
) -> Result<(), UpdateRefusal> {
    if current == offered
        || matrix.contains(&(current.to_string(), offered.to_string()))
    {
        return Ok(());
    }
    Err(UpdateRefusal::MixedVersion {
        current: current.to_string(),
        offered: offered.to_string(),
        // The version the release train requires the lagging
        // side to reach (via legal steps): the offered train.
        required: offered.to_string(),
    })
}

/// What the health gate reads (UPD-03). `liveness` is carried
/// so the gate can prove it ignores it: a live-but-wrong
/// build fails, by the falsifier in spec 038.
#[derive(Debug, Clone)]
pub struct HealthGateInput {
    /// Build stamp of the thing actually serving.
    pub served_stamp: String,
    /// The stamp the train requires.
    pub expected_stamp: String,
    pub capability_refreshed: bool,
    /// The synthetic job's verdict, from its payload exit
    /// code. `None` = no synthetic verdict yet.
    pub synthetic_payload_verdict: Option<String>,
    pub liveness: bool,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum GateVerdict {
    Pass,
    Fail { reason: String },
}

/// Evaluate the health gate. All three identity legs must
/// hold; liveness is never one of them.
pub fn evaluate_health_gate(input: &HealthGateInput) -> GateVerdict {
    if input.served_stamp != input.expected_stamp {
        return GateVerdict::Fail {
            reason: format!(
                "identity stamp mismatch: serving {}, train requires {}",
                input.served_stamp, input.expected_stamp
            ),
        };
    }
    if !input.capability_refreshed {
        return GateVerdict::Fail {
            reason: "capability probe not refreshed".to_string(),
        };
    }
    match &input.synthetic_payload_verdict {
        Some(v) if v == "success" => GateVerdict::Pass,
        Some(v) => GateVerdict::Fail {
            reason: format!("synthetic job payload verdict is {v}, not success"),
        },
        None => GateVerdict::Fail {
            reason: "no synthetic job payload verdict yet".to_string(),
        },
    }
}

/// Canary state for a staged rollout (UPD-02).
#[derive(Debug, Clone)]
pub struct Rollout {
    pub canary_host: String,
    pub canary_gate: GateVerdict,
    pub canary_passed_at_unix: Option<i64>,
    pub soak_required_s: i64,
}

/// The fleet may roll out only when the canary passed its
/// health gate AND the soak has elapsed since it passed.
pub fn fleet_rollout_allowed(
    rollout: &Rollout,
    now_unix: i64,
) -> Result<(), UpdateRefusal> {
    match (&rollout.canary_gate, rollout.canary_passed_at_unix) {
        (GateVerdict::Pass, Some(passed_at))
            if now_unix.saturating_sub(passed_at) >= rollout.soak_required_s =>
        {
            Ok(())
        }
        _ => Err(UpdateRefusal::CanaryGateNotPassed),
    }
}

/// FR-038-44: until the OV certificate is live, auto-update
/// requires signing or dual-channel pinning; with neither,
/// it is disabled.
pub fn auto_update_enabled(signing_live: bool, pinning_in_force: bool) -> bool {
    signing_live || pinning_in_force
}

/// UPD-06: an update never interrupts a running job.
pub fn check_update_window(running_jobs: usize) -> Result<(), UpdateRefusal> {
    if running_jobs > 0 {
        return Err(UpdateRefusal::DeferredRunningJob);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pin() -> ArtifactPin {
        ArtifactPin {
            version: "0.4.0".into(),
            sha256: "aa".repeat(32),
        }
    }

    // T038-56 upd07-tampered-artifact-refused + T038-30
    // hash-pins-compared-mechanically: an artifact whose
    // hash differs from the pin by one character is refused
    // as tampered (the comparison is mechanical, from the
    // records); a wrong-train artifact is refused by name.
    #[test]
    fn t038_56_upd07_tampered_artifact_refused() {
        assert!(verify_artifact(&pin(), &pin()).is_ok());
        let mut tampered = pin();
        tampered.sha256 = format!("{}b", "aa".repeat(31));
        match verify_artifact(&tampered, &pin()) {
            Err(r @ UpdateRefusal::TamperedArtifact { .. }) => {
                assert_eq!(r.severity(), "high")
            }
            other => panic!("expected tamper refusal, got {other:?}"),
        }
        let mut wrong_train = pin();
        wrong_train.version = "0.5.0".into();
        assert!(matches!(
            verify_artifact(&wrong_train, &pin()),
            Err(UpdateRefusal::VersionNotPinned { .. })
        ));
    }

    // T038-57 upd08-mixed-version-refusal: an illegal pair is
    // refused with the required version named; same-train and
    // matrix-listed pairs pass; the check is pure (a refusal
    // cannot corrupt state because it touches none).
    #[test]
    fn t038_57_upd08_mixed_version_refusal() {
        let mut matrix = BTreeSet::new();
        matrix.insert(("0.3.0".to_string(), "0.4.0".to_string()));
        assert!(mixed_version_legal(&matrix, "0.4.0", "0.4.0").is_ok());
        assert!(mixed_version_legal(&matrix, "0.3.0", "0.4.0").is_ok());
        match mixed_version_legal(&matrix, "0.2.0", "0.4.0") {
            Err(UpdateRefusal::MixedVersion {
                current,
                offered,
                required,
            }) => {
                assert_eq!(current, "0.2.0");
                assert_eq!(offered, "0.4.0");
                assert_eq!(required, "0.4.0", "the refusal names the required version");
            }
            other => panic!("expected mixed-version refusal, got {other:?}"),
        }
    }

    // T038-55 upd03-health-gate-is-identity: liveness alone
    // never passes; the gate needs the served identity, a
    // refreshed capability probe, and a synthetic payload
    // verdict of success.
    #[test]
    fn t038_55_upd03_health_gate_is_identity() {
        let base = HealthGateInput {
            served_stamp: "syncyard 0.4.0".into(),
            expected_stamp: "syncyard 0.4.0".into(),
            capability_refreshed: true,
            synthetic_payload_verdict: Some("success".into()),
            liveness: true,
        };
        assert_eq!(evaluate_health_gate(&base), GateVerdict::Pass);

        // Alive, but serving the old build: fail.
        let mut wrong_build = base.clone();
        wrong_build.served_stamp = "syncyard 0.3.0".into();
        assert!(matches!(
            evaluate_health_gate(&wrong_build),
            GateVerdict::Fail { .. }
        ));
        // Alive and right build, but the synthetic job's
        // payload said failure: fail.
        let mut bad_job = base.clone();
        bad_job.synthetic_payload_verdict = Some("failure".into());
        assert!(matches!(
            evaluate_health_gate(&bad_job),
            GateVerdict::Fail { .. }
        ));
        // Alive, right build, no synthetic verdict yet: fail.
        let mut no_job = base.clone();
        no_job.synthetic_payload_verdict = None;
        no_job.liveness = true;
        assert!(matches!(
            evaluate_health_gate(&no_job),
            GateVerdict::Fail { .. }
        ));
    }

    // T038-54 upd02-canary-gate: the fleet is blocked until
    // the one canary passes the health gate AND soaks.
    #[test]
    fn t038_54_upd02_canary_gate() {
        let mut rollout = Rollout {
            canary_host: "mini-server".into(),
            canary_gate: GateVerdict::Fail {
                reason: "not yet".into(),
            },
            canary_passed_at_unix: None,
            soak_required_s: 3600,
        };
        assert_eq!(
            fleet_rollout_allowed(&rollout, 10_000),
            Err(UpdateRefusal::CanaryGateNotPassed)
        );
        rollout.canary_gate = GateVerdict::Pass;
        rollout.canary_passed_at_unix = Some(10_000);
        // Passed, but the soak has not elapsed.
        assert_eq!(
            fleet_rollout_allowed(&rollout, 10_000 + 3599),
            Err(UpdateRefusal::CanaryGateNotPassed)
        );
        assert!(fleet_rollout_allowed(&rollout, 10_000 + 3600).is_ok());
    }

    // T038-58 unsigned-unpinned-host-no-autoupdate.
    #[test]
    fn t038_58_unsigned_unpinned_host_no_autoupdate() {
        assert!(!auto_update_enabled(false, false));
        assert!(auto_update_enabled(false, true));
        assert!(auto_update_enabled(true, false));
    }

    #[test]
    fn upd06_running_job_defers() {
        assert_eq!(
            check_update_window(1),
            Err(UpdateRefusal::DeferredRunningJob)
        );
        assert!(check_update_window(0).is_ok());
    }
}
