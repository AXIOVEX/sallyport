//! Syncyard tray/app (successor app; specs 035 and 037).
//!
//! P1 scaffold only: presentation (spec 035) and job control (spec 037)
//! are implemented in program phase P4, including singleton-by-install-
//! path enforcement (FR-035-17, the laptop two-tray fix). The app is
//! windowless in the console sense on every platform: it must never
//! open a visible console window (FR-035-21).

/// Role marker for diagnostics and the future identity stamp (UPD-03).
pub const CRATE_ROLE: &str = "tray-app";
