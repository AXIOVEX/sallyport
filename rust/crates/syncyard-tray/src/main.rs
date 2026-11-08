//! syncyard-tray binary entry.
//!
//! This build performs one headless diagnostic pass: acquire
//! the install-path singleton, write one heartbeat, assemble
//! one observation from the local sources, print the rendered
//! surfaces as text, and exit. The persistent GUI event loop
//! and the OS tray icon/menu rendering are the platform layer
//! that lands with field deployment (the named live-GUI gap,
//! PROGRESS.md P4) — nothing here pretends to be that loop,
//! and no process is left running.

// Windowless by construction (FR-035-21): no console window
// on any surface, on Windows by subsystem.
#![cfg_attr(windows, windows_subsystem = "windows")]

use std::path::PathBuf;
use std::process::ExitCode;

use syncyard_contract::isotime;
use syncyard_tray::connection::{
    ConnectionSignals, ConnectionThresholds, ConnectionTracker,
};
use syncyard_tray::control::{GatekeeperChannel, SocketGatekeeper};
use syncyard_tray::fleet::load_fleet_status;
use syncyard_tray::heartbeat::{scan_heartbeats, HeartbeatWriter, TrayHeartbeat};
use syncyard_tray::identity::BuildIdentity;
use syncyard_tray::observe::{build_observation, ObservationInputs};
use syncyard_tray::render::{
    build_menu, compose_tooltip, menu_fingerprint, HealthMark, IconMarks, LinkMark, MenuInput,
    RenderState,
};
use syncyard_tray::settings::Settings;
use syncyard_tray::singleton::{acquire, SingletonError};

fn arg_value(args: &[String], name: &str) -> Option<String> {
    args.windows(2)
        .find(|w| w[0] == name)
        .map(|w| w[1].clone())
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let app_dir = arg_value(&args, "--app-dir")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("syncyard-tray-state"));
    let install_path = arg_value(&args, "--install-path")
        .map(PathBuf::from)
        .or_else(|| {
            std::env::current_exe()
                .ok()
                .and_then(|p| p.parent().map(|d| d.to_path_buf()))
        })
        .unwrap_or_else(|| PathBuf::from("."));

    // FR-035-17: one instance per install path, by construction.
    let instance = match acquire(&install_path) {
        Ok(i) => i,
        Err(SingletonError::AlreadyRunning(p)) => {
            eprintln!(
                "syncyard-tray: refused — an instance from this install path is already running: {}",
                p.display()
            );
            return ExitCode::from(2);
        }
        Err(e) => {
            eprintln!("syncyard-tray: {e}");
            return ExitCode::from(1);
        }
    };

    let identity = BuildIdentity::current();
    let settings = Settings::load(&app_dir).unwrap_or_default();

    // A user start clears a previous stopped-by-user marker:
    // the app stays ended only until the user starts it again.
    let _ = std::fs::remove_file(syncyard_tray::exit::marker_path(&app_dir));

    // Heartbeat: write this tick's, then scan for foreigners.
    let heartbeat_dir = settings
        .heartbeat_dir
        .as_ref()
        .map(PathBuf::from)
        .unwrap_or_else(|| app_dir.join("heartbeats"));
    let writer = HeartbeatWriter::new(heartbeat_dir.clone(), instance.install_path.clone());
    let now = isotime::now_unix();
    let host = std::env::var("HOSTNAME")
        .or_else(|_| std::env::var("COMPUTERNAME"))
        .unwrap_or_else(|_| "unknown-host".to_string());
    let own_heartbeat_age = if writer.path().exists() {
        Some(0)
    } else {
        None
    };
    let hb = TrayHeartbeat {
        host: host.clone(),
        build: identity.build_stamp.clone(),
        train: identity.train.clone(),
        pid: std::process::id(),
        session: String::new(),
        started_at: isotime::now_iso(),
        ts: isotime::now_iso(),
        tick: 1,
        install_path: instance.install_path.to_string_lossy().to_string(),
        lanes: Vec::new(),
    };
    if let Err(e) = writer.write(&hb) {
        // A failed heartbeat write never stalls the app.
        eprintln!("syncyard-tray: heartbeat write failed (continuing): {e}");
    }
    let foreign = scan_heartbeats(&heartbeat_dir, &instance.install_path, now, 120);

    // Snapshot from the local daemon, when one is serving.
    let snapshot = arg_value(&args, "--gk-home").and_then(|home| {
        let ch = SocketGatekeeper {
            socket_path: PathBuf::from(home).join("gk.sock"),
        };
        ch.call(&serde_json::json!({"verb": "snapshot"})).ok()
    });

    let fleet_path = arg_value(&args, "--fleet-status")
        .map(PathBuf::from)
        .or_else(|| settings.fleet_status_path.as_ref().map(PathBuf::from));
    let fleet = fleet_path.as_deref().and_then(load_fleet_status);

    let inputs = ObservationInputs {
        now_unix_s: now,
        snapshot,
        fleet,
        heartbeat_age_s: own_heartbeat_age,
        heartbeat_ever_seen: own_heartbeat_age.is_some(),
        foreign,
        runner_up: true,
    };
    let obs = build_observation(identity.clone(), &settings, &inputs);

    // One tracker pass so transitions are exercised the same
    // way the live loop will drive them.
    let mut tracker = ConnectionTracker::new(ConnectionThresholds {
        stale_after_s: settings.connection_stale_s,
        disconnect_after_s: settings.connection_disconnect_s,
    });
    let _ = tracker.observe(
        &ConnectionSignals {
            heartbeat_age_s: inputs.heartbeat_age_s,
            snapshot_age_s: inputs
                .fleet
                .as_ref()
                .and_then(|f| f.age_s(now)),
        },
        now,
    );

    // Rendered surfaces, as text (the headless diagnostic form).
    println!("{}", obs.identity.header_line());
    if obs.identity_defect {
        println!("identity: DEFECT — build stamp disagrees with train");
    }
    println!("{}", obs.connection.describe());
    println!("{}", obs.fleet_line);
    println!("{}", obs.reporting.line());
    if obs.kill_switch {
        println!("repair kill switch: SET (self-healing halted)");
    }
    if let Some(f) = &obs.foreign_instance {
        println!(
            "FOREIGN TRAY INSTANCE: install {} (pid {:?}) heartbeat {}s old",
            f.install_path, f.pid, f.age_s
        );
    }
    for job in &obs.jobs {
        println!("  {}", job.line());
    }
    let tooltip = compose_tooltip(&[
        obs.identity.header_line(),
        format!(
            "{} · {}",
            if obs.runner_up { "up" } else { "down" },
            obs.connection.state.word()
        ),
        format!("{} waiting", obs.waiting),
    ]);
    println!("tooltip: {tooltip}");
    let menu = build_menu(&MenuInput {
        headers: vec![
            obs.identity.header_line(),
            obs.connection.describe(),
            obs.fleet_line.clone(),
        ],
        open_label: None,
        controls: Vec::new(),
        exit_effect: "stops the tray only; gatekeeper and agent keep running".to_string(),
    });
    let render = RenderState {
        icon: IconMarks {
            health: if obs.runner_up {
                HealthMark::Up
            } else {
                HealthMark::Down
            },
            link: LinkMark::from(obs.connection.state),
        },
        tooltip: tooltip.clone(),
        menu_fingerprint: menu_fingerprint(&menu),
    };
    println!("render: {render:?}");
    for item in &menu {
        println!("menu: {}", item.label);
    }
    ExitCode::SUCCESS
}
