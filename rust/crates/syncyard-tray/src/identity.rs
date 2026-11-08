//! Build identity (spec 035 section 1, spec 038 UPD-01):
//! every surface displays the train number and build stamp
//! this binary was built as, and a stamp that disagrees with
//! the train is a defect state — the app says which build it
//! is, it does not merely prove it is alive.

/// Train + build stamp as displayed on every surface.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BuildIdentity {
    /// Release train (the one version every component reports).
    pub train: String,
    /// Build stamp (provenance: commit / build id).
    pub build_stamp: String,
}

impl BuildIdentity {
    /// The identity of this binary: train from the workspace
    /// VERSION, stamp from `SYNCYARD_BUILD_STAMP` when the build
    /// provided one (falling back to the train itself, which
    /// agrees by construction).
    pub fn current() -> Self {
        let train = env!("CARGO_PKG_VERSION").to_string();
        let build_stamp = option_env!("SYNCYARD_BUILD_STAMP")
            .map(|s| s.to_string())
            .unwrap_or_else(|| train.clone());
        BuildIdentity { train, build_stamp }
    }

    /// True when the stamp does not carry the train — the
    /// defect state FR-035-01 requires surfaces to show.
    pub fn stamp_defect(&self) -> bool {
        !self.build_stamp.starts_with(&self.train)
    }

    /// The identity line every surface carries.
    pub fn header_line(&self) -> String {
        if self.build_stamp == self.train {
            format!("Syncyard {}", self.train)
        } else {
            format!("Syncyard {} · build {}", self.train, self.build_stamp)
        }
    }
}
