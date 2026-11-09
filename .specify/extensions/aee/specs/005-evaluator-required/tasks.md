# Tasks — Spec 005: Evaluator Extension Required

- [x] T01 Claims bundle drafted (ER-005-01..04) and assessed BEFORE
      implementation with the real engine (aee 1.5.0): `assessment.json`.
- [x] T02 `extension.yml`: `spec-kit-evaluator` → `required: true` with
      the rewritten comment; extension `version:` → 1.4.0.
- [x] T03 Runner: `assess` hard pre-flight on evaluator-extension
      presence (exit 2, names the extension + install command, no
      outputs written); module docstring records the requirement.
- [x] T04 Docs: assess command doc, README (requirements table,
      paragraph, install block, archive tags), and `docs/usage.md`
      teach the pair as one system; no optional framing remains.
- [x] T05 `check_install.py`: add the end-to-end absence exercise
      (remove evaluator dir → installed runner's `assess` exits 2
      naming the extension) alongside the both-enabled assertion.
- [x] T06 Tests: fixtures model an installed evaluator extension;
      absence-refusal test (exit 2, message, no outputs); manifest
      required-set assertions updated; `extension_version` stamp test
      → "1.4.0". Full suite green in the repo-pinned layout.
- [x] T07 CHANGELOG: `## 1.4.0 - 2026-10-12` section recording the
      correction and why.
- [x] T08 Claims re-assessed AFTER implementation with shipped-test
      evidence: `claims-after-implement.json` +
      `assessment-after-implement.json`; spec Status updated.
