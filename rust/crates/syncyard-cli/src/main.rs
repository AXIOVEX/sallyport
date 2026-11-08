//! CLI entry point (P1 stub). Verbs land in P2.

fn main() {
    println!(
        "syncyard-cli {} ({}) — scaffold only, contract version {}",
        env!("CARGO_PKG_VERSION"),
        syncyard_cli::CRATE_ROLE,
        syncyard_contract::CONTRACT_VERSION
    );
}
