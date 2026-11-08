//! Syncyard shared contract crate.
//!
//! The types every other Syncyard component agrees on:
//!
//! - [`JobRecord`] — a job as submitted (identity + execution spec).
//! - [`QueueRecord`] — the full persisted job record. Its serde shape is
//!   byte-compatible with the Python gatekeeper's queue-record JSON:
//!   records written by `gk.py` deserialize here, and records serialized
//!   here keep the same field names, the same explicit `null`s for
//!   not-yet-known run fields, and any unknown fields verbatim
//!   (see `QueueRecord::extra`). This is what makes the one-move cutover
//!   of spec 038 section 1 a state migration instead of a format break.
//! - The streaming-progress protocol of spec 034
//!   ([`ProgressEvent`], [`ProgressSnapshot`]).
//!
//! Compatibility rule: a field the Python gatekeeper writes must never
//! be renamed or dropped here. Fields that only exist in the Rust core
//! (e.g. `QueueRecord::seq`, spec 038 section 9) are optional and are
//! omitted from the JSON when unset, so a Rust-written record stays
//! readable by the Python gatekeeper during the cutover window.

pub mod acl;
pub mod backchannel;
pub mod capability;
pub mod control;
pub mod creds;
pub mod fleet;
pub mod images;
pub mod isotime;
pub mod job;
pub mod permissions;
pub mod progress;
pub mod queue;
pub mod rawaccess;
pub mod scan;
pub mod signing;

pub use backchannel::{
    macos_backchannel_policy, validate_backchannel_policy, BackchannelPolicy, BACKCHANNEL_OWNER,
    RESERVED_USE,
};
pub use capability::{
    ActivationState, CapabilityRecord, DockerPresence, Measure, MemoryPool, NodeClass,
};
pub use control::{
    CommandOutcome, ControlAction, ControlCommand, HostPublicKey, HostSecretKey, OutcomeStatus,
    DEFAULT_EXPIRY_S as COMMAND_DEFAULT_EXPIRY_S, MAX_EXPIRY_S as COMMAND_MAX_EXPIRY_S,
};
pub use creds::{audit_credentials, CredRole, Credential, ScopeViolation};
pub use fleet::{resolve_host, AnchorError, FleetHost, CONSTRAINT_ANCHOR_RESOLUTION_FAILED, FLEET};
pub use images::{
    validate_image_set, ImagePin, ImageSetError, REQUIRED_ARCHES, SHARED_IMAGE_NAMES,
};
pub use job::JobRecord;
pub use permissions::{validate_permissions, NamedGrant, PermissionError, PermissionPosture};
pub use progress::{
    parse_line, parse_lines, Assignment, CompletionReport, ProgressAck, ProgressBatch,
    ProgressEvent, ProgressSnapshot,
};
pub use queue::{JobState, QueueRecord};
pub use rawaccess::{check_raw_access, RawAccessError, ELEVATED_KIT_VENUE};
pub use scan::{scan_for_credentials, SecretHit};
pub use signing::{tier_from_identities, BuildStamp, SigningTier};

/// CLI/daemon contract version (spec 034 FR-034-11). Bumped whenever the
/// contract surface changes incompatibly; CI fails when the CLI and the
/// daemon disagree on it.
pub const CONTRACT_VERSION: u32 = 1;
