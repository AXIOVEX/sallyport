//! Wall-clock helpers.
//!
//! Spec 038 section 9: every record carries a monotonic per-source
//! sequence beside wall-clock time; ordering uses the sequence and
//! wall clock is display only. These helpers therefore only need to
//! produce and parse the gatekeeper's ISO-8601 UTC format
//! (`2026-11-08T10:14:45Z`), matching `gk.py`'s `now_iso()`.

use std::time::{SystemTime, UNIX_EPOCH};

/// Current time as an ISO-8601 UTC string, seconds precision.
pub fn now_iso() -> String {
    let secs = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    iso_from_unix(secs)
}

/// Current Unix time in seconds.
pub fn now_unix() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

/// Format Unix seconds as `YYYY-MM-DDTHH:MM:SSZ` (UTC).
pub fn iso_from_unix(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let rem = secs % 86_400;
    let (y, m, d) = civil_from_days(days);
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        y,
        m,
        d,
        rem / 3600,
        (rem % 3600) / 60,
        rem % 60
    )
}

/// Howard Hinnant's civil-from-days algorithm (public domain math).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = if z >= 0 { z } else { z - 146_096 } / 146_097;
    let doe = (z - era * 146_097) as u64; // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365; // [0, 399]
    let y = yoe as i64 + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32; // [1, 12]
    (if m <= 2 { y + 1 } else { y }, m, d)
}

/// Days-from-civil (inverse of [`civil_from_days`]).
fn days_from_civil(y: i64, m: u32, d: u32) -> i64 {
    let y = if m <= 2 { y - 1 } else { y };
    let era = if y >= 0 { y } else { y - 399 } / 400;
    let yoe = (y - era * 400) as u64; // [0, 399]
    let mp = if m > 2 { m - 3 } else { m + 9 } as u64; // [0, 11]
    let doy = (153 * mp + 2) / 5 + (d as u64) - 1; // [0, 365]
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy; // [0, 146096]
    era * 146_097 + doe as i64 - 719_468
}

/// Parse the gatekeeper's ISO-8601 UTC forms into Unix seconds.
/// Accepts a trailing `Z` and an optional fractional-second part.
/// Returns `None` on anything else (callers treat unparseable times
/// as unknown, never as zero — spec 038 FR-038-15's spirit).
pub fn unix_from_iso(s: &str) -> Option<i64> {
    let s = s.trim().strip_suffix('Z')?;
    let (date, time) = s.split_once('T')?;
    let mut dp = date.split('-');
    let y: i64 = dp.next()?.parse().ok()?;
    let mo: u32 = dp.next()?.parse().ok()?;
    let d: u32 = dp.next()?.parse().ok()?;
    let time = time.split('.').next()?;
    let mut tp = time.split(':');
    let h: i64 = tp.next()?.parse().ok()?;
    let mi: i64 = tp.next()?.parse().ok()?;
    let sec: i64 = tp.next()?.parse().ok()?;
    if !(1..=12).contains(&mo) || !(1..=31).contains(&d) || h > 23 || mi > 59 || sec > 60 {
        return None;
    }
    Some(days_from_civil(y, mo, d) * 86_400 + h * 3600 + mi * 60 + sec)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn iso_round_trip_known_instants() {
        // 2026-11-08T00:00:00Z is unix 1794096000.
        assert_eq!(iso_from_unix(1_794_096_000), "2026-11-08T00:00:00Z");
        assert_eq!(unix_from_iso("2026-11-08T00:00:00Z"), Some(1_794_096_000));
        assert_eq!(unix_from_iso("1970-01-01T00:00:00Z"), Some(0));
        assert_eq!(iso_from_unix(0), "1970-01-01T00:00:00Z");
        // Fractional seconds are accepted and truncated.
        assert_eq!(
            unix_from_iso("2026-11-08T10:14:45.123Z"),
            unix_from_iso("2026-11-08T10:14:45Z")
        );
        // Garbage is unknown, not zero.
        assert_eq!(unix_from_iso("not a time"), None);
        assert_eq!(unix_from_iso("2026-13-01T00:00:00Z"), None);
    }
}
