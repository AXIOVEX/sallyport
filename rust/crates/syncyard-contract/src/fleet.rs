//! Fleet naming and anchor resolution (spec 038 FR-038-29,
//! red-team M5).
//!
//! The ratified fleet names are the only names placement may
//! use. Old names keep working as *mappings* until each host's
//! cutover lands. Anchor resolution failure is a named
//! constraint that stops placement — resolution NEVER falls
//! back to another address form (no raw-IP guessing, no
//! short-name retry): a wrong address silently reached is
//! worse than a job that waits.

/// The named constraint ledgered when anchor resolution fails.
pub const CONSTRAINT_ANCHOR_RESOLUTION_FAILED: &str = "anchor_resolution_failed";

/// One fleet host: ratified name, pre-rename names that map to
/// it, and the anchor (tailnet identity) placement resolves.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct FleetHost {
    pub name: &'static str,
    pub old_names: &'static [&'static str],
    pub anchor: &'static str,
}

/// The ratified fleet (spec 038 section 10).
pub const FLEET: &[FleetHost] = &[
    FleetHost {
        name: "tower-compute",
        old_names: &["tristen-desktop"],
        anchor: "tower-compute",
    },
    FleetHost {
        name: "laptop-bench",
        old_names: &["tristen-laptop"],
        anchor: "laptop-bench",
    },
    FleetHost {
        name: "mini-server",
        old_names: &["testbench"],
        anchor: "mini-server",
    },
    FleetHost {
        name: "sbc-inference",
        old_names: &["jetson"],
        anchor: "sbc-inference",
    },
    FleetHost {
        name: "tower-offsite",
        old_names: &[],
        anchor: "tower-offsite",
    },
    FleetHost {
        name: "air-inference",
        old_names: &["macbook-air"],
        anchor: "air-inference",
    },
];

/// Anchor resolution failure. Carries the named constraint
/// and the name that failed — never a substitute address.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AnchorError {
    pub constraint: &'static str,
    pub requested: String,
}

impl std::fmt::Display for AnchorError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            f,
            "{}: no fleet host named {}",
            self.constraint, self.requested
        )
    }
}
impl std::error::Error for AnchorError {}

/// Resolve a host name (ratified or old) to its fleet record.
/// Old-name mappings persist until each cutover lands; the
/// returned record always carries the ratified name.
pub fn resolve_host(name: &str) -> Result<&'static FleetHost, AnchorError> {
    for host in FLEET {
        if host.name == name || host.old_names.contains(&name) {
            return Ok(host);
        }
    }
    Err(AnchorError {
        constraint: CONSTRAINT_ANCHOR_RESOLUTION_FAILED,
        requested: name.to_string(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    // T038-31 fleet-naming-and-anchor-failure: the six ratified
    // names resolve; old names map to the ratified record; an
    // unknown name fails with the named constraint and yields
    // no fallback address of any form.
    #[test]
    fn t038_31_fleet_naming_and_anchor_failure() {
        let ratified = [
            "tower-compute",
            "laptop-bench",
            "mini-server",
            "sbc-inference",
            "tower-offsite",
            "air-inference",
        ];
        assert_eq!(FLEET.len(), ratified.len());
        for name in ratified {
            let host = resolve_host(name).unwrap();
            assert_eq!(host.name, name);
            assert_eq!(host.anchor, name);
        }
        // Old-name mappings persist until cutover.
        assert_eq!(
            resolve_host("tristen-desktop").unwrap().name,
            "tower-compute"
        );
        assert_eq!(resolve_host("tristen-laptop").unwrap().name, "laptop-bench");
        // Failure is named, and there is no fallback to invent.
        let err = resolve_host("tristen-desktopp").unwrap_err();
        assert_eq!(err.constraint, CONSTRAINT_ANCHOR_RESOLUTION_FAILED);
        assert_eq!(err.requested, "tristen-desktopp");
        assert!(
            resolve_host("192.168.1.10").is_err(),
            "raw IPs are not anchors"
        );
        assert!(resolve_host("").is_err());
    }
}
