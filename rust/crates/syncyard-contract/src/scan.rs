//! Credential-shaped content scanning (spec 038 REP-06;
//! spec 033 secret-absence discipline, red-team M1).
//!
//! One scanner, two callers: the agent's job-start
//! secret-absence assertion (a planted leftover secret must
//! block the next job) and reporting's bundle scan (a bundle
//! containing credential-shaped content is refused, never
//! filed). A hit names the SHAPE and the line — never the
//! secret text itself, which must not travel into reports,
//! logs, or ledger entries about the hit.

/// One scanner hit: the shape matched and where. Deliberately
/// carries no matched text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SecretHit {
    pub kind: &'static str,
    pub line: usize,
}

fn is_long_token_char(b: u8) -> bool {
    b.is_ascii_alphanumeric() || matches!(b, b'_' | b'-' | b'.' | b'/' | b'+' | b'=')
}

/// Count the run of token characters starting at `at`.
fn token_run(bytes: &[u8], at: usize) -> usize {
    let mut n = 0;
    while at + n < bytes.len() && is_long_token_char(bytes[at + n]) {
        n += 1;
    }
    n
}

const PREFIX_SHAPES: &[(&str, &str)] = &[
    ("pypi-", "pypi_api_token"),
    ("ghp_", "github_pat_classic"),
    ("gho_", "github_oauth_token"),
    ("ghu_", "github_user_token"),
    ("github_pat_", "github_pat_fine_grained"),
    ("xoxb-", "slack_token"),
    ("xoxp-", "slack_token"),
    ("sk-proj-", "openai_project_key"),
    ("AKIA", "aws_access_key_id"),
    ("-----BEGIN", "private_key_block"),
];

/// Scan text for credential-shaped content. Line numbers are
/// 1-based. Shapes: known token prefixes followed by a
/// plausible token body, private-key block headers, and
/// `name = value` assignments whose name is credential-shaped
/// and whose value is long and token-like.
pub fn scan_for_credentials(text: &str) -> Vec<SecretHit> {
    let mut hits = Vec::new();
    for (idx, line) in text.lines().enumerate() {
        let lineno = idx + 1;
        let bytes = line.as_bytes();
        for (prefix, kind) in PREFIX_SHAPES {
            if let Some(pos) = line.find(prefix) {
                let after = pos + prefix.len();
                let plausible = if *prefix == "-----BEGIN" {
                    line.contains("PRIVATE KEY-----")
                } else if *prefix == "AKIA" {
                    token_run(bytes, after) >= 12
                        && bytes[pos..after + 12]
                            .iter()
                            .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit())
                } else {
                    token_run(bytes, after) >= 16
                };
                if plausible {
                    hits.push(SecretHit { kind, line: lineno });
                    break;
                }
            }
        }
        if hits.last().map(|h| h.line) == Some(lineno) {
            continue;
        }
        // name = value assignments with credential-shaped names.
        if let Some(eq) = line.find(['=', ':']) {
            let name = line[..eq].trim().trim_matches('"').to_lowercase();
            let name_hit = ["api_key", "apikey", "secret", "password", "passwd", "token"]
                .iter()
                .any(|w| name.ends_with(w) || name.contains(w));
            if name_hit {
                let value = line[eq + 1..]
                    .trim()
                    .trim_matches(|c| c == '"' || c == '\'' || c == ',');
                if value.len() >= 16 && value.bytes().all(is_long_token_char) {
                    hits.push(SecretHit {
                        kind: "credential_assignment",
                        line: lineno,
                    });
                }
            }
        }
    }
    hits
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scanner_names_shapes_never_text() {
        let text = "harmless line\ntoken = ghp_0123456789abcdefghij\nanother ok line\n";
        let hits = scan_for_credentials(text);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].line, 2);
        // The hit carries no secret material.
        let rendered = format!("{:?}", hits[0]);
        assert!(
            !rendered.contains("ghp_0123"),
            "hit must not carry the secret"
        );
    }

    #[test]
    fn scanner_shapes_and_clean_text() {
        assert!(scan_for_credentials("just build output\nall good\n").is_empty());
        assert_eq!(
            scan_for_credentials("-----BEGIN OPENSSH PRIVATE KEY-----\n")[0].kind,
            "private_key_block"
        );
        assert_eq!(
            scan_for_credentials("aws = AKIAIOSFODNN7EXAMPLE\n")[0].kind,
            "aws_access_key_id"
        );
        assert_eq!(
            scan_for_credentials("api_key = abcdefghijklmnopqrstuvwxyz\n")[0].kind,
            "credential_assignment"
        );
        // Short values and prose are not credential-shaped.
        assert!(scan_for_credentials("token = short\n").is_empty());
        assert!(scan_for_credentials("the token count is large today\n").is_empty());
    }
}
