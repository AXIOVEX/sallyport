//! The container run-spec builder (spec 038 section 3,
//! FR-038-08). Every container-venue job runs under the
//! fleet's Docker posture, which is not configurable per
//! job: non-root user, read-only root filesystem, all
//! capabilities dropped, the Docker socket NEVER mounted,
//! secrets delivered only as tmpfs files, and egress
//! restricted to the allowlist declared for the job's
//! image in the venue configuration. A job that asks for
//! anything outside the posture is refused by name — there
//! is no flag that widens it. Images are the four shared
//! images (contract `images`), addressed by digest pin.

use syncyard_contract::images::{ImagePin, SHARED_IMAGE_NAMES};

#[derive(Debug, Clone, PartialEq)]
pub struct ContainerRunSpec {
    pub image: String,
    pub image_ref: String,
    pub user: String,
    pub read_only_rootfs: bool,
    pub cap_drop_all: bool,
    pub docker_socket_mounted: bool,
    pub secrets_via_tmpfs: bool,
    pub egress_allowlist: Vec<String>,
    pub command: Vec<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DockerSpecError {
    /// The image is not one of the four shared images.
    UnknownImage { image: String },
    /// The pin is not a sha256 digest (a tag is not a pin).
    NotDigestPinned { image: String },
    /// The job asked for egress the venue did not declare
    /// for this image.
    EgressNotAllowed { image: String, host: String },
    /// The job tried to widen the posture itself.
    PostureWidening { what: String },
}

impl std::fmt::Display for DockerSpecError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            DockerSpecError::UnknownImage { image } => {
                write!(f, "image {image} is not one of the four shared images")
            }
            DockerSpecError::NotDigestPinned { image } => {
                write!(f, "image {image} is not pinned by a sha256 digest")
            }
            DockerSpecError::EgressNotAllowed { image, host } => write!(
                f,
                "egress to {host} is not on {image}'s declared allowlist"
            ),
            DockerSpecError::PostureWidening { what } => {
                write!(f, "the Docker posture cannot be widened: {what}")
            }
        }
    }
}
impl std::error::Error for DockerSpecError {}

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

impl ContainerRunSpec {
    /// Build the spec for one container job. `pin` must name
    /// one of the four shared images and carry a sha256
    /// digest; `want_egress` must be a subset of
    /// `declared_egress` (the venue configuration's allowlist
    /// for this image — empty means no network egress).
    pub fn for_job(
        pin: &ImagePin,
        command: Vec<String>,
        want_egress: &[String],
        declared_egress: &[String],
    ) -> Result<ContainerRunSpec, DockerSpecError> {
        if !SHARED_IMAGE_NAMES.contains(&pin.name.as_str()) {
            return Err(DockerSpecError::UnknownImage {
                image: pin.name.clone(),
            });
        }
        if !is_digest(&pin.digest) {
            return Err(DockerSpecError::NotDigestPinned {
                image: pin.name.clone(),
            });
        }
        for host in want_egress {
            if !declared_egress.contains(host) {
                return Err(DockerSpecError::EgressNotAllowed {
                    image: pin.name.clone(),
                    host: host.clone(),
                });
            }
        }
        Ok(ContainerRunSpec {
            image: pin.name.clone(),
            image_ref: format!("{}@{}", pin.name, pin.digest),
            user: "10001:10001".to_string(),
            read_only_rootfs: true,
            cap_drop_all: true,
            docker_socket_mounted: false,
            secrets_via_tmpfs: true,
            egress_allowlist: want_egress.to_vec(),
            command,
        })
    }

    /// Refuse any attempt to run a container with the
    /// socket mounted (a named posture widening).
    pub fn refuse_socket_mount() -> DockerSpecError {
        DockerSpecError::PostureWidening {
            what: "docker socket mount".to_string(),
        }
    }

    /// The `docker run` argv this spec renders to. The
    /// posture flags are literal in the argv, so an audit
    /// can read them off the command line itself.
    pub fn argv(&self) -> Vec<String> {
        let mut v: Vec<String> = vec![
            "docker".into(),
            "run".into(),
            "--rm".into(),
            "--user".into(),
            self.user.clone(),
            "--read-only".into(),
            "--cap-drop".into(),
            "ALL".into(),
            "--security-opt".into(),
            "no-new-privileges".into(),
        ];
        if self.secrets_via_tmpfs {
            v.push("--tmpfs".into());
            v.push("/run/secrets:rw,noexec,nosuid,size=1m".into());
        }
        v.push(self.image_ref.clone());
        v.extend(self.command.iter().cloned());
        v
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn pin(name: &str) -> ImagePin {
        ImagePin {
            name: name.to_string(),
            digest: format!("sha256:{}", "a".repeat(64)),
            arches: vec!["linux/amd64".into(), "linux/arm64".into()],
        }
    }

    // T038-20 docker-run-spec-nonroot-rocap: the rendered
    // spec carries the whole posture, the socket is never
    // mounted, unknown images, tag-pins and undeclared
    // egress are refused by name.
    #[test]
    fn t038_20_docker_run_spec_nonroot_rocap() {
        let declared = vec!["github.com".to_string()];
        let spec = ContainerRunSpec::for_job(
            &pin("linux-dev"),
            vec!["/payload/run.sh".into()],
            &["github.com".to_string()],
            &declared,
        )
        .unwrap();
        assert_eq!(spec.user, "10001:10001", "non-root");
        assert!(spec.read_only_rootfs);
        assert!(spec.cap_drop_all);
        assert!(!spec.docker_socket_mounted, "socket NEVER mounted");
        assert!(spec.secrets_via_tmpfs);
        let argv = spec.argv().join(" ");
        assert!(argv.contains("--read-only"), "{argv}");
        assert!(argv.contains("--cap-drop ALL"), "{argv}");
        assert!(argv.contains("--user 10001:10001"), "{argv}");
        assert!(
            !argv.contains("docker.sock"),
            "no socket path anywhere in the argv: {argv}"
        );
        assert!(
            spec.image_ref.contains("@sha256:"),
            "the image is digest-pinned: {}",
            spec.image_ref
        );
        // Egress beyond the declared allowlist: refused.
        assert_eq!(
            ContainerRunSpec::for_job(
                &pin("linux-dev"),
                vec![],
                &["169.254.169.254".to_string()],
                &declared,
            ),
            Err(DockerSpecError::EgressNotAllowed {
                image: "linux-dev".into(),
                host: "169.254.169.254".into(),
            })
        );
        // No declared egress at all (the metadata posture):
        // any ask refuses.
        assert!(matches!(
            ContainerRunSpec::for_job(&pin("data-py"), vec![], &["pypi.org".to_string()], &[]),
            Err(DockerSpecError::EgressNotAllowed { .. })
        ));
        // An image outside the shared set: refused.
        assert!(matches!(
            ContainerRunSpec::for_job(&pin("someone/elses-image"), vec![], &[], &[]),
            Err(DockerSpecError::UnknownImage { .. })
        ));
        // A tag is not a pin: refused.
        let mut tagged = pin("linux-dev");
        tagged.digest = "latest".to_string();
        assert!(matches!(
            ContainerRunSpec::for_job(&tagged, vec![], &[], &[]),
            Err(DockerSpecError::NotDigestPinned { .. })
        ));
    }
}
