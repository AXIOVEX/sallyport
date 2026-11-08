//! Syncyard gatekeeper daemon (spec 038 section 2, spec 032).
//!
//! The long-lived daemon holds live job records RAM-authoritative
//! and serves them over a Unix domain socket; the filesystem in the
//! state home is a write-behind cache flushed every 60 s and on
//! transitions with verified writes. Single writer via flock,
//! fencing via a monotonic epoch on the off-VM record, mandatory
//! boot reconciliation, and a two-phase ledgered dispatch protocol
//! with a GitHub-verifiable idempotency key.
//!
//! Module map:
//! - [`state`] — RAM state, the five record classes, flush, snapshot.
//! - [`vwrite`] — verified writes + the wave-aware writer trait.
//! - [`lock`] — the single-writer flock + dead-owner owner locks.
//! - [`offvm`] — the off-VM store trait, directory store, fencing.
//! - [`dispatch`] — the two-phase dispatch protocol + recovery.
//! - [`lanes`] — venue lanes, settle-first occupancy, starvation.
//! - [`reconcile`] — boot reconciliation + ghost completion.
//! - [`migrate`] — migrate-history with SHA-256 manifest + chain.
//! - [`publish`] — per-destination verified publishing.
//! - [`progress`] — streaming-progress ingestion and views
//!   (spec 034): attributed batches, cadence, staleness, and
//!   verdict hygiene on the daemon side.
//! - [`serve`] — the Unix-socket server and verb handlers.
//! - [`time`] — wall-clock helpers (display only; ordering is seq).

pub mod control;
pub mod dispatch;
pub mod lanes;
pub mod lock;
pub mod migrate;
pub mod offvm;
pub mod profile;
pub mod progress;
pub mod publish;
pub mod reconcile;
pub mod registry;
pub mod repair;
pub mod reporting;
pub mod resources;
pub mod serve;
pub mod state;
pub mod time;
pub mod update;
pub mod vwrite;
pub mod workflowpin;

/// Role marker for diagnostics and the future identity stamp (UPD-03).
pub const CRATE_ROLE: &str = "gatekeeper-daemon";
