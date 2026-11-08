//! CLI entry point: `syncyard-cli [--home DIR] [--offvm DIR] <verb> ...`.
//!
//! The state home comes from --home or SYNCYARD_HOME. There is
//! deliberately no compiled-in default: pointing the CLI at the
//! live Python gatekeeper's directory must always be an explicit
//! act (state-home separation, FR-038-11).

use std::path::PathBuf;

use syncyard_cli::run_cli;
use syncyard_daemon::state::StateHome;

fn main() {
    let raw: Vec<String> = std::env::args().skip(1).collect();
    let mut home: Option<PathBuf> = std::env::var_os("SYNCYARD_HOME").map(PathBuf::from);
    let mut offvm: Option<PathBuf> = None;
    let mut args: Vec<String> = Vec::new();
    let mut i = 0;
    while i < raw.len() {
        match raw[i].as_str() {
            "--home" => {
                home = raw.get(i + 1).map(PathBuf::from);
                i += 2;
            }
            "--offvm" => {
                offvm = raw.get(i + 1).map(PathBuf::from);
                i += 2;
            }
            _ => {
                args.push(raw[i].clone());
                i += 1;
            }
        }
    }
    // version/schema do not need a state home.
    let needs_home = !matches!(
        args.first().map(|s| s.as_str()),
        Some("version") | Some("schema")
    );
    let home = match home {
        Some(h) => StateHome::new(h),
        None if !needs_home => StateHome::new(PathBuf::from(".")),
        None => {
            eprintln!(
                "no state home: pass --home DIR or set SYNCYARD_HOME \
                 (no default by design, FR-038-11)"
            );
            std::process::exit(2);
        }
    };
    let offvm = offvm.unwrap_or_else(|| home.root().join("offvm"));
    let outcome = run_cli(&args, &home, &offvm);
    println!("{}", outcome.stdout);
    std::process::exit(outcome.code);
}
