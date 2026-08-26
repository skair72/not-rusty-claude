"""scripts/trim-config.py - the config bisect helper.

Its whole reason to exist is that it must not damage the config it reads, so
that is what gets tested hardest: writing into $HOME is refused outright, and
the source file is byte-identical after every operation.
"""

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRIM = ROOT / "scripts" / "trim-config.py"

SAMPLE = {"projects": {"/a": [1, 2, 3]}, "theme": "dark",
          "cachedGrowthBookFeatures": {"x": "y" * 40}, "numStartups": 5}


def _run(source, out_dir, *args):
    return subprocess.run(
        [sys.executable, str(TRIM), str(out_dir), *args],
        env={"NRC_GLOBAL_CONFIG": str(source), "PATH": "/usr/bin:/bin",
             "HOME": str(pathlib.Path.home())},
        capture_output=True, text=True, timeout=60)


def _source(tmp_path):
    src = tmp_path / "global.json"
    src.write_text(json.dumps(SAMPLE))
    return src


def test_dropping_a_key_leaves_every_other_key_untouched(tmp_path):
    src = _source(tmp_path)
    proc = _run(src, tmp_path / "out", "projects")
    assert proc.returncode == 0, proc.stderr

    written = json.loads((tmp_path / "out" / ".claude.json").read_text())
    assert "projects" not in written
    assert written == {k: v for k, v in SAMPLE.items() if k != "projects"}


def test_keep_inverts_the_selection(tmp_path):
    src = _source(tmp_path)
    proc = _run(src, tmp_path / "out", "--keep", "theme", "numStartups")
    assert proc.returncode == 0, proc.stderr

    written = json.loads((tmp_path / "out" / ".claude.json").read_text())
    assert written == {"theme": "dark", "numStartups": 5}


def test_it_refuses_to_write_into_home(tmp_path):
    """The one thing that would make a bisect destroy its own evidence."""
    src = _source(tmp_path)
    proc = _run(src, pathlib.Path.home(), "projects")

    assert proc.returncode != 0, "writing into $HOME was allowed"
    assert "refusing" in proc.stderr


def test_the_source_config_is_never_modified(tmp_path):
    src = _source(tmp_path)
    before = src.read_bytes()
    for args in (("projects",), ("--keep", "theme"), ("--list",)):
        _run(src, tmp_path / "out", *args)
    assert src.read_bytes() == before, "the helper edited the config it read"


def test_list_reports_sizes_largest_first_and_writes_nothing(tmp_path):
    src = _source(tmp_path)
    out = tmp_path / "out"
    proc = _run(src, out, "--list")

    assert proc.returncode == 0, proc.stderr
    keys = [ln.split()[-1] for ln in proc.stdout.splitlines() if ln.strip()]
    assert keys[0] == "cachedGrowthBookFeatures", proc.stdout
    assert set(keys) == set(SAMPLE)
    assert not out.exists(), "--list created an output directory"


def test_a_key_that_is_not_there_warns_rather_than_failing(tmp_path):
    src = _source(tmp_path)
    proc = _run(src, tmp_path / "out", "projects", "noSuchKey")

    assert proc.returncode == 0, proc.stderr
    assert "noSuchKey" in proc.stderr
    written = json.loads((tmp_path / "out" / ".claude.json").read_text())
    assert "projects" not in written
