"""scripts/build.sh end-to-end, driven by synthetic kilobyte-scale binaries.

Two properties are under test.

*The recovery plan.* The docs' worst case is "keep the previous working
build/extract/". A rebuild that fails - because the input is not a Bun
standalone, or because the transform is rejected - must leave the last
known-good artifacts exactly where they were.

*The closing summary describes THIS artifact.* build.sh prints a list of ways
the build differs from the native binary, and that list is not a constant: the
image-processing gap is closed in a default build and open in an opt-out one.
It was previously unasserted - deleting the reassignment left the whole suite
green while every default build kept announcing a gap it no longer had, which
is worse than saying nothing, because it sends the reader to findings.md
looking for the fix to a problem that is already fixed.
"""

import pathlib
import re
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

# GOOD_ENTRY has no `Bun.isStandaloneExecutable` gate at all, so the shim finds
# nothing to do. This one does: the declaration and the image branch, cut down
# from build/extract/cli.original.js (linux-x64 2.1.222), keeping the anchor
# string and the branch's own `if(<gate>())try{` shape that postprocess.py
# selects on. A build from this binary is a SHIMMED build and has to describe
# itself as one.
SHIMMABLE_ENTRY = (
    b"// @bun @bytecode @bun-cjs\n"
    b"(function(exports, require, module, __filename, __dirname) {\n"
    b"function CE(){return Bun.isStandaloneExecutable===!0}"
    b"if(CE()){let r={mode:\"embedded\",command:process.execPath};}"
    b"async function uYe(){if(Fbo)return Fbo.default;if(CE())try{let r=await "
    b"Promise.resolve().then(() => (uys(),cys)),n=r.sharp||r.default;return "
    b"Fbo={default:n},n}catch{console.warn(\"Native image processor not "
    b"available, falling back to sharp\");return null}}"
    b"})\n")


def _synthetic_binary(path, entry=GOOD_ENTRY):
    payload = fixtures.build_payload([("/$bunfs/root/cli", entry, 1)])
    path.write_bytes(fixtures.build_elf(payload))
    return path


def _build(out_dir, native, env=None):
    return subprocess.run(
        ["bash", str(BUILD_SH), str(native)],
        capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(out_dir),
             "OUT_DIR": str(out_dir), "BUN_BIN": "/nonexistent/bun",
             **(env or {})})


def _gap_list(result):
    """The parenthesised list in build.sh's closing `not identical` warning."""
    match = re.search(r"not identical to the native binary \(([^)]*)\)",
                      result.stderr)
    assert match, f"no gap-list warning printed:\n{result.stderr}"
    return match.group(1)


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
    """The EXIT trap must clean up a build that failed *after* staging exists.

    This has to use the rejected-transform binary, not `/bin/ls`. `/bin/ls`
    dies inside `find_bun_section_elf` before `extract_bun.py` has created the
    staging directory, so there is nothing to leak and the assertion passes
    even with `cleanup()` emptied or `trap cleanup EXIT` deleted. The
    untransformable entry gets all the way through extraction - the staging
    directory exists and is populated - and only then does `postprocess.py`'s
    `check()` refuse to write, leaving the trap as the only thing that removes
    it.
    """
    native = _synthetic_binary(tmp_path / "native")
    out = tmp_path / "out"
    assert _build(out, native).returncode == 0
    bad = _synthetic_binary(tmp_path / "bad", entry=UNTRANSFORMABLE_ENTRY)

    assert _build(out, bad).returncode != 0

    leaked = sorted(p.name for p in out.glob(".extract*"))
    assert leaked == [], f"staging directories left behind: {leaked}"
    assert sorted(p.name for p in out.iterdir()) == ["extract"]


def test_a_failed_extraction_leaves_no_staging_directory(tmp_path):
    """The other failure mode, for completeness. Weaker by construction: the
    extractor dies before it creates the staging directory, so this can only
    ever confirm that nothing was created, never that it was cleaned up.
    """
    native = _synthetic_binary(tmp_path / "native")
    out = tmp_path / "out"
    assert _build(out, native).returncode == 0

    assert _build(out, shutil.which("ls") or "/bin/ls").returncode != 0

    assert sorted(p.name for p in out.glob(".extract*")) == []


def test_the_gap_list_describes_the_artifact_that_was_just_built(tmp_path):
    """A default build closes the image-processing gap, so the summary must not
    still list it. Asserted as the exact list, not as a substring: the whole
    defect being pinned here is a stale list that is *nearly* right."""
    native = _synthetic_binary(tmp_path / "native", entry=SHIMMABLE_ENTRY)

    result = _build(tmp_path / "out", native)

    assert result.returncode == 0, result.stderr
    assert "image shim APPLIED" in result.stdout
    assert _gap_list(result) == "sandbox, ripgrep, install identity"


def test_an_unshimmable_binary_still_lists_the_image_processing_gap(tmp_path):
    """The other half, and the reason the list cannot simply be corrected once
    and hardcoded: this entry module has no gate to rewrite, so the artifact
    really does still have the gap findings.md 11 describes."""
    native = _synthetic_binary(tmp_path / "native", entry=GOOD_ENTRY)

    result = _build(tmp_path / "out", native)

    assert result.returncode == 0, result.stderr
    assert "image shim NOT APPLIED" in result.stderr
    assert _gap_list(result) == "image processing, sandbox, ripgrep"


def test_any_non_empty_opt_out_value_is_an_opt_out_here_too(tmp_path):
    """build.sh decides with `[ -n ... ]` and postprocess.py with a truthiness
    test, so both must read `NRC_NO_IMAGE_SHIM=false` as "do not shim". Under
    the older `!= "1"` rule they disagreed: postprocess.py would shim, build.sh
    would print the opt-out message, and a shim that genuinely FAILED would be
    announced as a deliberate choice - the one wording that stops anyone
    looking. The value here is deliberately not "1" and deliberately reads as
    false to a human."""
    native = _synthetic_binary(tmp_path / "native", entry=SHIMMABLE_ENTRY)

    result = _build(tmp_path / "out", native, env={"NRC_NO_IMAGE_SHIM": "false"})

    assert result.returncode == 0, result.stderr
    assert "image shim applied     : 0" in result.stdout
    assert "NRC_NO_IMAGE_SHIM is set" in result.stderr, (
        "the opt-out was honoured but announced as a failure to find the gate")
    assert _gap_list(result) == "image processing, sandbox, ripgrep"
