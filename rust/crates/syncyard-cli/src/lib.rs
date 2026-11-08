//! Syncyard CLI (spec 038 sections 2 and 9).
//!
//! P1 scaffold only: the verb set at parity with the Python gatekeeper's
//! CLI (submit, dispatch, status, sweep, snapshot, migrate-history, ...)
//! is implemented in program phase P2 as thin daemon-socket clients with
//! a direct-file fallback. The CLI/daemon contract is versioned
//! (spec 034 FR-034-11); see `syncyard_contract::CONTRACT_VERSION`.

/// Role marker for diagnostics and the future identity stamp (UPD-03).
pub const CRATE_ROLE: &str = "cli";
