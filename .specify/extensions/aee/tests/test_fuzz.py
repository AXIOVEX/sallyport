"""Property-based (fuzz) tests for the path-safe AEE runner.

These use Hypothesis to exercise the pure, security-relevant helper
``_safe_segment`` in ``scripts/python/run_aee.py`` with large numbers of
generated inputs. The goal is to confirm, for arbitrary input shape, that the
phase-normalization invariant holds: a value is accepted exactly when it is a
non-empty string of lowercase alphanumerics/underscores, and the result is a
well-formed, idempotent ``after_*`` segment.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

RUNNER = Path(__file__).parents[1] / "scripts" / "python" / "run_aee.py"
SPEC = importlib.util.spec_from_file_location("run_aee_fuzz", RUNNER)
assert SPEC and SPEC.loader
run_aee = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(run_aee)

_ALLOWED = "abcdefghijklmnopqrstuvwxyz0123456789_"
# Broad alphabet that includes the separators and traversal characters that
# matter to the phase-normalization guard.
_FUZZY = st.text(
    alphabet=st.sampled_from(_ALLOWED + "-. /\\:\u0000\t\n"),
    min_size=0,
    max_size=40,
)
_VALID = st.text(alphabet=st.sampled_from(_ALLOWED), min_size=1, max_size=40)


@settings(max_examples=300, deadline=None)
@given(value=_FUZZY)
def test_safe_segment_is_normalized_or_rejected(value: str) -> None:
    """_safe_segment either raises or returns a well-formed ``after_*`` name."""
    try:
        result = run_aee._safe_segment(value)
    except ValueError:
        return
    assert result.startswith("after_")
    assert all(c in _ALLOWED for c in result)


@settings(max_examples=300, deadline=None)
@given(value=_FUZZY)
def test_safe_segment_is_idempotent_on_success(value: str) -> None:
    """A value that survives normalization never changes on a second pass."""
    try:
        once = run_aee._safe_segment(value)
    except ValueError:
        return
    assert run_aee._safe_segment(once) == once


@settings(max_examples=300, deadline=None)
@given(value=_VALID)
def test_safe_segment_accepts_exactly_well_formed_values(value: str) -> None:
    """Characterization: well-formed inputs are accepted and normalized once."""
    if value == "after_":
        # A bare "after_" names no lifecycle phase and is rejected
        # (RT3-21); it is the one well-formed-looking exception.
        with pytest.raises(ValueError):
            run_aee._safe_segment(value)
        return
    result = run_aee._safe_segment(value)
    expected = value if value.startswith("after_") else f"after_{value}"
    assert result == expected


@settings(max_examples=300, deadline=None)
@given(value=_FUZZY)
def test_safe_segment_rejects_malformed_inputs(value: str) -> None:
    """Characterization: empty or non-alphanumeric inputs are always rejected."""
    well_formed = bool(value) and all(c in _ALLOWED for c in value)
    if well_formed:
        return
    try:
        run_aee._safe_segment(value)
    except ValueError:
        return
    raise AssertionError(f"expected ValueError for malformed phase {value!r}")
