# Retired desktop-label transport workflows

The YAML files in this directory are the retired desktop-label
generation of the Syncyard transport workflows. They are **not
live workflows**: they are kept outside `.github/workflows/` so
GitHub Actions cannot dispatch them, and they must not be copied
back there or treated as the current transport contract.

This generation bound the fixed desktop runner labels
(`[self-hosted, Windows, X64, desktop, gpu]`) and predates the
lane-addressed contract: it has no `lane` input, no `hold_file`, and no running-job registry. The live
transport copies are under `runner/workflows/`; the production
dispatch door is the separate `electrohire/desktop-runner` repo.

These files are retained only as history for reading old runs
and contract changes.
