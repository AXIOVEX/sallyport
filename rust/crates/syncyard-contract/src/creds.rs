//! Per-function credentials and the token-scope audit
//! (spec 038 FR-038-40, red-team C5; spec 036 FR-036-12).
//!
//! Dispatch, state-branch write, status publish, and issue
//! reporting use SEPARATE credentials, each least-privilege
//! for its one function. The audit is mechanical over the
//! declared scope sets: it runs in the release suite against
//! the fleet's credential inventory record, and its rules are
//! fixture-proven here.

use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// The four credentialed functions. A fifth function means a
/// fifth credential, never a wider scope on an existing one.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum CredRole {
    Dispatch,
    StateWrite,
    Publish,
    Report,
}

/// One credential's declared scopes. Scope strings are the
/// provider's own names (GitHub App / token permission
/// names), recorded verbatim in the inventory.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Credential {
    pub name: String,
    pub role: CredRole,
    pub scopes: BTreeSet<String>,
}

/// A scope-audit violation, naming the credential and the
/// rule it broke.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ScopeViolation {
    pub credential: String,
    pub rule: &'static str,
}

/// The full scope vocabulary the audit reasons about. Any
/// scope outside the role's allowed set is a violation, so a
/// quietly widened token fails the audit by construction.
fn allowed_scopes(role: CredRole) -> &'static [&'static str] {
    match role {
        // Trigger and observe door-workflow runs; nothing else.
        CredRole::Dispatch => &["actions:write", "actions:read"],
        // The off-VM state branch only (contents on one repo,
        // recorded in the inventory name).
        CredRole::StateWrite => &["contents:write", "contents:read"],
        // Status publishing to the backchannel repo only.
        CredRole::Publish => &["contents:write", "contents:read"],
        // REP-07: create and comment on issues, and NOTHING
        // else.
        CredRole::Report => &["issues:write"],
    }
}

/// Audit a credential inventory. Rules:
/// 1. No wildcard scope anywhere.
/// 2. Every scope must belong to the credential's role set.
/// 3. The reporting credential cannot dispatch (no actions
///    scopes) — implied by rule 2, asserted by name because
///    C5 names it.
/// 4. The state credential cannot write code or workflows —
///    implied by rule 2, asserted by name because C5 names
///    it.
/// 5. Each role is present exactly once: a missing function
///    credential or a duplicate is an inventory defect.
pub fn audit_credentials(creds: &[Credential]) -> Vec<ScopeViolation> {
    let mut out = Vec::new();
    for role in [
        CredRole::Dispatch,
        CredRole::StateWrite,
        CredRole::Publish,
        CredRole::Report,
    ] {
        let n = creds.iter().filter(|c| c.role == role).count();
        if n == 0 {
            out.push(ScopeViolation {
                credential: format!("{role:?}"),
                rule: "role_missing",
            });
        } else if n > 1 {
            out.push(ScopeViolation {
                credential: format!("{role:?}"),
                rule: "role_duplicated",
            });
        }
    }
    for cred in creds {
        let allowed = allowed_scopes(cred.role);
        for scope in &cred.scopes {
            if scope == "*" || scope.ends_with(":*") {
                out.push(ScopeViolation {
                    credential: cred.name.clone(),
                    rule: "wildcard_scope",
                });
            } else if !allowed.contains(&scope.as_str()) {
                out.push(ScopeViolation {
                    credential: cred.name.clone(),
                    rule: "scope_outside_role",
                });
            }
        }
        if cred.role == CredRole::Report
            && cred.scopes.iter().any(|s| s.starts_with("actions:"))
        {
            out.push(ScopeViolation {
                credential: cred.name.clone(),
                rule: "report_credential_can_dispatch",
            });
        }
        if cred.role == CredRole::StateWrite
            && cred
                .scopes
                .iter()
                .any(|s| s == "workflows:write" || s == "code:write")
        {
            out.push(ScopeViolation {
                credential: cred.name.clone(),
                rule: "state_credential_writes_code",
            });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn inventory() -> Vec<Credential> {
        let mk = |name: &str, role: CredRole, scopes: &[&str]| Credential {
            name: name.into(),
            role,
            scopes: scopes.iter().map(|s| s.to_string()).collect(),
        };
        vec![
            mk("gk-dispatch", CredRole::Dispatch, &["actions:write", "actions:read"]),
            mk("gk-state", CredRole::StateWrite, &["contents:write", "contents:read"]),
            mk("gk-publish", CredRole::Publish, &["contents:write", "contents:read"]),
            mk("gk-report", CredRole::Report, &["issues:write"]),
        ]
    }

    // T036-13 token-scope-audit + T038-47
    // separate-credentials-least-privilege: the least-
    // privilege inventory audits clean, and each named C5
    // breach is caught by name.
    #[test]
    fn t036_13_t038_47_token_scope_audit() {
        assert_eq!(audit_credentials(&inventory()), vec![]);

        // The reporting credential grows a dispatch scope.
        let mut bad = inventory();
        bad[3].scopes.insert("actions:write".into());
        let v = audit_credentials(&bad);
        assert!(v.iter().any(|x| x.rule == "report_credential_can_dispatch"));
        assert!(v.iter().any(|x| x.rule == "scope_outside_role"));

        // The state credential grows a workflow scope.
        let mut bad = inventory();
        bad[1].scopes.insert("workflows:write".into());
        assert!(audit_credentials(&bad)
            .iter()
            .any(|x| x.rule == "state_credential_writes_code"));

        // A wildcard anywhere fails.
        let mut bad = inventory();
        bad[0].scopes.insert("*".into());
        assert!(audit_credentials(&bad)
            .iter()
            .any(|x| x.rule == "wildcard_scope"));

        // A missing function credential is an inventory defect.
        let mut bad = inventory();
        bad.retain(|c| c.role != CredRole::Publish);
        assert!(audit_credentials(&bad)
            .iter()
            .any(|x| x.rule == "role_missing"));
    }
}
