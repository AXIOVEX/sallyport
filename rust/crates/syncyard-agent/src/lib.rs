//! Syncyard backchannel agent (P3).
//!
//! The per-host runner-side agent for the native venue: it polls
//! the gatekeeper daemon for assignments, executes payloads
//! windowlessly, streams their progress back per spec 034, and
//! reports completion with the payload's own exit code as the
//! verdict source (spec 020 — wrapper success is never job
//! success).
//!
//! Module map:
//! - [`channel`] — the `ControlChannel` trait + error type: the
//!   venue surface between agent and daemon.
//! - [`fake`] — the in-memory channel the suite runs against.
//! - [`socket`] — the real channel: the daemon's Unix socket.
//! - [`singleton`] — one instance per install path, by flock.
//! - [`spawn`] — windowless-by-construction payload spawning
//!   (spec 038 FR-038-54).
//! - [`runner`] — one assignment end to end: progress-file
//!   preparation (FR-034-01), tail-and-forward, completion.
//! - [`agent`] — the supervised unit: singleton + work cycles
//!   + completion-delivery retry.
//!
//! The agent is windowless by construction on every platform
//! and must never open a visible console window; the binary is
//! built with the `windows` subsystem on Windows (see main.rs).

pub mod agent;
pub mod channel;
pub mod dockerspec;
pub mod fake;
pub mod launchd;
pub mod preflight;
pub mod probe;
pub mod runner;
pub mod singleton;
pub mod socket;
pub mod spawn;

/// Role marker for diagnostics and the future identity stamp (UPD-03).
pub const CRATE_ROLE: &str = "backchannel-agent";
