//! Traceability matrix generator (spec 036 section 1, task T002).
//!
//! The matrix is generated from the spec files, never hand-maintained:
//! every FR in specs 034-039 must map to at least one named test case
//! and at least one acceptance mode in the spec text itself, and every
//! named test case must exist in the suite.
//!
//! Suite existence convention: a Rust test claims a named test case by
//! carrying its id — in spec form `T034-02` or snake form `t034_02` —
//! in its source (normally a trace comment above the test). Ids found
//! in the suite that no spec names are orphans ("a test names no FR").
//!
//! Exit code is 0 only when there are no gaps of any class:
//! FR-without-test, FR-without-mode, named-test-missing-from-suite,
//! or orphan suite id. CI treats non-zero as failure (FR-036-01).
//!
//! Usage:
//!   syncyard-tracegen [--specs DIR] [--suite DIR] [--out-dir DIR]
//! Defaults: --specs specs  --suite rust  --out-dir rust/traceability
//! (paths relative to the current directory; run from the repo root).

use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

#[derive(Debug, Clone)]
struct NamedTest {
    id: String,
    slug: String,
}

#[derive(Debug)]
struct FrRow {
    spec: String,
    fr: String,
    tests: Vec<NamedTest>,
    modes: Vec<String>,
}

fn main() -> ExitCode {
    let mut specs_dir = PathBuf::from("specs");
    let mut suite_dir = PathBuf::from("rust");
    let mut out_dir = PathBuf::from("rust/traceability");
    let mut args = env::args().skip(1);
    while let Some(a) = args.next() {
        match a.as_str() {
            "--specs" => specs_dir = PathBuf::from(args.next().unwrap_or_default()),
            "--suite" => suite_dir = PathBuf::from(args.next().unwrap_or_default()),
            "--out-dir" => out_dir = PathBuf::from(args.next().unwrap_or_default()),
            "-h" | "--help" => {
                println!("usage: syncyard-tracegen [--specs DIR] [--suite DIR] [--out-dir DIR]");
                return ExitCode::SUCCESS;
            }
            other => {
                eprintln!("unknown argument: {other}");
                return ExitCode::from(2);
            }
        }
    }

    // ---- discover and parse the specs ------------------------------
    let mut spec_dirs: Vec<PathBuf> = Vec::new();
    match fs::read_dir(&specs_dir) {
        Ok(rd) => {
            for e in rd.flatten() {
                let name = e.file_name().to_string_lossy().to_string();
                if let Some(prefix) = name.get(..3) {
                    if ("034"..="039").contains(&prefix) && name.as_bytes().get(3) == Some(&b'-') {
                        spec_dirs.push(e.path());
                    }
                }
            }
        }
        Err(e) => {
            eprintln!("cannot read specs dir {}: {e}", specs_dir.display());
            return ExitCode::from(2);
        }
    }
    spec_dirs.sort();
    if spec_dirs.len() != 6 {
        eprintln!(
            "expected 6 spec dirs (034-039) under {}, found {}",
            specs_dir.display(),
            spec_dirs.len()
        );
        return ExitCode::from(2);
    }

    let mut rows: Vec<FrRow> = Vec::new();
    for dir in &spec_dirs {
        let spec_name = dir.file_name().unwrap().to_string_lossy().to_string();
        let path = dir.join("spec.md");
        let text = match fs::read_to_string(&path) {
            Ok(t) => t,
            Err(e) => {
                eprintln!("cannot read {}: {e}", path.display());
                return ExitCode::from(2);
            }
        };
        for line in text.lines() {
            if let Some(row) = parse_fr_line(&spec_name, line) {
                rows.push(row);
            }
        }
    }

    // ---- scan the suite --------------------------------------------
    let mut suite_files: Vec<PathBuf> = Vec::new();
    collect_rs_files(&suite_dir, &mut suite_files);
    suite_files.sort();
    // test id -> files that carry it
    let mut suite_ids: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    for f in &suite_files {
        let text = fs::read_to_string(f).unwrap_or_default();
        for id in find_test_ids(&text) {
            suite_ids
                .entry(id)
                .or_default()
                .insert(f.display().to_string());
        }
    }

    // ---- evaluate ---------------------------------------------------
    let mut named_total = 0usize;
    let mut fr_no_test: Vec<&FrRow> = Vec::new();
    let mut fr_no_mode: Vec<&FrRow> = Vec::new();
    let mut missing_from_suite: Vec<(&FrRow, &NamedTest)> = Vec::new();
    let mut spec_named_ids: BTreeSet<String> = BTreeSet::new();
    for row in &rows {
        if row.tests.is_empty() {
            fr_no_test.push(row);
        }
        if row.modes.is_empty() {
            fr_no_mode.push(row);
        }
        for t in &row.tests {
            named_total += 1;
            spec_named_ids.insert(t.id.clone());
            if !suite_ids.contains_key(&t.id) {
                missing_from_suite.push((row, t));
            }
        }
    }
    let orphans: Vec<&String> = suite_ids
        .keys()
        .filter(|id| !spec_named_ids.contains(*id))
        .collect();
    let in_suite = named_total - missing_from_suite.len();
    let gaps = fr_no_test.len() + fr_no_mode.len() + missing_from_suite.len() + orphans.len();

    // ---- emit -------------------------------------------------------
    if let Err(e) = fs::create_dir_all(&out_dir) {
        eprintln!("cannot create {}: {e}", out_dir.display());
        return ExitCode::from(2);
    }
    let md = render_markdown(
        &rows,
        &suite_ids,
        &fr_no_test,
        &fr_no_mode,
        &missing_from_suite,
        &orphans,
    );
    if let Err(e) = fs::write(out_dir.join("matrix.md"), md) {
        eprintln!("cannot write matrix.md: {e}");
        return ExitCode::from(2);
    }
    let json = render_json(&rows, &suite_ids);
    if let Err(e) = fs::write(out_dir.join("matrix.json"), json) {
        eprintln!("cannot write matrix.json: {e}");
        return ExitCode::from(2);
    }

    println!(
        "TRACEGEN specs=6 fr_rows={} named_tests={} named_in_suite={}",
        rows.len(),
        named_total,
        in_suite
    );
    println!(
        "TRACEGEN gaps: fr_without_test={} fr_without_mode={} named_missing_from_suite={} orphan_suite_ids={}",
        fr_no_test.len(),
        fr_no_mode.len(),
        missing_from_suite.len(),
        orphans.len()
    );
    println!("TRACEGEN matrix written to {}", out_dir.display());
    if gaps == 0 {
        println!("TRACEGEN RESULT green");
        ExitCode::SUCCESS
    } else {
        println!("TRACEGEN RESULT gaps={} (matrix is red)", gaps);
        ExitCode::from(1)
    }
}

/// Parse one markdown line as an FR table row, if it is one.
///
/// Row shape: `| FR-id | requirement... | test list | mode |`. The
/// requirement text may itself contain `|`, so the first cell is the
/// FR id, the last cell is the mode, the second-to-last is the test
/// list, and everything between is the requirement.
fn parse_fr_line(spec: &str, line: &str) -> Option<FrRow> {
    let line = line.trim();
    if !line.starts_with('|') {
        return None;
    }
    let parts: Vec<&str> = line.split('|').collect();
    // parts[0] and parts[last] are empty (line starts/ends with '|').
    if parts.len() < 6 {
        return None;
    }
    let fr = parts[1].trim();
    if !is_fr_id(fr) {
        return None;
    }
    let tests_cell = parts[parts.len() - 3].trim();
    let mode_cell = parts[parts.len() - 2].trim();
    let tests = find_named_tests(tests_cell);
    let modes: Vec<String> = mode_cell
        .split('+')
        .map(|m| m.trim().to_string())
        .filter(|m| !m.is_empty())
        .collect();
    Some(FrRow {
        spec: spec.to_string(),
        fr: fr.to_string(),
        tests,
        modes,
    })
}

/// FR identifiers: `FR-NNN-NN` (specs 034-038) or `FR-MAC-NN` (039).
fn is_fr_id(s: &str) -> bool {
    let rest = match s.strip_prefix("FR-") {
        Some(r) => r,
        None => return false,
    };
    if let Some(mac) = rest.strip_prefix("MAC-") {
        return mac.len() == 2 && mac.bytes().all(|b| b.is_ascii_digit());
    }
    let mut it = rest.split('-');
    match (it.next(), it.next(), it.next()) {
        (Some(a), Some(b), None) => {
            a.len() == 3
                && a.bytes().all(|c| c.is_ascii_digit())
                && b.len() == 2
                && b.bytes().all(|c| c.is_ascii_digit())
        }
        _ => false,
    }
}

/// Extract named test cases (id + slug) from a spec's test-list cell.
/// Cell form: `T034-02 grammar-accept-and-ignore; T034-03 ...`.
fn find_named_tests(cell: &str) -> Vec<NamedTest> {
    let mut out = Vec::new();
    for entry in cell.split(';') {
        let entry = entry.trim();
        for id in find_test_ids(entry) {
            let slug = entry
                .split_once(&id)
                .map(|(_, rest)| rest.trim().to_string())
                .unwrap_or_default();
            out.push(NamedTest { id, slug });
        }
    }
    out
}

/// Find test-case ids in text, in either spec form (`T034-02`) or
/// Rust snake form (`t034_02`); ids are returned in spec form.
/// Boundaries are checked so longer digit runs do not false-match.
fn find_test_ids(text: &str) -> Vec<String> {
    let b = text.as_bytes();
    let mut out = Vec::new();
    let mut i = 0;
    while i + 7 <= b.len() {
        let is_t = b[i] == b'T' || b[i] == b't';
        let prev_ok = i == 0 || !(b[i - 1].is_ascii_alphanumeric() || b[i - 1] == b'_');
        if is_t
            && prev_ok
            && b[i + 1].is_ascii_digit()
            && b[i + 2].is_ascii_digit()
            && b[i + 3].is_ascii_digit()
            && (b[i + 4] == b'-' || b[i + 4] == b'_')
            && b[i + 5].is_ascii_digit()
            && b[i + 6].is_ascii_digit()
            && (i + 7 >= b.len() || !b[i + 7].is_ascii_digit())
        {
            let id = format!(
                "T{}-{}",
                std::str::from_utf8(&b[i + 1..i + 4]).unwrap(),
                std::str::from_utf8(&b[i + 5..i + 7]).unwrap()
            );
            if !out.contains(&id) {
                out.push(id);
            }
            i += 7;
        } else {
            i += 1;
        }
    }
    out
}

/// Recursively collect `.rs` files under `dir`, skipping build output.
fn collect_rs_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let rd = match fs::read_dir(dir) {
        Ok(r) => r,
        Err(_) => return,
    };
    for e in rd.flatten() {
        let path = e.path();
        let name = e.file_name().to_string_lossy().to_string();
        if path.is_dir() {
            if name != "target" && name != ".git" {
                collect_rs_files(&path, out);
            }
        } else if name.ends_with(".rs") {
            out.push(path);
        }
    }
}

fn render_markdown(
    rows: &[FrRow],
    suite_ids: &BTreeMap<String, BTreeSet<String>>,
    fr_no_test: &[&FrRow],
    fr_no_mode: &[&FrRow],
    missing: &[(&FrRow, &NamedTest)],
    orphans: &[&String],
) -> String {
    let mut s = String::new();
    s.push_str("# Traceability matrix (generated by syncyard-tracegen, spec 036)\n\n");
    s.push_str("Do not hand-edit: regenerate with `cargo run -p syncyard-tracegen`.\n\n");
    let named: usize = rows.iter().map(|r| r.tests.len()).sum();
    let in_suite = named - missing.len();
    s.push_str(&format!(
        "Summary: {} FRs, {} named test cases, {} named tests present in suite, \
         {} FRs without a named test, {} FRs without a mode, {} named tests missing \
         from suite, {} orphan suite ids.\n\n",
        rows.len(),
        named,
        in_suite,
        fr_no_test.len(),
        fr_no_mode.len(),
        missing.len(),
        orphans.len()
    ));
    let mut current_spec = String::new();
    for row in rows {
        if row.spec != current_spec {
            current_spec = row.spec.clone();
            s.push_str(&format!(
                "\n## {}\n\n| FR | Named test case(s) | Mode | In suite |\n|---|---|---|---|\n",
                row.spec
            ));
        }
        let tests = row
            .tests
            .iter()
            .map(|t| format!("{} {}", t.id, t.slug))
            .collect::<Vec<_>>()
            .join("; ");
        let present = row
            .tests
            .iter()
            .map(|t| {
                if suite_ids.contains_key(&t.id) {
                    format!("{} yes", t.id)
                } else {
                    format!("{} NO", t.id)
                }
            })
            .collect::<Vec<_>>()
            .join("; ");
        s.push_str(&format!(
            "| {} | {} | {} | {} |\n",
            row.fr,
            tests,
            row.modes.join(" + "),
            present
        ));
    }
    if !missing.is_empty() {
        s.push_str("\n## Named tests missing from the suite\n\n");
        for (row, t) in missing {
            s.push_str(&format!(
                "- {} {} ({}, {})\n",
                t.id, t.slug, row.spec, row.fr
            ));
        }
    }
    if !orphans.is_empty() {
        s.push_str("\n## Orphan suite ids (in suite, named by no spec)\n\n");
        for id in orphans {
            s.push_str(&format!("- {id}\n"));
        }
    }
    s
}

fn render_json(rows: &[FrRow], suite_ids: &BTreeMap<String, BTreeSet<String>>) -> String {
    let mut s = String::from("{\n  \"frs\": [\n");
    for (i, row) in rows.iter().enumerate() {
        let tests: Vec<String> = row
            .tests
            .iter()
            .map(|t| {
                format!(
                    "{{\"id\": \"{}\", \"slug\": \"{}\", \"in_suite\": {}}}",
                    esc(&t.id),
                    esc(&t.slug),
                    suite_ids.contains_key(&t.id)
                )
            })
            .collect();
        s.push_str(&format!(
            "    {{\"spec\": \"{}\", \"fr\": \"{}\", \"tests\": [{}], \"modes\": [{}]}}{}\n",
            esc(&row.spec),
            esc(&row.fr),
            tests.join(", "),
            row.modes
                .iter()
                .map(|m| format!("\"{}\"", esc(m)))
                .collect::<Vec<_>>()
                .join(", "),
            if i + 1 == rows.len() { "" } else { "," }
        ));
    }
    s.push_str("  ]\n}\n");
    s
}

fn esc(s: &str) -> String {
    s.replace('\\', "\\\\").replace('"', "\\\"")
}
