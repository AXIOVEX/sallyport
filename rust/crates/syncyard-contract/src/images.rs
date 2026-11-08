//! The four shared container images (spec 038 FR-038-17,
//! spec 033 inherited).
//!
//! Docker execution uses ONLY these four images, each pinned
//! by digest, each publishing a multi-arch manifest. The pin
//! set itself is release data (it changes when images are
//! rebuilt); what lives here is the canonical name set and the
//! mechanical validation every candidate pin set must pass
//! before any venue may use it. A tag is not a pin: `latest`
//! is a moving target and is refused by shape.

use serde::{Deserialize, Serialize};

/// The canonical four, by name. No fifth image may join
/// without a spec change.
pub const SHARED_IMAGE_NAMES: [&str; 4] = ["linux-dev", "data-py", "zephyr", "node-web"];

/// Architectures every image manifest must carry (the fleet
/// runs x86_64 hosts and the aarch64 Jetson, spec 038
/// FR-038-22).
pub const REQUIRED_ARCHES: [&str; 2] = ["linux/amd64", "linux/arm64"];

/// One image's pin: digest-pinned, with the architectures its
/// published manifest carries.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct ImagePin {
    pub name: String,
    /// `sha256:<64 lowercase hex>` — never a tag.
    pub digest: String,
    pub arches: Vec<String>,
}

/// Why a candidate pin set was refused. Every variant names
/// the defect; none is a generic "invalid".
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ImageSetError {
    MissingImage(String),
    UnknownImage(String),
    DuplicateImage(String),
    NotDigestPinned(String),
    MissingArch { image: String, arch: String },
}

impl std::fmt::Display for ImageSetError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            ImageSetError::MissingImage(n) => write!(f, "shared image {n} has no pin"),
            ImageSetError::UnknownImage(n) => {
                write!(f, "{n} is not one of the four shared images")
            }
            ImageSetError::DuplicateImage(n) => write!(f, "image {n} is pinned twice"),
            ImageSetError::NotDigestPinned(n) => {
                write!(f, "image {n} is not pinned by a sha256 digest")
            }
            ImageSetError::MissingArch { image, arch } => {
                write!(f, "image {image} manifest lacks required arch {arch}")
            }
        }
    }
}
impl std::error::Error for ImageSetError {}

fn is_digest(s: &str) -> bool {
    match s.strip_prefix("sha256:") {
        Some(hex) => {
            hex.len() == 64
                && hex
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        }
        None => false,
    }
}

/// Validate a candidate pin set against the spec 033 rules:
/// exactly the four shared images, each digest-pinned, each
/// carrying every required architecture.
pub fn validate_image_set(pins: &[ImagePin]) -> Result<(), ImageSetError> {
    for name in SHARED_IMAGE_NAMES {
        let found: Vec<&ImagePin> = pins.iter().filter(|p| p.name == name).collect();
        match found.len() {
            0 => return Err(ImageSetError::MissingImage(name.to_string())),
            1 => {}
            _ => return Err(ImageSetError::DuplicateImage(name.to_string())),
        }
        let pin = found[0];
        if !is_digest(&pin.digest) {
            return Err(ImageSetError::NotDigestPinned(name.to_string()));
        }
        for arch in REQUIRED_ARCHES {
            if !pin.arches.iter().any(|a| a == arch) {
                return Err(ImageSetError::MissingArch {
                    image: name.to_string(),
                    arch: arch.to_string(),
                });
            }
        }
    }
    for pin in pins {
        if !SHARED_IMAGE_NAMES.contains(&pin.name.as_str()) {
            return Err(ImageSetError::UnknownImage(pin.name.clone()));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn good_set() -> Vec<ImagePin> {
        SHARED_IMAGE_NAMES
            .iter()
            .enumerate()
            .map(|(i, name)| ImagePin {
                name: name.to_string(),
                digest: format!("sha256:{:064x}", i + 1),
                arches: vec!["linux/amd64".into(), "linux/arm64".into()],
            })
            .collect()
    }

    // T038-19 four-images-pinned-multiarch: a set of exactly
    // the four shared images, digest-pinned and multi-arch,
    // validates; every deviation is refused by name.
    #[test]
    fn t038_19_four_images_pinned_multiarch() {
        assert!(validate_image_set(&good_set()).is_ok());

        let mut missing = good_set();
        missing.retain(|p| p.name != "zephyr");
        assert_eq!(
            validate_image_set(&missing),
            Err(ImageSetError::MissingImage("zephyr".into()))
        );

        let mut fifth = good_set();
        fifth.push(ImagePin {
            name: "custom".into(),
            digest: format!("sha256:{:064x}", 9),
            arches: vec!["linux/amd64".into(), "linux/arm64".into()],
        });
        assert_eq!(
            validate_image_set(&fifth),
            Err(ImageSetError::UnknownImage("custom".into()))
        );

        // A tag is not a pin.
        let mut tagged = good_set();
        tagged[0].digest = "latest".into();
        assert_eq!(
            validate_image_set(&tagged),
            Err(ImageSetError::NotDigestPinned("linux-dev".into()))
        );

        // Single-arch is not multi-arch.
        let mut one_arch = good_set();
        one_arch[1].arches = vec!["linux/amd64".into()];
        assert_eq!(
            validate_image_set(&one_arch),
            Err(ImageSetError::MissingArch {
                image: "data-py".into(),
                arch: "linux/arm64".into()
            })
        );
    }
}
