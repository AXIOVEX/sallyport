//! Tray/app entry point (P1 stub). The successor app lands in P4.

fn main() {
    println!(
        "syncyard-tray {} ({}) — scaffold only, app lands in P4",
        env!("CARGO_PKG_VERSION"),
        syncyard_tray::CRATE_ROLE
    );
}
