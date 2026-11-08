//! Syncyard gatekeeper daemon (spec 038 section 2).
//!
//! P1 scaffold only: the daemon itself (RAM-authoritative state, Unix
//! socket, single-writer flock, fencing epoch, boot reconciliation) is
//! implemented in program phase P2. This crate exists so the workspace
//! layout, naming, and contract dependency are in place and compiling.

/// Role marker for diagnostics and the future identity stamp (UPD-03).
pub const CRATE_ROLE: &str = "gatekeeper-daemon";
