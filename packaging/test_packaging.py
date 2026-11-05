#!/usr/bin/env python3
"""Standalone-distribution battery (spec 017).

Proves, from a clean copy of the tree (never in the checkout):

  1. pyproject.toml declares the one distribution `syncyard`:
     version dynamic from the VERSION file, the console
     script `syncyard` pointing at the gatekeeper's own
     main(), zero runtime dependencies, Apache-2.0.
  2. `python -m build` produces an sdist and a wheel (the
     wheel is built from the sdist, so sdist completeness is
     proven, not assumed) whose version equals VERSION.
  3. The wheel carries syncyard/gk.py, syncyard/config.py
     and syncyard/backchannel/bc.py BYTE-IDENTICAL to the
     checkout files (spec 017 FR-003: the packaged code is
     the checkout code).
  4. The wheel installs into a fresh venv with no network
     and no dependencies, and the installed command surface
     works: `syncyard --help` and `syncyard status` exit 0,
     the config defaults load, and the installed version
     equals VERSION.
  5. docs/changelog.md is byte-identical to CHANGELOG.md
     (the docs site can never carry a different history).
  6. The release workflow keeps its structural invariants
     (spec 017 FR-004): v* tag trigger plus a manual dry
     run, twine check, OIDC trusted publishing scoped by
     id-token: write, and no stored-credential fields.

Requires: the `build` package importable by the interpreter
running this battery (CI installs it), and venv support.

Run: python3 packaging/test_packaging.py   (exit 0 = all pass)
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

fails = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'} {name}"
          + (f" - {detail}" if detail and not ok else ""))
    if not ok:
        fails.append(name)


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main():
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()

    # --- 1. pyproject invariants -------------------------------------------
    try:
        import tomllib
        with open(ROOT / "pyproject.toml", "rb") as fh:
            pp = tomllib.load(fh)
    except Exception as e:  # noqa: BLE001 - a battery must name its failure
        check("pyproject.toml parses", False, str(e))
        pp = None
    if pp is not None:
        proj = pp.get("project", {})
        check("distribution name is syncyard",
              proj.get("name") == "syncyard")
        check("version is dynamic",
              "version" in proj.get("dynamic", []))
        dyn = (pp.get("tool", {}).get("setuptools", {})
               .get("dynamic", {}).get("version", {}))
        dyn_file = dyn.get("file")
        if isinstance(dyn_file, list):
            dyn_file = dyn_file[0] if dyn_file else None
        check("dynamic version reads the VERSION file",
              dyn_file == "VERSION", f"got {dyn_file!r}")
        check("console script is the gatekeeper's own main()",
              proj.get("scripts", {}).get("syncyard") == "syncyard.gk:main",
              f"got {proj.get('scripts', {})!r}")
        check("zero runtime dependencies",
              proj.get("dependencies", []) == [],
              f"got {proj.get('dependencies')!r}")
        lic = proj.get("license", {})
        lic_text = lic.get("text", lic) if isinstance(lic, dict) else lic
        check("license is Apache-2.0", lic_text == "Apache-2.0",
              f"got {lic_text!r}")
        pkg_dir = (pp.get("tool", {}).get("setuptools", {})
                   .get("package-dir", {}))
        check("package-dir maps the checkout directories",
              pkg_dir.get("syncyard") == "gatekeeper"
              and pkg_dir.get("syncyard.backchannel") == "backchannel",
              f"got {pkg_dir!r}")

    # --- 5. changelog copy ---------------------------------------------------
    check("docs/changelog.md is byte-identical to CHANGELOG.md",
          (ROOT / "docs" / "changelog.md").read_bytes()
          == (ROOT / "CHANGELOG.md").read_bytes())

    # --- 6. release workflow invariants --------------------------------------
    rel = (ROOT / ".github" / "workflows" / "release.yml") \
        .read_text(encoding="utf-8")
    check("release workflow triggers on v* tags",
          'tags: ["v*"]' in rel)
    check("release workflow has a manual dry-run trigger",
          "workflow_dispatch:" in rel)
    check("release workflow builds and twine-checks",
          "python3 -m build" in rel and "twine check" in rel)
    check("release workflow publishes via trusted publishing",
          "gh-action-pypi-publish" in rel and "id-token: write" in rel)
    check("release workflow stores no PyPI credential",
          "password:" not in rel and "PYPI_TOKEN" not in rel
          and "secrets." not in rel)

    # --- 2-4. build, identity, clean install ---------------------------------
    if importlib.util.find_spec("build") is None:
        check("build package available (pip install build)", False,
              "the `build` package is not importable by this interpreter")
    else:
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            src = td / "src"
            src.mkdir()
            for name in ("pyproject.toml", "MANIFEST.in", "VERSION",
                         "LICENSE", "README.md", "CHANGELOG.md"):
                shutil.copy2(ROOT / name, src / name)
            for name in ("gatekeeper", "backchannel"):
                shutil.copytree(
                    ROOT / name, src / name,
                    ignore=shutil.ignore_patterns(
                        "__pycache__", "queue", "ledger.jsonl",
                        ".sweep.lock", "test-tmp"))
            dist = td / "dist"
            r = run([sys.executable, "-m", "build",
                     "--outdir", str(dist), str(src)],
                    cwd=td)
            check("python -m build succeeds", r.returncode == 0,
                  (r.stdout + r.stderr).strip()[-400:])
            wheels = list(dist.glob("*.whl"))
            sdists = list(dist.glob("*.tar.gz"))
            check("sdist and wheel produced with the VERSION version",
                  len(wheels) == 1 and len(sdists) == 1
                  and wheels[0].name.startswith(f"syncyard-{version}-")
                  and sdists[0].name == f"syncyard-{version}.tar.gz",
                  f"got {[p.name for p in dist.iterdir()]}")
            if wheels and sdists:
                _check_sdist(sdists[0], version)
                _check_wheel_and_install(wheels[0], version, td)

    print()
    if fails:
        print(f"TEST_PACKAGING: FAIL ({len(fails)} failing)")
        return 1
    print("TEST_PACKAGING: ALL PASS")
    return 0


def _check_sdist(sdist, version):
    with tarfile.open(sdist) as tf:
        names = tf.getnames()
    prefix = f"syncyard-{version}/"
    check("sdist carries VERSION (dynamic version source)",
          prefix + "VERSION" in names)
    check("sdist carries the package sources at their real paths",
          prefix + "gatekeeper/gk.py" in names
          and prefix + "backchannel/bc.py" in names)


def _check_wheel_and_install(wheel, version, td):
    with zipfile.ZipFile(wheel) as zf:
        names = zf.namelist()
        meta_name = [n for n in names if n.endswith(".dist-info/METADATA")]
        meta = zf.read(meta_name[0]).decode("utf-8") if meta_name else ""
        payload = {n: zf.read(n) for n in names}
    check("wheel metadata: name and version",
          f"Name: syncyard" in meta and f"Version: {version}" in meta)
    check("wheel metadata: no runtime dependencies",
          "Requires-Dist:" not in meta)
    for pkg_path, checkout_path in (
            ("syncyard/gk.py", "gatekeeper/gk.py"),
            ("syncyard/config.py", "gatekeeper/config.py"),
            ("syncyard/backchannel/bc.py", "backchannel/bc.py")):
        check(f"wheel carries {pkg_path} byte-identical to the checkout",
              pkg_path in payload
              and payload[pkg_path] == (ROOT / checkout_path).read_bytes())

    venv = td / "venv"
    r = run([sys.executable, "-m", "venv", str(venv)])
    check("fresh venv created", r.returncode == 0,
          (r.stdout + r.stderr).strip()[-300:])
    if r.returncode != 0:
        return
    bindir = venv / ("Scripts" if os.name == "nt" else "bin")
    py = bindir / ("python.exe" if os.name == "nt" else "python")
    exe = bindir / ("syncyard.exe" if os.name == "nt" else "syncyard")
    r = run([str(py), "-m", "pip", "install", "--no-index",
             "--no-deps", str(wheel)])
    check("wheel installs offline into the fresh venv",
          r.returncode == 0, (r.stdout + r.stderr).strip()[-300:])
    if r.returncode != 0:
        return
    r = run([str(exe), "--help"])
    check("installed `syncyard --help` exits 0", r.returncode == 0,
          (r.stdout + r.stderr).strip()[-300:])
    r = run([str(exe), "status"], cwd=td)
    check("installed `syncyard status` exits 0", r.returncode == 0,
          (r.stdout + r.stderr).strip()[-300:])
    probe = (
        "import importlib.metadata as m\n"
        "import syncyard.gk, syncyard.backchannel.bc\n"
        "import syncyard.config\n"
        "cfg = syncyard.config.load_config()\n"
        "assert cfg['door_repo'], 'config defaults failed to load'\n"
        f"assert m.version('syncyard') == '{version}'\n"
        "print('installed surface ok')\n"
    )
    r = run([str(py), "-c", probe])
    check("installed modules import; config defaults load; "
          "installed version == VERSION",
          r.returncode == 0 and "installed surface ok" in r.stdout,
          (r.stdout + r.stderr).strip()[-300:])


if __name__ == "__main__":
    sys.exit(main())
