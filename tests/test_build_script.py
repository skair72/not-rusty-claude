"""scripts/build.sh end-to-end, driven by synthetic kilobyte-scale binaries.

The property under test is the one the docs' worst-case recovery plan depends
on: "keep the previous working build/extract/". A rebuild that fails - because
the input is not a Bun standalone, or because the transform is rejected - must
leave the last known-good artifacts exactly where they were.
"""

import pathlib
import shutil
import subprocess

import fixtures

ROOT = pathlib.Path(__file__).resolve().parent.parent
BUILD_SH = ROOT / "scripts" / "build.sh"

GOOD_ENTRY = (b"// @bun @bytecode @bun-cjs\n"
              b"(function(exports, require, module, __filename, __dirname) {\n"
              b"module.exports=1;\n"
              b"})\n")
# no trailing `})` -> postprocess.py's check() rejects it and writes nothing
UNTRANSFORMABLE_ENTRY = b"// @bun\n(function(exports, require, module) { oops\n"


def _synthetic_binary(path, entry=GOOD_ENTRY):
    payload = fixtures.build_payload([("/$bunfs/root/cli", entry, 1)])
    path.write_bytes(fixtures.build_elf(payload))
    return path


def _build(out_dir, native):
    return subprocess.run(
        ["bash", str(BUILD_SH), str(native)],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(out_dir),
             "OUT_DIR": str(out_dir), "BUN_BIN": "/nonexistent/bun"})


def test_build_sh_produces_the_artifacts(tmp_path):
    native = _synthetic_binary(tmp_path / "native")
    out = tmp_path / "out"

    result = _build(out, native)

    assert result.returncode == 0, result.stderr
    assert (out / "extract" / "cli.original.js").is_file()
    assert (out / "extract" / "cli.original.cjs").is_file()
    assert (out / "extract" / "cli.js").is_file()


def test_a_failed_extraction_leaves_the_previous_build_intact(tmp_path):
    """Reproduces the reported loss: `scripts/build.sh /bin/ls` on top of a
    good build used to rm -rf the artifacts before extraction could fail."""
    native = _synthetic_binary(tmp_path / "native")
    out = tmp_path / "out"
    assert _build(out, native).returncode == 0
    good = (out / "extract" / "cli.original.cjs").read_bytes()

    result = _build(out, shutil.which("ls") or "/bin/ls")

    assert result.returncode != 0
    assert (out / "extract" / "cli.original.cjs").read_bytes() == good
    assert (out / "extract" / "cli.js").is_file()


def test_a_rejected_transform_leaves_the_previous_build_intact(tmp_path):
    """The other failure mode: extraction succeeds, postprocess.py's check()
    refuses to write. The good build must still survive."""
    native = _synthetic_binary(tmp_path / "native")
    out = tmp_path / "out"
    assert _build(out, native).returncode == 0
    good = (out / "extract" / "cli.original.cjs").read_bytes()
    bad = _synthetic_binary(tmp_path / "bad", entry=UNTRANSFORMABLE_ENTRY)

    result = _build(out, bad)

    assert result.returncode != 0
    assert (out / "extract" / "cli.original.cjs").read_bytes() == good


def test_no_staging_directory_is_left_behind(tmp_path):
    native = _synthetic_binary(tmp_path / "native")
    out = tmp_path / "out"
    _build(out, native)
    _build(out, shutil.which("ls") or "/bin/ls")

    assert sorted(p.name for p in out.iterdir()) == ["extract"]
