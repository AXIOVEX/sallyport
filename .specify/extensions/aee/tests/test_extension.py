"""Deterministic validation of the Spec Kit extension manifest and docs.

Codifies the extension contribution rules from github/spec-kit so the
manifest and its documentation cannot silently drift out of compliance.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parents[1]


@pytest.fixture(scope="module")
def manifest() -> dict:
    with (ROOT / "extension.yml").open(encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def test_manifest_schema_version(manifest: dict) -> None:
    assert str(manifest["schema_version"]) == "1.0"


def test_extension_identity(manifest: dict) -> None:
    extension = manifest["extension"]
    assert re.fullmatch(r"[a-z0-9-]+", extension["id"]), (
        "id must be lowercase-hyphenated"
    )
    assert re.fullmatch(r"\d+\.\d+\.\d+", str(extension["version"])), (
        "version must be semver"
    )
    assert str(extension["repository"]).startswith("https://")
    assert extension["license"]


def test_description_is_concise(manifest: dict) -> None:
    description = manifest["extension"]["description"]
    assert 0 < len(description) < 100, (
        f"description is {len(description)} chars, keep under 100"
    )


def test_required_speckit_version(manifest: dict) -> None:
    assert manifest["requires"]["speckit_version"]


def test_tags(manifest: dict) -> None:
    tags = manifest["tags"]
    assert 2 <= len(tags) <= 5
    assert all(re.fullmatch(r"[a-z0-9-]+", tag) for tag in tags)


def test_provided_command_files_exist(manifest: dict) -> None:
    for command in manifest["provides"]["commands"]:
        assert (ROOT / command["file"]).is_file(), (
            f"missing command file: {command['file']}"
        )


def test_provided_template_files_exist_and_parse(manifest: dict) -> None:
    for template in manifest["provides"]["templates"]:
        path = ROOT / template["file"]
        assert path.is_file(), f"missing template file: {template['file']}"
        if path.suffix == ".json":
            json.loads(path.read_text(encoding="utf-8"))


def test_assess_command_documents_claim_vocabulary() -> None:
    text = (ROOT / "commands" / "speckit.aee.assess.md").read_text(encoding="utf-8")
    assert "## Claim field vocabulary" in text
    # Spot-check the easily-misguessed values (underscores, not hyphens or spaces).
    for value in ("partially_supported", "insufficient_evidence", "contradicts"):
        assert value in text, f"vocabulary section missing {value!r}"
