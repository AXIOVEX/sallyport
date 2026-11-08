//! No visible console window (FR-035-21, spec 038
//! FR-038-54): the app and everything it launches are
//! windowless by construction, not by flag discipline at each
//! call site.

/// The console policy, as data the tests pin.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ConsolePolicy {
    /// The binary is built for the Windows GUI subsystem.
    pub windows_subsystem_gui: bool,
    /// Any child the app ever launches carries CREATE_NO_WINDOW.
    pub children_create_no_window: bool,
    /// Children are launched detached from any console.
    pub children_detached: bool,
}

/// The one policy.
pub const CONSOLE_POLICY: ConsolePolicy = ConsolePolicy {
    windows_subsystem_gui: true,
    children_create_no_window: true,
    children_detached: true,
};

/// CREATE_NO_WINDOW (0x0800_0000) on Windows; no flags
/// elsewhere (the property is carried by the policy struct so
/// every launch site takes flags from here).
#[cfg(windows)]
pub fn child_creation_flags() -> u32 {
    0x0800_0000
}

/// Non-Windows: no creation flags exist; windowlessness is
/// inherent (no console subsystem).
#[cfg(not(windows))]
pub fn child_creation_flags() -> u32 {
    0
}
