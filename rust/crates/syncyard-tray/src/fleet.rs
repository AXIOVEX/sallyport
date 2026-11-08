//! The local fleet-status file: the tray's fleet line is
//! rendered from the file the fleet-status publisher maintains
//! on this host, with staleness as a first-class fact (spec 035
//! sections 1-2, FR-035-18). Parsing is tolerant (a `Value`
//! walk): a field the file does not carry is unknown, never
//! invented.

use std::path::Path;

use syncyard_contract::isotime;

/// The tray-relevant projection of the fleet-status file.
#[derive(Debug, Clone, PartialEq)]
pub struct FleetStatus {
    pub generated_at: Option<String>,
    pub generated_unix: Option<i64>,
    pub source: Option<String>,
    pub mode: Option<String>,
    pub queue_state: Option<String>,
    pub waiting_total: u64,
    pub running_total: u64,
    pub active_count: usize,
    pub waiting_count: usize,
}

/// Freshness of a fleet value against its stated bound.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Freshness {
    Fresh,
    Stale { age_s: i64 },
    Unknown,
}

/// Parse the fleet-status file's text; `None` only when the
/// text is not JSON at all.
pub fn parse_fleet_status(text: &str) -> Option<FleetStatus> {
    let v: serde_json::Value = serde_json::from_str(text).ok()?;
    let generated_at = v
        .get("generated_at")
        .and_then(|x| x.as_str())
        .map(|s| s.to_string());
    let generated_unix = generated_at
        .as_deref()
        .and_then(isotime::unix_from_iso);
    Some(FleetStatus {
        generated_at,
        generated_unix,
        source: v.get("source").and_then(|x| x.as_str()).map(|s| s.to_string()),
        mode: v.get("mode").and_then(|x| x.as_str()).map(|s| s.to_string()),
        queue_state: v
            .get("queue_state")
            .and_then(|x| x.as_str())
            .map(|s| s.to_string()),
        waiting_total: v.get("waiting_total").and_then(|x| x.as_u64()).unwrap_or(0),
        running_total: v.get("running_total").and_then(|x| x.as_u64()).unwrap_or(0),
        active_count: v
            .get("active")
            .and_then(|x| x.as_array())
            .map(|a| a.len())
            .unwrap_or(0),
        waiting_count: v
            .get("waiting")
            .and_then(|x| x.as_array())
            .map(|a| a.len())
            .unwrap_or(0),
    })
}

/// Read + parse the fleet-status file at `path`.
pub fn load_fleet_status(path: &Path) -> Option<FleetStatus> {
    let text = std::fs::read_to_string(path).ok()?;
    parse_fleet_status(&text)
}

impl FleetStatus {
    /// Age of the snapshot at `now_unix_s` (None = unknown age).
    pub fn age_s(&self, now_unix_s: i64) -> Option<i64> {
        self.generated_unix.map(|g| now_unix_s - g)
    }

    /// Freshness against the bound: past the bound the value
    /// renders stale **with its age** (FR-035-18).
    pub fn freshness(&self, now_unix_s: i64, bound_s: i64) -> Freshness {
        match self.age_s(now_unix_s) {
            Some(age) if age <= bound_s => Freshness::Fresh,
            Some(age) => Freshness::Stale { age_s: age },
            None => Freshness::Unknown,
        }
    }

    /// The fleet line in the shared vocabulary (spec 035
    /// section 4): counts verbatim from the file, staleness
    /// stated when the freshness rule says so.
    pub fn line(&self, now_unix_s: i64, bound_s: i64) -> String {
        let base = format!(
            "fleet: {} running · {} waiting",
            self.running_total, self.waiting_total
        );
        match self.freshness(now_unix_s, bound_s) {
            Freshness::Fresh => base,
            Freshness::Stale { age_s } => format!("{base} · stale {age_s}s"),
            Freshness::Unknown => format!("{base} · age unknown"),
        }
    }
}
