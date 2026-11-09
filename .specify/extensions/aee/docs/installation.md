# Installation by name and dependency verification

In a project initialized with Spec Kit, the installation command is:

```bash
specify extension add aee
```

## Register the publisher catalog once

Specify 1.2.2 still treats the default community catalog as discovery-only.
AEE cannot change that policy before it is installed. Review the publisher's
[catalog.json](../catalog.json) and register it once per project:

```bash
specify extension catalog add https://raw.githubusercontent.com/electrohire/spec-kit-aee/main/catalog.json --name electrohire --install-allowed
```

Then run `specify extension add aee`. The publisher catalog contains only AEE
and Evaluator, with versioned release archive URLs and SHA-256 digests. It does
not grant installation permission to the full community catalog. Project catalog
configuration overrides the defaults. If your organization already maintains a
catalog, include the AEE entry there instead, or add this source to the existing
project configuration after reviewing it. Existing catalog priorities determine
which entry wins. `SPECKIT_CATALOG_URL`, when set, overrides project configuration;
use your organization's catalog instructions in that case.

Python >=3.11 and Specify on PATH are prerequisites. The publisher-catalog route
was checked with Specify 1.2.2. A user or agent must explicitly select the catalog;
installing AEE does not silently change project trust settings.

## What installs when

| Step | Installed component |
| --- | --- |
| `specify extension add aee` | AEE adapter, templates, hooks, and agent commands/skills |
| First AEE operation | Compatible `applied-epistemic-engineering>=1.6.0,<2` and `jsonschema>=4.0` in a private runtime when needed |
| First `assess` or `compose` | Evaluator 1.1.2, downloaded and SHA-256 verified by the runner when absent |

The extension manager's `requires.tools` metadata does not install Python
packages. AEE's runner handles runtime provisioning. An existing compatible
engine in the active Python takes precedence. Set `SPECKIT_AEE_AUTO_INSTALL=0`
to disable automatic provisioning. Network access is needed for initial setup.
Optional RTK, Headroom, token-router, and Ollama integrations are not required or
automatically installed.

## Reproduce the installation smoke

In a development environment containing Specify and PyYAML:

```bash
python scripts/check_install.py --catalog-url https://raw.githubusercontent.com/electrohire/spec-kit-aee/main/catalog.json --expect-aee-version 1.5.2 --clean-interpreter
```

The check creates a disposable project, registers the publisher catalog, installs
AEE by name, runs the adapter on a Python interpreter with no engine or
jsonschema, and checks the provisioned versions, generated commands/skills,
assessment, composition, gates, ledger, review, gaps, and evaluator recovery.
An expected scored failure from the draft example is part of the check.

On 2026-10-19, the published AEE 1.5.2 archive passed the clean-interpreter smoke
on Windows/Python 3.12 with Specify 1.2.2. It provisioned engine 1.5.3,
jsonschema 4.26.0, and Evaluator 1.1.2. A stock-catalog install by name was
separately confirmed to refuse with the community discovery-only message.
The same smoke then passed using `specify extension add aee` after registering
the candidate publisher catalog served over loopback; it downloaded the real
published releases and provisioned the same runtime dependency versions.
These results distinguish the adapter's working dependency provisioning from
the source registration required by Specify.
