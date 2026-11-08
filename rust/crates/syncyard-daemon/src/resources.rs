//! Resource honesty (spec 038 FR-038-14, red-team H3).
//!
//! Declared job needs are enforced as hard limits: a job
//! whose measured use exceeds its enforced envelope kills
//! ITSELF, not its host or co-tenants — the decision made
//! here, executed by the venue. Measured-versus-declared is
//! recorded per program; repeat under-declaration
//! automatically widens that program's enforced envelopes, so
//! a program that chronically lies about its needs gets a
//! bigger, honest envelope instead of repeated kills.

use std::collections::BTreeMap;

/// How many under-declarations trigger an automatic widening.
pub const WIDEN_AFTER_UNDERDECLARATIONS: u32 = 3;

/// One program's resource ledger line (measured vs declared).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UsageRecord {
    pub program: String,
    pub declared_ram_mb: u64,
    pub measured_ram_mb: u64,
    pub underdeclared: bool,
    /// The envelope was widened by this record.
    pub widened_to_mb: Option<u64>,
}

/// Per-program envelope tracker.
#[derive(Debug, Default)]
pub struct EnvelopeTracker {
    /// Enforced RAM envelope per program (MB). A program's
    /// first declaration sets its envelope.
    envelopes: BTreeMap<String, u64>,
    underdeclarations: BTreeMap<String, u32>,
    pub records: Vec<UsageRecord>,
}

impl EnvelopeTracker {
    /// The hard-limit decision for a running job: measured
    /// use above the enforced envelope is a self-kill; the
    /// host and co-tenants are never the victim.
    pub fn exceeds_envelope(&self, program: &str, measured_ram_mb: u64) -> bool {
        match self.envelopes.get(program) {
            Some(limit) => measured_ram_mb > *limit,
            None => false,
        }
    }

    pub fn envelope_mb(&self, program: &str) -> Option<u64> {
        self.envelopes.get(program).copied()
    }

    /// Record one job's measured-versus-declared use. The
    /// returned record is what the caller ledgers (FR-038-14:
    /// measured-vs-declared is ledgered per program).
    pub fn record_usage(
        &mut self,
        program: &str,
        declared_ram_mb: u64,
        measured_ram_mb: u64,
    ) -> UsageRecord {
        self.envelopes
            .entry(program.to_string())
            .or_insert(declared_ram_mb);
        let underdeclared = measured_ram_mb > declared_ram_mb;
        let mut widened_to_mb = None;
        if underdeclared {
            let count = {
                let c = self
                    .underdeclarations
                    .entry(program.to_string())
                    .or_insert(0);
                *c += 1;
                *c
            };
            if count >= WIDEN_AFTER_UNDERDECLARATIONS {
                let env = self.envelopes.get_mut(program).unwrap();
                if measured_ram_mb > *env {
                    *env = measured_ram_mb;
                    widened_to_mb = Some(measured_ram_mb);
                }
                self.underdeclarations.insert(program.to_string(), 0);
            }
        }
        let record = UsageRecord {
            program: program.to_string(),
            declared_ram_mb,
            measured_ram_mb,
            underdeclared,
            widened_to_mb,
        };
        self.records.push(record.clone());
        record
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // T038-15 resource-limits-enforced: the envelope set by
    // declaration is the hard limit; exceeding it is the
    // job's own kill, decided here.
    #[test]
    fn t038_15_resource_limits_enforced() {
        let mut tr = EnvelopeTracker::default();
        tr.record_usage("prog", 1024, 900);
        assert!(!tr.exceeds_envelope("prog", 1024));
        assert!(
            tr.exceeds_envelope("prog", 1025),
            "one MB over the envelope is over"
        );
        assert!(!tr.exceeds_envelope("never-declared", 1 << 30));
    }

    // T038-16 underdeclaration-widens-envelope: every use is
    // recorded measured-vs-declared; the third repeat
    // under-declaration widens the enforced envelope to the
    // measured truth.
    #[test]
    fn t038_16_underdeclaration_widens_envelope() {
        let mut tr = EnvelopeTracker::default();
        let r1 = tr.record_usage("prog", 1024, 1500);
        assert!(r1.underdeclared && r1.widened_to_mb.is_none());
        let r2 = tr.record_usage("prog", 1024, 1600);
        assert!(r2.underdeclared && r2.widened_to_mb.is_none());
        assert_eq!(tr.envelope_mb("prog"), Some(1024), "not yet widened");
        let r3 = tr.record_usage("prog", 1024, 1700);
        assert_eq!(r3.widened_to_mb, Some(1700));
        assert_eq!(tr.envelope_mb("prog"), Some(1700));
        // Honest declarations never widen and never count.
        let r4 = tr.record_usage("prog", 2048, 1000);
        assert!(!r4.underdeclared);
        assert_eq!(tr.records.len(), 4, "every use is recorded");
    }
}
