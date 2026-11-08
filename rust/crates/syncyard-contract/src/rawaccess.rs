//! Raw-device access routing (spec 038 FR-038-48,
//! writer-route defects 2026-11-06).
//!
//! A non-elevated runner cannot raw-write a physical drive.
//! Any job needing raw device access MUST route through the
//! elevated kit venue and MUST say so in its own record. The
//! gatekeeper-side half of that rule — routing and record —
//! is mechanical and lives here; the kit-side device open is
//! Windows-host code, proven on the host.

/// The only venue a raw-device job may bind to.
pub const ELEVATED_KIT_VENUE: &str = "elevated-kit";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RawAccessError {
    /// The job needs raw access but is bound elsewhere.
    WrongVenue { venue: String },
    /// The job's record does not state its raw-access need.
    NotStatedInRecord,
}

impl std::fmt::Display for RawAccessError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            RawAccessError::WrongVenue { venue } => write!(
                f,
                "raw-device job bound to venue {venue}; raw access routes only via {ELEVATED_KIT_VENUE}"
            ),
            RawAccessError::NotStatedInRecord => {
                write!(f, "raw-device job does not state its need in its record")
            }
        }
    }
}
impl std::error::Error for RawAccessError {}

/// Check a job's raw-access routing. `record_states_need` is
/// whether the job record itself declares the raw-device
/// requirement (e.g. a `raw-device` flag on the record).
pub fn check_raw_access(
    needs_raw_device: bool,
    venue: &str,
    record_states_need: bool,
) -> Result<(), RawAccessError> {
    if !needs_raw_device {
        return Ok(());
    }
    if !record_states_need {
        return Err(RawAccessError::NotStatedInRecord);
    }
    if venue != ELEVATED_KIT_VENUE {
        return Err(RawAccessError::WrongVenue {
            venue: venue.to_string(),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    // T038-63 (record/route half; the OS-level refusal on the
    // laptop is the field half): a raw-device job routes only
    // through the elevated kit venue and only when its record
    // states the need.
    #[test]
    fn t038_63_raw_access_routing_and_record() {
        assert!(check_raw_access(false, "native", false).is_ok());
        assert!(check_raw_access(true, ELEVATED_KIT_VENUE, true).is_ok());
        assert_eq!(
            check_raw_access(true, "native", true),
            Err(RawAccessError::WrongVenue {
                venue: "native".into()
            })
        );
        assert_eq!(
            check_raw_access(true, ELEVATED_KIT_VENUE, false),
            Err(RawAccessError::NotStatedInRecord)
        );
    }
}
