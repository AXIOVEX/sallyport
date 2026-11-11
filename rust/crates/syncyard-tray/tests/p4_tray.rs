//! P4 tray tests: spec 035 presentation surface, headless.
//! Every rule is exercised through the crate's pure modules
//! over fixture records — the payload verdicts are these
//! tests' own results.

use std::path::{Path, PathBuf};

use serde_json::{json, Value};

use syncyard_tray::connection::{
    evaluate, ConnectionSignals, ConnectionThresholds, ConnectionTracker, LinkState,
};
use syncyard_tray::exit::{
    perform_exit, read_stopped_marker, watchdog_relaunch_decision, StoppedByUserMarker,
    WatchdogDecision,
};
use syncyard_tray::fleet::{parse_fleet_status, Freshness};
use syncyard_tray::heartbeat::{scan_heartbeats, HeartbeatWriter, LaneBeat, TrayHeartbeat};
use syncyard_tray::identity::BuildIdentity;
use syncyard_tray::observe::{build_observation, ObservationInputs};
use syncyard_tray::platform::{child_creation_flags, CONSOLE_POLICY};
use syncyard_tray::render::{
    build_menu, compose_tooltip, menu_fingerprint, words, HealthMark, IconMarks, LinkMark,
    MenuInput, MenuItem, MenuKind, RenderState,
};
use syncyard_tray::settings::{AppSurface, Settings, TrayApp};
use syncyard_tray::singleton::{acquire, refusal_log_path, SingletonError};

/// 2026-11-08T10:00:00Z.
const NOW: i64 = 1_794_132_000;

fn scratch(tag: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("sy-p4-tray-{}-{tag}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir
}

fn identity() -> BuildIdentity {
    BuildIdentity {
        train: "0.4.0".to_string(),
        build_stamp: "0.4.0".to_string(),
    }
}

/// A daemon snapshot fixture shaped exactly like the daemon's
/// own: one completed job (verdict verbatim), one running job
/// whose progress view is serialized from the daemon's real
/// ProgressView type.
fn snapshot_fixture() -> Value {
    let view = syncyard_daemon::progress::ProgressView {
        job_id: "job-run".to_string(),
        display: "in progress".to_string(),
        age_s: Some(12),
        elapsed_s: Some(90),
        started: true,
        last_marker: Some("HALFWAY".to_string()),
        heartbeats: 3,
        fraction: Some((3, 10)),
        label: None,
        completed_word: None,
        regressions: 0,
        regression_note: None,
        malformed_progress_count: 0,
        unknown_line_count: 0,
        quarantined_lines: 0,
        source: Some("agent".to_string()),
        run_id: Some(425),
    };
    json!({
        "generated_at": "2026-11-08T10:00:00Z",
        "source": "syncyard-daemon",
        "jobs_total": 2,
        "waiting": 1,
        "active": 1,
        "kill_switch": false,
        "jobs": [
            {
                "id": "job-done", "key": "k-done", "program": "p",
                "state": "completed", "lane": "quick", "venue": "native",
                "priority": 0, "priority_source": "auto", "held": false,
                "created_at": "2026-11-08T09:00:00Z",
                "dispatched_at": "2026-11-08T09:01:00Z",
                "completed_at": "2026-11-08T09:05:00Z",
                "run_id": 424, "run_conclusion": "success",
                "verdict_source": "harvest"
            },
            {
                "id": "job-run", "key": "k-run", "program": "p",
                "state": "dispatched", "lane": "quick", "venue": "native",
                "priority": 0, "priority_source": "auto", "held": false,
                "created_at": "2026-11-08T09:57:00Z",
                "dispatched_at": "2026-11-08T09:58:30Z",
                "completed_at": null,
                "run_id": 425, "run_conclusion": null,
                "verdict_source": null
            }
        ],
        "progress": { "job-run": serde_json::to_value(&view).unwrap() },
        "reporting": {"queue_depth": 4, "dedupe_rate": 0.98, "cap_state": "ok"}
    })
}

fn inputs(now: i64) -> ObservationInputs {
    ObservationInputs {
        now_unix_s: now,
        snapshot: Some(snapshot_fixture()),
        fleet: parse_fleet_status(
            r#"{"generated_at":"2026-11-08T09:59:00Z","source":"gatekeeper","mode":"live","queue_state":"open","running_total":1,"waiting_total":1,"active":[{}],"waiting":[{}]}"#,
        ),
        heartbeat_age_s: Some(30),
        heartbeat_ever_seen: true,
        foreign: None,
        runner_up: true,
    }
}

// T035-01 identity-displayed
#[test]
fn t035_01_identity_on_every_surface() {
    let id = identity();
    assert!(id.header_line().contains("0.4.0"));
    let menu = build_menu(&MenuInput {
        headers: vec![id.header_line()],
        open_label: None,
        controls: vec![],
        exit_effect: "stops the tray only".to_string(),
    });
    assert_eq!(menu[0].label, id.header_line());
    assert_eq!(menu[0].kind, MenuKind::Header);
    let tip = compose_tooltip(&[id.header_line(), "up, linked".to_string()]);
    assert!(tip.starts_with("Syncyard 0.4.0"));
}

// T035-02 identity-stamp-mismatch
#[test]
fn t035_02_stamp_mismatch_is_a_defect_state() {
    let bad = BuildIdentity {
        train: "0.4.0".to_string(),
        build_stamp: "0.3.9-deadbeef".to_string(),
    };
    assert!(bad.stamp_defect());
    let obs = build_observation(bad, &Settings::default(), &inputs(NOW));
    assert!(obs.identity_defect);
    let good = build_observation(identity(), &Settings::default(), &inputs(NOW));
    assert!(!good.identity_defect);
    // A stamp that carries the train plus provenance agrees.
    let stamped = BuildIdentity {
        train: "0.4.0".to_string(),
        build_stamp: "0.4.0+abc1234".to_string(),
    };
    assert!(!stamped.stamp_defect());
}

// T035-03 record-values-verbatim
#[test]
fn t035_03_rendered_values_are_the_record_values() {
    let obs = build_observation(identity(), &Settings::default(), &inputs(NOW));
    let done = obs.jobs.iter().find(|j| j.id == "job-done").unwrap();
    assert_eq!(done.state, "completed");
    assert_eq!(done.conclusion.as_deref(), Some("success"));
    assert!(done.line().contains("success"));
    assert_eq!(obs.waiting, 1);
    assert_eq!(obs.active, 1);
    assert_eq!(obs.total, 2);
}

// T035-04 interim-not-verdict
#[test]
fn t035_04_interim_delivery_is_still_running_never_a_verdict() {
    let obs = build_observation(identity(), &Settings::default(), &inputs(NOW));
    let run = obs.jobs.iter().find(|j| j.id == "job-run").unwrap();
    let line = run.line();
    assert!(line.contains("still running"), "{line}");
    assert!(line.contains("90s"), "{line}");
    assert!(line.contains("in progress"), "{line}");
    for verdict_word in ["success", "failure", "passed", "failed", "verdict"] {
        assert!(
            !line.to_lowercase().contains(verdict_word),
            "interim line carries a verdict word: {line}"
        );
    }
    assert_eq!(run.conclusion, None);
}

// T035-05 connection-signals (state derivation)
#[test]
fn t035_05_connection_states_from_signal_ages() {
    let t = ConnectionThresholds::default();
    let s = |hb, snap| ConnectionSignals {
        heartbeat_age_s: hb,
        snapshot_age_s: snap,
    };
    assert_eq!(evaluate(&s(Some(30), None), &t).state, LinkState::Connected);
    assert_eq!(evaluate(&s(Some(500), None), &t).state, LinkState::Stale);
    assert_eq!(
        evaluate(&s(Some(5000), None), &t).state,
        LinkState::Disconnected
    );
    // Heartbeat primary: a fresh heartbeat wins over a stale snapshot.
    let r = evaluate(&s(Some(10), Some(5000)), &t);
    assert_eq!(r.state, LinkState::Connected);
    assert_eq!(r.rested_on, "backchannel heartbeat");
    // Snapshot secondary when no heartbeat exists.
    let r = evaluate(&s(None, Some(10)), &t);
    assert_eq!(r.state, LinkState::Connected);
    assert_eq!(r.rested_on, "fleet snapshot");
}

// T035-06 connection-no-false-connected
#[test]
fn t035_06_no_signal_ever_is_disconnected_never_connected() {
    let r = evaluate(
        &ConnectionSignals::default(),
        &ConnectionThresholds::default(),
    );
    assert_eq!(r.state, LinkState::Disconnected);
    assert!(!r.any_signal_ever);
    assert!(r.describe().contains("no signal seen"));
}

// T035-07 icon-independence
#[test]
fn t035_07_icon_marks_independent_and_render_on_change_only() {
    let obs = build_observation(identity(), &Settings::default(), &inputs(NOW));
    let marks = IconMarks {
        health: HealthMark::Up,
        link: LinkMark::from(obs.connection.state),
    };
    // Down runner + connected link: the marks do not collapse.
    let marks2 = IconMarks {
        health: HealthMark::Down,
        ..marks
    };
    assert_ne!(marks, marks2);
    assert_eq!(marks2.link, marks.link);
    assert_eq!(marks.health.word(), "up");
    assert_eq!(marks2.health.word(), "down");
    // Two identical observations produce identical render
    // states: the shell redraws on change only.
    let render = |o: &syncyard_tray::observe::TrayObservation| RenderState {
        icon: IconMarks {
            health: HealthMark::Up,
            link: LinkMark::from(o.connection.state),
        },
        tooltip: o.fleet_line.clone(),
        menu_fingerprint: o.jobs.len().to_string(),
    };
    let obs2 = build_observation(identity(), &Settings::default(), &inputs(NOW));
    assert_eq!(render(&obs), render(&obs2));
}

// T035-08 connection-wording
#[test]
fn t035_08_wording_names_the_signal_and_the_heartbeat_only_case() {
    let t = ConnectionThresholds::default();
    let hb_only = evaluate(
        &ConnectionSignals {
            heartbeat_age_s: Some(30),
            snapshot_age_s: None,
        },
        &t,
    );
    let d = hb_only.describe();
    assert!(d.contains("signal: backchannel heartbeat only"), "{d}");
    let with_snap = evaluate(
        &ConnectionSignals {
            heartbeat_age_s: Some(30),
            snapshot_age_s: Some(60),
        },
        &t,
    );
    let d2 = with_snap.describe();
    assert!(d2.contains("backchannel heartbeat"), "{d2}");
    assert!(!d2.contains("heartbeat only"), "{d2}");
}

// T035-09 connection-transitions-logged
#[test]
fn t035_09_transitions_logged_with_age_steady_state_silent() {
    let mut tr = ConnectionTracker::new(ConnectionThresholds::default());
    let s = |age| ConnectionSignals {
        heartbeat_age_s: age,
        snapshot_age_s: None,
    };
    tr.observe(&s(Some(10)), NOW);
    tr.observe(&s(Some(20)), NOW + 30);
    tr.observe(&s(Some(30)), NOW + 60);
    assert_eq!(tr.transitions.len(), 1, "steady state logs nothing");
    tr.observe(&s(Some(400)), NOW + 400);
    assert_eq!(tr.transitions.len(), 2);
    let t2 = &tr.transitions[1];
    assert_eq!(t2.from, Some(LinkState::Connected));
    assert_eq!(t2.to, LinkState::Stale);
    assert_eq!(t2.causing_signal_age_s, Some(400));
    assert_eq!(t2.at_unix_s, NOW + 400);
}

// T035-10 indicator-display-only
#[test]
fn t035_10_observing_and_rendering_writes_nothing() {
    let dir = scratch("t10");
    let fleet_file = dir.join("fleet-status.json");
    std::fs::write(
        &fleet_file,
        r#"{"generated_at":"2026-11-08T09:59:00Z","running_total":1,"waiting_total":1}"#,
    )
    .unwrap();
    let before = dir_snapshot(&dir);
    let fleet = syncyard_tray::fleet::load_fleet_status(&fleet_file);
    let mut inp = inputs(NOW);
    inp.fleet = fleet;
    let obs = build_observation(identity(), &Settings::default(), &inp);
    let _ = compose_tooltip(std::slice::from_ref(&obs.fleet_line));
    let _ = build_menu(&MenuInput {
        headers: vec![obs.connection.describe()],
        open_label: None,
        controls: vec![],
        exit_effect: "x".to_string(),
    });
    let _ = evaluate(
        &ConnectionSignals {
            heartbeat_age_s: Some(1),
            snapshot_age_s: None,
        },
        &ConnectionThresholds::default(),
    );
    assert_eq!(dir_snapshot(&dir), before, "the display path wrote to disk");
}

fn dir_snapshot(dir: &Path) -> Vec<(String, Vec<u8>)> {
    let mut out: Vec<(String, Vec<u8>)> = std::fs::read_dir(dir)
        .unwrap()
        .flatten()
        .map(|e| {
            (
                e.file_name().to_string_lossy().to_string(),
                std::fs::read(e.path()).unwrap_or_default(),
            )
        })
        .collect();
    out.sort();
    out
}

// T035-11 tooltip-budget
#[test]
fn t035_11_tooltip_within_budget_whole_parts_only() {
    let parts: Vec<String> = vec![
        "Syncyard 0.4.0".into(),
        "up, linked".into(),
        "3 waiting".into(),
        "2 running long job names here".into(),
        "extra tail part".into(),
    ];
    let tip = compose_tooltip(&parts);
    assert!(tip.chars().count() <= 63, "{tip}");
    // Every rendered fragment is a whole original part.
    for fragment in tip.split(", ") {
        assert!(
            parts.iter().any(|p| p == fragment)
                || parts[0].contains(fragment)
                || fragment == "up"
                || fragment == "linked",
            "fragment {fragment:?} is not a whole part of {tip:?}"
        );
    }
    // Already-short tooltips pass through untouched.
    assert_eq!(compose_tooltip(&["a".into(), "b".into()]), "a, b");
    // Priority: the first part is kept, the tail is dropped.
    let tip2 = compose_tooltip(&["FIRST".into(), "x".repeat(40), "y".repeat(40)]);
    assert!(tip2.starts_with("FIRST"), "{tip2}");
}

// T035-12 status-vocabulary
#[test]
fn t035_12_vocabulary_is_exactly_the_shared_set() {
    let mut seen: Vec<&str> = vec![
        HealthMark::Up.word(),
        HealthMark::Down.word(),
        LinkMark::Linked.word(),
        LinkMark::LinkStale.word(),
        LinkMark::Unlinked.word(),
        LinkState::Connected.word(),
        LinkState::Stale.word(),
        LinkState::Disconnected.word(),
    ];
    seen.sort();
    seen.dedup();
    assert_eq!(seen, vec!["down", "link stale", "linked", "unlinked", "up"]);
    assert_eq!(words::UP, "up");
    assert_eq!(words::DOWN, "down");
    // "N waiting" is the waiting-count form.
    let tip = compose_tooltip(&[format!("{} waiting", 7)]);
    assert_eq!(tip, "7 waiting");
}

// T035-13 menu-completeness (skeleton order)
#[test]
fn t035_13_menu_skeleton_order_exit_last_and_enabled() {
    let menu = build_menu(&MenuInput {
        headers: vec!["Syncyard 0.4.0".into(), "connection: linked".into()],
        open_label: Some("Open dashboard".into()),
        controls: vec![MenuItem::action("Stop syncyard-agent", "stop-agent")],
        exit_effect: "stops the tray only".to_string(),
    });
    let kinds: Vec<MenuKind> = menu.iter().map(|m| m.kind).collect();
    assert_eq!(
        kinds,
        vec![
            MenuKind::Header,
            MenuKind::Header,
            MenuKind::Action, // Open
            MenuKind::Action, // per-item control
            MenuKind::Settings,
            MenuKind::Updates,
            MenuKind::Exit,
        ]
    );
    let last = menu.last().unwrap();
    assert_eq!(last.kind, MenuKind::Exit);
    assert!(last.enabled, "Exit is always enabled");
    assert!(last.label.contains("stops the tray only"));
    // No Open surface exists → the row is omitted, not greyed.
    let menu2 = build_menu(&MenuInput {
        headers: vec!["h".into()],
        open_label: None,
        controls: vec![],
        exit_effect: "x".to_string(),
    });
    assert!(!menu2.iter().any(|m| m.label.contains("Open")));
    // Fingerprints differ when the menu differs.
    assert_ne!(menu_fingerprint(&menu), menu_fingerprint(&menu2));
}

// T035-14 control-verbs
#[test]
fn t035_14_verbs_start_stop_enable_disable_only_for_permissions() {
    let mut enable = MenuItem::action("Enable syncyard-agent (supervisor)", "enable-agent");
    enable.supervisor_permission = true;
    let menu = build_menu(&MenuInput {
        headers: vec![],
        open_label: None,
        controls: vec![
            MenuItem::action("Start syncyard-agent", "start-agent"),
            MenuItem::action("Stop syncyard-agent", "stop-agent"),
            MenuItem::action("Restart gatekeeper", "restart-gk"),
            enable,
        ],
        exit_effect: "x".to_string(),
    });
    for item in &menu {
        let verb = item.label.split_whitespace().next().unwrap_or("");
        if ["Enable", "Disable"].contains(&verb) {
            assert!(
                item.supervisor_permission,
                "Enable/Disable on a non-permission row: {}",
                item.label
            );
        }
        if ["Start", "Stop", "Restart"].contains(&verb) {
            assert!(
                !item.supervisor_permission || verb == "Enable",
                "service verb on a permission row: {}",
                item.label
            );
        }
    }
}

// T035-15 disabled-with-reason
#[test]
fn t035_15_greyed_items_carry_reasons_status_lines_are_not_actions() {
    let greyed = MenuItem::unavailable("Stop syncyard-agent", "not running");
    assert!(!greyed.enabled);
    assert_eq!(greyed.reason.as_deref(), Some("not running"));
    let header = MenuItem::header("connection: unlinked");
    assert_eq!(header.kind, MenuKind::Header);
    assert!(header.enabled, "status lines never render disabled");
    assert!(header.action.is_none());
}

// T035-16 state-consistency
#[test]
fn t035_16_one_observation_moves_icon_tooltip_and_menu_together() {
    let obs = build_observation(identity(), &Settings::default(), &inputs(NOW));
    let marks = IconMarks {
        health: HealthMark::Up,
        link: LinkMark::from(obs.connection.state),
    };
    let tooltip = compose_tooltip(&[
        obs.identity.header_line(),
        format!("up · {}", obs.connection.state.word()),
        format!("{} waiting", obs.waiting),
    ]);
    let menu = build_menu(&MenuInput {
        headers: vec![obs.identity.header_line(), obs.connection.describe()],
        open_label: None,
        controls: vec![],
        exit_effect: "x".to_string(),
    });
    // All three surfaces agree because all three read the one
    // observation from the one tick.
    assert_eq!(marks.link.word(), obs.connection.state.word());
    assert!(tooltip.contains(obs.connection.state.word()));
    assert!(menu[1].label.contains(obs.connection.state.word()));
    assert_eq!(obs.connection.state, LinkState::Connected);
}

// T035-17 exit-behavior
#[test]
fn t035_17_exit_writes_marker_and_log_and_states_its_effect() {
    let dir = scratch("t17");
    let marker = StoppedByUserMarker {
        host: "test-host".into(),
        pid: 1234,
        install_path: "C:\\Apps\\Syncyard".into(),
        stopped_at: "2026-11-08T10:00:00Z".into(),
        build: "0.4.0".into(),
    };
    let (marker_file, log_file) = perform_exit(&dir, &marker).unwrap();
    assert!(marker_file.exists());
    let read = read_stopped_marker(&dir).unwrap();
    assert_eq!(read, marker);
    let log = std::fs::read_to_string(&log_file).unwrap();
    assert!(log.contains("exited by user"), "{log}");
    // The Exit row states its effect on supervised things.
    let menu = build_menu(&MenuInput {
        headers: vec![],
        open_label: None,
        controls: vec![],
        exit_effect: "stops the tray only; gatekeeper and agent keep running".to_string(),
    });
    let exit_row = menu.last().unwrap();
    assert!(exit_row.label.contains("gatekeeper and agent keep running"));
}

// T035-18 exit-stays-ended (test half of test + human-as-oracle)
#[test]
fn t035_18_watchdog_honors_the_marker_for_the_full_window() {
    let marker = StoppedByUserMarker {
        host: "h".into(),
        pid: 1,
        install_path: "p".into(),
        stopped_at: "2026-11-08T10:00:00Z".into(),
        build: "0.4.0".into(),
    };
    // Simulated minutes 0..=10 after exit: never a relaunch.
    for _minute in 0..=10 {
        assert_eq!(
            watchdog_relaunch_decision(Some(&marker)),
            WatchdogDecision::StayDown
        );
    }
    assert_eq!(watchdog_relaunch_decision(None), WatchdogDecision::Relaunch);
}

// T035-19 heartbeat
#[test]
fn t035_19_heartbeat_fields_atomic_and_failure_never_stalls() {
    let dir = scratch("t19");
    let install = dir.join("install-a");
    let writer = HeartbeatWriter::new(dir.join("beats"), install.clone());
    let hb = TrayHeartbeat {
        host: "test-host".into(),
        build: "0.4.0".into(),
        train: "0.4.0".into(),
        pid: 42,
        session: "session-1".into(),
        started_at: "2026-11-08T09:00:00Z".into(),
        ts: "2026-11-08T10:00:00Z".into(),
        tick: 7,
        install_path: install.to_string_lossy().to_string(),
        lanes: vec![LaneBeat {
            name: "quick".into(),
            running: true,
            faulted: false,
        }],
    };
    let path = writer.write(&hb).unwrap();
    let text = std::fs::read_to_string(&path).unwrap();
    let back: TrayHeartbeat = serde_json::from_str(&text).unwrap();
    assert_eq!(back, hb);
    for field in [
        "host",
        "build",
        "pid",
        "session",
        "started_at",
        "ts",
        "tick",
        "lanes",
    ] {
        assert!(text.contains(&format!("\"{field}\"")), "field {field}");
    }
    // A failed write is an error value; the app still observes.
    let blocker = dir.join("blocker");
    std::fs::write(&blocker, "not a dir").unwrap();
    let bad_writer = HeartbeatWriter::new(blocker.join("beats"), install);
    assert!(bad_writer.write(&hb).is_err());
    let obs = build_observation(identity(), &Settings::default(), &inputs(NOW));
    assert_eq!(
        obs.total, 2,
        "observation still assembles after the failed write"
    );
}

// T035-20 singleton-by-install-path + the defect-named test for
// FR-035-17 (defect: laptop-two-tray-fight-2026-11-06).
#[test]
fn t035_20_singleton_by_install_path() {
    run_two_tray_fight("t20");
}

#[test]
fn laptop_two_tray_fight_2026_11_06() {
    run_two_tray_fight("t20-fight");
}

/// The fight itself, executed under both test names above.
/// Each execution gets its OWN scratch dir: the harness runs
/// the two tests concurrently in one process, and sharing one
/// fixture dir (same install paths, same heartbeat temp-file
/// names) lets one execution delete the other's files
/// mid-flight. The macOS CI failure of 2026-11-11 (the
/// heartbeat write below failing NotFound) was exactly that
/// race, not a platform difference.
fn run_two_tray_fight(tag: &str) {
    let dir = scratch(tag);
    let install_a = dir.join("Programs").join("SyncyardTray");
    let install_b = dir.join("build-tray-r13");
    // First instance of install A takes the path.
    let first = acquire(&install_a).unwrap();
    // THE FIELD DEFECT: a second launch of the same install is
    // refused, and the refusal is in the install's log.
    let second = acquire(&install_a);
    assert!(
        matches!(second, Err(SingletonError::AlreadyRunning(_))),
        "second instance from the same path must be refused"
    );
    let log = std::fs::read_to_string(refusal_log_path(&install_a)).unwrap();
    assert!(log.contains("refused"), "{log}");
    // A DIFFERENT install path is a different singleton domain…
    let other = acquire(&install_b).unwrap();
    // …but it is detected and surfaced, never silently
    // coexisting: its fresh heartbeat shows up in A's scan.
    let beats = dir.join("beats");
    let writer_b = HeartbeatWriter::new(beats.clone(), install_b.clone());
    let canon_b = std::fs::canonicalize(&install_b).unwrap();
    writer_b
        .write(&TrayHeartbeat {
            host: "laptop".into(),
            build: "0.4.0".into(),
            train: "0.4.0".into(),
            pid: 99,
            session: "s".into(),
            started_at: "2026-11-08T09:59:00Z".into(),
            ts: "2026-11-08T10:00:00Z".into(),
            tick: 3,
            install_path: canon_b.to_string_lossy().to_string(),
            lanes: vec![],
        })
        .unwrap();
    let foreign =
        scan_heartbeats(&beats, &install_a, NOW, 120).expect("the foreign instance is detected");
    assert_eq!(foreign.install_path, canon_b.to_string_lossy());
    let mut inp = inputs(NOW);
    inp.foreign = Some(foreign);
    let obs = build_observation(identity(), &Settings::default(), &inp);
    assert!(
        obs.foreign_instance.is_some(),
        "the foreign instance is surfaced in the app's state"
    );
    // A stale foreign heartbeat is history, not a conflict.
    assert!(scan_heartbeats(&beats, &install_a, NOW + 3600, 120).is_none());
    // Dropping the guard frees the path for a later launch.
    drop(first);
    drop(other);
    let third = acquire(&install_a);
    assert!(third.is_ok(), "the path frees when the instance ends");
}

// T035-21 staleness-rendering
#[test]
fn t035_21_stale_values_render_stale_with_age() {
    let fleet = parse_fleet_status(
        r#"{"generated_at":"2026-11-08T09:00:00Z","running_total":2,"waiting_total":5}"#,
    )
    .unwrap();
    assert_eq!(fleet.freshness(NOW, 900), Freshness::Stale { age_s: 3600 });
    let line = fleet.line(NOW, 900);
    assert!(line.contains("stale 3600s"), "{line}");
    assert!(line.contains("2 running"), "{line}");
    assert!(line.contains("5 waiting"), "{line}");
    assert_eq!(fleet.freshness(NOW - 3600 + 60, 900), Freshness::Fresh);
    // No timestamp at all: age unknown, never "fresh".
    let no_ts = parse_fleet_status(r#"{"running_total":0,"waiting_total":0}"#).unwrap();
    assert_eq!(no_ts.freshness(NOW, 900), Freshness::Unknown);
}

// T035-22 reporting-health-visible
#[test]
fn t035_22_reporting_health_rendered_verbatim_or_unknown() {
    let obs = build_observation(identity(), &Settings::default(), &inputs(NOW));
    let line = obs.reporting.line();
    assert!(line.contains("queue 4"), "{line}");
    assert!(line.contains("dedupe 0.98"), "{line}");
    assert!(line.contains("daily cap ok"), "{line}");
    // Cap-breached state renders as published, not softened.
    let mut snap = snapshot_fixture();
    snap["reporting"] = json!({"queue_depth": 9, "dedupe_rate": 0.5, "cap_state": "breached"});
    let mut inp = inputs(NOW);
    inp.snapshot = Some(snap);
    let obs = build_observation(identity(), &Settings::default(), &inp);
    assert!(obs.reporting.line().contains("breached"));
    // Absent reporting state is "unknown", never invented.
    let mut inp2 = inputs(NOW);
    inp2.snapshot = None;
    let obs2 = build_observation(identity(), &Settings::default(), &inp2);
    assert!(obs2.reporting.line().contains("unknown"));
}

// T035-23 settings-in-app (also T038-42 settings-in-app)
#[test]
fn t035_23_settings_live_in_the_one_app() {
    // T038-42 settings-in-app: editing happens only through
    // the app's settings surface.
    let mut app = TrayApp::new(identity(), Settings::default());
    let new = Settings {
        fleet_stale_s: 300,
        ..Settings::default()
    };
    assert!(
        app.edit_settings(new.clone()).is_err(),
        "no editing off the settings surface"
    );
    app.open_settings();
    assert_eq!(app.surface, AppSurface::Settings);
    app.edit_settings(new).unwrap();
    assert_eq!(app.settings.fleet_stale_s, 300);
    // Invalid settings are refused on the surface, not stored.
    let bad = Settings {
        connection_stale_s: 900,
        connection_disconnect_s: 900,
        ..Settings::default()
    };
    assert!(app.edit_settings(bad).is_err());
    // Persistence is an app-owned file, round-tripped.
    let dir = scratch("t23");
    app.settings.save(&dir).unwrap();
    let loaded = Settings::load(&dir).unwrap();
    assert_eq!(loaded, app.settings);
    // One app, one binary: the crate ships exactly one bin and
    // no settings-editor binary or src/bin directory.
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let toml = std::fs::read_to_string(manifest_dir.join("Cargo.toml")).unwrap();
    assert!(!toml.contains("[[bin]]"), "exactly the one implicit bin");
    assert!(manifest_dir.join("src/main.rs").exists());
    assert!(!manifest_dir.join("src/bin").exists());
}

// T035-24 windowless-mandate (construction half; the live
// half is a field gate on real hosts)
#[test]
fn t035_24_windowless_by_construction() {
    let p = CONSOLE_POLICY;
    let flags = [
        p.windows_subsystem_gui,
        p.children_create_no_window,
        p.children_detached,
    ];
    assert_eq!(flags, [true, true, true]);
    #[cfg(windows)]
    assert_eq!(child_creation_flags(), 0x0800_0000);
    #[cfg(not(windows))]
    assert_eq!(child_creation_flags(), 0);
    // The binary itself is built for the windows subsystem.
    let main_src =
        std::fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/main.rs")).unwrap();
    assert!(
        main_src.contains("windows_subsystem = \"windows\""),
        "main.rs carries the windows subsystem attribute"
    );
}

// T034-14 tray-mcp-parity (tray half): the job line's progress
// values ARE the daemon view's values from the same snapshot —
// the tray polls nothing independently.
#[test]
fn t034_14_progress_values_equal_the_daemon_view() {
    let obs = build_observation(identity(), &Settings::default(), &inputs(NOW));
    let run = obs.jobs.iter().find(|j| j.id == "job-run").unwrap();
    assert_eq!(run.progress_display.as_deref(), Some("in progress"));
    assert_eq!(run.progress_fraction, Some((3, 10)));
    assert_eq!(run.progress_age_s, Some(12));
    // A job with no view in the snapshot shows no progress —
    // the tray does not go and fetch one.
    let done = obs.jobs.iter().find(|j| j.id == "job-done").unwrap();
    assert_eq!(done.progress_display, None);
}

// T037-01 pane-is-only-control-home (audit half): no other
// crate issues control commands. Contract defines the types;
// the daemon verifies/executes; only the tray signs+issues.
#[test]
fn t037_01_only_the_tray_issues_commands() {
    let rust_root = Path::new(env!("CARGO_MANIFEST_DIR")).join("..");
    for crate_name in ["syncyard-daemon", "syncyard-cli", "syncyard-agent"] {
        let src = rust_root.join(crate_name).join("src");
        for entry in walk_rs(&src) {
            let text = std::fs::read_to_string(&entry).unwrap();
            assert!(
                !text.contains(".sign("),
                "{} must not sign control commands",
                entry.display()
            );
            assert!(
                !text.contains("CommandIssuer"),
                "{} must not issue control commands",
                entry.display()
            );
        }
    }
    // And the tray control module itself exists as the home.
    assert!(Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("src/control.rs")
        .exists());
}

fn walk_rs(dir: &Path) -> Vec<PathBuf> {
    let mut out = Vec::new();
    if let Ok(entries) = std::fs::read_dir(dir) {
        for e in entries.flatten() {
            let p = e.path();
            if p.is_dir() {
                out.extend(walk_rs(&p));
            } else if p.extension().is_some_and(|x| x == "rs") {
                out.push(p);
            }
        }
    }
    out
}
