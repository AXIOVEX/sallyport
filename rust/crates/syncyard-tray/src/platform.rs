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

/// The host surface the control app presents on (spec 035's
/// rules on every platform; spec 039 FR-MAC-09 for macOS;
/// spec 038 section 12 for COSMIC/Wayland).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostSurface {
    WindowsTray,
    MacMenuBar,
    LinuxTray,
    /// COSMIC on Wayland: the session forbids several things
    /// X11/Windows trays take for granted (below).
    CosmicWayland,
}

/// What a surface may and must do, as data. Two invariants
/// hold on EVERY row: the surface carries the running build's
/// identity stamp (the served-identity rule, spec 035
/// section 1 / FR-MAC-09), and the app is windowless.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SurfaceDescriptor {
    pub surface: HostSurface,
    /// The presence's name on this platform.
    pub presence: &'static str,
    /// Spec 035 served-identity: the surface shows the build
    /// stamp of the app actually running.
    pub carries_identity_stamp: bool,
    /// Global hotkeys (Wayland sessions do not offer them).
    pub global_hotkeys: bool,
    /// Always-on-top windows (likewise).
    pub always_on_top: bool,
    pub windowless: bool,
}

/// The surface descriptor for a platform + session. `os` is
/// the platform family ("windows", "macos", "linux");
/// `session` is the desktop session name when known
/// ("cosmic", "gnome", ...).
pub fn surface_for(os: &str, session: Option<&str>) -> SurfaceDescriptor {
    let session = session.unwrap_or("").to_lowercase();
    match os {
        "macos" => SurfaceDescriptor {
            surface: HostSurface::MacMenuBar,
            presence: "menu bar",
            carries_identity_stamp: true,
            global_hotkeys: true,
            always_on_top: false,
            windowless: true,
        },
        "windows" => SurfaceDescriptor {
            surface: HostSurface::WindowsTray,
            presence: "system tray",
            carries_identity_stamp: true,
            global_hotkeys: true,
            always_on_top: false,
            windowless: true,
        },
        _ if session.contains("cosmic") || session.contains("wayland") => SurfaceDescriptor {
            surface: HostSurface::CosmicWayland,
            presence: "panel applet (Wayland)",
            carries_identity_stamp: true,
            global_hotkeys: false,
            always_on_top: false,
            windowless: true,
        },
        _ => SurfaceDescriptor {
            surface: HostSurface::LinuxTray,
            presence: "system tray",
            carries_identity_stamp: true,
            global_hotkeys: true,
            always_on_top: false,
            windowless: true,
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::identity::BuildIdentity;

    // T039-17 menubar-app-identity-stamp: on macOS the app's
    // presence is the MENU BAR (not a tray icon), and the
    // surface carries the identity stamp of the build
    // actually running — the same BuildIdentity every other
    // surface renders, subject to the same stamp-defect rule.
    #[test]
    fn t039_17_menubar_app_identity_stamp() {
        let d = surface_for("macos", None);
        assert_eq!(d.surface, HostSurface::MacMenuBar);
        assert_eq!(d.presence, "menu bar");
        assert!(d.carries_identity_stamp);
        assert!(d.windowless);
        let id = BuildIdentity::current();
        assert_eq!(id.train, "0.4.0");
        let line = id.header_line();
        assert!(line.starts_with("Syncyard 0.4.0"), "{line}");
        assert!(!id.stamp_defect(), "this build's stamp agrees with its train");
        // A stamp from another train IS the defect state.
        let foreign = BuildIdentity {
            train: "0.4.0".into(),
            build_stamp: "0.3.0-xyz".into(),
        };
        assert!(foreign.stamp_defect());
    }

    // T038-29 cosmic-wayland-tray (policy half): on a
    // COSMIC/Wayland session the surface degrades by rule —
    // no global hotkeys, no always-on-top — while the
    // presentation invariants (identity stamp, windowless)
    // hold unchanged. The live COSMIC rendering proof is a
    // field act on tower-offsite (the fleet's COSMIC host),
    // recorded in PROGRESS.md.
    #[test]
    fn t038_29_cosmic_wayland_tray() {
        let d = surface_for("linux", Some("cosmic"));
        assert_eq!(d.surface, HostSurface::CosmicWayland);
        assert!(!d.global_hotkeys, "Wayland offers no global hotkeys");
        assert!(!d.always_on_top);
        assert!(d.carries_identity_stamp);
        assert!(d.windowless);
        // Session detection is by the session name the host
        // reports; a plain Linux session keeps the tray row.
        assert_eq!(surface_for("linux", Some("gnome")).surface, HostSurface::LinuxTray);
        assert_eq!(surface_for("linux", None).surface, HostSurface::LinuxTray);
        // Every surface row carries the stamp: no exceptions.
        for os in ["windows", "macos", "linux"] {
            assert!(surface_for(os, None).carries_identity_stamp);
        }
    }
}

