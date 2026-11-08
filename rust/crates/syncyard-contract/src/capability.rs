//! Host capability records (spec 039 FR-MAC-04; spec 038
//! FR-038-15 / FR-038-16).
//!
//! One record per host, produced by the host's capability probe
//! and consumed by placement. Two honesty rules are structural,
//! not conventional:
//!
//! - **Unknown is never zero.** Every probed quantity is a
//!   [`Measure`]: either a known value or explicitly unknown.
//!   There is no way to write a `0` that means "we did not look".
//! - **Unified memory is one pool.** [`MemoryPool`] has a total
//!   and an inference reservation against that same total. It
//!   has no VRAM field, and its serialized form must never grow
//!   one: a VRAM figure on a unified-memory host is a fabricated
//!   number (spec 039 falsifier).

use serde::{Deserialize, Serialize};

/// A probed quantity: known, or honestly unknown.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Measure<T> {
    Known(T),
    Unknown,
}

impl<T> Measure<T> {
    pub fn as_option(&self) -> Option<&T> {
        match self {
            Measure::Known(v) => Some(v),
            Measure::Unknown => None,
        }
    }
    pub fn is_unknown(&self) -> bool {
        matches!(self, Measure::Unknown)
    }
}

/// Unified memory: ONE pool (spec 039 FR-MAC-04 / FR-MAC-06).
/// The inference service's ceiling counts against the same
/// pool via `inference_reserved_mb`; there is deliberately no
/// separate graphics memory figure.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MemoryPool {
    pub total_mb: Measure<u64>,
    /// Reserved for the co-resident inference service, counted
    /// against `total_mb` (0 when no inference service runs).
    pub inference_reserved_mb: u64,
}

impl MemoryPool {
    /// Memory available to Syncyard jobs: total minus the
    /// inference reservation. `None` when the total is unknown
    /// (placement must then refuse, never assume).
    pub fn available_for_jobs_mb(&self) -> Option<u64> {
        match &self.total_mb {
            Measure::Known(t) => Some(t.saturating_sub(self.inference_reserved_mb)),
            Measure::Unknown => None,
        }
    }
}

/// Whether a job may hard-require this node (spec 038
/// FR-038-16, red-team M2): opportunistic nodes (a shared
/// laptop-class machine, the Mac) are non-requireable.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum NodeClass {
    Standard,
    Opportunistic,
}

/// Docker engine presence, probed, never assumed
/// (spec 039 FR-MAC-03).
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum DockerPresence {
    /// No engine answered the probe: a native-only host,
    /// recorded as such, never an error state.
    Absent,
    /// An engine answered. `version` is `None` when the engine
    /// answered but did not report a parseable version.
    Present { version: Option<String> },
}

/// Windows-VM venue activation state (spec 038 section 5,
/// FR-038-21): a continuously probed capability field, not a
/// one-time install fact.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ActivationState {
    NotApplicable,
    Active,
    Deactivated,
    Unknown,
}

/// One host's capability record.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CapabilityRecord {
    /// Fleet alias the record is registered under
    /// (e.g. `air-inference`).
    pub host: String,
    /// Observation time (unix seconds). Placement refuses
    /// records older than its threshold (FR-038-15).
    pub observed_at_unix: i64,
    pub chip: Measure<String>,
    pub cores: Measure<u32>,
    pub memory: MemoryPool,
    pub disk_total_mb: Measure<u64>,
    pub disk_free_mb: Measure<u64>,
    /// e.g. `macOS 27.0.1 (26A434)`; Linux/Windows hosts carry
    /// their own OS identity in the same field.
    pub os: Measure<String>,
    /// GPU compute support (Metal on macOS). A bool measure —
    /// never a memory figure.
    pub accelerator: Measure<bool>,
    pub docker: DockerPresence,
    pub tailscale_identity: Measure<String>,
    /// The co-resident inference endpoint, advertised only
    /// when the probe found it live (FR-MAC-11).
    pub inference_endpoint: Option<String>,
    /// Venues this host offers (spec 038 section 3 names:
    /// `native`, `docker`, `windows-vm`, `elevated-kit`).
    pub venues: Vec<String>,
    pub node_class: NodeClass,
    pub activation: ActivationState,
    /// Fanless/thermal limits and similar host truths, stated
    /// in the record (FR-MAC-06).
    pub thermal_note: Option<String>,
}

impl CapabilityRecord {
    /// Staleness: a record is stale when its age exceeds the
    /// caller's threshold. A record timestamped in the future
    /// is treated as fresh (clock skew is a display problem;
    /// ordering never uses wall clock, spec 038 section 9).
    pub fn is_stale(&self, now_unix: i64, threshold_s: i64) -> bool {
        now_unix.saturating_sub(self.observed_at_unix) > threshold_s
    }

    pub fn offers_venue(&self, venue: &str) -> bool {
        self.venues.iter().any(|v| v == venue)
    }

    /// The docker venue is offered only when an engine
    /// actually answered the probe (FR-MAC-03).
    pub fn docker_venue_available(&self) -> bool {
        matches!(self.docker, DockerPresence::Present { .. }) && self.offers_venue("docker")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mac_record() -> CapabilityRecord {
        CapabilityRecord {
            host: "air-inference".into(),
            observed_at_unix: 1_000,
            chip: Measure::Known("Apple M1".into()),
            cores: Measure::Known(8),
            memory: MemoryPool {
                total_mb: Measure::Known(8192),
                inference_reserved_mb: 4096,
            },
            disk_total_mb: Measure::Known(256_000),
            disk_free_mb: Measure::Known(28_000),
            os: Measure::Known("macOS 27.0.1 (26A434)".into()),
            accelerator: Measure::Known(true),
            docker: DockerPresence::Absent,
            tailscale_identity: Measure::Known("air-inference".into()),
            inference_endpoint: Some("http://127.0.0.1:11434".into()),
            venues: vec!["native".into()],
            node_class: NodeClass::Opportunistic,
            activation: ActivationState::NotApplicable,
            thermal_note: Some("fanless; sustained all-core batch routes elsewhere".into()),
        }
    }

    // T039-08 (parser/record half): the record carries unified
    // memory as ONE pool and no VRAM figure exists anywhere in
    // its serialized form; unknown quantities stay unknown.
    #[test]
    fn t039_08_unified_pool_one_pool_no_vram() {
        let rec = mac_record();
        assert_eq!(rec.memory.available_for_jobs_mb(), Some(4096));
        let json = serde_json::to_string(&rec).unwrap().to_lowercase();
        assert!(
            !json.contains("vram"),
            "capability record must never carry a VRAM figure: {json}"
        );
        let mut unknown = mac_record();
        unknown.disk_free_mb = Measure::Unknown;
        assert!(unknown.disk_free_mb.is_unknown());
        let json = serde_json::to_string(&unknown).unwrap();
        assert!(json.contains("\"unknown\""), "unknown serializes as unknown: {json}");
        // An unknown total yields no available figure, never 0.
        unknown.memory.total_mb = Measure::Unknown;
        assert_eq!(unknown.memory.available_for_jobs_mb(), None);
    }

    #[test]
    fn staleness_threshold() {
        let rec = mac_record();
        assert!(!rec.is_stale(1_000 + 900, 900));
        assert!(rec.is_stale(1_000 + 901, 900));
        assert!(!rec.is_stale(500, 900), "future stamp is not stale");
    }

    // FR-MAC-03 record half: docker venue availability follows
    // the probe, not the venue list alone.
    #[test]
    fn docker_availability_follows_probe() {
        let mut rec = mac_record();
        rec.venues.push("docker".into());
        assert!(!rec.docker_venue_available(), "listed but engine absent");
        rec.docker = DockerPresence::Present {
            version: Some("27.0.0".into()),
        };
        assert!(rec.docker_venue_available());
    }
}
