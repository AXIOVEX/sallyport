//! The capability registry and placement checks (spec 038
//! FR-038-15 / FR-038-16; spec 039 FR-MAC-03 / 05 / 06 / 11).
//!
//! Every capability record carries its observation time;
//! placement refuses stale records with a ledgered named
//! constraint, refuses hard requirements against opportunistic
//! nodes, refuses docker routing to a host whose engine did
//! not answer, refuses a deactivated windows-vm venue, and
//! refuses batch campaigns to an opportunistic node while a
//! standard host can take them. Unknown is refused, never
//! treated as zero or as consent.
//!
//! Sleep honesty (FR-MAC-05): a sleeping host is unavailable,
//! and a host that has woken but not yet re-registered is
//! also unavailable — re-registration comes before work, by
//! construction of the state machine here.

use std::collections::BTreeMap;

use syncyard_contract::{ActivationState, CapabilityRecord, NodeClass};

use crate::state::DaemonState;

/// Named constraints (ledgered verbatim on refusal).
pub const CONSTRAINT_CAPABILITY_UNKNOWN: &str = "capability_unknown";
pub const CONSTRAINT_CAPABILITY_STALE: &str = "capability_stale";
pub const CONSTRAINT_OPPORTUNISTIC_NOT_REQUIREABLE: &str = "opportunistic_node_not_requireable";
pub const CONSTRAINT_DOCKER_VENUE_UNAVAILABLE: &str = "docker_venue_unavailable";
pub const CONSTRAINT_VENUE_OFFLINE_ACTIVATION: &str = "venue_offline_activation";
pub const CONSTRAINT_BATCH_ROUTES_ELSEWHERE: &str = "batch_campaign_routes_elsewhere";
pub const CONSTRAINT_INSUFFICIENT_MEMORY_POOL: &str = "insufficient_unified_memory_pool";
pub const CONSTRAINT_HOST_ASLEEP: &str = "host_asleep";
pub const CONSTRAINT_HOST_NOT_REREGISTERED: &str = "host_not_reregistered";

/// Wake state of one host, tracked separately from its
/// capability record (a record can be fresh while the host
/// sleeps).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WakeState {
    Awake,
    Asleep,
    /// Woke, but has not re-registered yet: unavailable.
    WokeUnregistered,
}

/// What kind of work is asking for placement.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum JobClass {
    Normal,
    /// Sustained all-core batch work (FR-MAC-06): must not
    /// route to an opportunistic node when a workhorse can
    /// take it.
    BatchCampaign,
}

/// A placement request against one host.
#[derive(Debug, Clone)]
pub struct PlacementRequest {
    pub host: String,
    pub venue: Option<String>,
    pub needs_ram_mb: Option<u64>,
    /// The job names this exact host as a hard requirement.
    pub hard_require_host: bool,
    pub class: JobClass,
}

/// Placement verdict. A refusal carries the named constraint
/// the daemon ledgers (FR-038-15).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Placement {
    Placed,
    Refused { constraint: &'static str, detail: String },
}

fn refused(constraint: &'static str, detail: impl Into<String>) -> Placement {
    Placement::Refused {
        constraint,
        detail: detail.into(),
    }
}

/// The registry: capability records by host alias, plus wake
/// states.
#[derive(Debug, Default)]
pub struct CapabilityRegistry {
    records: BTreeMap<String, CapabilityRecord>,
    wake: BTreeMap<String, WakeState>,
    /// Placement refuses records older than this (seconds).
    pub staleness_threshold_s: i64,
}

impl CapabilityRegistry {
    pub fn new(staleness_threshold_s: i64) -> Self {
        CapabilityRegistry {
            records: BTreeMap::new(),
            wake: BTreeMap::new(),
            staleness_threshold_s,
        }
    }

    pub fn register(&mut self, rec: CapabilityRecord) {
        // (Re-)registration is also the wake path's final
        // step: a host that registers is awake and known.
        self.wake.insert(rec.host.clone(), WakeState::Awake);
        self.records.insert(rec.host.clone(), rec);
    }

    pub fn record(&self, host: &str) -> Option<&CapabilityRecord> {
        self.records.get(host)
    }

    /// The host went to sleep: unavailable immediately.
    pub fn note_sleep(&mut self, host: &str) {
        self.wake.insert(host.to_string(), WakeState::Asleep);
    }

    /// The host woke. It is NOT yet available: it must
    /// re-register (a fresh `register`) before accepting
    /// work (FR-MAC-05).
    pub fn note_wake(&mut self, host: &str) {
        self.wake.insert(host.to_string(), WakeState::WokeUnregistered);
    }

    pub fn wake_state(&self, host: &str) -> WakeState {
        self.wake.get(host).copied().unwrap_or(WakeState::Awake)
    }

    /// The inference endpoint a host advertises: present only
    /// when the host's probe found the service live
    /// (FR-MAC-11). Absence is not an error.
    pub fn advertised_inference_endpoint(&self, host: &str) -> Option<&str> {
        self.records.get(host)?.inference_endpoint.as_deref()
    }

    /// Decide placement of one job on one host.
    pub fn place(&self, req: &PlacementRequest, now_unix: i64) -> Placement {
        let rec = match self.records.get(&req.host) {
            Some(r) => r,
            None => {
                return refused(
                    CONSTRAINT_CAPABILITY_UNKNOWN,
                    format!("no capability record for {}", req.host),
                )
            }
        };
        match self.wake_state(&req.host) {
            WakeState::Asleep => {
                return refused(CONSTRAINT_HOST_ASLEEP, format!("{} is asleep", req.host))
            }
            WakeState::WokeUnregistered => {
                return refused(
                    CONSTRAINT_HOST_NOT_REREGISTERED,
                    format!("{} woke but has not re-registered", req.host),
                )
            }
            WakeState::Awake => {}
        }
        if rec.is_stale(now_unix, self.staleness_threshold_s) {
            return refused(
                CONSTRAINT_CAPABILITY_STALE,
                format!(
                    "{} capability observed at {}, threshold {}s",
                    req.host, rec.observed_at_unix, self.staleness_threshold_s
                ),
            );
        }
        if req.hard_require_host && rec.node_class == NodeClass::Opportunistic {
            return refused(
                CONSTRAINT_OPPORTUNISTIC_NOT_REQUIREABLE,
                format!("{} is an opportunistic, non-requireable node", req.host),
            );
        }
        if let Some(venue) = &req.venue {
            if venue == "docker" && !rec.docker_venue_available() {
                return refused(
                    CONSTRAINT_DOCKER_VENUE_UNAVAILABLE,
                    format!("no docker engine answers on {}", req.host),
                );
            }
            if venue == "windows-vm" && rec.activation == ActivationState::Deactivated {
                return refused(
                    CONSTRAINT_VENUE_OFFLINE_ACTIVATION,
                    format!("windows-vm guest on {} is deactivated", req.host),
                );
            }
            if !rec.offers_venue(venue) {
                return refused(
                    CONSTRAINT_DOCKER_VENUE_UNAVAILABLE,
                    format!("{} does not offer venue {venue}", req.host),
                );
            }
        }
        if req.class == JobClass::BatchCampaign && rec.node_class == NodeClass::Opportunistic {
            let workhorse_available = self.records.values().any(|other| {
                other.host != req.host
                    && other.node_class == NodeClass::Standard
                    && self.wake_state(&other.host) == WakeState::Awake
                    && !other.is_stale(now_unix, self.staleness_threshold_s)
                    && match &req.venue {
                        Some(v) => other.offers_venue(v),
                        None => true,
                    }
            });
            if workhorse_available {
                return refused(
                    CONSTRAINT_BATCH_ROUTES_ELSEWHERE,
                    format!(
                        "batch campaign must not route to opportunistic {} while a standard host can take it",
                        req.host
                    ),
                );
            }
        }
        if let Some(needs) = req.needs_ram_mb {
            match rec.memory.available_for_jobs_mb() {
                Some(avail) if needs <= avail => {}
                Some(avail) => {
                    return refused(
                        CONSTRAINT_INSUFFICIENT_MEMORY_POOL,
                        format!(
                            "job needs {needs} MB, {} pool has {avail} MB after reservations",
                            req.host
                        ),
                    )
                }
                None => {
                    return refused(
                        CONSTRAINT_CAPABILITY_UNKNOWN,
                        format!("{} memory total is unknown; refusing to assume", req.host),
                    )
                }
            }
        }
        Placement::Placed
    }

    /// Place, and ledger a refusal under its named constraint
    /// (FR-038-15: the refusal is a named constraint in the
    /// ledger, not a silent skip).
    pub fn place_and_ledger(
        &self,
        state: &mut DaemonState,
        req: &PlacementRequest,
        now_unix: i64,
    ) -> Placement {
        let verdict = self.place(req, now_unix);
        if let Placement::Refused {
            constraint,
            detail,
        } = &verdict
        {
            let mut fields = BTreeMap::new();
            fields.insert(
                "host".to_string(),
                serde_json::Value::String(req.host.clone()),
            );
            fields.insert(
                "constraint".to_string(),
                serde_json::Value::String(constraint.to_string()),
            );
            fields.insert(
                "detail".to_string(),
                serde_json::Value::String(detail.clone()),
            );
            state.ledger("placement_refused", fields);
        }
        verdict
    }
}

/// A distributed-inference job's node set (FR-038-16): every
/// node is a hard requirement, so losing one mid-job is job
/// failure — never silent degradation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DistributedJob {
    pub id: String,
    pub required_nodes: Vec<String>,
    pub failed: Option<String>,
}

impl DistributedJob {
    pub fn new(id: impl Into<String>, required_nodes: Vec<String>) -> Self {
        DistributedJob {
            id: id.into(),
            required_nodes,
            failed: None,
        }
    }

    /// A required node was lost mid-job. Returns the job's
    /// outcome word: `failure`, by rule.
    pub fn note_node_lost(&mut self, host: &str) -> Option<&'static str> {
        if self.required_nodes.iter().any(|n| n == host) {
            self.failed = Some(format!("required node {host} lost mid-job"));
            return Some("failure");
        }
        None
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use syncyard_contract::{DockerPresence, Measure, MemoryPool};

    fn rec(host: &str, class: NodeClass) -> CapabilityRecord {
        CapabilityRecord {
            host: host.into(),
            observed_at_unix: 10_000,
            chip: Measure::Known("test-chip".into()),
            cores: Measure::Known(8),
            memory: MemoryPool {
                total_mb: Measure::Known(8192),
                inference_reserved_mb: 4096,
            },
            disk_total_mb: Measure::Known(256_000),
            disk_free_mb: Measure::Known(28_000),
            os: Measure::Known("test-os".into()),
            accelerator: Measure::Known(false),
            docker: DockerPresence::Absent,
            tailscale_identity: Measure::Known(host.into()),
            inference_endpoint: None,
            venues: vec!["native".into()],
            node_class: class,
            activation: ActivationState::NotApplicable,
            thermal_note: None,
        }
    }

    fn req(host: &str) -> PlacementRequest {
        PlacementRequest {
            host: host.into(),
            venue: Some("native".into()),
            needs_ram_mb: None,
            hard_require_host: false,
            class: JobClass::Normal,
        }
    }

    // T038-17 stale-capability-refused: a record past its
    // threshold refuses placement; a fresh one places; an
    // unrecorded host refuses as unknown, never as zero.
    #[test]
    fn t038_17_stale_capability_refused() {
        let mut reg = CapabilityRegistry::new(900);
        reg.register(rec("mini-server", NodeClass::Standard));
        assert_eq!(reg.place(&req("mini-server"), 10_500), Placement::Placed);
        match reg.place(&req("mini-server"), 10_000 + 901) {
            Placement::Refused { constraint, .. } => {
                assert_eq!(constraint, CONSTRAINT_CAPABILITY_STALE)
            }
            other => panic!("expected stale refusal, got {other:?}"),
        }
        match reg.place(&req("ghost-host"), 10_500) {
            Placement::Refused { constraint, .. } => {
                assert_eq!(constraint, CONSTRAINT_CAPABILITY_UNKNOWN)
            }
            other => panic!("expected unknown refusal, got {other:?}"),
        }
    }

    // T038-18 opportunistic-node-not-requireable + the
    // mid-job node-loss rule.
    #[test]
    fn t038_18_opportunistic_node_not_requireable() {
        let mut reg = CapabilityRegistry::new(900);
        reg.register(rec("air-inference", NodeClass::Opportunistic));
        let mut r = req("air-inference");
        r.hard_require_host = true;
        match reg.place(&r, 10_100) {
            Placement::Refused { constraint, .. } => {
                assert_eq!(constraint, CONSTRAINT_OPPORTUNISTIC_NOT_REQUIREABLE)
            }
            other => panic!("expected refusal, got {other:?}"),
        }
        // Not hard-required: the same node may take work.
        assert_eq!(reg.place(&req("air-inference"), 10_100), Placement::Placed);

        let mut job = DistributedJob::new("dist-1", vec!["n1".into(), "n2".into()]);
        assert_eq!(job.note_node_lost("elsewhere"), None);
        assert_eq!(job.note_node_lost("n2"), Some("failure"));
        assert!(job.failed.is_some());
    }

    // T039-07 engine-stopped-no-docker-routing: with no
    // engine answering, a docker job is refused by name even
    // if the host lists the venue; when an engine answers,
    // it routes.
    #[test]
    fn t039_07_engine_stopped_no_docker_routing() {
        let mut reg = CapabilityRegistry::new(900);
        let mut host = rec("mini-server", NodeClass::Standard);
        host.venues.push("docker".into());
        reg.register(host);
        let mut r = req("mini-server");
        r.venue = Some("docker".into());
        match reg.place(&r, 10_100) {
            Placement::Refused { constraint, .. } => {
                assert_eq!(constraint, CONSTRAINT_DOCKER_VENUE_UNAVAILABLE)
            }
            other => panic!("expected docker refusal, got {other:?}"),
        }
        let mut host = rec("mini-server", NodeClass::Standard);
        host.venues.push("docker".into());
        host.docker = DockerPresence::Present {
            version: Some("27.0.0".into()),
        };
        reg.register(host);
        assert_eq!(reg.place(&r, 10_100), Placement::Placed);
    }

    // T038-23 activation-deactivation-offlines-venue: the
    // guest's activation is a probed capability field; when a
    // probe reports deactivation, the venue is offline with
    // the named constraint.
    #[test]
    fn t038_23_activation_deactivation_offlines_venue() {
        let mut reg = CapabilityRegistry::new(900);
        let mut host = rec("tower-compute", NodeClass::Standard);
        host.venues = vec!["windows-vm".into()];
        host.activation = ActivationState::Active;
        reg.register(host);
        let mut r = req("tower-compute");
        r.venue = Some("windows-vm".into());
        assert_eq!(reg.place(&r, 10_100), Placement::Placed);

        let mut host = rec("tower-compute", NodeClass::Standard);
        host.venues = vec!["windows-vm".into()];
        host.activation = ActivationState::Deactivated;
        reg.register(host);
        match reg.place(&r, 10_200) {
            Placement::Refused { constraint, .. } => {
                assert_eq!(constraint, CONSTRAINT_VENUE_OFFLINE_ACTIVATION)
            }
            other => panic!("expected activation refusal, got {other:?}"),
        }
    }

    // T039-11 unified-pool-caps-enforced: needs are enforced
    // against the unified pool AFTER the inference
    // reservation counts against the same pool (8 GB total,
    // 4 GB inference ceiling -> 4096 MB for jobs).
    #[test]
    fn t039_11_unified_pool_caps_enforced() {
        let mut reg = CapabilityRegistry::new(900);
        reg.register(rec("air-inference", NodeClass::Opportunistic));
        let mut r = req("air-inference");
        r.needs_ram_mb = Some(4096);
        assert_eq!(reg.place(&r, 10_100), Placement::Placed);
        r.needs_ram_mb = Some(4097);
        match reg.place(&r, 10_100) {
            Placement::Refused { constraint, .. } => {
                assert_eq!(constraint, CONSTRAINT_INSUFFICIENT_MEMORY_POOL)
            }
            other => panic!("expected pool refusal, got {other:?}"),
        }
    }

    // T039-12 batch-campaigns-route-elsewhere.
    #[test]
    fn t039_12_batch_campaigns_route_elsewhere() {
        let mut reg = CapabilityRegistry::new(900);
        reg.register(rec("air-inference", NodeClass::Opportunistic));
        reg.register(rec("tower-compute", NodeClass::Standard));
        let mut r = req("air-inference");
        r.class = JobClass::BatchCampaign;
        match reg.place(&r, 10_100) {
            Placement::Refused { constraint, .. } => {
                assert_eq!(constraint, CONSTRAINT_BATCH_ROUTES_ELSEWHERE)
            }
            other => panic!("expected elsewhere refusal, got {other:?}"),
        }
        // A normal job is unaffected by the campaign rule.
        assert_eq!(reg.place(&req("air-inference"), 10_100), Placement::Placed);
    }

    // T039-09 (registry half) sleep-wake-no-loss-no-double-run:
    // asleep refuses; woke-but-unregistered refuses;
    // re-registration restores placement. Job loss/duplication
    // is guarded by the dispatch protocol (T038-35).
    #[test]
    fn t039_09_sleep_wake_reregistration() {
        let mut reg = CapabilityRegistry::new(900);
        reg.register(rec("air-inference", NodeClass::Opportunistic));
        assert_eq!(reg.place(&req("air-inference"), 10_100), Placement::Placed);
        reg.note_sleep("air-inference");
        match reg.place(&req("air-inference"), 10_200) {
            Placement::Refused { constraint, .. } => assert_eq!(constraint, CONSTRAINT_HOST_ASLEEP),
            other => panic!("expected asleep refusal, got {other:?}"),
        }
        reg.note_wake("air-inference");
        match reg.place(&req("air-inference"), 10_300) {
            Placement::Refused { constraint, .. } => {
                assert_eq!(constraint, CONSTRAINT_HOST_NOT_REREGISTERED)
            }
            other => panic!("expected re-register refusal, got {other:?}"),
        }
        reg.register(rec("air-inference", NodeClass::Opportunistic));
        assert_eq!(reg.place(&req("air-inference"), 10_400), Placement::Placed);
    }

    // T039-19 inference-endpoint-advertised-when-live
    // (registry half; the probe half is in the agent).
    #[test]
    fn t039_19_inference_endpoint_advertised_when_live() {
        let mut reg = CapabilityRegistry::new(900);
        reg.register(rec("air-inference", NodeClass::Opportunistic));
        assert_eq!(reg.advertised_inference_endpoint("air-inference"), None);
        let mut live = rec("air-inference", NodeClass::Opportunistic);
        live.inference_endpoint = Some("http://127.0.0.1:11434".into());
        reg.register(live);
        assert_eq!(
            reg.advertised_inference_endpoint("air-inference"),
            Some("http://127.0.0.1:11434")
        );
    }

    // T039-20 inference-independent-of-agent (model half):
    // the endpoint lives in the capability record, not in any
    // agent lifecycle state: sleep/wake cycles of the agent's
    // host tracking do not clear an advertised endpoint, and
    // the endpoint record survives independent of placement.
    #[test]
    fn t039_20_inference_independent_of_agent() {
        let mut reg = CapabilityRegistry::new(900);
        let mut live = rec("air-inference", NodeClass::Opportunistic);
        live.inference_endpoint = Some("http://127.0.0.1:11434".into());
        reg.register(live);
        reg.note_sleep("air-inference");
        reg.note_wake("air-inference");
        // Agent-side lifecycle churn never touches the
        // inference service's advertisement.
        assert_eq!(
            reg.advertised_inference_endpoint("air-inference"),
            Some("http://127.0.0.1:11434")
        );
    }
}
