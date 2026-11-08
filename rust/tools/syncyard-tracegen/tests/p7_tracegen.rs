//! P7 tracegen tests: the generator's own named cases from
//! spec 036 sections 1/4/5/7 and the spec 039 platform lint.
//!
//! The binary is driven against fixture dirs (specs + suite)
//! built in temp dirs, and against the real repo for the
//! completeness audits. Nothing here writes into the repo:
//! every run points --out-dir at a temp dir.

use std::path::{Path, PathBuf};
use std::process::Command;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_syncyard-tracegen")
}

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p7-tg-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn run(args: &[&str]) -> (i32, String) {
    let out = Command::new(bin()).args(args).output().unwrap();
    (
        out.status.code().unwrap_or(-1),
        String::from_utf8_lossy(&out.stdout).to_string()
            + &String::from_utf8_lossy(&out.stderr),
    )
}

/// Build a 6-spec fixture tree. `spec034` is the spec.md
/// body for the 034 dir; the other five get a stub FR each.
fn fixture_specs(root: &Path, spec034: &str) {
    let names = [
        "034-fixture",
        "035-fixture",
        "036-fixture",
        "037-fixture",
        "038-fixture",
        "039-fixture",
    ];
    for (i, n) in names.iter().enumerate() {
        let d = root.join(n);
        std::fs::create_dir_all(&d).unwrap();
        let body = if i == 0 {
            spec034.to_string()
        } else {
            format!("| FR-03{}-01 | stub | T03{}-01 stub-case | test |\n", i + 4, i + 4)
        };
        std::fs::write(d.join("spec.md"), body).unwrap();
    }
}

fn fixture_suite(root: &Path, rs_body: &str) {
    let d = root.join("crates").join("demo").join("src");
    std::fs::create_dir_all(&d).unwrap();
    std::fs::write(d.join("lib.rs"), rs_body).unwrap();
}

/// A suite-id string built at runtime. Fixture ids are
/// assembled, never written literally in this file: the
/// real tracegen scan reads every .rs file in the tree, and
/// a literal fixture id here would register as a false
/// claim (or a false orphan) in the real matrix.
fn sid(spec: u32, n: u32) -> String {
    format!("t{spec:03}_{n:02}")
}

/// The stub-case ids for fixture specs 035..=039.
fn stub_ids() -> String {
    (35..=39)
        .map(|s| sid(s, 1))
        .collect::<Vec<_>>()
        .join(" ")
}

// T036-01 generation-completeness: a fixture package whose
// FRs all carry named cases and modes, with every case in
// the suite, generates a GREEN matrix (exit 0), and the
// JSON records each case in_suite.
#[test]
fn t036_01_generation_completeness() {
    let root = scratch("t01");
    let specs = root.join("specs");
    fixture_specs(
        &specs,
        "| FR-034-01 | req one | T034-01 alpha-case | test |\n\
         | FR-034-02 | req two | T034-02 beta-case | gate |\n",
    );
    let suite = root.join("rust");
    let body = format!(
        "// {} alpha\n// {} beta\n// {} stubs\n",
        sid(34, 1),
        sid(34, 2),
        stub_ids()
    );
    fixture_suite(&suite, &body);
    let out_dir = root.join("out");
    let (code, stdout) = run(&[
        "--specs",
        specs.to_str().unwrap(),
        "--suite",
        suite.to_str().unwrap(),
        "--out-dir",
        out_dir.to_str().unwrap(),
    ]);
    assert_eq!(code, 0, "{stdout}");
    assert!(stdout.contains("TRACEGEN RESULT green"), "{stdout}");
    let json: serde_json::Value =
        serde_json::from_str(&std::fs::read_to_string(out_dir.join("matrix.json")).unwrap())
            .unwrap();
    let frs = json["frs"].as_array().unwrap();
    assert!(frs.iter().all(|fr| fr["tests"]
        .as_array()
        .unwrap()
        .iter()
        .all(|t| t["in_suite"] == serde_json::Value::Bool(true))));
    let _ = std::fs::remove_dir_all(&root);
}

// T036-02 orphan-and-missing-fail: a missing named case, an
// orphan suite id, and an FR without a mode each register
// as their own gap class and the run FAILS (exit 1) with
// the counts named — the matrix is never quietly green.
#[test]
fn t036_02_orphan_and_missing_fail() {
    let root = scratch("t02");
    let specs = root.join("specs");
    fixture_specs(
        &specs,
        "| FR-034-01 | req one | T034-01 alpha-case | test |\n\
         | FR-034-02 | req two | T034-02 beta-case | test |\n\
         | FR-034-03 | req three | T034-03 gamma-case |  |\n",
    );
    let suite = root.join("rust");
    // The 02 case missing; a 99 case as the orphan; stubs
    // for the rest.
    let body = format!(
        "// {} alpha\n// {} gamma\n// {} orphan\n// {}\n",
        sid(34, 1),
        sid(34, 3),
        sid(34, 99),
        stub_ids()
    );
    fixture_suite(&suite, &body);
    let out_dir = root.join("out");
    let (code, stdout) = run(&[
        "--specs",
        specs.to_str().unwrap(),
        "--suite",
        suite.to_str().unwrap(),
        "--out-dir",
        out_dir.to_str().unwrap(),
    ]);
    assert_eq!(code, 1, "{stdout}");
    assert!(stdout.contains("fr_without_mode=1"), "{stdout}");
    assert!(stdout.contains("named_missing_from_suite=1"), "{stdout}");
    assert!(stdout.contains("orphan_suite_ids=1"), "{stdout}");
    let _ = std::fs::remove_dir_all(&root);
}

// T036-03 real-package-fr-tables: against the REAL repo,
// every FR row in specs 034-039 carries at least one named
// test case and at least one acceptance mode — the FR-table
// completeness the whole matrix rests on. (Named cases may
// legitimately be missing from the suite — those are the
// field-gated list; the FR tables themselves must be whole.)
#[test]
fn t036_03_real_package_fr_tables() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..");
    let out_dir = scratch("t03-out");
    let (code, stdout) = run(&[
        "--specs",
        repo.join("specs").to_str().unwrap(),
        "--suite",
        repo.join("rust").to_str().unwrap(),
        "--out-dir",
        out_dir.to_str().unwrap(),
    ]);
    assert!(
        stdout.contains("fr_without_test=0"),
        "code={code}\n{stdout}"
    );
    assert!(stdout.contains("fr_without_mode=0"), "{stdout}");
    assert!(stdout.contains("fr_rows=126"), "{stdout}");
    let _ = std::fs::remove_dir_all(&out_dir);
}

fn coverage_fixture(files: &[(&str, u64, u64)]) -> String {
    let items: Vec<String> = files
        .iter()
        .map(|(name, covered, total)| {
            format!(
                "{{\"filename\": \"{name}\", \"summary\": {{\"lines\": {{\"count\": {total}, \"covered\": {covered}}}}}}}"
            )
        })
        .collect();
    format!("{{\"data\": [{{\"files\": [{}]}}]}}", items.join(", "))
}

// T036-05 uncovered-lines-reported + T036-06
// coverage-gate-fails-below-bar: the verdict mode reports
// each crate's percentage AND uncovered-line count, fails
// (exit 1) naming the crate below its bar, ignores non-src
// files, and passes a fixture that meets every bar.
#[test]
fn t036_05_uncovered_lines_reported_and_t036_06_gate_fails_below_bar() {
    let root = scratch("cov");
    let report = root.join("export.json");
    std::fs::write(
        &report,
        coverage_fixture(&[
            ("/x/rust/crates/syncyard-contract/src/lib.rs", 97, 100),
            ("/x/rust/crates/syncyard-daemon/src/state.rs", 850, 1000),
            ("/x/rust/crates/syncyard-daemon/tests/big.rs", 0, 500),
            ("/x/rust/crates/syncyard-cli/src/lib.rs", 90, 100),
            ("/x/rust/crates/syncyard-agent/src/lib.rs", 90, 100),
            ("/x/rust/crates/syncyard-tray/src/lib.rs", 85, 100),
        ]),
    )
    .unwrap();
    let (code, stdout) = run(&["coverage", "--report", report.to_str().unwrap()]);
    assert_eq!(code, 1, "{stdout}");
    // daemon: 85.00% < 90 bar, 150 uncovered lines, and the
    // tests/ file (0/500) must NOT drag it to 56%.
    assert!(stdout.contains("COVERAGE daemon 85.00%"), "{stdout}");
    assert!(stdout.contains("uncovered=150"), "{stdout}");
    assert!(stdout.contains("COVERAGE contract 97.00%"), "{stdout}");
    assert!(stdout.contains("COVERAGE RESULT red"), "{stdout}");

    std::fs::write(
        &report,
        coverage_fixture(&[
            ("/x/rust/crates/syncyard-contract/src/lib.rs", 96, 100),
            ("/x/rust/crates/syncyard-daemon/src/state.rs", 91, 100),
            ("/x/rust/crates/syncyard-cli/src/lib.rs", 86, 100),
            ("/x/rust/crates/syncyard-agent/src/lib.rs", 86, 100),
            ("/x/rust/crates/syncyard-tray/src/lib.rs", 81, 100),
        ]),
    )
    .unwrap();
    let (code, stdout) = run(&["coverage", "--report", report.to_str().unwrap()]);
    assert_eq!(code, 0, "{stdout}");
    assert!(stdout.contains("COVERAGE RESULT green"), "{stdout}");
    let _ = std::fs::remove_dir_all(&root);
}

// T039-02 platform-assumption-lint: the lint is clean on
// the real workspace and catches a planted OS-specific
// path literal in shipped code (while ignoring the same
// literal inside a test module, where fixtures live).
#[test]
fn t039_02_platform_assumption_lint() {
    let repo_rust = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let (code, stdout) = run(&["lint-platform", "--suite", repo_rust.to_str().unwrap()]);
    assert_eq!(code, 0, "{stdout}");
    assert!(stdout.contains("PLATFORM-LINT hits=0"), "{stdout}");

    let root = scratch("lint");
    let suite = root.join("rust");
    fixture_suite(
        &suite,
        "pub fn bad() { let p = \"C:\\\\work\"; }\n#[cfg(test)]\nmod tests { fn f() { let q = \"C:\\\\fixture\"; } }\n",
    );
    let (code, stdout) = run(&["lint-platform", "--suite", suite.to_str().unwrap()]);
    assert_eq!(code, 1, "{stdout}");
    assert!(stdout.contains("PLATFORM-LINT hits=1"), "{stdout}");
    let _ = std::fs::remove_dir_all(&root);
}

// T038-66 docs-set-complete + T038-02 state-freeze-fallback
// + T038-67 control-plane-single-home + T038-68
// door-repo-versioning-owned: the prose deliverables exist,
// carry the elements their FRs require, and are reachable
// from the published nav. (T038-02's claim is the cutover
// plan's named fallback window; T038-67's is the plan's
// named single control-plane end state.)
#[test]
fn t038_66_docs_set_complete_with_t038_02_t038_67_t038_68() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..");
    let docs = repo.join("docs");
    let read = |name: &str| {
        std::fs::read_to_string(docs.join(name))
            .unwrap_or_else(|e| panic!("docs/{name} must exist: {e}"))
    };
    let threat = read("threat-model.md");
    assert!(threat.contains("## Assets") && threat.contains("## Actors"));
    let diag = read("diagnostics-privacy-retention.md");
    assert!(diag.contains("must never carry") && diag.contains("## Retention"));
    let runbook = read("owner-recovery-runbook.md");
    assert!(runbook.contains("restic") && runbook.contains("daemon_boot_reconciled"));
    let cutover = read("cutover-plan.md");
    // T038-02: the fallback is a named state-freeze window.
    assert!(cutover.contains("state-freeze window"), "{cutover}");
    // T038-67: the end state is a single control plane in a
    // named home.
    assert!(cutover.contains("a single control plane"), "{cutover}");
    assert!(cutover.contains("/var/lib/syncyard"), "{cutover}");
    // T038-68: the door record names owner + versioning.
    let door = read("door-repo.md");
    assert!(door.contains("run.yml") && door.contains("Desktop Access"));
    assert!(door.contains("version"));
    let decom = read("python-decommissioning-plan.md");
    assert!(decom.contains("## Retirement order"));
    let budget = read("github-api-budget.md");
    assert!(budget.contains("1,000"));
    // Every deliverable is in the published nav.
    let nav = std::fs::read_to_string(repo.join("mkdocs.yml")).unwrap();
    for page in [
        "threat-model.md",
        "diagnostics-privacy-retention.md",
        "cutover-plan.md",
        "python-decommissioning-plan.md",
        "github-api-budget.md",
        "owner-recovery-runbook.md",
        "door-repo.md",
    ] {
        assert!(nav.contains(page), "mkdocs nav missing: {page}");
    }
}

// T036-07 release-gate-composition + T036-17
// no-billing-required-tests + T036-12 ci-matrix-all-os
// + T039-01 mac-ci-both-arches: the composed local gate
// exists, names its steps in order, and contains no
// GitHub/billing surface; the CI workflow carries the
// all-OS matrix including both darwin targets on a macOS
// runner (the darwin cross-check evidence from this
// machine is in the P7 target logs; the workflow is what
// runs it on a real macOS runner in CI).
#[test]
fn t036_07_gate_composition_t036_17_no_billing_t036_12_ci_matrix_t039_01() {
    let repo = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("..")
        .join("..");
    let gate = std::fs::read_to_string(repo.join("rust").join("release-gate.sh")).unwrap();
    // Steps present and in order (the STEPS array's exact
    // strings, so header prose cannot false-match).
    let order = [
        "cargo fmt --check (rust/)",
        "cargo clippy --workspace -- -D warnings (rust/)",
        "cargo test --workspace (rust/)",
        "syncyard-tracegen matrix (specs vs suite)",
        "syncyard-tracegen lint-platform",
        "coverage verdict (rust/coverage.sh)",
    ];
    let mut last = 0;
    for step in order {
        let pos = gate
            .find(step)
            .unwrap_or_else(|| panic!("gate is missing step: {step}"));
        assert!(pos >= last, "gate step out of order: {step}");
        last = pos;
    }
    // No billing surface anywhere in the local gate path.
    for script in ["release-gate.sh", "coverage.sh"] {
        let text = std::fs::read_to_string(repo.join("rust").join(script)).unwrap();
        for bad in ["gh api", "api.github.com", "curl ", "wget "] {
            assert!(
                !text.contains(bad),
                "{script} must stay local-only, found: {bad}"
            );
        }
    }
    // The dry run prints the composition without executing.
    let out = Command::new("bash")
        .arg(repo.join("rust").join("release-gate.sh"))
        .arg("--dry-run")
        .output()
        .unwrap();
    assert!(out.status.success());
    let text = String::from_utf8_lossy(&out.stdout);
    assert!(text.contains("step 1") && text.contains("step 6"), "{text}");

    // CI matrix: every OS, both darwin arches, aarch64 Linux.
    let wf = std::fs::read_to_string(repo.join(".github").join("workflows").join("rust.yml"))
        .unwrap();
    for needle in [
        "ubuntu-latest",
        "windows-latest",
        "macos-latest",
        "aarch64-apple-darwin",
        "x86_64-apple-darwin",
        "aarch64-unknown-linux-gnu",
    ] {
        assert!(wf.contains(needle), "rust.yml matrix missing: {needle}");
    }
}
