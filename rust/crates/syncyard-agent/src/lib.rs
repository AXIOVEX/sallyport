//! Syncyard backchannel agent.
//!
//! P1 scaffold only: the agent (progress-file transport per spec 034
//! section 3, capability probe, windowless-by-construction supervision)
//! is implemented in program phase P3. The agent is windowless by
//! construction on every platform (spec 038 FR-038-54) and must never
//! open a visible console window.

/// Role marker for diagnostics and the future identity stamp (UPD-03).
pub const CRATE_ROLE: &str = "backchannel-agent";
