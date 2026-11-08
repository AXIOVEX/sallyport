//! Auto-fix bounds (spec 038 FR-038-41, FIX-01..08).
//!
//! Repairs are an enumerated recipe list, each with a named
//! defect and a post-repair verification read from the
//! repaired thing's own record (FIX-01). Budgets bind: at
//! most one repair per component per cycle and a stated
//! daily maximum, with exponential backoff (FIX-02). Two
//! consecutive failed repairs latch the recipe off until a
//! human or the supervising agent clears it (FIX-03). The
//! never-fix list is absolute (FIX-05). Hardware repairs
//! verify after apply and revert to known-safe on failure,
//! with no retry in the same boot (FIX-06). Every attempt is
//! recorded here (FIX-07: an unrecorded repair is a defect in
//! the repairer). The FIX-08 kill switch lives in
//! [`crate::control`]; this engine consults the same boolean.

/// Classes no recipe may ever touch (FIX-05).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FixClass {
    PayloadCode,
    GitHistory,
    RecordVerdicts,
    SecurityPolicy,
    FirmwareOrActivation,
    Deletion,
}

/// One enumerated repair recipe (FIX-01).
#[derive(Debug, Clone, Copy)]
pub struct Recipe {
    pub name: &'static str,
    pub defect: &'static str,
    /// Set when the recipe would touch a never-fix class;
    /// such recipes exist in the list only so an attempt is
    /// refused by name rather than improvised.
    pub never_fix: Option<FixClass>,
}

/// The repair engine's books.
#[derive(Debug)]
pub struct RepairEngine {
    pub daily_max: u32,
    pub base_backoff_s: i64,
    attempts: Vec<RepairAttempt>,
    /// component -> unix time of its last attempt this cycle
    /// bookkeeping is by day + count.
    daily_counts: std::collections::BTreeMap<String, (i64, u32)>,
    last_attempt_at: std::collections::BTreeMap<String, i64>,
    consecutive_failures: std::collections::BTreeMap<String, u32>,
    latched_off: std::collections::BTreeSet<String>,
    pub kill_switch: bool,
}

/// One recorded attempt (FIX-07).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RepairAttempt {
    pub recipe: String,
    pub component: String,
    pub at_unix: i64,
    pub outcome: String,
}

/// What an attempt did.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RepairOutcome {
    Applied,
    FailedVerification,
    RefusedNeverFix(FixClass),
    RefusedKillSwitch,
    RefusedLatchedOff,
    RefusedDailyBudget,
    RefusedBackoff { retry_at_unix: i64 },
}

impl RepairOutcome {
    pub fn as_str(&self) -> &'static str {
        match self {
            RepairOutcome::Applied => "applied",
            RepairOutcome::FailedVerification => "failed_verification",
            RepairOutcome::RefusedNeverFix(_) => "refused_never_fix",
            RepairOutcome::RefusedKillSwitch => "refused_kill_switch",
            RepairOutcome::RefusedLatchedOff => "refused_latched_off",
            RepairOutcome::RefusedDailyBudget => "refused_daily_budget",
            RepairOutcome::RefusedBackoff { .. } => "refused_backoff",
        }
    }
}

impl RepairEngine {
    pub fn new(daily_max: u32, base_backoff_s: i64) -> Self {
        RepairEngine {
            daily_max,
            base_backoff_s,
            attempts: Vec::new(),
            daily_counts: Default::default(),
            last_attempt_at: Default::default(),
            consecutive_failures: Default::default(),
            latched_off: Default::default(),
            kill_switch: false,
        }
    }

    pub fn attempts(&self) -> &[RepairAttempt] {
        &self.attempts
    }

    pub fn is_latched_off(&self, recipe: &str) -> bool {
        self.latched_off.contains(recipe)
    }

    /// A human or the supervising agent clears a latch
    /// (FIX-03); nothing else does.
    pub fn clear_latch(&mut self, recipe: &str) {
        self.latched_off.remove(recipe);
        self.consecutive_failures.remove(recipe);
    }

    fn day_of(unix: i64) -> i64 {
        unix.div_euclid(86_400)
    }

    /// Attempt one repair. `verify` is the FIX-01
    /// post-repair verification, read from the repaired
    /// thing's own record by the caller.
    pub fn attempt(
        &mut self,
        recipe: &Recipe,
        component: &str,
        now_unix: i64,
        verify: impl FnOnce() -> bool,
    ) -> RepairOutcome {
        let outcome = self.attempt_inner(recipe, component, now_unix, verify);
        self.attempts.push(RepairAttempt {
            recipe: recipe.name.to_string(),
            component: component.to_string(),
            at_unix: now_unix,
            outcome: outcome.as_str().to_string(),
        });
        outcome
    }

    fn attempt_inner(
        &mut self,
        recipe: &Recipe,
        component: &str,
        now_unix: i64,
        verify: impl FnOnce() -> bool,
    ) -> RepairOutcome {
        if let Some(class) = recipe.never_fix {
            return RepairOutcome::RefusedNeverFix(class);
        }
        if self.kill_switch {
            return RepairOutcome::RefusedKillSwitch;
        }
        if self.latched_off.contains(recipe.name) {
            return RepairOutcome::RefusedLatchedOff;
        }
        // Daily budget (FIX-02).
        let today = Self::day_of(now_unix);
        let (day, count) = self
            .daily_counts
            .get(component)
            .copied()
            .unwrap_or((today, 0));
        let count = if day == today { count } else { 0 };
        if count >= self.daily_max {
            return RepairOutcome::RefusedDailyBudget;
        }
        // Exponential backoff since this component's last
        // attempt: base * 2^(attempts today - 1) (FIX-02).
        if let Some(last) = self.last_attempt_at.get(component) {
            let wait = self
                .base_backoff_s
                .saturating_mul(1i64 << count.min(20));
            if now_unix < last + wait {
                return RepairOutcome::RefusedBackoff {
                    retry_at_unix: last + wait,
                };
            }
        }
        self.daily_counts
            .insert(component.to_string(), (today, count + 1));
        self.last_attempt_at.insert(component.to_string(), now_unix);
        if verify() {
            self.consecutive_failures.remove(recipe.name);
            RepairOutcome::Applied
        } else {
            let fails = {
                let f = self
                    .consecutive_failures
                    .entry(recipe.name.to_string())
                    .or_insert(0);
                *f += 1;
                *f
            };
            if fails >= 2 {
                self.latched_off.insert(recipe.name.to_string());
            }
            RepairOutcome::FailedVerification
        }
    }
}

/// A hardware-affecting repair (FIX-06): apply, verify from
/// the device's own read-back, revert to known-safe on
/// failure, and never retry within the same boot.
#[derive(Debug)]
pub struct HardwareRepair {
    pub known_safe: i64,
    attempted_boots: std::collections::BTreeSet<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HardwareOutcome {
    AppliedVerified,
    RevertedToKnownSafe,
    RefusedSameBootRetry,
}

impl HardwareRepair {
    pub fn new(known_safe: i64) -> Self {
        HardwareRepair {
            known_safe,
            attempted_boots: Default::default(),
        }
    }

    /// `apply` sets the value and returns the read-back.
    pub fn apply_and_verify(
        &mut self,
        boot_id: &str,
        target: i64,
        apply: impl Fn(i64) -> i64,
    ) -> (HardwareOutcome, i64) {
        if !self.attempted_boots.insert(boot_id.to_string()) {
            return (HardwareOutcome::RefusedSameBootRetry, self.known_safe);
        }
        let readback = apply(target);
        if readback == target {
            (HardwareOutcome::AppliedVerified, readback)
        } else {
            let reverted = apply(self.known_safe);
            (HardwareOutcome::RevertedToKnownSafe, reverted)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const RECIPE: Recipe = Recipe {
        name: "restart-stale-agent",
        defect: "agent-heartbeat-stale",
        never_fix: None,
    };

    // T038-48 fix02-repair-budgets: one attempt per component
    // is followed by exponential backoff, and the daily
    // maximum binds absolutely.
    #[test]
    fn t038_48_fix02_repair_budgets() {
        let mut eng = RepairEngine::new(2, 60);
        assert_eq!(eng.attempt(&RECIPE, "agent", 1_000, || true), RepairOutcome::Applied);
        // Immediate retry: backoff (base 60s x 2^1 after one
        // attempt today).
        match eng.attempt(&RECIPE, "agent", 1_030, || true) {
            RepairOutcome::RefusedBackoff { retry_at_unix } => {
                assert_eq!(retry_at_unix, 1_000 + 120)
            }
            other => panic!("expected backoff, got {other:?}"),
        }
        // After backoff, the second attempt fits the daily max.
        assert_eq!(eng.attempt(&RECIPE, "agent", 1_200, || true), RepairOutcome::Applied);
        // Third attempt on the same day (t=5000 is still day
        // 0): the daily budget refuses.
        assert_eq!(
            eng.attempt(&RECIPE, "agent", 5_000, || true),
            RepairOutcome::RefusedDailyBudget
        );
        // Every attempt — applied or refused — is recorded.
        assert_eq!(eng.attempts().len(), 4);
    }

    // T038-49 fix03-latch-off: two consecutive failed repairs
    // latch the recipe off until cleared by name.
    #[test]
    fn t038_49_fix03_latch_off() {
        let mut eng = RepairEngine::new(10, 0);
        assert_eq!(
            eng.attempt(&RECIPE, "agent", 1_000, || false),
            RepairOutcome::FailedVerification
        );
        assert!(!eng.is_latched_off(RECIPE.name));
        assert_eq!(
            eng.attempt(&RECIPE, "agent", 2_000, || false),
            RepairOutcome::FailedVerification
        );
        assert!(eng.is_latched_off(RECIPE.name));
        assert_eq!(
            eng.attempt(&RECIPE, "agent", 3_000, || true),
            RepairOutcome::RefusedLatchedOff
        );
        eng.clear_latch(RECIPE.name);
        assert_eq!(eng.attempt(&RECIPE, "agent", 4_000, || true), RepairOutcome::Applied);
    }

    // T038-50 fix05-never-fix-list: every never-fix class is
    // refused by name, before budgets or latches are read.
    #[test]
    fn t038_50_fix05_never_fix_list() {
        let mut eng = RepairEngine::new(10, 0);
        for class in [
            FixClass::PayloadCode,
            FixClass::GitHistory,
            FixClass::RecordVerdicts,
            FixClass::SecurityPolicy,
            FixClass::FirmwareOrActivation,
            FixClass::Deletion,
        ] {
            let recipe = Recipe {
                name: "tempting-shortcut",
                defect: "none",
                never_fix: Some(class),
            };
            assert_eq!(
                eng.attempt(&recipe, "agent", 1_000, || true),
                RepairOutcome::RefusedNeverFix(class)
            );
        }
    }

    // T038-51 fix06-gpu-verify-revert: apply, verify from the
    // read-back, revert to known-safe on mismatch, and no
    // retry in the same boot.
    #[test]
    fn t038_51_fix06_gpu_verify_revert() {
        let mut hw = HardwareRepair::new(180);
        // Faithful device: the read-back matches the target.
        let (outcome, value) = hw.apply_and_verify("boot-1", 220, |v| v);
        assert_eq!(outcome, HardwareOutcome::AppliedVerified);
        assert_eq!(value, 220);

        let mut hw = HardwareRepair::new(180);
        // Device applies nothing (reads back the old value).
        let (outcome, value) = hw.apply_and_verify("boot-1", 220, |v| {
            if v == 180 {
                180 // the revert write lands
            } else {
                150 // the target write did not take
            }
        });
        assert_eq!(outcome, HardwareOutcome::RevertedToKnownSafe);
        assert_eq!(value, 180);
        // Same boot: no retry, whatever the caller wants.
        let (outcome, _) = hw.apply_and_verify("boot-1", 220, |v| v);
        assert_eq!(outcome, HardwareOutcome::RefusedSameBootRetry);
        // Next boot may try again.
        let (outcome, value) = hw.apply_and_verify("boot-2", 220, |v| v);
        assert_eq!(outcome, HardwareOutcome::AppliedVerified);
        assert_eq!(value, 220);
    }

    #[test]
    fn kill_switch_halts_repairs() {
        let mut eng = RepairEngine::new(10, 0);
        eng.kill_switch = true;
        assert_eq!(
            eng.attempt(&RECIPE, "agent", 1_000, || true),
            RepairOutcome::RefusedKillSwitch
        );
    }
}
