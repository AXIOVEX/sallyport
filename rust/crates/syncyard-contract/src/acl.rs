//! The tailnet ACL matrix as a versioned artifact, its
//! automated deny-case probes, and the backup-key placement
//! rule (spec 038 FR-038-30, red-team M5).
//!
//! The matrix is data under version control. Probes run from
//! each node class and their results are audited against the
//! matrix mechanically: a deny case observed reachable is a
//! violation, named by its (from, to, port) triple. Live
//! probing happens on the fleet; the audit logic — the part
//! that must never drift from the matrix — lives here and is
//! fixture-proven.

use serde::{Deserialize, Serialize};

/// Matrix version; bumped on any rule change, carried in
/// every audit result.
pub const ACL_MATRIX_VERSION: u32 = 1;

/// One matrix rule. Deny is explicit, never the absence of an
/// allow: an unlisted triple is `Unlisted` at audit time, so
/// a new service cannot silently inherit access.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct AclRule {
    pub from: &'static str,
    pub to: &'static str,
    pub port: u16,
    pub allow: bool,
}

/// The fleet ACL matrix (v1). Node classes are the ratified
/// fleet names plus the operator class. The deny rows are the
/// cases the probes exist to prove: opportunistic and
/// inference nodes never reach control-plane state, and no
/// fleet node reaches another node's agent control socket.
pub const ACL_MATRIX: &[AclRule] = &[
    // The gatekeeper host reaches every node's agent port.
    AclRule { from: "mini-server", to: "tower-compute", port: 8722, allow: true },
    AclRule { from: "mini-server", to: "laptop-bench", port: 8722, allow: true },
    AclRule { from: "mini-server", to: "air-inference", port: 8722, allow: true },
    // Deny cases (the probes' reason to exist):
    AclRule { from: "air-inference", to: "mini-server", port: 8722, allow: false },
    AclRule { from: "air-inference", to: "tower-compute", port: 8722, allow: false },
    AclRule { from: "sbc-inference", to: "mini-server", port: 8722, allow: false },
    AclRule { from: "tower-offsite", to: "laptop-bench", port: 8722, allow: false },
    AclRule { from: "laptop-bench", to: "tower-compute", port: 8722, allow: false },
];

/// One probe observation: from a node of class `from`, was
/// `to:port` reachable?
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ProbeResult {
    pub from: String,
    pub to: String,
    pub port: u16,
    pub reachable: bool,
}

/// An audit finding. Deny breaches and unlisted observations
/// are violations; a listed allow that probes unreachable is
/// an availability finding, reported separately so it can
/// never masquerade as a security pass or failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AclFinding {
    /// A deny case was observed reachable.
    DenyBreached { from: String, to: String, port: u16 },
    /// A probe covered a triple the matrix does not list.
    Unlisted { from: String, to: String, port: u16 },
    /// An allowed path probed unreachable (availability).
    AllowUnreachable { from: String, to: String, port: u16 },
    /// A matrix rule no probe covered (the audit is only as
    /// good as its coverage; silence is a finding).
    Unprobed { from: String, to: String, port: u16 },
}

/// Audit probe results against the matrix. Every matrix rule
/// must be probed; every probe must be listed.
pub fn audit_probes(matrix: &[AclRule], probes: &[ProbeResult]) -> Vec<AclFinding> {
    let mut out = Vec::new();
    for rule in matrix {
        match probes.iter().find(|p| {
            p.from.as_str() == rule.from && p.to.as_str() == rule.to && p.port == rule.port
        }) {
            None => out.push(AclFinding::Unprobed {
                from: rule.from.to_string(),
                to: rule.to.to_string(),
                port: rule.port,
            }),
            Some(p) => {
                if !rule.allow && p.reachable {
                    out.push(AclFinding::DenyBreached {
                        from: rule.from.to_string(),
                        to: rule.to.to_string(),
                        port: rule.port,
                    });
                } else if rule.allow && !p.reachable {
                    out.push(AclFinding::AllowUnreachable {
                        from: rule.from.to_string(),
                        to: rule.to.to_string(),
                        port: rule.port,
                    });
                }
            }
        }
    }
    for p in probes {
        if !matrix
            .iter()
            .any(|r| r.from == p.from.as_str() && r.to == p.to.as_str() && r.port == p.port)
        {
            out.push(AclFinding::Unlisted {
                from: p.from.clone(),
                to: p.to.clone(),
                port: p.port,
            });
        }
    }
    out
}

/// Backup encryption keys are held OFF the backup target
/// (red-team M5): a key stored on the machine it protects is
/// lost with it. `key_locations` are host names.
pub fn check_backup_key_placement(
    backup_target_host: &str,
    key_locations: &[String],
) -> Result<(), String> {
    if key_locations.is_empty() {
        return Err("backup_key_unlocated: no key location is recorded".to_string());
    }
    if key_locations.iter().any(|h| h == backup_target_host) {
        return Err(format!(
            "backup_key_on_target: a backup encryption key is stored on its own target {backup_target_host}"
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn agreeing_probes() -> Vec<ProbeResult> {
        ACL_MATRIX
            .iter()
            .map(|r| ProbeResult {
                from: r.from.to_string(),
                to: r.to.to_string(),
                port: r.port,
                reachable: r.allow,
            })
            .collect()
    }

    // T038-32 acl-deny-probes: probes that agree with the
    // matrix audit clean; a deny case observed reachable is
    // one named violation; unprobed rules and unlisted probes
    // are findings, never silence.
    #[test]
    fn t038_32_acl_deny_probes() {
        assert_eq!(audit_probes(ACL_MATRIX, &agreeing_probes()), vec![]);

        let mut breached = agreeing_probes();
        let p = breached
            .iter_mut()
            .find(|p| p.from == "air-inference" && p.to == "mini-server")
            .unwrap();
        p.reachable = true;
        assert_eq!(
            audit_probes(ACL_MATRIX, &breached),
            vec![AclFinding::DenyBreached {
                from: "air-inference".into(),
                to: "mini-server".into(),
                port: 8722
            }]
        );

        let mut short = agreeing_probes();
        short.pop();
        assert!(matches!(
            audit_probes(ACL_MATRIX, &short)[0],
            AclFinding::Unprobed { .. }
        ));

        let mut extra = agreeing_probes();
        extra.push(ProbeResult {
            from: "air-inference".into(),
            to: "sbc-inference".into(),
            port: 22,
            reachable: false,
        });
        assert!(audit_probes(ACL_MATRIX, &extra)
            .iter()
            .any(|f| matches!(f, AclFinding::Unlisted { .. })));
    }

    // T038-33 backup-keys-off-target.
    #[test]
    fn t038_33_backup_keys_off_target() {
        assert!(check_backup_key_placement("mini-server", &["tower-compute".into()]).is_ok());
        let err = check_backup_key_placement(
            "mini-server",
            &["tower-compute".into(), "mini-server".into()],
        )
        .unwrap_err();
        assert!(err.starts_with("backup_key_on_target"), "{err}");
        assert!(check_backup_key_placement("mini-server", &[]).is_err());
    }
}
