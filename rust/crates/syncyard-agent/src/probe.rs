//! Host capability probing (spec 039 FR-MAC-04, FR-MAC-03b).
//!
//! The macOS probe parses `system_profiler`-style output:
//! chip, core counts, unified memory, disk, macOS version
//! and build, Metal support. Anything the output does not
//! state becomes `Measure::Unknown` in the record — never 0
//! (the spec's falsifier is structural: unknown values have
//! no numeric form). Unified memory is ONE pool; the
//! contract record type has no VRAM field at all.
//!
//! The Docker venue is probed, never assumed (FR-MAC-03b):
//! the agent advertises the docker venue only when an
//! engine answers. A host with no engine is a native-only
//! host, recorded as such — a value, never an error state.

use syncyard_contract::capability::{
    ActivationState, CapabilityRecord, DockerPresence, Measure, MemoryPool, NodeClass,
};

/// What a Docker engine probe found.
#[derive(Debug, Clone, PartialEq)]
pub struct DockerEngineInfo {
    pub running: bool,
    pub version: Option<String>,
}

impl DockerEngineInfo {
    pub fn presence(&self) -> DockerPresence {
        if self.running {
            DockerPresence::Present {
                version: self.version.clone(),
            }
        } else {
            DockerPresence::Absent
        }
    }
}

/// The engine probe seam. Production probes shell out to
/// `docker version` / the engine socket; tests use fakes.
pub trait DockerProbe {
    fn engine(&self) -> DockerEngineInfo;
}

/// A probe that always reports no engine (the honest
/// default until a real probe is wired on the host).
pub struct NoDockerProbe;
impl DockerProbe for NoDockerProbe {
    fn engine(&self) -> DockerEngineInfo {
        DockerEngineInfo {
            running: false,
            version: None,
        }
    }
}

/// The venues this host advertises: native always (the
/// agent itself is running), docker only when an engine
/// answers (FR-MAC-03).
pub fn advertised_venues(docker: &DockerEngineInfo) -> Vec<String> {
    let mut venues = vec!["native".to_string()];
    if docker.running {
        venues.push("docker".to_string());
    }
    venues
}

/// Parse a decimal-GB figure as system_profiler prints it
/// ("251.1 GB") into MB (the record's unit).
fn parse_gb_to_mb(text: &str) -> Option<u64> {
    let num: f64 = text.trim().trim_end_matches("GB").trim().parse().ok()?;
    Some((num * 1000.0) as u64)
}

/// Extract `key: value` from one profiler section's lines.
fn field<'a>(lines: &[&'a str], key: &str) -> Option<&'a str> {
    lines.iter().find_map(|l| {
        let l = l.trim();
        let (k, v) = l.split_once(':')?;
        if k.trim() == key {
            Some(v.trim())
        } else {
            None
        }
    })
}

fn measure<T>(v: Option<T>) -> Measure<T> {
    match v {
        Some(x) => Measure::Known(x),
        None => Measure::Unknown,
    }
}

/// Build the capability record from system_profiler-style
/// output plus the Docker probe result.
pub fn record_from_profiler(
    node: &str,
    profiler_output: &str,
    docker: &DockerEngineInfo,
    tailscale_name: Option<String>,
    observed_unix: i64,
) -> CapabilityRecord {
    // Split into sections by top-level headers ("Hardware:").
    let mut sections: Vec<(String, Vec<&str>)> = Vec::new();
    for line in profiler_output.lines() {
        if !line.starts_with(char::is_whitespace) && line.trim_end().ends_with(':') && !line.is_empty()
        {
            sections.push((line.trim_end_matches(':').trim().to_string(), Vec::new()));
        } else if let Some((_, lines)) = sections.last_mut() {
            lines.push(line);
        }
    }
    let section = |name: &str| -> Vec<&str> {
        sections
            .iter()
            .find(|(n, _)| n == name)
            .map(|(_, l)| l.clone())
            .unwrap_or_default()
    };
    let hw = section("Hardware");
    let sw = section("Software");
    let storage = section("Storage");
    let gfx = section("Graphics/Displays");

    let chip = field(&hw, "Chip")
        .or_else(|| field(&hw, "Processor Name"))
        .map(|s| s.to_string());
    let cores = field(&hw, "Total Number of Cores").and_then(|s| {
        s.split_whitespace().next()?.parse::<u32>().ok()
    });
    let memory_mb = field(&hw, "Memory").and_then(parse_gb_to_mb);
    // The OS identity is carried as one string, version and
    // build together, exactly as the profiler states it.
    let os = field(&sw, "System Version").map(|s| s.to_string());
    let disk_total_mb = field(&storage, "Capacity").and_then(parse_gb_to_mb);
    let disk_free_mb = field(&storage, "Free")
        .or_else(|| field(&storage, "Available"))
        .and_then(parse_gb_to_mb);
    let metal = if gfx.is_empty() {
        Measure::Unknown
    } else {
        let supported = gfx.iter().any(|l| {
            let l = l.to_lowercase();
            l.contains("metal") && (l.contains("supported") || l.contains("metal 3") || l.contains("metal 4"))
        });
        Measure::Known(supported)
    };
    // Thermal truth, stated in the record (FR-MAC-06): the
    // fanless lines are the MacBook Airs.
    let thermal_note = match field(&hw, "Model Name") {
        Some(m) if m.contains("MacBook Air") => {
            Some("fanless (MacBook Air): sustained all-core batch work routes elsewhere".to_string())
        }
        _ => None,
    };

    CapabilityRecord {
        host: node.to_string(),
        observed_at_unix: observed_unix,
        chip: measure(chip),
        cores: measure(cores),
        memory: MemoryPool {
            total_mb: measure(memory_mb),
            inference_reserved_mb: 0,
        },
        disk_total_mb: measure(disk_total_mb),
        disk_free_mb: measure(disk_free_mb),
        os: measure(os),
        accelerator: metal,
        docker: docker.presence(),
        tailscale_identity: measure(tailscale_name),
        inference_endpoint: None,
        venues: advertised_venues(docker),
        node_class: NodeClass::Opportunistic,
        // The profiler does not state activation; the record
        // says so rather than guessing.
        activation: ActivationState::Unknown,
        thermal_note,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // A synthetic system_profiler-shaped fixture (values from
    // the spec's proving-machine description: M1, 8 GB,
    // macOS 27.0.1 build 26A434). Synthetic by construction —
    // the human-as-oracle comparison against the real machine
    // is acceptance test 5, a field act.
    const FIXTURE: &str = "\
Hardware:

    Hardware Overview:

      Model Name: MacBook Air
      Model Identifier: MacBookAir10,1
      Chip: Apple M1
      Total Number of Cores: 8 (4 performance and 4 efficiency)
      Memory: 8 GB

Software:

    System Software Overview:

      System Version: macOS 27.0.1 (26A434)
      Kernel Version: Darwin 27.0.0

Storage:

    Data:

      Free: 29.5 GB
      Capacity: 251.1 GB

Graphics/Displays:

    Apple M1:

      Chipset Model: Apple M1
      Metal Support: Metal 3
";

    struct FakeProbe(DockerEngineInfo);
    impl DockerProbe for FakeProbe {
        fn engine(&self) -> DockerEngineInfo {
            self.0.clone()
        }
    }

    // T039-08 capability-matches-system-profiler (parser
    // half): every field the profiler states lands in the
    // record; unified memory is one pool of 8 GB; no VRAM
    // figure exists anywhere in the type.
    #[test]
    fn t039_08_profiler_fixture_parses_to_record() {
        let docker = DockerEngineInfo {
            running: false,
            version: None,
        };
        let rec = record_from_profiler(
            "air-inference",
            FIXTURE,
            &docker,
            Some("air-inference".into()),
            1_700_000_000,
        );
        assert_eq!(rec.chip, Measure::Known("Apple M1".to_string()));
        assert_eq!(rec.cores, Measure::Known(8));
        assert_eq!(rec.memory.total_mb, Measure::Known(8000));
        assert_eq!(rec.disk_total_mb, Measure::Known(251_100));
        assert_eq!(rec.disk_free_mb, Measure::Known(29_500));
        assert_eq!(
            rec.os,
            Measure::Known("macOS 27.0.1 (26A434)".to_string())
        );
        assert_eq!(rec.accelerator, Measure::Known(true));
        assert_eq!(rec.docker, DockerPresence::Absent);
        assert!(rec.thermal_note.as_deref().unwrap().contains("fanless"));
        // The serialized record carries no VRAM figure, ever.
        let json = serde_json::to_string(&rec).unwrap().to_lowercase();
        assert!(!json.contains("vram"), "{json}");
        // Unknown is never zero: strip Memory from the output
        // and the record says Unknown, with no numeric form.
        let no_mem = FIXTURE.replace("      Memory: 8 GB\n", "");
        let rec2 = record_from_profiler("air-inference", &no_mem, &docker, None, 0);
        assert_eq!(rec2.memory.total_mb, Measure::Unknown);
        assert_eq!(rec2.memory.total_mb.as_option(), None);
    }

    // T039-06 docker-venue-probed-not-assumed: the venue is
    // advertised only when the probe finds an engine
    // answering; no engine = native-only, recorded, NOT an
    // error.
    #[test]
    fn t039_06_docker_venue_probed_not_assumed() {
        let running = FakeProbe(DockerEngineInfo {
            running: true,
            version: Some("27.3.1".into()),
        });
        assert_eq!(
            advertised_venues(&running.engine()),
            vec!["native".to_string(), "docker".to_string()]
        );
        let absent = NoDockerProbe;
        let info = absent.engine();
        assert_eq!(info.presence(), DockerPresence::Absent);
        assert_eq!(advertised_venues(&info), vec!["native".to_string()]);
        // The record carries the finding as data.
        let rec = record_from_profiler("air-inference", FIXTURE, &info, None, 0);
        assert_eq!(rec.docker, DockerPresence::Absent);
        assert_eq!(rec.venues, vec!["native".to_string()]);
    }
}
