#!/usr/bin/env python3
"""Install both real extensions in a disposable Spec Kit project; no inference.

Every verification is an explicit check that raises with a message
(RT3-16): bare ``assert`` statements are stripped under ``python -O``,
which would silently turn this script into a no-op that prints PASS.
The AEE extension under test can be installed from a local directory
(the ``--dev`` flow) or, with ``--from-archive``, from a release zip —
the flow real users follow.
"""
from __future__ import annotations

import argparse
import functools
import http.server
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass


def _serve_archive(archive: Path) -> tuple[http.server.ThreadingHTTPServer, str]:
    """Serve a release zip on an ephemeral localhost port.

    ``specify extension add --from`` accepts URLs only (a bare local
    path is rejected as an invalid URL), so archive mode serves the
    zip over loopback HTTP — the same download path a real catalog
    install exercises, without any network dependency.
    """
    handler = functools.partial(_QuietHandler, directory=str(archive.parent))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/{archive.name}"


class CheckFailed(RuntimeError):
    """A verification in this script failed; the message says which."""


def check(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def _retry_after_clearing_readonly(
    func: Callable[[str], object], path: str, _exc: object
) -> None:
    """rmtree error handler: clear the read-only bit and retry the call.

    The scratch install copies the evaluator extension *with* its
    `.git` directory, whose object files are read-only. Windows
    refuses to unlink a read-only file (POSIX unlink ignores the
    bit), so removal of the real installed tree fails there with
    WinError 5 unless the bit is cleared first. The third argument is
    the exception (3.12+ ``onexc``) or an exc_info tuple (``onerror``);
    it is not needed either way.
    """
    os.chmod(path, stat.S_IWRITE)
    func(path)


def _rmtree(root: Path) -> None:
    """Remove a tree that may contain a copied `.git`, on any OS."""
    if sys.version_info >= (3, 12):
        shutil.rmtree(root, onexc=_retry_after_clearing_readonly)
    else:
        shutil.rmtree(root, onerror=_retry_after_clearing_readonly)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--evaluator-dir",
        type=Path,
        default=None,
        help="install the evaluator extension from this directory first; "
        "without it, the AEE runner installs its pinned release itself",
    )
    parser.add_argument("--aee-dir", type=Path, default=ROOT)
    parser.add_argument(
        "--from-archive",
        type=Path,
        default=None,
        help="install the AEE extension from this release zip instead "
        "of the local --aee-dir directory",
    )
    parser.add_argument(
        "--from-url",
        default=None,
        help="install the AEE extension by passing this archive URL "
        "(e.g. an immutable GitHub commit or tag archive) to specify",
    )
    parser.add_argument(
        "--catalog-url",
        default=None,
        help="register this reviewed publisher catalog, then install with "
        "'specify extension add aee' (no --from)",
    )
    parser.add_argument(
        "--expect-aee-version",
        default=None,
        help="fail unless specify reports exactly this installed AEE "
        "version (for checks of a published release archive)",
    )
    parser.add_argument(
        "--clean-interpreter",
        action="store_true",
        help="run the installed runner on a fresh package-free Python with "
        "no aee on PATH (only specify), so it must provision the engine "
        "and jsonschema from the package index itself",
    )
    args = parser.parse_args()
    evaluator = (
        args.evaluator_dir.resolve(strict=True) if args.evaluator_dir else None
    )
    specify = shutil.which("specify")
    check(
        specify is not None,
        "specify not found: activate the requirements/dev.txt environment",
    )
    archive: Path | None = None
    aee_source: Path | None = None
    server: http.server.ThreadingHTTPServer | None = None
    archive_url: str | None = None
    check(
        sum(bool(value) for value in (args.from_archive, args.from_url, args.catalog_url)) <= 1,
        "--from-archive, --from-url and --catalog-url are mutually exclusive",
    )
    if args.from_url is not None:
        archive_url = args.from_url
    elif args.from_archive is not None:
        archive = args.from_archive.resolve(strict=True)
        server, archive_url = _serve_archive(archive)
    elif args.catalog_url is None:
        aee_source = args.aee_dir.resolve(strict=True)
    env = {**os.environ, "PYTHONUTF8": "1", "NO_COLOR": "1"}
    try:
        _run_checks(specify, evaluator, aee_source, archive_url, env,
                    args.clean_interpreter, args.expect_aee_version, args.catalog_url)
    finally:
        if server is not None:
            server.shutdown()
    origin = (
        args.catalog_url if args.catalog_url
        else args.from_url if args.from_url
        else "release archive" if archive is not None
        else "local directory"
    )
    companion = "local directory" if evaluator is not None else "runner auto-install"
    print(f"PASS: initialized project, both extensions, eleven rendered commands, "
          f"five AEE hooks, resolved placeholders, all seven installed "
          f"operations, and evaluator reinstall by assess (aee installed "
          f"from {origin}; evaluator first installed from {companion}; "
          f"runner on {'a clean interpreter' if args.clean_interpreter else 'this interpreter'})")


def _clean_interpreter(
    tools: Path, specify: str, env: dict[str, str]
) -> tuple[str, dict[str, str]]:
    """A package-free venv and a PATH exposing specify but no aee.

    PATH keeps system directories but drops every directory holding an
    `aee` command (in CI that is the locked environment, which also
    holds python and specify); specify comes back through a shim
    directory containing only it.
    """
    venv = tools / "clean-venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(venv)],
                   check=True)
    bin_dir = venv / ("Scripts" if os.name == "nt" else "bin")
    shim = tools / "specify-only"
    shim.mkdir()
    if os.name == "nt":
        shutil.copy2(specify, shim / Path(specify).name)
    else:
        (shim / "specify").symlink_to(specify)
    kept = [
        entry for entry in env.get("PATH", "").split(os.pathsep)
        if entry and shutil.which("aee", path=entry) is None
    ]
    path = os.pathsep.join([str(bin_dir), str(shim), *kept])
    clean_env = {**env, "PATH": path}
    python = str(bin_dir / ("python.exe" if os.name == "nt" else "python"))
    probe = subprocess.run(
        [python, "-c", ("import importlib.util as u; raise SystemExit("
                        "bool(u.find_spec('aee') or u.find_spec('jsonschema')))")],
        env=clean_env, check=False,
    )
    check(probe.returncode == 0, "the clean interpreter must lack aee and jsonschema")
    check(shutil.which("aee", path=path) is None, "no aee may remain on PATH")
    check(shutil.which("specify", path=path) is not None, "specify must stay on PATH")
    return python, clean_env


def _run_checks(
    specify: str,
    evaluator: Path | None,
    aee_source: Path | None,
    archive_url: str | None,
    env: dict[str, str],
    clean_interpreter: bool = False,
    expect_aee_version: str | None = None,
    catalog_url: str | None = None,
) -> None:
    with tempfile.TemporaryDirectory(prefix="aee-install-") as directory,             tempfile.TemporaryDirectory(prefix="aee-clean-python-") as tools:
        root = Path(directory)
        python = sys.executable
        if clean_interpreter:
            python, env = _clean_interpreter(Path(tools), specify, env)

        def command(*parts: str, code: int = 0) -> str:
            # `specify extension add --from <url>` prompts
            # "Continue with installation? [y/N]:" and hangs forever on
            # an unattended stdin; feed affirmative answers (harmless
            # for the non-interactive commands).
            completed = subprocess.run(
                list(parts), cwd=root, env=env, text=True, encoding="utf-8",
                input="y\n" * 20, capture_output=True, check=False,
            )
            check(
                completed.returncode == code,
                f"command {parts!r} exited {completed.returncode}, "
                f"expected {code}:\n{completed.stdout}{completed.stderr}",
            )
            return completed.stdout

        command(specify, "init", "--here", "--integration", "claude",
                "--ignore-agent-tools", "--non-interactive")
        if evaluator is not None:
            command(specify, "extension", "add", "--dev", str(evaluator))
        if catalog_url is not None:
            command(specify, "extension", "catalog", "add", catalog_url,
                    "--name", "electrohire", "--install-allowed")
            command(specify, "extension", "add", "aee")
        elif archive_url is not None:
            command(specify, "extension", "add", "aee", "--from", archive_url)
        else:
            check(aee_source is not None, "an aee source is required")
            command(specify, "extension", "add", "--dev", str(aee_source))
        listed = command(specify, "extension", "list", "--json")
        installed = {item["id"]: item for item in json.loads(listed)}
        check(
            installed["aee"]["enabled"],
            f"aee must be enabled after install: {listed}",
        )
        if expect_aee_version is not None:
            check(
                installed["aee"]["version"] == expect_aee_version,
                f"expected AEE {expect_aee_version}, specify installed "
                f"{installed['aee']['version']}",
            )
        check(
            installed["aee"]["provides"]["commands"] == 11,
            "aee must provide eleven commands, got "
            f"{installed['aee']['provides']['commands']}",
        )
        check(
            installed["aee"]["provides"]["hooks"] == 5,
            "aee must provide five hooks, got "
            f"{installed['aee']['provides']['hooks']}",
        )
        commands = list((root / ".claude/commands").glob("speckit.aee.*.md"))
        commands += list((root / ".claude/skills").glob("speckit-aee-*/SKILL.md"))
        check(len(commands) == 11, f"expected eleven rendered commands: {commands}")
        for path in commands:
            content = path.read_text(encoding="utf-8")
            if path.name == "SKILL.md":
                frontmatter = yaml.safe_load(content.split("---", 2)[1])
                check(bool(frontmatter.get("name")), f"skill name missing: {path}")
                check(bool(frontmatter.get("description")), f"skill description missing: {path}")
            check(
                not re.search(r"__SPECKIT_COMMAND_[A-Z_]+__", content),
                f"unresolved command placeholder left in {path}",
            )
        hooks = yaml.safe_load((root / ".specify/extensions.yml").read_text())["hooks"]
        check("after_verify" in hooks, f"after_verify hook missing: {hooks}")
        shutil.copyfile(root / ".specify/extensions/aee/templates/aee-claims.json",
                        root / "claims.json")
        claims_path = root / "claims.json"
        claims = json.loads(claims_path.read_text())
        claims["claims"][0]["status"] = "draft"
        claims["claims"][0]["evidence"] = []
        claims_path.write_text(json.dumps(claims))
        result = command(python,
                         str(root / ".specify/extensions/aee/scripts/python/run_aee.py"),
                         "assess", "--input", "claims.json", code=1)
        paths = json.loads(result)
        # In clean installs assess provisions the evaluator. Its skills cannot
        # exist before that first operation has installed the companion.
        evaluator_skills = list((root / ".claude/skills").glob("speckit-evaluator-*/SKILL.md"))
        evaluator_commands = list((root / ".claude/commands").glob("speckit.evaluator.*.md"))
        check(len(evaluator_skills + evaluator_commands) == 4,
              "evaluator must render four discoverable operation skills/commands")
        for path in evaluator_skills:
            frontmatter = yaml.safe_load(path.read_text(encoding="utf-8").split("---", 2)[1])
            check(bool(frontmatter.get("name")) and bool(frontmatter.get("description")),
                  f"invalid evaluator skill metadata: {path}")
        if clean_interpreter:
            # The clean interpreter has no engine and no jsonschema, so the
            # runner must have provisioned its private runtime from the
            # package index.
            runtime = root / ".specify/extensions/aee/.runtime/venv"
            runtime_python = runtime / (
                "Scripts/python.exe" if os.name == "nt" else "bin/python"
            )
            probe = subprocess.run(
                [str(runtime_python), "-c",
                 ("import aee.ledger, jsonschema, importlib.metadata as m; "
                  "print(m.version('applied-epistemic-engineering'), "
                  "m.version('jsonschema'))")],
                capture_output=True, text=True, check=False,
            )
            check(
                probe.returncode == 0,
                f"the runner must provision its runtime: {probe.stderr}",
            )
            engine_version, jsonschema_version = probe.stdout.split()
            print(f"provisioned runtime: applied-epistemic-engineering "
                  f"{engine_version}, jsonschema {jsonschema_version}")
        # Without --evaluator-dir the runner installed its required
        # companion itself during this first assess.
        listed = command(specify, "extension", "list", "--json")
        installed = {item["id"]: item for item in json.loads(listed)}
        check(
            "evaluator" in installed and installed["evaluator"]["enabled"],
            f"both extensions must be enabled after the first assess: {listed}",
        )
        if evaluator is None:
            # The runner installed its pinned evaluator, not another one.
            pinned = re.search(
                r'^EVALUATOR_VERSION = "([^"]+)"',
                (root / ".specify/extensions/aee/scripts/python/run_aee.py")
                .read_text(encoding="utf-8"),
                re.MULTILINE,
            )
            check(
                pinned is not None
                and installed["evaluator"]["version"] == pinned.group(1),
                f"the runner must install its pinned evaluator version: {listed}",
            )
            print(f"evaluator installed by the runner: "
                  f"{installed['evaluator']['version']}")
        check(
            (root / paths["assessment"]).is_file(),
            f"assess named a missing assessment file: {paths}",
        )
        # Spec 006 (LO-05): the installed assess must surface the lane
        # pair end to end — a lane_pair block whose lane-1 outcome is
        # the installed assessment's own recorded lane-1 outcome, and
        # whose one-line display names the headline and both lanes.
        installed_assessment = json.loads(
            (root / paths["assessment"]).read_text(encoding="utf-8")
        )
        recorded_lane_1 = (
            installed_assessment.get("lanes", {}).get("lane_1", {}).get("outcome")
        )
        pair = paths.get("lane_pair")
        check(
            isinstance(pair, dict) and recorded_lane_1 is not None,
            f"assess summary must carry a lane_pair for an assessment "
            f"that records lanes: {paths}",
        )
        check(
            "acceptance_basis" in pair,
            f"lane_pair must carry the acceptance_basis key (the "
            f"recorded acts' basis, or null when none is recorded): {pair}",
        )
        check(
            pair["lane_1_outcome"] == recorded_lane_1
            and pair["headline_outcome"] == installed_assessment.get("outcome")
            and pair["lane_2_state"] in {"open", "complete"}
            and "lane 1:" in pair["display"]
            and "lane 2:" in pair["display"]
            and pair["display"].startswith("headline:"),
            f"lane_pair must match the installed assessment's own "
            f"lanes and name both lanes: {pair} vs assessment lanes "
            f"{installed_assessment.get('lanes')}",
        )
        print(f"lane pair from the installed assess: {pair['display']}")
        check(
            (root / paths["evaluator_result"]).is_file(),
            f"assess named a missing evaluator result: {paths}",
        )
        runner = str(root / ".specify/extensions/aee/scripts/python/run_aee.py")
        # The installed evaluator's real Python composer, run through the
        # runner so its jsonschema dependency is provisioned with AEE's.
        command(python, runner, "compose", "--phase", "plan",
                "--output", "composed.json", code=1)
        check(
            (root / "composed.json").is_file(),
            "compose wrote no composed.json; the evaluator composer failed "
            "(see its stderr above)",
        )
        composed = json.loads((root / "composed.json").read_text(encoding="utf-8"))
        check(
            composed["phase"] == "after_plan" and composed["outcome"] == "iterate",
            f"compose must combine the AEE result: {composed}",
        )
        command(python, runner, "gate", "--input", paths["assessment"], code=1)
        verified = json.loads(command(python, runner, "verify"))
        check(
            verified["valid"] and verified["entries"] == 1,
            f"ledger must verify with exactly one entry: {verified}",
        )
        graph = json.loads(command(python, runner, "graph", "--input", "claims.json"))
        check(
            (root / graph["graph"]).is_file(),
            f"graph named a missing file: {graph}",
        )
        challenge = json.loads(command(python, runner, "challenge",
                                       "--input", "claims.json", code=1))
        check(
            (root / challenge["challenge"]).is_file(),
            f"challenge named a missing file: {challenge}",
        )
        (root / "matrix.md").write_text(
            "| Test ID | Requirements | Layer | Gate |\n"
            "| T-API-001 | Returns 200 | integration | gate-1 |\n")
        (root / "evidence").mkdir()
        command(python, runner, "gaps", "--matrix", "matrix.md",
                "--evidence", "evidence", "--output", "GAPS.md")
        check(
            "**Open:** 1" in (root / "GAPS.md").read_text(),
            "gap register must show the one open gap",
        )
        # Message security (Spec 007): the installed battery gate runs
        # offline against its vendored corpus and must pass its bars —
        # the epistemic layer challenges at least 95% of the planted
        # injections and zero legitimate controls.
        battery = command(python,
                          str(root / ".specify/extensions/aee/scripts/python/"
                              "message_security_battery.py"))
        battery_report = json.loads(battery)
        check(
            battery_report["gate"] == "pass"
            and battery_report["results"]["injection_challenged"] == 55
            and battery_report["results"]["control_challenged"] == 0,
            f"installed message-security battery gate must pass with the "
            f"recorded counts: {battery_report}",
        )
        print("message-security battery gate from the installed extension: "
              "pass (55/56 injections challenged, 0/50 controls challenged)")
        # RT3-01 exit-code contract, exercised against the installed
        # runner: a missing input is a clean refusal (exit 2), never a
        # traceback and never the scored-verdict code 1.
        refused = subprocess.run(
            [python, runner, "assess", "--input", "does-not-exist.json"],
            cwd=root, env=env, text=True, encoding="utf-8",
            capture_output=True, check=False,
        )
        check(
            refused.returncode == 2,
            f"missing input must exit 2, got {refused.returncode}",
        )
        check(
            "Traceback" not in refused.stderr and "refused:" in refused.stderr,
            f"missing input must refuse cleanly: {refused.stderr}",
        )
        # Spec 005 (ER-04): the evaluator extension is a required
        # companion, enforced by the runner itself. Remove it from the
        # installed project and, with automatic installation disabled,
        # the installed runner's assess must refuse — exit 2, naming
        # the missing extension — instead of scoring without the
        # common evaluator.
        evaluator_dir = root / ".specify" / "extensions" / "evaluator"
        assessments_dir = root / ".specify" / "extensions" / "aee" / "assessments"
        before = {path.name for path in assessments_dir.glob("*.json")}
        _rmtree(evaluator_dir)
        refused = subprocess.run(
            [python, runner, "assess", "--input", "claims.json"],
            cwd=root, env={**env, "SPECKIT_AEE_AUTO_INSTALL": "0"}, text=True,
            encoding="utf-8", capture_output=True, check=False,
        )
        check(
            refused.returncode == 2,
            "assess without the evaluator extension must exit 2, got "
            f"{refused.returncode}",
        )
        check(
            "spec-kit-evaluator" in refused.stderr
            and "Traceback" not in refused.stderr,
            "assess without the evaluator extension must refuse cleanly "
            f"naming it: {refused.stderr}",
        )
        check(
            {path.name for path in assessments_dir.glob("*.json")} == before,
            "a refused assess must not write a new assessment",
        )
        # With automatic installation enabled (the default), the same
        # assess reinstalls the pinned evaluator release through Spec
        # Kit and then scores (--no-ledger: the ledger refuses an exact
        # replay of the assessment already recorded above).
        reinstalled = json.loads(command(python, runner, "assess",
                                         "--input", "claims.json", "--no-ledger",
                                         code=1))
        check(
            reinstalled["evaluator_extension_present"]
            and (evaluator_dir / "extension.yml").is_file(),
            f"assess must reinstall the evaluator extension: {reinstalled}",
        )


if __name__ == "__main__":
    try:
        main()
    except CheckFailed as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
