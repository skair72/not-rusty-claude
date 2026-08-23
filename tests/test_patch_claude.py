"""tools/patch_claude.py - the length-preserving byte patcher.

The invariant under test is the one the tool exists to protect: Mach-O offsets
are absolute, so a patched file that is one byte longer or shorter than its
input is a corrupt binary. Everything here is driven against synthetic
kilobyte-scale fixtures in tmp_path; nothing points at a real binary.

`--no-sign` returns from main() before any codesign call, so the whole
byte-patching half runs end-to-end on Linux through the real CLI. The signing
half is macOS-only, so those three helpers are exercised as a unit with the
module's run() stubbed - which is also the only way to observe the *order* of
the codesign calls relative to the write.
"""

import os
import pathlib
import re
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "patch_claude.py"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

# patch_claude does no container parsing at all - it is a byte find/replace -
# so a fixture needs no valid load commands. The magic is here only so a reader
# can see what kind of file the tool believes it is holding.
MACHO_MAGIC = bytes.fromhex("cffaedfe")


def _binary(path, body):
    path.write_bytes(MACHO_MAGIC + bytes(28) + body)
    return path


def _patch(*args):
    """Run the real CLI. Everything the tool says, including die(), goes to
    stdout; stderr is folded in so an unhandled traceback is visible too."""
    proc = subprocess.run([sys.executable, str(TOOL)] + [str(a) for a in args],
                          capture_output=True, text=True)
    return types.SimpleNamespace(rc=proc.returncode,
                                 out=ANSI.sub("", proc.stdout + proc.stderr))


# --------------------------------------------------------------------------
# the length invariant, as a property
# --------------------------------------------------------------------------

BODY = "head NEEDLE mid NEEDLE tail ÉÉ end".encode("utf-8")

LENGTH_CASES = [
    ("NEEDLE", "N", []),                       # shorter, padded
    ("NEEDLE", "NEEDLE", []),                  # same length, no padding at all
    ("NEEDLE", "", []),                        # erased: the whole span is pad
    ("NEEDLE", "N", ["--occurrence", "2"]),    # one hit of several
    ("NEEDLE", "N", ["--pad", "\\0"]),         # NUL pad, not space
    ("ÉÉ", "e", []),                           # 4 bytes of old, 1 of new
    ("ÉÉ", "abÉ", []),                         # 4 bytes in, 4 bytes out
]


@pytest.mark.parametrize("old,new,extra", LENGTH_CASES)
def test_the_patched_file_is_exactly_as_long_as_its_input(tmp_path, old, new, extra):
    """The load-bearing property. Not "the happy path produced the string I
    wanted" - every accepted combination of --old/--new/--pad/--occurrence has
    to come out the same size, because a Mach-O that grew mid-file is corrupt
    however right the replacement text is."""
    binary = _binary(tmp_path / "claude.bin", BODY)
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", old, "--new", new,
                    "--out", out, "--no-sign", *extra)

    assert result.rc == 0, result.out
    assert out.stat().st_size == binary.stat().st_size
    assert binary.read_bytes() == MACHO_MAGIC + bytes(28) + BODY


def test_nothing_outside_the_matched_span_moves(tmp_path):
    """Stronger than the size check, which an off-by-one that stole a byte from
    the left and gave it back on the right would still satisfy."""
    body = b"A" * 100 + b"NEEDLE" + b"B" * 100
    binary = _binary(tmp_path / "claude.bin", body)
    original = binary.read_bytes()
    out = tmp_path / "patched.bin"

    assert _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                  "--out", out, "--no-sign").rc == 0

    patched = out.read_bytes()
    off = original.index(b"NEEDLE")
    differing = [i for i, (a, b) in enumerate(zip(original, patched)) if a != b]
    assert differing == list(range(off, off + len(b"NEEDLE")))
    assert patched[off:off + 6] == b"z     "


def test_padding_lands_at_the_end_of_the_replacement(tmp_path):
    """Documented as load-bearing in the module docstring: with the quotes
    included in --old/--new the padding falls outside the JS string literal, so
    the string's *value* is exactly what was typed. That only holds if the pad
    is appended, never prepended or centred."""
    binary = _binary(tmp_path / "claude.bin", b'x=("long original text");')
    out = tmp_path / "patched.bin"

    assert _patch("--bin", binary, "--old", '"long original text"',
                  "--new", '"short"', "--out", out, "--no-sign").rc == 0

    assert out.read_bytes().endswith(b'x=("short"             );')


def test_a_replacement_longer_than_the_original_is_refused_before_any_write(tmp_path):
    binary = _binary(tmp_path / "claude.bin", BODY)
    original = binary.read_bytes()
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "NEEDLESS",
                    "--out", out, "--no-sign")

    assert result.rc != 0
    assert "longer than the original (8 > 6 bytes)" in result.out
    assert not out.exists()
    assert binary.read_bytes() == original


def test_length_is_measured_in_bytes_not_characters(tmp_path):
    """'ÉÉ' is two characters and four bytes. A replacement of four
    characters fits the character count and overflows the file."""
    binary = _binary(tmp_path / "claude.bin", BODY)
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "ÉÉ", "--new", "abÉd",
                    "--out", out, "--no-sign")

    assert result.rc != 0
    assert "(5 > 4 bytes)" in result.out
    assert not out.exists()


def test_an_empty_new_blanks_the_span_with_pad(tmp_path):
    binary = _binary(tmp_path / "claude.bin", b"keep NEEDLE keep")
    out = tmp_path / "patched.bin"

    assert _patch("--bin", binary, "--old", "NEEDLE", "--new", "",
                  "--pad", "\\0", "--out", out, "--no-sign").rc == 0

    assert out.read_bytes().endswith(b"keep \0\0\0\0\0\0 keep")


# --------------------------------------------------------------------------
# --pad
# --------------------------------------------------------------------------

def test_a_multi_byte_pad_is_refused(tmp_path):
    """--pad fills a byte-counted span, so anything but one byte breaks the
    length invariant. Measured with the guard neutered: --pad é turned a
    10-byte input into a 25-byte output (the escape round-trip mangles it to
    four bytes, five times over)."""
    binary = _binary(tmp_path / "claude.bin", BODY)
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "N",
                    "--pad", "é", "--out", out, "--no-sign")

    assert result.rc != 0
    assert "--pad must be exactly one byte" in result.out
    assert not out.exists()


def test_nul_pad_is_accepted_as_an_escape(tmp_path):
    """'\\0' arrives as two characters and has to survive unicode_escape as one
    NUL byte, or C strings cannot be patched at all."""
    binary = _binary(tmp_path / "claude.bin", b"/very/long/path\0")
    out = tmp_path / "patched.bin"

    assert _patch("--bin", binary, "--old", "/very/long/path", "--new", "/tmp/p",
                  "--pad", "\\0", "--out", out, "--no-sign").rc == 0

    assert out.read_bytes().endswith(b"/tmp/p\0\0\0\0\0\0\0\0\0\0")


# --------------------------------------------------------------------------
# --occurrence
# --------------------------------------------------------------------------

def test_all_occurrences_are_patched_by_default(tmp_path):
    binary = _binary(tmp_path / "claude.bin", b"a NEEDLE b NEEDLE c NEEDLE d")
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--out", out, "--no-sign")

    assert "Found 3 occurrence(s); patching 3" in result.out
    assert out.read_bytes().endswith(b"a z      b z      c z      d")


def test_a_one_based_index_patches_only_that_hit(tmp_path):
    binary = _binary(tmp_path / "claude.bin", b"a NEEDLE b NEEDLE c NEEDLE d")
    out = tmp_path / "patched.bin"

    assert _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                  "--occurrence", "2", "--out", out, "--no-sign").rc == 0

    assert out.read_bytes().endswith(b"a NEEDLE b z      c NEEDLE d")


@pytest.mark.parametrize("occurrence,message", [
    ("4", "--occurrence 4 out of range (found 3)"),
    ("0", "--occurrence 0 out of range (found 3)"),
    ("-1", "--occurrence -1 out of range (found 3)"),
    ("2.5", "--occurrence must be 'all' or an integer"),
    ("first", "--occurrence must be 'all' or an integer"),
])
def test_a_bad_occurrence_is_refused_and_writes_nothing(tmp_path, occurrence, message):
    binary = _binary(tmp_path / "claude.bin", b"a NEEDLE b NEEDLE c NEEDLE d")
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--occurrence", occurrence, "--out", out, "--no-sign")

    assert result.rc != 0
    assert message in result.out
    assert not out.exists()


# --------------------------------------------------------------------------
# what counts as a match at all
# --------------------------------------------------------------------------

def test_an_absent_old_is_refused(tmp_path):
    binary = _binary(tmp_path / "claude.bin", BODY)
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "ABSENT", "--new", "z",
                    "--out", out, "--no-sign")

    assert result.rc != 0
    assert "string not found in the binary" in result.out
    assert not out.exists()


def test_an_empty_old_is_refused(tmp_path):
    """b"".find() succeeds at every offset, so an empty --old "matches" the
    whole file. Measured on a 1 MiB fixture before this guard existed: 1,048,577
    reported occurrences, each one previewed, 41 s of wall clock and 56 MB of
    RSS - and then exit 0 claiming a successful patch. The file this tool is
    aimed at is the ~300 MB native binary, where that is a hang."""
    binary = _binary(tmp_path / "claude.bin", BODY)
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "", "--new", "",
                    "--out", out, "--no-sign")

    assert result.rc != 0
    assert "--old is empty" in result.out
    assert not out.exists()


def test_overlapping_matches_are_refused(tmp_path):
    """find_all advances one byte at a time, so `aaa` matches twice in `aaaa`
    and both hits get patched. Measured before this guard: --old aaa --new z
    turned b"PREFIX aaaa SUFFIX" into b"PREFIX zz   SUFFIX" - the second write
    landed on the first replacement's padding, producing a doubled `z` nobody
    asked for. The file is still exactly the right length, so the size check
    cannot see it; only refusing before the write can."""
    binary = _binary(tmp_path / "claude.bin", b"PREFIX aaaa SUFFIX")
    original = binary.read_bytes()
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "aaa", "--new", "z",
                    "--out", out, "--no-sign")

    assert result.rc != 0
    assert "overlap" in result.out
    assert not out.exists()
    assert binary.read_bytes() == original


def test_occurrence_is_the_escape_hatch_for_an_overlapping_old(tmp_path):
    """Refusing outright would make a self-overlapping --old unpatchable, so
    the refusal has to survive --occurrence collapsing the hits to one."""
    binary = _binary(tmp_path / "claude.bin", b"PREFIX aaaa SUFFIX")
    out = tmp_path / "patched.bin"

    assert _patch("--bin", binary, "--old", "aaa", "--new", "z",
                  "--occurrence", "1", "--out", out, "--no-sign").rc == 0

    assert out.read_bytes().endswith(b"PREFIX z  a SUFFIX")


# --------------------------------------------------------------------------
# where the output goes
# --------------------------------------------------------------------------

def test_a_missing_binary_is_refused(tmp_path):
    result = _patch("--bin", tmp_path / "nope.bin", "--old", "a", "--new", "b",
                    "--out", tmp_path / "patched.bin", "--no-sign")

    assert result.rc != 0
    assert "no such file" in result.out


def test_neither_out_nor_in_place_is_an_error_not_a_silent_no_op(tmp_path):
    binary = _binary(tmp_path / "claude.bin", BODY)
    original = binary.read_bytes()

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z", "--no-sign")

    assert result.rc != 0
    assert "choose --out <path> or --in-place" in result.out
    assert binary.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["claude.bin"]


def test_dry_run_writes_nothing_at_all(tmp_path):
    """Including the .bak: --dry-run --in-place must not touch the filesystem,
    or a rehearsal leaves state behind."""
    binary = _binary(tmp_path / "claude.bin", BODY)
    original = binary.read_bytes()

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--in-place", "--dry-run")

    assert result.rc == 0
    assert "dry run - nothing written" in result.out
    assert binary.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["claude.bin"]


def test_in_place_patches_the_binary_and_keeps_a_backup(tmp_path):
    binary = _binary(tmp_path / "claude.bin", b"a NEEDLE b")
    original = binary.read_bytes()

    assert _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                  "--in-place", "--no-sign").rc == 0

    assert binary.read_bytes().endswith(b"a z      b")
    assert binary.stat().st_size == len(original)
    assert (tmp_path / "claude.bin.bak").read_bytes() == original


def test_the_backup_stays_the_pristine_original_across_repeated_runs(tmp_path):
    """The .bak is the only way back from a bad patch. A second --in-place run
    must not overwrite it with the already-patched first result, or the
    original is gone."""
    binary = _binary(tmp_path / "claude.bin", b"a NEEDLE b")
    original = binary.read_bytes()

    assert _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                  "--in-place", "--no-sign").rc == 0
    assert _patch("--bin", binary, "--old", "z ", "--new", "y",
                  "--in-place", "--no-sign").rc == 0

    assert binary.read_bytes().endswith(b"a y      b")
    assert (tmp_path / "claude.bin.bak").read_bytes() == original


def test_out_writes_a_copy_and_leaves_the_input_alone(tmp_path):
    binary = _binary(tmp_path / "claude.bin", b"a NEEDLE b")
    original = binary.read_bytes()
    out = tmp_path / "patched.bin"

    assert _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                  "--out", out, "--no-sign").rc == 0

    assert binary.read_bytes() == original
    assert out.read_bytes().endswith(b"a z      b")
    assert os.access(out, os.X_OK)


def test_out_pointing_at_bin_does_not_corrupt_bin(tmp_path):
    """--out <the input> is a plausible way to ask for --in-place, and it must
    not half-write the input. shutil.copy2 refuses same-file today, which is
    what keeps this safe; a "skip the copy when they are the same" shortcut
    would silently turn it into an --in-place with no .bak."""
    binary = _binary(tmp_path / "claude.bin", b"a NEEDLE b")
    original = binary.read_bytes()

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--out", binary, "--no-sign")

    assert result.rc != 0
    assert binary.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir()) == ["claude.bin"]


# --------------------------------------------------------------------------
# the macOS-only half: helpers against a stubbed run()
# --------------------------------------------------------------------------

ENT_XML = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<plist version="1.0"><dict>'
           '<key>com.apple.security.cs.allow-jit</key><true/>'
           '</dict></plist>')

IDENTIFIER = "com.anthropic.claude-code"


def _result(returncode=0, stdout="", stderr=""):
    return types.SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def test_the_identifier_is_read_off_the_original(patch_claude, monkeypatch):
    """Why the tool bothers, in its own words: codesign otherwise "invents one
    from the filename", renaming com.anthropic.claude-code and breaking
    anything keyed on the identifier. That half is macOS behaviour and is not
    checkable here; what is checkable is that the parser finds the line."""
    monkeypatch.setattr(patch_claude, "run", lambda cmd, **kw: _result(
        stdout=f"Executable=/x/claude\nIdentifier={IDENTIFIER}\nFormat=Mach-O thin (arm64)\n"))

    assert patch_claude.original_identifier("/x/claude") == IDENTIFIER


def test_the_identifier_is_found_on_stderr_too(patch_claude, monkeypatch):
    """The parser concatenates stdout and stderr rather than reading one of
    them, so both halves of that choice need pinning. Which stream real
    codesign uses cannot be established on this host - that it does not matter
    can be."""
    monkeypatch.setattr(patch_claude, "run", lambda cmd, **kw: _result(
        stderr=f"Executable=/x/claude\nIdentifier={IDENTIFIER}\n"))

    assert patch_claude.original_identifier("/x/claude") == IDENTIFIER


def test_no_identifier_line_yields_none(patch_claude, monkeypatch):
    monkeypatch.setattr(patch_claude, "run", lambda cmd, **kw: _result(
        returncode=1, stderr="/x/claude: code object is not signed at all\n"))

    assert patch_claude.original_identifier("/x/claude") is None


def test_entitlements_are_written_as_the_plist_codesign_printed(patch_claude, tmp_path, monkeypatch):
    monkeypatch.setattr(patch_claude, "run", lambda cmd, **kw: _result(stdout=ENT_XML + "\n"))
    dest = tmp_path / "ent.plist"

    assert patch_claude.dump_entitlements("/x/claude", str(dest)) == str(dest)

    assert dest.read_text() == ENT_XML


def test_entitlements_are_taken_from_stderr_when_stdout_is_empty(patch_claude, tmp_path, monkeypatch):
    monkeypatch.setattr(patch_claude, "run", lambda cmd, **kw: _result(stderr=ENT_XML))
    dest = tmp_path / "ent.plist"

    assert patch_claude.dump_entitlements("/x/claude", str(dest)) == str(dest)

    assert dest.read_text() == ENT_XML


def test_no_entitlements_yields_none_and_writes_no_plist(patch_claude, tmp_path, monkeypatch):
    """"No entitlements" has to be signalled as None, not as an empty plist,
    because None is what makes resign() omit --entitlements entirely (pinned
    below). An empty file handed to codesign is a different request from not
    passing the flag."""
    monkeypatch.setattr(patch_claude, "run", lambda cmd, **kw: _result(
        stderr="/x/claude: no entitlements\n"))
    dest = tmp_path / "ent.plist"

    assert patch_claude.dump_entitlements("/x/claude", str(dest)) is None

    assert not dest.exists()


def test_resign_keeps_the_hardened_runtime_and_carries_both_arguments(patch_claude, monkeypatch):
    calls = []
    monkeypatch.setattr(patch_claude, "run",
                        lambda cmd, **kw: (calls.append(list(cmd)), _result())[1])

    patch_claude.resign("/x/claude", "/tmp/ent.plist", IDENTIFIER)

    assert calls == [["codesign", "--force", "--options", "runtime",
                      "-i", IDENTIFIER, "--entitlements", "/tmp/ent.plist",
                      "--sign", "-", "/x/claude"]]


def test_resign_omits_what_it_does_not_have(patch_claude, monkeypatch):
    """An empty -i or --entitlements is not the same as omitting it: codesign
    would take the empty value literally."""
    calls = []
    monkeypatch.setattr(patch_claude, "run",
                        lambda cmd, **kw: (calls.append(list(cmd)), _result())[1])

    patch_claude.resign("/x/claude", None, None)

    assert calls == [["codesign", "--force", "--options", "runtime",
                      "--sign", "-", "/x/claude"]]


def test_a_failed_resign_is_fatal(patch_claude, monkeypatch):
    """By this point the patched bytes are already on disk, and per the module
    docstring an invalid signature under the hardened runtime is a SIGKILL on
    launch. A non-zero exit is the only thing that tells the caller the file it
    is now holding is not usable."""
    monkeypatch.setattr(patch_claude, "run", lambda cmd, **kw: _result(
        returncode=1, stderr="/x/claude: bundle format unrecognized\n"))

    with pytest.raises(SystemExit) as excinfo:
        patch_claude.resign("/x/claude", None, IDENTIFIER)

    assert excinfo.value.code == 1


# --------------------------------------------------------------------------
# the ordering of the codesign calls around the write
# --------------------------------------------------------------------------

def _stub_codesign(module, monkeypatch):
    """Replace the module's run() and record, for every command, the bytes of
    the file it was pointed at *at the moment it ran*. That snapshot is what
    makes the ordering visible: whether codesign was asked about the original
    binary or about one that had already been overwritten. The entitlements
    plist is snapshotted for the same reason - it lives in a temporary
    directory that is gone by the time the assertions run."""
    calls = []

    def fake_run(cmd, **kw):
        cmd = list(cmd)
        target = cmd[-1]
        ent = cmd[cmd.index("--entitlements") + 1] if "--entitlements" in cmd else None
        calls.append(types.SimpleNamespace(
            cmd=cmd,
            seen=pathlib.Path(target).read_bytes() if os.path.isfile(target) else None,
            ent=pathlib.Path(ent).read_text() if ent and os.path.isfile(ent) else None))
        if cmd[:2] == ["codesign", "-d"] and "--entitlements" in cmd:
            return _result(stdout=ENT_XML)
        if "-dvvv" in cmd:
            return _result(stdout=f"Identifier={IDENTIFIER}\n")
        return _result()

    monkeypatch.setattr(module, "run", fake_run)
    return calls


def _reads(calls):
    return [c for c in calls
            if c.cmd[0] == "codesign" and ("-dvvv" in c.cmd or c.cmd[1] == "-d")]


@pytest.mark.parametrize("mode", ["--in-place", "--out"])
def test_signing_metadata_is_read_before_the_bytes_are_overwritten(
        patch_claude, tmp_path, monkeypatch, mode):
    """Under --in-place the source *is* the destination. Reading the
    entitlements and the identifier after the write means asking codesign about
    a file whose signature no longer describes its bytes, and those two answers
    are what the re-signature is built from. Read them while the original is
    still on disk."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    dest = binary if mode == "--in-place" else tmp_path / "patched.bin"
    where = [mode] if mode == "--in-place" else [mode, str(dest)]
    calls = _stub_codesign(patch_claude, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["patch_claude.py", "--bin", str(binary),
                                      "--old", "OLDSTRING", "--new", "NEW"] + where)

    patch_claude.main()

    reads = _reads(calls)
    assert len(reads) == 2, [c.cmd for c in calls]
    for call in reads:
        assert b"OLDSTRING" in call.seen, \
            f"{call.cmd[:2]} was answered from patched bytes"


@pytest.mark.parametrize("mode", ["--in-place", "--out"])
def test_the_signature_is_applied_to_the_patched_bytes(
        patch_claude, tmp_path, monkeypatch, mode):
    """The other half of the ordering: signing has to happen after the write,
    or the signature is invalidated by the patch that follows it."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    dest = binary if mode == "--in-place" else tmp_path / "patched.bin"
    where = [mode] if mode == "--in-place" else [mode, str(dest)]
    calls = _stub_codesign(patch_claude, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["patch_claude.py", "--bin", str(binary),
                                      "--old", "OLDSTRING", "--new", "NEW"] + where)

    patch_claude.main()

    signed = [c for c in calls if "--sign" in c.cmd]
    assert len(signed) == 1
    call = signed[0]
    assert call.cmd[-1] == str(dest)
    assert b"NEW" in call.seen and b"OLDSTRING" not in call.seen
    assert call.cmd[call.cmd.index("-i") + 1] == IDENTIFIER
    assert call.ent == ENT_XML


def test_dry_run_never_invokes_codesign(patch_claude, tmp_path, monkeypatch):
    """A rehearsal must not shell out to anything - and in particular the
    metadata reads must sit after the --dry-run return, not before it."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    calls = _stub_codesign(patch_claude, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["patch_claude.py", "--bin", str(binary),
                                      "--old", "OLDSTRING", "--new", "NEW",
                                      "--in-place", "--dry-run"])

    patch_claude.main()

    assert calls == []


def test_no_sign_never_invokes_codesign(patch_claude, tmp_path, monkeypatch):
    """--no-sign is what makes this tool testable off macOS; if it reached
    codesign for any reason it would blow up on a host that has none."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    calls = _stub_codesign(patch_claude, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["patch_claude.py", "--bin", str(binary),
                                      "--old", "OLDSTRING", "--new", "NEW",
                                      "--in-place", "--no-sign"])

    patch_claude.main()

    assert calls == []
    assert binary.read_bytes().endswith(b"a NEW       b")


def test_an_explicit_identifier_is_not_read_off_the_binary(patch_claude, tmp_path, monkeypatch):
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    calls = _stub_codesign(patch_claude, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["patch_claude.py", "--bin", str(binary),
                                      "--old", "OLDSTRING", "--new", "NEW",
                                      "--in-place", "--identifier", "com.example.mine"])

    patch_claude.main()

    assert [c.cmd for c in calls if "-dvvv" in c.cmd] == []
    signed = [c.cmd for c in calls if "--sign" in c.cmd][0]
    assert signed[signed.index("-i") + 1] == "com.example.mine"
