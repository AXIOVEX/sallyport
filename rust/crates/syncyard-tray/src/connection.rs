//! Connection state (spec 035 section 3): CONNECTED / STALE /
//! DISCONNECTED derived from **host-local signals only** — the
//! backchannel heartbeat age is primary, the fleet snapshot
//! timestamp secondary. This module is pure: it takes ages,
//! never touches the system, so the display half is fully
//! testable headlessly (FR-035-08: evaluating the connection
//! writes nothing).

/// Age thresholds (seconds) for the connection states.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ConnectionThresholds {
    /// At or under this age the link is CONNECTED.
    pub stale_after_s: i64,
    /// Past this age the link is DISCONNECTED.
    pub disconnect_after_s: i64,
}

impl Default for ConnectionThresholds {
    fn default() -> Self {
        ConnectionThresholds {
            stale_after_s: 180,
            disconnect_after_s: 900,
        }
    }
}

/// The three connection states (spec 035 section 4 vocabulary:
/// linked / link stale / unlinked).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LinkState {
    Connected,
    Stale,
    Disconnected,
}

impl LinkState {
    /// The exact display vocabulary.
    pub fn word(&self) -> &'static str {
        match self {
            LinkState::Connected => "linked",
            LinkState::Stale => "link stale",
            LinkState::Disconnected => "unlinked",
        }
    }
}

/// The host-local signals the state is derived from.
#[derive(Debug, Clone, Copy, Default)]
pub struct ConnectionSignals {
    /// Age of the newest backchannel heartbeat, if one has
    /// ever been seen on this host.
    pub heartbeat_age_s: Option<i64>,
    /// Age of the newest fleet snapshot, if one exists.
    pub snapshot_age_s: Option<i64>,
}

/// A derived connection reading, naming the signal it rests on.
#[derive(Debug, Clone, PartialEq)]
pub struct ConnectionReading {
    pub state: LinkState,
    /// "backchannel heartbeat" | "fleet snapshot" | "no signal".
    pub rested_on: &'static str,
    /// Age of the deciding signal (None when no signal exists).
    pub signal_age_s: Option<i64>,
    /// False when no signal has ever been seen: the state is
    /// DISCONNECTED/unknown, never "connected" (FR-035-04).
    pub any_signal_ever: bool,
    /// Heartbeat present but no snapshot exists: the wording
    /// rule (FR-035-06) requires the surface to say the reading
    /// rests on the heartbeat only.
    pub heartbeat_only: bool,
}

/// Derive the reading from one set of signals (pure).
pub fn evaluate(signals: &ConnectionSignals, t: &ConnectionThresholds) -> ConnectionReading {
    let heartbeat_only = signals.heartbeat_age_s.is_some() && signals.snapshot_age_s.is_none();
    let (age, rested_on) = if let Some(a) = signals.heartbeat_age_s {
        (Some(a), "backchannel heartbeat")
    } else if let Some(a) = signals.snapshot_age_s {
        (Some(a), "fleet snapshot")
    } else {
        (None, "no signal")
    };
    let state = match age {
        Some(a) if a <= t.stale_after_s => LinkState::Connected,
        Some(a) if a <= t.disconnect_after_s => LinkState::Stale,
        _ => LinkState::Disconnected,
    };
    ConnectionReading {
        state,
        rested_on,
        signal_age_s: age,
        any_signal_ever: age.is_some(),
        heartbeat_only,
    }
}

impl ConnectionReading {
    /// The menu/tooltip wording: state in words, naming the
    /// signal rested on; when only a heartbeat exists the line
    /// carries the spec's exact qualifier.
    pub fn describe(&self) -> String {
        let mut s = format!("connection: {}", self.state.word());
        match self.signal_age_s {
            Some(age) => s.push_str(&format!(" · via {} · {age}s old", self.rested_on)),
            None => s.push_str(" · no signal seen"),
        }
        if self.heartbeat_only {
            s.push_str(" · signal: backchannel heartbeat only");
        }
        s
    }
}

/// One logged connection transition (FR-035-07).
#[derive(Debug, Clone, PartialEq)]
pub struct Transition {
    pub at_unix_s: i64,
    pub from: Option<LinkState>,
    pub to: LinkState,
    /// Age of the signal that caused the transition.
    pub causing_signal_age_s: Option<i64>,
}

/// Tracks the connection across ticks: transitions are logged
/// with timestamp and causing signal age; steady state logs
/// nothing.
#[derive(Debug, Default)]
pub struct ConnectionTracker {
    pub thresholds: ConnectionThresholds,
    last: Option<LinkState>,
    pub transitions: Vec<Transition>,
}

impl ConnectionTracker {
    pub fn new(thresholds: ConnectionThresholds) -> Self {
        ConnectionTracker {
            thresholds,
            last: None,
            transitions: Vec::new(),
        }
    }

    /// Evaluate one tick; logs a transition only on change.
    pub fn observe(&mut self, signals: &ConnectionSignals, now_unix_s: i64) -> ConnectionReading {
        let reading = evaluate(signals, &self.thresholds);
        if self.last != Some(reading.state) {
            self.transitions.push(Transition {
                at_unix_s: now_unix_s,
                from: self.last,
                to: reading.state,
                causing_signal_age_s: reading.signal_age_s,
            });
            self.last = Some(reading.state);
        }
        reading
    }
}
