//! Agent entry point (P1 stub). The agent lands in P3.

fn main() {
    println!(
        "syncyard-agent {} ({}) — scaffold only, agent lands in P3",
        env!("CARGO_PKG_VERSION"),
        syncyard_agent::CRATE_ROLE
    );
}
