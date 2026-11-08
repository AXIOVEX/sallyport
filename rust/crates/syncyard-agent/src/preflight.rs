//! Job-start preflight (spec 038 SEC-04): before a payload
//! runs, its script is scanned for credential-shaped
//! content (the contract scanner). A hit refuses the job
//! before start; the refusal names the SHAPE and the LINE,
//! never the secret text itself — the refusal is safe to
//! ledger, log, and show.

use std::path::Path;

use syncyard_contract::scan::{scan_for_credentials, SecretHit};

#[derive(Debug, Clone, PartialEq)]
pub struct PreflightRefusal {
    pub hits: Vec<SecretHit>,
}

impl std::fmt::Display for PreflightRefusal {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let shapes: Vec<String> = self
            .hits
            .iter()
            .map(|h| format!("{} at line {}", h.kind, h.line))
            .collect();
        write!(
            f,
            "preflight refused: credential-shaped content ({})",
            shapes.join(", ")
        )
    }
}
impl std::error::Error for PreflightRefusal {}

/// Scan a payload script's text. Pure over the text so the
/// runner and tests share one path.
pub fn scan_script_text(text: &str) -> Result<(), PreflightRefusal> {
    let hits = scan_for_credentials(text);
    if hits.is_empty() {
        Ok(())
    } else {
        Err(PreflightRefusal { hits })
    }
}

/// Scan the payload script at `path` before the job starts.
pub fn scan_payload_script(path: &Path) -> Result<(), String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("preflight cannot read {}: {e}", path.display()))?;
    scan_script_text(&text).map_err(|r| r.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    // T038-21 preflight-secret-absence-scan: a planted
    // credential-shaped line refuses the job before start,
    // the refusal names shape + line and NEVER the secret;
    // a clean script passes.
    #[test]
    fn t038_21_preflight_secret_absence_scan() {
        let planted = "aws_secret_access_key = wJalrXUtnFEMIK7MDENGbPxRfiCYzEXAMPLEKEY";
        let script = format!("#!/bin/bash\necho starting\n{planted}\necho done\n");
        let err = scan_script_text(&script).unwrap_err();
        assert_eq!(err.hits.len(), 1);
        assert_eq!(err.hits[0].line, 3);
        let shown = err.to_string();
        assert!(shown.contains("line 3"), "{shown}");
        assert!(
            !shown.contains("wJalrXUtnFEMI"),
            "the refusal must never echo the secret: {shown}"
        );
        // File path variant: same behavior from disk.
        let dir = std::env::temp_dir().join(format!("sy-p7-preflight-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let p = dir.join("payload.sh");
        std::fs::write(&p, &script).unwrap();
        let err2 = scan_payload_script(&p).unwrap_err();
        assert!(err2.contains("credential-shaped"), "{err2}");
        assert!(!err2.contains("wJalrXUtnFEMI"));
        // A clean script passes.
        std::fs::write(&p, "#!/bin/bash\necho hello\nexit 0\n").unwrap();
        assert!(scan_payload_script(&p).is_ok());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
