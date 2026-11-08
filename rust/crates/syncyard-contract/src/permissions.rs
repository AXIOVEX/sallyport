//! Host permission posture (spec 039 FR-MAC-08).
//!
//! The agent requests NO broad TCC grants by default. A
//! grant a job class actually needs is named in that job
//! class's record WITH its reason; a grant without a reason
//! does not validate, and a default grant does not validate
//! at all. First-run prompts stay an owner-at-keyboard step;
//! nothing here pre-grants anything.

use serde::{Deserialize, Serialize};

/// One named permission grant and why a job class needs it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct NamedGrant {
    /// e.g. `full-disk-access`, `accessibility`.
    pub grant: String,
    /// The reason, in the job class's record, in words.
    pub reason: String,
}

/// A host's permission posture.
#[derive(Debug, Clone, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct PermissionPosture {
    /// Grants requested for every job by default. MUST be
    /// empty (FR-MAC-08); the field exists so a violation is
    /// representable and refused, not unrepresentable.
    pub default_grants: Vec<NamedGrant>,
    /// (job class, grant) pairs for classes that genuinely
    /// need a grant.
    pub job_class_grants: Vec<(String, NamedGrant)>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PermissionError {
    /// A default grant exists; defaults must be empty.
    DefaultGrantPresent(String),
    /// A job-class grant without a reason (or without a
    /// grant name).
    GrantUnnamed { job_class: String },
}

impl std::fmt::Display for PermissionError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            PermissionError::DefaultGrantPresent(g) => {
                write!(f, "default TCC grant present: {g} (defaults must be empty)")
            }
            PermissionError::GrantUnnamed { job_class } => {
                write!(f, "job class {job_class} names a grant without a reason")
            }
        }
    }
}
impl std::error::Error for PermissionError {}

/// Validate a host's permission posture against FR-MAC-08.
pub fn validate_permissions(p: &PermissionPosture) -> Result<(), PermissionError> {
    if let Some(g) = p.default_grants.first() {
        return Err(PermissionError::DefaultGrantPresent(g.grant.clone()));
    }
    for (job_class, grant) in &p.job_class_grants {
        if grant.grant.trim().is_empty() || grant.reason.trim().is_empty() {
            return Err(PermissionError::GrantUnnamed {
                job_class: job_class.clone(),
            });
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // T039-15 no-default-tcc-grants: the default posture
    // carries no grants, and any default grant fails
    // validation by name.
    #[test]
    fn t039_15_no_default_tcc_grants() {
        assert!(validate_permissions(&PermissionPosture::default()).is_ok());
        let bad = PermissionPosture {
            default_grants: vec![NamedGrant {
                grant: "full-disk-access".into(),
                reason: "convenience".into(),
            }],
            job_class_grants: vec![],
        };
        assert_eq!(
            validate_permissions(&bad),
            Err(PermissionError::DefaultGrantPresent(
                "full-disk-access".into()
            ))
        );
    }

    // T039-16 needed-grants-named-with-reason: a job class
    // may carry a grant only with its reason attached.
    #[test]
    fn t039_16_needed_grants_named_with_reason() {
        let ok = PermissionPosture {
            default_grants: vec![],
            job_class_grants: vec![(
                "backup-restore".into(),
                NamedGrant {
                    grant: "full-disk-access".into(),
                    reason: "restores read protected library paths".into(),
                },
            )],
        };
        assert!(validate_permissions(&ok).is_ok());
        let mut unnamed = ok.clone();
        unnamed.job_class_grants[0].1.reason = "  ".into();
        assert_eq!(
            validate_permissions(&unnamed),
            Err(PermissionError::GrantUnnamed {
                job_class: "backup-restore".into()
            })
        );
    }
}
