//! Signing tiers (spec 039 FR-MAC-07; spec 038 Q3).
//!
//! The tier rule is settled: Developer ID when a certificate
//! exists, else ad-hoc, and the produced tier is ALWAYS
//! reported — a build that will not say how it was signed is
//! a defect state, not a detail. The tier is a value every
//! build stamp carries, so "which tier" can never be absent,
//! only stated.

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SigningTier {
    /// Signed with a Developer ID Application identity
    /// (notarization-eligible).
    DeveloperId,
    /// Ad-hoc signed (`codesign -s -`): the first-proof tier
    /// when no Developer ID exists on the machine.
    AdHoc,
    /// Not signed at all. Legal for local dev builds only;
    /// never distributable, and reports as itself.
    Unsigned,
}

impl SigningTier {
    pub fn as_str(&self) -> &'static str {
        match self {
            SigningTier::DeveloperId => "developer-id",
            SigningTier::AdHoc => "ad-hoc",
            SigningTier::Unsigned => "unsigned",
        }
    }
}

/// Compute the tier a build on this machine would produce,
/// from the codesigning identities the machine reports (the
/// `security find-identity` list). An empty list is the
/// recorded state of the proving machine (0 valid identities,
/// 2026-11-06): the tier is ad-hoc, per the settled rule.
pub fn tier_from_identities(identities: &[String]) -> SigningTier {
    if identities
        .iter()
        .any(|i| i.contains("Developer ID Application"))
    {
        SigningTier::DeveloperId
    } else {
        SigningTier::AdHoc
    }
}

/// The build stamp every surface shows (spec 035
/// served-identity rule; UPD-03 health gates read it). The
/// tier field is not optional: a stamp without a tier cannot
/// be constructed through this type's constructor.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct BuildStamp {
    pub train: String,
    pub tier: SigningTier,
}

impl BuildStamp {
    pub fn new(train: impl Into<String>, tier: SigningTier) -> Self {
        BuildStamp {
            train: train.into(),
            tier,
        }
    }
    /// The one-line identity rendering surfaces display.
    pub fn render(&self) -> String {
        format!("train {} · signed {}", self.train, self.tier.as_str())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // T039-14 signing-tier-reported (logic half): the tier is
    // computed from the machine's identities and is present
    // in every stamp rendering — including the recorded
    // proving-machine case, 0 valid identities -> ad-hoc.
    #[test]
    fn t039_14_signing_tier_reported() {
        assert_eq!(tier_from_identities(&[]), SigningTier::AdHoc);
        assert_eq!(
            tier_from_identities(&["1) ABC \"Developer ID Application: X\"".into()]),
            SigningTier::DeveloperId
        );
        assert_eq!(
            tier_from_identities(&["1) ABC \"Apple Development: Y\"".into()]),
            SigningTier::AdHoc,
            "a non-Developer-ID identity does not raise the tier"
        );
        let stamp = BuildStamp::new("0.4.0", tier_from_identities(&[]));
        assert_eq!(stamp.render(), "train 0.4.0 · signed ad-hoc");
        let json = serde_json::to_string(&stamp).unwrap();
        assert!(json.contains("\"tier\":\"ad_hoc\""), "tier is in the stamp: {json}");
    }
}
