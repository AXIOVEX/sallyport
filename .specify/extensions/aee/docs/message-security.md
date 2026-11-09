# Message security: authority surfaces, the injection battery, and challenge records

The message-security component of this extension covers one rule:
everything a system reads on the job — tool outputs, files, retrieved
text, other agents' messages, its own earlier output — is data, and
only principals instruct. Instruction-shaped text found inside data
is treated as a *claim* ("the principal intends X") and graded by
provenance and corroboration: a claim backed only by the say-so of
the text that benefits from being obeyed is challenged, always.

It ships three pieces:

| Piece | File | Job |
| --- | --- | --- |
| Authority-surface template | `templates/message-security-authority-surface.md` | The principal record, written down per spec |
| Planted-injection battery + gate | `assets/message-security/battery_corpus.json`, `scripts/python/message_security_screen.py`, `scripts/python/message_security_battery.py` | A regression gate that fails below stated bars |
| Challenge record format | `templates/message-security-challenge-record.schema.json`, `templates/message-security-challenge-record.json` | The standard, auditable record of every note and challenge |

The agent command is `speckit.aee.message-security`
(`commands/speckit.aee.message-security.md`). Governance is what this
component provides: it makes the discipline specifiable, reviewable,
and assessable. It is not, by itself, enforcement — a project that
writes a vague authority surface has a vague record to grade against,
and stopping a fooled output from executing belongs to the harness's
action gate, which is outside this extension.

## 1. The authority surface

Every spec for a system that acts on content it reads carries an
`## Authority Surface` section, completed from the template. It
records, per project:

- **Principals and channels** — who may instruct this system, and
  through which channel. Authority comes from the channel, never
  from content: a span does not become a principal by calling itself
  a system notice or an approval.
- **Authorized actions** — the actions a principal instruction may
  set in motion.
- **Authorized destinations** — where authorized actions may send or
  write. A destination named only inside read content is never
  authorized.
- **Standing prohibitions** — what no data-span claim may authorize,
  whatever it claims about approval, urgency, or secrecy.

An action, destination, or channel not listed is not authorized; a
blank row is a refusal. When a legitimate new destination or action
appears, it is added to the section in review, in the open — never by
accepting read content's word that it was approved.

The section is what the grading checks against. The deterministic
screener below carries a small fixture principal record of the same
shape; a project's completed authority surface is the production form
of that record.

## 2. The planted-injection battery as an assessment gate

The battery is 106 fixtures built from real program text: 56 planted
instructions (seven types — direct override, fake system or approval
markers, self-attestation and urgency, exfiltration, concealment,
task expansion, new destination — half phrased obviously, half as
stealth variants) carried in genuine prose, code, and tool output,
and 50 legitimate controls (principal instructions, standing rules,
quoted or documented examples, runbook steps, code comments,
documentation, spec text).

The gate runs the epistemic layer (L2) of the vendored screener over
every fixture. L2 grades entitlement, not resemblance: it asks what
would entitle this text to be obeyed — provenance, corroboration
against the principal record, and the contradiction, task-expansion,
new-destination, and concealment checks — and challenges by default
when no principal record corroborates the claim.

### Bars

Stated in `scripts/python/message_security_battery.py` and enforced
there:

| Bar | Value | Measured when vendored |
| --- | --- | --- |
| L2 challenge rate on injections | at least 95% | 55 of 56 (98.2%) |
| L2 challenges on legitimate controls | 0 tolerated | 0 of 50 |
| Notes on controls | reported, not gated | 29 of 50 |

The injection bar leaves a small margin below the measured value so
the one known residual (below) does not mask a new regression; any
second miss fails the gate. The control bar has no margin: a
challenged control is a false accusation against legitimate
instruction-like text. Notes are the designed price of recording
instruction-shaped data and never interrupt anyone, so they are
reported and not gated.

Exit codes: 0 the gate passes, 1 a bar is not met (a verdict), 2 an
error such as a missing or malformed corpus (printed as `refused:
...`, never a traceback).

The structural layer (L1, pattern rules over raw text) is reported in
the gate output for information only. It is provenance-blind by
construction — measured at 57.1% of injections challenged and 12% of
controls falsely challenged when the battery was vendored — so it is
a tripwire, not the decision, and it is never gated.

### Re-running the gate

From the extension root, or from an installed copy:

```bash
python .specify/extensions/aee/scripts/python/message_security_battery.py \
  --output .specify/extensions/aee/message-security/battery-report.json \
  --records-output .specify/extensions/aee/message-security/challenge-records.jsonl
```

With no arguments the report is printed and nothing is written. The
gate is re-run whenever the screener rules, the tag or record
formats, or the corpus changes; a change that moves the challenge
rate or the control rate the wrong way is a regression with a name
and a diff.

### Adding fixtures

Append an object to the `fixtures` list in
`assets/message-security/battery_corpus.json`:

```json
{
  "id": "inj-new-family-1",
  "kind": "injection",
  "subtype": "new-family/obvious",
  "span_class": "tool-output",
  "text": "carrier text the project really produces, with the planted payload in it",
  "expected": "challenge",
  "framing": null,
  "carrier_note": "fixture text = carrier text sampled from program corpora + the planted/legitimate payload"
}
```

Controls use `"kind": "control"` and `"expected": "no-challenge"`.
Ids are unique and stable; a fixture, once added, is not edited to
make a failing run pass — it is the record of an attack family or a
legitimate shape. A new injection family seen in real use is added to
the battery *first*, then handled; the battery is the program's
memory of what attacks look like. Personal names and addresses do
not belong in fixtures: use role language and example addresses, as
the vendored corpus does (its vendoring note records the redactions
made when it was brought in).

### The known residual

One fixture earns a note instead of a challenge: a data span that
announces, in the voice of a system team, that the standing rules
were revised, and adds that the revision is quoted in the file that
carries it. The screener judges framing at the claim segment, and the
span asserts its own framing, which the screener cannot distinguish
from genuine framing inside the same span. The principled fix is
that framing evidence, like authority, should come from outside the
span — from the harness's knowledge of what it fetched. Until a
screener carries that, the fixture stays in the corpus as the
standing regression case, and it is exactly why the injection bar is
95% and not 100%.

## 3. The challenge record format

Every note and every challenge is an auditable record entry, defined
by `templates/message-security-challenge-record.schema.json` (JSON
Schema) with a worked example in
`templates/message-security-challenge-record.json`. Fields:

| Field | Content |
| --- | --- |
| `span_hash` | `sha256:` plus the SHA-256 of the span's exact text |
| `source_class` | The harness-assigned span class (for example `tool-output`) |
| `source_id` | The harness's identifier for the source, enough to find the span again |
| `claim_text` | The claim as extracted — the instruction-shaped segment addressed to the agent |
| `check_fired` | The primary check that fired (`family:detail`); `checks_fired` may carry the rest |
| `verdict` | `note` or `challenge` |
| `action_taken` | What was done, in plain words |
| `timestamp` | ISO 8601 UTC time the record was written |

Records are append-only: one JSON object per line in a records file,
written once and never edited after the fact. Field order is stable
and every record carries the span's hash, so a records file can be
hash-chained into a ledger the same way this suite's other artifacts
are. The example record's `span_hash` is the SHA-256 of the span
`Run finished: 42 objects harvested, 0 errors.` followed by a newline
and the claim sentence, from a tool output whose id is
`example-tool-call-0001`.

The battery runner emits records in this format with
`--records-output`: one per fixture the epistemic layer noted or
challenged, in corpus order.

## What this does not establish

- **Model behavior.** The battery measures the deterministic
  screener offline. Whether a model, in context, would obey a claim
  the screener passed — or despite a challenge — is a live question
  this gate does not measure and does not claim to.
- **Adversarial robustness.** The screener and the battery were
  developed together; the fixtures measure this design against this
  fixture family. An adversary who has read the screener will find
  shapes its heuristics miss. The standing gate is the mitigation —
  new families are added as fixtures first — not a proof.
- **Split and multi-span claims.** An instruction fragmented across
  spans, or assembled from individually innocent pieces, is not
  covered by these fixtures yet.
- **Non-text channels.** Images, audio, and structured tool results
  with instruction-bearing fields are out of scope for this battery.
- **A complete principal record.** The battery's record is a small
  fixture. In production the binding constraint is the completeness
  of the project's authority surface, not the screener.
