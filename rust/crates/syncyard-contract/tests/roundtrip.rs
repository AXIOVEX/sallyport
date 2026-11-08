//! Round-trip compatibility with the Python gatekeeper's queue-record
//! JSON. The fixture is a real completed record written by `gk.py`
//! (fleet-mirror cycle 20261108T0946, run 42468614831), copied
//! verbatim; this test is the evidence that the Rust contract reads
//! the Python system's state without loss — the property the one-move
//! cutover (spec 038 section 1) stands on.

use syncyard_contract::{JobRecord, JobState, QueueRecord};

const REAL_RECORD: &str = include_str!("fixtures/queue-record-fleet-mirror-20261108t0946.json");

#[test]
fn real_python_record_round_trips_losslessly() {
    let original: serde_json::Value =
        serde_json::from_str(REAL_RECORD).expect("fixture is valid JSON");
    let record: QueueRecord =
        serde_json::from_str(REAL_RECORD).expect("real record deserializes into QueueRecord");

    // Typed spot checks against the record's own values.
    assert_eq!(
        record.id,
        "20261108T094635Z-fleet-mirror-20261108t0946-e0c9ae"
    );
    assert_eq!(record.key, "fleet-mirror-20261108T0946");
    assert_eq!(record.program, "desktop-access");
    assert_eq!(record.state, JobState::Completed);
    assert_eq!(record.run_id, Some(42468614831));
    assert_eq!(record.run_status.as_deref(), Some("completed"));
    assert_eq!(record.run_conclusion.as_deref(), Some("success"));
    assert_eq!(
        record.script_sha256.as_deref(),
        Some("dc164521a6519978e98577bfb7bb80b7ce3597560cbd28ed3b5d9b86d7381f01")
    );
    assert!(record.markers.iter().any(|m| m == "MIRROR_DONE"));
    assert_eq!(record.completed_at.as_deref(), Some("2026-11-08T09:51:08Z"));
    assert!(record.extra.is_empty(), "no field went unrecognized");

    // The full record, reserialized, is value-identical to the
    // Python-written original (key order aside, JSON object equality).
    let reserialized = serde_json::to_value(&record).expect("record serializes");
    assert_eq!(reserialized, original);
}

#[test]
fn fresh_submission_shape_with_explicit_nulls_round_trips() {
    // What gk.py writes at submit time: run fields present as null,
    // lifecycle fields absent entirely.
    let submitted = r#"{
        "id": "20261108T120000Z-demo-abc123",
        "key": "demo",
        "program": "desktop-access",
        "workflow": "run.yml",
        "lane": "quick",
        "cwd": "C:\\Users\\trist\\Development",
        "expected_min": 5.0,
        "state": "submitted",
        "created_at": "2026-11-08T12:00:00Z",
        "run_id": null,
        "run_status": null,
        "run_conclusion": null,
        "adopted": false,
        "verdict_source": null,
        "watermarks": {},
        "flags": [],
        "maintenance": false,
        "notify_chat": null,
        "priority": 0,
        "priority_source": "auto",
        "venue": "auto",
        "script_path": "C:\\jobs\\demo.ps1",
        "script_sha256": "00"
    }"#;
    let original: serde_json::Value = serde_json::from_str(submitted).unwrap();
    let record: QueueRecord = serde_json::from_str(submitted).unwrap();
    assert_eq!(record.state, JobState::Submitted);
    assert_eq!(record.run_id, None);
    assert_eq!(record.dispatched_at, None);
    assert!(record.markers.is_empty());
    // Reserialization adds exactly the four harvest-state fields at
    // their empty defaults. That is the Python shape too: gk.py writes
    // markers/log_tail/log_pending at harvest (possibly as [] / false)
    // and not_in_list_noted in the sweep, so a record that has passed
    // one sweep carries them explicitly; before that, Python reads
    // them via .get defaults. Nothing is renamed, dropped, or altered.
    let mut expected = original;
    let obj = expected.as_object_mut().unwrap();
    obj.insert("markers".to_string(), serde_json::json!([]));
    obj.insert("log_tail".to_string(), serde_json::json!([]));
    obj.insert("log_pending".to_string(), serde_json::json!(false));
    obj.insert("not_in_list_noted".to_string(), serde_json::json!(false));
    let reserialized = serde_json::to_value(&record).unwrap();
    assert_eq!(reserialized, expected);
}

#[test]
fn unknown_fields_are_preserved_in_extra() {
    // A record written by a newer gatekeeper, carrying a field this
    // contract version does not know, must round-trip losslessly.
    let mut value: serde_json::Value = serde_json::from_str(REAL_RECORD).unwrap();
    value
        .as_object_mut()
        .unwrap()
        .insert("future_field".to_string(), serde_json::json!({"x": 1}));
    let record: QueueRecord = serde_json::from_value(value.clone()).unwrap();
    assert_eq!(
        record.extra.get("future_field"),
        Some(&serde_json::json!({"x": 1}))
    );
    let reserialized = serde_json::to_value(&record).unwrap();
    assert_eq!(reserialized, value);
}

#[test]
fn job_record_view_matches_queue_record() {
    let record: QueueRecord = serde_json::from_str(REAL_RECORD).unwrap();
    let job = JobRecord::from(&record);
    assert_eq!(job.id, record.id);
    assert_eq!(job.key, record.key);
    assert_eq!(job.script_path, record.script_path);
    assert_eq!(job.expected_min, record.expected_min);
    assert_eq!(job.created_at, record.created_at);
}
