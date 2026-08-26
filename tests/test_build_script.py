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


def _build_env(out_dir, env=None):
    """The environment a build runs under. Exposed, not inlined, so a test can
    assert on the HOME it actually chooses - see the OS-littering test below."""
    return {"PATH": "/usr/bin:/bin",
            "HOME": str(fixtures.scratch_home(out_dir)),
            "OUT_DIR": str(out_dir), "BUN_BIN": "/nonexistent/bun",
            **(env or {})}


def _build(out_dir, native, env=None):
    return subprocess.run(
        ["bash", str(BUILD_SH), str(native)],
        capture_output=True, text=True, env=_build_env(out_dir, env))


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


def _marked_entry(marker):
    """A transformable entry module carrying a distinguishable string, so a
    rebuild's output can be told apart from the build it replaced."""
    return (b"// @bun @bytecode @bun-cjs\n"
            b"(function(exports, require, module, __filename, __dirname) {\n"
            b"module.exports=\"" + marker + b"\";\n"
            b"})\n")


def test_a_second_successful_build_replaces_the_artifact_and_leaves_no_debris(tmp_path):
    """The swap's HAPPY path, which every other test here leaves unpinned.

    The three rebuild tests below all make the second build FAIL, so none of
    them reaches `mv "$WORK" "$PREV"` at all, and the surviving assertion about
    debris only looks at OUT_DIR's top level. Turning that mv into `cp -r`
    therefore survived the whole suite - and it is not a harmless mutation:
    $WORK still exists afterwards, so the following `mv "$STAGE" "$WORK"` moves
    the staging directory INSIDE it. The user is left with build/extract/
    containing the OLD cli.original.cjs plus a nested .extract.stage.NNN
    holding the new one, after a build that reported success. Running a
    rebuilt artifact that is silently the previous release's is exactly what
    this swap exists to prevent, so a successful rebuild is asserted here on
    both halves: the artifact is the new bytes, and nothing is left over.
    """
    out = tmp_path / "out"
    first = _synthetic_binary(tmp_path / "native1", entry=_marked_entry(b"BUILD-ONE"))
    assert _build(out, first).returncode == 0
    extract = out / "extract"
    first_names = sorted(p.name for p in extract.iterdir())
    assert b"BUILD-ONE" in (extract / "cli.original.cjs").read_bytes()

    second = _synthetic_binary(tmp_path / "native2", entry=_marked_entry(b"BUILD-TWO"))
    result = _build(out, second)

    assert result.returncode == 0, result.stderr
    cjs = (extract / "cli.original.cjs").read_bytes()
    assert b"BUILD-TWO" in cjs, "the rebuild reported success but the artifact is stale"
    assert b"BUILD-ONE" not in cjs
    assert sorted(p.name for p in extract.iterdir()) == first_names, \
        "the swap left something extra inside %s" % extract
    assert [str(p.relative_to(out)) for p in out.rglob(".extract*")] == []
    assert sorted(p.name for p in out.iterdir()) == ["extract"]


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


def test_the_os_littering_in_home_does_not_count_against_the_output_dir(tmp_path):
    """Reproduces, on Linux, the only failure a real Mac has ever produced here.

    The build helper used to pass HOME=OUT_DIR. On macOS the first process to
    touch CoreFoundation creates ~/Library, so both "nothing left behind"
    assertions saw ['Library', 'extract'] and blamed build.sh for a directory
    the operating system had made. Reported from a real Mac on 2026-08-24, the
    first macOS run this project has ever had.

    The dir is created in whatever HOME the helper actually chooses - reading
    it back from _build_env rather than recomputing it - because a version of
    this test that assumed the sibling passed happily with HOME=OUT_DIR
    restored, i.e. it asserted nothing.
    """
    native = _synthetic_binary(tmp_path / "native")
    out = tmp_path / "out"
    home = pathlib.Path(_build_env(out)["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "Library").mkdir()

    assert _build(out, native).returncode == 0

    assert sorted(p.name for p in out.iterdir()) == ["extract"]


def _versions_dir(home, entries):
    """Lay out $HOME/.local/share/claude/versions the way a real install does."""
    v = home / ".local" / "share" / "claude" / "versions"
    v.mkdir(parents=True, exist_ok=True)
    for name, payload in entries:
        (v / name).write_bytes(payload)
    return v


def _autodiscover(home, out_dir):
    """build.sh with NO argument, so it has to find the binary itself."""
    return subprocess.run(
        ["bash", str(BUILD_SH)], capture_output=True, text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(home),
             "OUT_DIR": str(out_dir), "BUN_BIN": "/nonexistent/bun"})


def test_auto_discovery_skips_a_dud_newer_version(tmp_path):
    """The defect a real Mac exposed on 2026-08-24. An interrupted auto-update
    left versions/2.1.241 at 0 bytes, sorting NEWER than the 2.1.239 actually
    in use. `ls | sort -V | tail -1` picked the dud, `-f` accepted it, and the
    extractor reported "input is only 0 bytes" - which reads as a bug in this
    repo rather than a broken install on the machine."""
    home = tmp_path / "home"
    good = fixtures.build_elf(fixtures.build_payload(
        [("/$bunfs/root/cli", GOOD_ENTRY, 1)])) + b"\0" * 2_000_000
    _versions_dir(home, [("2.1.239", good), ("2.1.241", b"")])

    result = _autodiscover(home, tmp_path / "out")

    assert result.returncode == 0, result.stderr
    assert "2.1.239" in result.stdout
    assert "ignored unusable version(s)" in result.stderr
    assert "2.1.241" in result.stderr


def test_auto_discovery_skips_a_truncated_dud_at_the_size_boundary(tmp_path):
    """The value of build.sh's MIN_NATIVE_BYTES, pinned from both sides.

    The sibling test above writes its dud as 0 bytes, which fails any threshold
    from 1 upward - so MIN_NATIVE_BYTES=1048576 could be mutated to 1 and the
    suite stayed green, while the "truncated" case the constant's own comment
    names (an interrupted update leaves "a 0-byte or truncated entry") sailed
    through and got selected. Here the newer version is a real binary cut to
    exactly one byte BELOW the threshold, and the older one is exactly AT it:
    a threshold any lower selects the truncated 2.1.241 and the extractor then
    reports a container error for what is really a broken install, and a
    threshold any higher rejects the good binary too and the build dies with
    "native Claude binary not found".
    """
    home = tmp_path / "home"
    good = fixtures.build_elf(fixtures.build_payload(
        [("/$bunfs/root/cli", GOOD_ENTRY, 1)]))
    good = good + b"\0" * (1048576 - len(good))
    assert len(good) == 1048576
    dud = good[:1048575]
    _versions_dir(home, [("2.1.239", good), ("2.1.241", dud)])

    result = _autodiscover(home, tmp_path / "out")

    assert result.returncode == 0, result.stderr
    assert "2.1.239" in result.stdout
    assert "2.1.241" in result.stderr
    assert "ignored unusable version(s)" in result.stderr


def test_an_explicitly_passed_stub_is_refused_as_an_install_problem(tmp_path):
    """-f alone accepted a 0-byte file. The message has to point at the install,
    not at the container format, or the next person debugs the wrong thing."""
    stub = tmp_path / "stub.bin"
    stub.write_bytes(b"")

    result = _build(tmp_path / "out", stub)

    assert result.returncode != 0
    assert "too small to be a Claude standalone" in result.stderr
    assert "container magic" not in result.stderr


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
    really does still have the gap findings.md 10 describes."""
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
