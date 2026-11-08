//! Daemon entry point (P1 stub). The `serve` implementation lands in P2.

fn main() {
    println!(
        "syncyard-daemon {} ({}) — scaffold only, serve lands in P2",
        env!("CARGO_PKG_VERSION"),
        syncyard_daemon::CRATE_ROLE
    );
}
