//! Windowless payload spawning (spec 038 FR-038-54: no
//! component of the core may open a visible console window on
//! any platform, ever — a fleet hard constraint, owner's order
//! 2026-11-06).
//!
//! Windowlessness is by construction, not by discipline: every
//! payload the agent spawns goes through [`apply_plan`], and the
//! plan is a pure value the suite can assert on. On Windows the
//! plan carries `CREATE_NO_WINDOW`; the agent binary itself is
//! built with the `windows` subsystem (see main.rs), so neither
//! the agent nor anything it launches ever allocates a console.

use std::process::Command;

/// `CREATE_NO_WINDOW` (Win32 process creation flag 0x08000000).
pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// How a payload process is created. A pure value, asserted on
/// by the T038-70 test on every platform.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct SpawnPlan {
    /// The payload must never get a visible console window.
    pub windowless: bool,
    /// Win32 creation flags (0 on platforms without them).
    pub creation_flags: u32,
}

/// The one spawn plan for payloads.
pub fn payload_spawn_plan() -> SpawnPlan {
    SpawnPlan {
        windowless: true,
        creation_flags: if cfg!(windows) { CREATE_NO_WINDOW } else { 0 },
    }
}

/// Apply the plan to a command about to be spawned. This is the
/// only path by which the agent creates processes.
pub fn apply_plan(cmd: &mut Command) -> SpawnPlan {
    let plan = payload_spawn_plan();
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(plan.creation_flags);
    }
    #[cfg(not(windows))]
    {
        // No console concept to suppress; the plan's windowless
        // bit is the cross-platform statement of the constraint.
        let _ = plan.creation_flags;
        let _ = &cmd;
    }
    plan
}

#[cfg(test)]
mod tests {
    use super::*;

    // T038-70 core-windowless-everywhere (spec 038 FR-038-54),
    // agent half: the payload spawn plan is windowless by
    // construction on every platform, and on Windows it carries
    // exactly CREATE_NO_WINDOW.
    #[test]
    fn t038_70_core_windowless_everywhere() {
        let plan = payload_spawn_plan();
        assert!(plan.windowless, "payload spawns must be windowless");
        if cfg!(windows) {
            assert_eq!(plan.creation_flags, CREATE_NO_WINDOW);
        } else {
            assert_eq!(plan.creation_flags, 0);
        }
        // apply_plan returns the same plan it enforces.
        let mut cmd = Command::new("true");
        assert_eq!(apply_plan(&mut cmd), plan);
    }
}
