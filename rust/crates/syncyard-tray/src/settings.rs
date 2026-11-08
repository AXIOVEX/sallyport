//! Settings (FR-035-20, spec 038 FR-038-38): the app's
//! settings live **inside the one app** as a separate surface
//! — there is no second binary and no external settings
//! editor. This module is the settings model (stored in an
//! app-owned file) plus the surface state machine the shell
//! renders; editing happens only through the app's own
//! settings surface.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::identity::BuildIdentity;

/// The app's settings. Threshold defaults are spec 035
/// section 3's (180 s / 900 s).
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Settings {
    /// Connection: age at which the link reads STALE.
    pub connection_stale_s: i64,
    /// Connection: age at which the link reads DISCONNECTED.
    pub connection_disconnect_s: i64,
    /// Fleet values older than this render stale, with age.
    pub fleet_stale_s: i64,
    /// Where the fleet-status file lives on this host.
    pub fleet_status_path: Option<String>,
    /// Shared heartbeat directory (foreign-instance detection).
    pub heartbeat_dir: Option<String>,
    /// Directory command files are written into for the
    /// backchannel to carry to the gatekeeper host (spec 037).
    pub command_outbox_dir: Option<String>,
}

impl Default for Settings {
    fn default() -> Self {
        Settings {
            connection_stale_s: 180,
            connection_disconnect_s: 900,
            fleet_stale_s: 900,
            fleet_status_path: None,
            heartbeat_dir: None,
            command_outbox_dir: None,
        }
    }
}

impl Settings {
    /// The settings file's name in the app state directory.
    pub fn file_path(state_dir: &Path) -> PathBuf {
        state_dir.join("tray-settings.json")
    }

    /// Load from the app state directory; defaults when the
    /// file does not exist yet.
    pub fn load(state_dir: &Path) -> std::io::Result<Settings> {
        match std::fs::read_to_string(Self::file_path(state_dir)) {
            Ok(text) => serde_json::from_str(&text)
                .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e)),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Settings::default()),
            Err(e) => Err(e),
        }
    }

    /// Save atomically to the app state directory.
    pub fn save(&self, state_dir: &Path) -> std::io::Result<()> {
        self.validate()
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidInput, e))?;
        std::fs::create_dir_all(state_dir)?;
        let text = serde_json::to_string_pretty(self)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
        let tmp = state_dir.join("tray-settings.json.tmp");
        std::fs::write(&tmp, text)?;
        std::fs::rename(&tmp, Self::file_path(state_dir))
    }

    /// Shape rules: thresholds positive, stale before
    /// disconnect — a settings value that breaks the
    /// presentation math is refused, never stored.
    pub fn validate(&self) -> Result<(), String> {
        if self.connection_stale_s <= 0 || self.connection_disconnect_s <= 0 {
            return Err("connection thresholds must be positive".to_string());
        }
        if self.connection_stale_s >= self.connection_disconnect_s {
            return Err("connection stale threshold must be below the disconnect threshold"
                .to_string());
        }
        if self.fleet_stale_s <= 0 {
            return Err("fleet staleness bound must be positive".to_string());
        }
        Ok(())
    }
}

/// The app's surfaces. Settings is one surface of the one
/// app; switching to it is the only way settings are edited.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AppSurface {
    Status,
    JobControl,
    Settings,
}

/// The app object the shell drives: identity, settings, and
/// the current surface.
pub struct TrayApp {
    pub identity: BuildIdentity,
    pub settings: Settings,
    pub surface: AppSurface,
}

impl TrayApp {
    pub fn new(identity: BuildIdentity, settings: Settings) -> Self {
        TrayApp {
            identity,
            settings,
            surface: AppSurface::Status,
        }
    }

    /// Open the settings surface (the Settings menu row's act).
    pub fn open_settings(&mut self) {
        self.surface = AppSurface::Settings;
    }

    /// Open the job-control surface.
    pub fn open_job_control(&mut self) {
        self.surface = AppSurface::JobControl;
    }

    /// Back to the status surface.
    pub fn open_status(&mut self) {
        self.surface = AppSurface::Status;
    }

    /// Edit settings: possible only while the settings surface
    /// is the open one — there is no other editing path in the
    /// app, and no external editor exists.
    pub fn edit_settings(&mut self, new: Settings) -> Result<(), String> {
        if self.surface != AppSurface::Settings {
            return Err("settings are edited on the settings surface".to_string());
        }
        new.validate()?;
        self.settings = new;
        Ok(())
    }
}
