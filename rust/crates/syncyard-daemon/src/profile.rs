//! Host profiles, applied transactionally (spec 038
//! FR-038-45, red-team M4).
//!
//! A profile change takes a restore point FIRST, applies,
//! and passes a post-change health gate; on gate failure it
//! reverts to the restore point automatically. One machine
//! at a time, under canary discipline: the planner refuses a
//! second concurrent application.

use std::collections::BTreeMap;

/// A host profile: declarative settings by key.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HostProfile {
    pub host: String,
    pub settings: BTreeMap<String, String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ProfileOutcome {
    Applied,
    /// The health gate failed; the host was reverted to the
    /// restore point taken before the change.
    Reverted {
        reason: String,
    },
}

/// Apply `new` to `host` transactionally. Returns the
/// outcome and the restore point that was taken (kept by the
/// caller as the train's record). On gate failure `host` is
/// byte-identical to its pre-change state.
pub fn apply_profile(
    host: &mut HostProfile,
    new: &HostProfile,
    health_gate: impl Fn(&HostProfile) -> Result<(), String>,
) -> (ProfileOutcome, HostProfile) {
    let restore_point = host.clone();
    host.settings = new.settings.clone();
    match health_gate(host) {
        Ok(()) => (ProfileOutcome::Applied, restore_point),
        Err(reason) => {
            *host = restore_point.clone();
            (ProfileOutcome::Reverted { reason }, restore_point)
        }
    }
}

/// One-machine-at-a-time discipline.
#[derive(Debug, Default)]
pub struct ProfilePlanner {
    in_flight: Option<String>,
}

impl ProfilePlanner {
    pub fn begin(&mut self, host: &str) -> Result<(), String> {
        if let Some(other) = &self.in_flight {
            return Err(format!(
                "profile application already in flight on {other}; one machine at a time"
            ));
        }
        self.in_flight = Some(host.to_string());
        Ok(())
    }
    pub fn finish(&mut self, host: &str) {
        if self.in_flight.as_deref() == Some(host) {
            self.in_flight = None;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn profile(host: &str, power: &str) -> HostProfile {
        let mut settings = BTreeMap::new();
        settings.insert("gpu.power_limit_w".to_string(), power.to_string());
        HostProfile {
            host: host.into(),
            settings,
        }
    }

    // T038-59 profile-transactional-revert: restore point
    // first, health gate after, automatic revert on failure,
    // one machine at a time.
    #[test]
    fn t038_59_profile_transactional_revert() {
        // Gate passes: the change sticks, and the restore
        // point is the pre-change profile.
        let mut host = profile("tower-compute", "180");
        let (outcome, restore) =
            apply_profile(&mut host, &profile("tower-compute", "200"), |_| Ok(()));
        assert_eq!(outcome, ProfileOutcome::Applied);
        assert_eq!(host.settings["gpu.power_limit_w"], "200");
        assert_eq!(restore.settings["gpu.power_limit_w"], "180");

        // Gate fails: the host is byte-identical to before.
        let mut host = profile("tower-compute", "180");
        let before = host.clone();
        let (outcome, _) = apply_profile(&mut host, &profile("tower-compute", "999"), |_| {
            Err("gpu unreachable after change".to_string())
        });
        assert_eq!(
            outcome,
            ProfileOutcome::Reverted {
                reason: "gpu unreachable after change".into()
            }
        );
        assert_eq!(host, before, "revert restores the exact pre-change profile");

        // One machine at a time.
        let mut planner = ProfilePlanner::default();
        planner.begin("tower-compute").unwrap();
        assert!(planner.begin("laptop-bench").is_err());
        planner.finish("tower-compute");
        assert!(planner.begin("laptop-bench").is_ok());
    }
}
