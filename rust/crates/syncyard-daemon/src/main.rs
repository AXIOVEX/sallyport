//! Daemon entry point: `syncyard-daemon serve --home DIR [--offvm DIR]`.
//!
//! Boot sequence (spec 038 section 2): take the state lock, load
//! disk state, acquire the fencing epoch on the off-VM record, run
//! boot reconciliation, announce the recovered state and gaps by
//! name (ERR-09), then serve. The GitHub run-state source is wired
//! at cutover; until then reconciliation runs against an empty
//! source (records keep their disk state, divergences are none) and
//! dispatch uses no live transport — `serve` therefore refuses to
//! start without an explicit `--allow-no-transport` acknowledgement
//! in this P2 form, so it can never be mistaken for the production
//! daemon. The full boot path is exercised by the crate's tests
//! with fakes.

use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
use std::sync::{Arc, Mutex};

use syncyard_daemon::dispatch::Dispatcher;
use syncyard_daemon::offvm::{acquire_epoch, DirOffVmStore};
use syncyard_daemon::reconcile::{boot_reconcile, RunInfo, RunStateSource};
use syncyard_daemon::serve::{serve, Engine, ServeConfig};
use syncyard_daemon::state::{DaemonState, StateHome};
use syncyard_daemon::vwrite::FsWriter;

/// No live GitHub transport yet (P2 named gap).
struct NoTransport;
impl Dispatcher for NoTransport {
    fn trigger(
        &self,
        _job: &syncyard_contract::QueueRecord,
        _key: &str,
    ) -> Result<syncyard_daemon::dispatch::RunRef, String> {
        Err("no dispatch transport wired (P2: GitHub transport is cutover wiring)".to_string())
    }
    fn find_by_key(&self, _key: &str) -> Result<Option<syncyard_daemon::dispatch::RunRef>, String> {
        Ok(None)
    }
}
impl RunStateSource for NoTransport {
    fn run_info(&self, _run_id: u64) -> Result<Option<RunInfo>, String> {
        Ok(None)
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.len() < 2 || args[1] != "serve" {
        eprintln!("usage: syncyard-daemon serve --home DIR [--offvm DIR] --allow-no-transport");
        std::process::exit(2);
    }
    let mut home_dir: Option<PathBuf> = None;
    let mut offvm_dir: Option<PathBuf> = None;
    let mut allow_no_transport = false;
    let mut i = 2;
    while i < args.len() {
        match args[i].as_str() {
            "--home" => {
                home_dir = args.get(i + 1).map(PathBuf::from);
                i += 2;
            }
            "--offvm" => {
                offvm_dir = args.get(i + 1).map(PathBuf::from);
                i += 2;
            }
            "--allow-no-transport" => {
                allow_no_transport = true;
                i += 1;
            }
            other => {
                eprintln!("unknown argument: {other}");
                std::process::exit(2);
            }
        }
    }
    let Some(home_dir) = home_dir else {
        eprintln!("--home DIR is required (state-home separation, FR-038-11)");
        std::process::exit(2);
    };
    if !allow_no_transport {
        eprintln!(
            "refusing to serve: no dispatch transport is wired in this build \
             (P2 gap). Pass --allow-no-transport to run the state engine only."
        );
        std::process::exit(2);
    }
    let home = StateHome::new(home_dir);
    std::fs::create_dir_all(home.queue_dir()).expect("state home queue dir");
    let _lock = match syncyard_daemon::lock::try_state_lock(&home.lock_path()) {
        Ok(Some(lock)) => lock,
        Ok(None) => {
            eprintln!("another writer holds the state lock; refusing to serve");
            std::process::exit(1);
        }
        Err(e) => {
            eprintln!("state lock failed: {e}");
            std::process::exit(1);
        }
    };
    let store = Arc::new(DirOffVmStore::new(
        offvm_dir.unwrap_or_else(|| home.root().join("offvm")),
    ));
    let holder = format!("syncyard-daemon-{}", std::process::id());
    let token = match acquire_epoch(store.as_ref(), &holder) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("fencing epoch acquisition failed: {e}");
            std::process::exit(1);
        }
    };
    let (mut state, gaps) = DaemonState::load(home.clone());
    let transport = NoTransport;
    let report = boot_reconcile(&mut state, store.as_ref(), &transport, &[], gaps);
    println!(
        "BOOT recovered_submits={:?} recovered_dispatches={:?} ghosts={:?} gaps={:?} epoch={}",
        report.recovered_submits,
        report.recovered_dispatches,
        report.ghosts,
        report.gaps,
        token.epoch
    );
    let engine = Arc::new(Mutex::new(Engine {
        state,
        store,
        dispatcher: Arc::new(NoTransport),
        token,
        writer: Arc::new(FsWriter::default()),
    }));
    let shutdown = Arc::new(AtomicBool::new(false));
    if let Err(e) = serve(engine, &home, ServeConfig::default(), shutdown) {
        eprintln!("serve failed: {e}");
        std::process::exit(1);
    }
}
