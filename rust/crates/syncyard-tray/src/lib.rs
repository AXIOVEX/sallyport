//! syncyard-tray: the successor tray app (spec 035
//! presentation + spec 037 job control + spec 038's
//! settings-in-app and one-version-one-train rules).
//!
//! The crate is layered for headless testability: every
//! presentation rule lives in pure modules (`connection`,
//! `fleet`, `observe`, `render`) over gatekeeper records; the
//! OS shell integration (real tray icon/menu rendering on
//! Windows/Linux/macOS) sits behind the thin platform layer
//! and is NOT implemented in this phase — see PROGRESS.md P4
//! for the named live-GUI gap. What is implemented and proven
//! here: the full presentation state machine, the
//! singleton-by-install-path discipline (FR-035-17), the
//! heartbeat + foreign-instance detection, settings as a
//! surface of this one app, and spec 037 control (signed
//! command files + the FIX-08 kill switch) end to end against
//! the real daemon.

pub mod connection;
pub mod control;
pub mod exit;
pub mod fleet;
pub mod heartbeat;
pub mod identity;
pub mod observe;
pub mod platform;
pub mod render;
pub mod settings;
pub mod singleton;

pub const CRATE_ROLE: &str = "tray-app";
