"""Reproducible path-input fuzzing of the adapter's output boundary."""

import importlib.util
import random
from pathlib import Path

RUNNER = Path(__file__).parents[1] / "scripts/python/run_aee.py"
SPEC = importlib.util.spec_from_file_location("fuzz_runner", RUNNER)
assert SPEC is not None and SPEC.loader is not None
RUN_AEE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUN_AEE)


def test_generated_paths_cannot_escape_project(tmp_path: Path) -> None:
    root = (tmp_path / "project").resolve()
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("unchanged", encoding="utf-8")
    rng = random.Random(20261016)
    candidates = [Path("claims.json"), Path("../outside.txt"), outside]
    for _ in range(300):
        parts = rng.choices(["claims", "nested", ".", ".."], k=rng.randrange(1, 7))
        candidates.append(Path(*parts) / "output.json")
    accepted = rejected = 0
    for candidate in candidates:
        try:
            output = RUN_AEE._safe_output(root, candidate)
        except (ValueError, OSError):
            rejected += 1
            continue
        assert output.resolve().is_relative_to(root)
        accepted += 1
    assert accepted and rejected
    assert outside.read_text(encoding="utf-8") == "unchanged"
