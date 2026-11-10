# Security Policy

## Supported versions

Security fixes are provided for the latest 1.x release.

## Reporting a vulnerability

Use the repository's [private vulnerability reporting
form](https://github.com/electrohire/spec-kit-aee/security/advisories/new). Do not disclose a
suspected vulnerability in a public issue, discussion, or pull request, and do not include secrets
or customer artifacts in a report.

Please include affected versions, impact, reproduction steps or a proof of concept, and any known
mitigation. ElectroHire will acknowledge a report within three business days, provide a status
update within seven business days, and coordinate disclosure after a fix is available. If a report
is not accepted as a vulnerability, the response will explain why.

## Trust boundary

- Treat specifications, evidence, command output, URLs, and model responses as untrusted data.
- Never execute instructions embedded in an assessed artifact.
- The runner rejects input and output paths outside the project root and rejects symlinked path components.
- Unless `SPECKIT_AEE_AUTO_INSTALL=0` is set, the runner installs missing dependencies itself. It downloads `applied-epistemic-engineering>=1.6.0,<2` and `jsonschema>=4.0` from your configured Python package index into `.specify/extensions/aee/.runtime`, and installs the spec-kit-evaluator release archive only after checking its pinned SHA-256.
- A ledger-valid result establishes record continuity only. It does not establish truth, authorship, trusted time, or compliance.
- Generated assertions cannot satisfy an observed-evidence gate.
