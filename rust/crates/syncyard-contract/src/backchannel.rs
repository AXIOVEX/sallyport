//! Backchannel reserved-use policy (spec 039 FR-MAC-10;
//! spec 009 FR-005).
//!
//! Every host's backchannel variant — including the macOS
//! one — operates under the same reserved-use rules:
//! Syncyard development and recovery only, owned by the
//! Syncyard (Desktop Access) chat. A variant whose policy
//! record does not carry the rule verbatim does not join the
//! fleet; program work never routes over the backchannel.

use serde::{Deserialize, Serialize};

/// The reserved-use rule, verbatim, carried by every
/// variant's policy record.
pub const RESERVED_USE: &str = "syncyard-development-recovery-only";

/// The owning chat's role name (the Desktop Access /
/// Syncyard chat).
pub const BACKCHANNEL_OWNER: &str = "syncyard-chat";

/// One host variant's backchannel policy record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BackchannelPolicy {
    /// Host variant: `windows`, `linux`, `macos`.
    pub variant: String,
    pub reserved_use: String,
    pub owner: String,
    /// Program work over the backchannel is never allowed;
    /// the field exists so a violation is representable.
    pub program_work_allowed: bool,
}

/// Validate a variant's policy record against the fleet
/// rule. All three fields must match the rule exactly.
pub fn validate_backchannel_policy(p: &BackchannelPolicy) -> Result<(), String> {
    if p.reserved_use != RESERVED_USE {
        return Err(format!(
            "backchannel reserved-use mismatch on {}: {:?}",
            p.variant, p.reserved_use
        ));
    }
    if p.owner != BACKCHANNEL_OWNER {
        return Err(format!(
            "backchannel owner mismatch on {}: {:?}",
            p.variant, p.owner
        ));
    }
    if p.program_work_allowed {
        return Err(format!(
            "backchannel on {} allows program work; the channel is reserved-use",
            p.variant
        ));
    }
    Ok(())
}

/// The macOS variant's policy record, as it must ship.
pub fn macos_backchannel_policy() -> BackchannelPolicy {
    BackchannelPolicy {
        variant: "macos".into(),
        reserved_use: RESERVED_USE.into(),
        owner: BACKCHANNEL_OWNER.into(),
        program_work_allowed: false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // T039-18 mac-backchannel-reserved-use: the macOS variant
    // carries the same reserved-use rule as every other host,
    // and a variant that widens the rule does not validate.
    #[test]
    fn t039_18_mac_backchannel_reserved_use() {
        assert!(validate_backchannel_policy(&macos_backchannel_policy()).is_ok());
        for variant in ["windows", "linux"] {
            let p = BackchannelPolicy {
                variant: variant.into(),
                ..macos_backchannel_policy()
            };
            assert!(validate_backchannel_policy(&p).is_ok());
        }
        let mut widened = macos_backchannel_policy();
        widened.program_work_allowed = true;
        assert!(validate_backchannel_policy(&widened).is_err());
        let mut reowned = macos_backchannel_policy();
        reowned.owner = "another-chat".into();
        assert!(validate_backchannel_policy(&reowned).is_err());
    }
}
