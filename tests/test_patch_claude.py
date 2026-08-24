"""tools/patch_claude.py - the length-preserving byte patcher.

The invariant under test is the one the tool exists to protect: Mach-O offsets
are absolute, so a patched file that is one byte longer or shorter than its
input is a corrupt binary. Everything here is driven against synthetic
kilobyte-scale fixtures in tmp_path. Two tests read the real shipped Mach-O -
read-only, and only through code_signature_range() and a search - because the
code-signature guard is the one place where the tool has to understand the
container format, and a fixture built by the same understanding cannot check
that understanding. They skip when that binary is not on the host.

`--no-sign` returns from main() before any codesign call, so the whole
byte-patching half runs end-to-end on Linux through the real CLI. The signing
half is macOS-only, so its helpers are exercised as a unit with the module's
run() stubbed - which is also the only way to observe the *order* of the
codesign calls relative to the write, and of the quarantine removal relative to
the signature.

The one command in that tail this host can really execute is the --verify
launch of the patched file, so those two tests hand the tool a shell script
instead of the Mach-O-shaped fixture and let the launch through the stub.
"""

import mmap
import os
import pathlib
import re
import struct
import subprocess
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TOOL = ROOT / "tools" / "patch_claude.py"
ANSI = re.compile(r"\x1b\[[0-9;]*m")

# The patching itself does no container parsing - it is a byte find/replace -
# so these fixtures need no valid load commands, and the magic is here only so
# a reader can see what kind of file the tool believes it is holding. A header
# of zeroes behind it means ncmds is 0, which is exactly how the code-signature
# guard reads "no load commands to check against"; the fixtures that do need a
# real LC_CODE_SIGNATURE are built by _signed_macho() further down.
MACHO_MAGIC = bytes.fromhex("cffaedfe")


def _binary(path, body):
    path.write_bytes(MACHO_MAGIC + bytes(28) + body)
    return path


def _patch(*args, env=None):
    """Run the real CLI. Everything the tool says, including die(), goes to
    stdout; stderr is folded in so an unhandled traceback is visible too.

    `env` replaces the child's environment wholesale, which is how the
    codesign pre-flight is driven from both sides on any host: point PATH at an
    empty directory and shutil.which() finds nothing, whatever the platform."""
    proc = subprocess.run([sys.executable, str(TOOL)] + [str(a) for a in args],
                          capture_output=True, text=True, env=env)
    return types.SimpleNamespace(rc=proc.returncode,
                                 out=ANSI.sub("", proc.stdout + proc.stderr))


def _no_codesign_env(tmp_path):
    """An environment whose PATH holds nothing at all, so the tool's
    `shutil.which("codesign")` fails the same way on Linux and on macOS."""
    empty = tmp_path / "empty-path"
    empty.mkdir(exist_ok=True)
    return dict(os.environ, PATH=str(empty))


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
    length invariant. Measured on this fixture with the guard neutered: --pad é
    turned the 68-byte input into a 98-byte output. The escape round-trip
    mangles é to four bytes, five of them fill the six-byte span left by
    --new N, and BODY holds two NEEDLEs: +15 bytes twice."""
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
    whole file. Re-measured on a 1 MiB fixture with the guard replaced by
    `pass`, output redirected to a file: 1,048,577 reported occurrences,
    3,145,739 lines of preview under --dry-run and 3,145,742 under --no-sign,
    54.3-54.5 MiB peak RSS, 5-6 s wall on this shared host. Those two exit 0
    claiming a successful patch; the signing path here, which has no codesign,
    prints the same flood and only then refuses - so the exit status varies
    with the host and the flood does not. Three lines of preview per byte of
    input is the shape that matters: the file this tool is aimed at is the
    ~300 MB native binary, where it is a hang."""
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


@pytest.mark.parametrize("body,old,gap,corruption", [
    (b"PREFIX aaaaa SUFFIX",   "aaaa", 1, b"PREFIX zz    SUFFIX"),
    (b"PREFIX ababab SUFFIX",  "abab", 2, b"PREFIX z z    SUFFIX"),
    (b"PREFIX abcabca SUFFIX", "abca", 3, b"PREFIX z  z    SUFFIX"),
])
def test_the_overlap_refusal_covers_every_gap_below_the_length_of_old(
        tmp_path, body, old, gap, corruption):
    """The threshold is `< len(old)`, and it has to be tested as a threshold.
    Tested only at gap 1 (`aaa` in `aaaa`) it can be lowered to any constant
    >= 2 - `< 2`, `< 3` - with every other test still green, which re-admits
    exactly the doubled replacement the guard exists to stop. So: a 4-byte
    --old at gaps 1, 2 and 3, which no constant below 4 survives.

    `corruption` is what each case actually produced with the guard disabled
    (`touching = []`), measured on these three fixtures with --new z
    --no-sign: `PREFIX zz    SUFFIX`, `PREFIX z z    SUFFIX`,
    `PREFIX z  z    SUFFIX`. Every one is a doubled `z` at exactly the right
    file length, so neither splice()'s length check nor the on-disk size check
    can see it and only refusing before the write can. The mutant `< 2` still
    catches the gap-1 case and produced the last two verbatim. The corruption
    is asserted on only as "this is not what came out"; the pass condition is
    the refusal.
    """
    binary = _binary(tmp_path / "claude.bin", body)
    original = binary.read_bytes()
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", old, "--new", "z",
                    "--out", out, "--no-sign")

    assert result.rc != 0, result.out
    assert f"start within {len(old)} bytes of the previous one" in result.out
    assert "1 of the 2 hits" in result.out
    assert not out.exists()
    assert binary.read_bytes() == original
    assert not binary.read_bytes().endswith(corruption)


def test_back_to_back_hits_are_not_overlapping_hits(tmp_path):
    """The boundary the refusal turns on. `NEEDLENEEDLE` puts the second hit
    exactly len(--old) bytes after the first: adjacent, sharing no byte, so
    both replacements land inside their own span and neither touches the
    other's padding. A minified bundle produces exactly this - refusing it (the
    off-by-one that tests `<=` instead of `<`) would make the common case
    unpatchable."""
    binary = _binary(tmp_path / "claude.bin", b"PREFIX NEEDLENEEDLE SUFFIX")
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--out", out, "--no-sign")

    assert result.rc == 0, result.out
    assert "Found 2 occurrence(s); patching 2" in result.out
    assert out.read_bytes().endswith(b"PREFIX z     z      SUFFIX")


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


@pytest.mark.parametrize("spell", ["same", "symlink"])
def test_out_pointing_at_bin_does_not_corrupt_bin(tmp_path, spell):
    """--out <the input> is a plausible way to ask for --in-place, and
    answering it as one would be an in-place patch with no .bak - the one
    thing that can undo a bad patch. It is refused explicitly rather than left
    to shutil.copy2's same-file check, because the copy no longer happens at
    all: the destination is created by the write of the patched bytes.
    `samefile`, not a string compare, so the second spelling - a symlink
    pointing back at the input - is caught by the same line."""
    binary = _binary(tmp_path / "claude.bin", b"a NEEDLE b")
    original = binary.read_bytes()
    if spell == "symlink":
        dest = tmp_path / "link.bin"
        dest.symlink_to(binary)
    else:
        dest = binary

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--out", dest, "--no-sign")

    assert result.rc != 0
    assert "--out names the input; use --in-place" in result.out
    assert binary.read_bytes() == original
    assert sorted(p.name for p in tmp_path.iterdir() if not p.is_symlink()) \
        == ["claude.bin"]


# --------------------------------------------------------------------------
# hits inside the code-signature blob
# --------------------------------------------------------------------------

def _macho_bytes(body, sig):
    """Header + one LC_CODE_SIGNATURE + body + sig; see _signed_macho."""
    sig_off = 32 + 16 + len(body)
    header = (MACHO_MAGIC
              + struct.pack("<iiI", 0x0100000C, 0, 2)   # arm64, MH_EXECUTE
              + struct.pack("<III", 1, 16, 0)           # ncmds, sizeofcmds, flags
              + struct.pack("<I", 0))                   # reserved
    lc = struct.pack("<IIII", 0x1D, 16, sig_off, len(sig))
    return header + lc + body + sig, sig_off


_SIGNED_MACHO_TAIL = _macho_bytes(b"a NEEDLE b", b"S" * 40)[0][4:]


def _signed_macho(path, body, sig):
    """A thin little-endian 64-bit Mach-O carrying one real LC_CODE_SIGNATURE.

    Header (32 bytes) + that one load command (16) + `body` + `sig`, with the
    command pointing at where `sig` actually landed. Nothing else is filled in,
    because code_signature_range() reads the magic, ncmds/sizeofcmds and the
    command table and nothing else - and a fixture that carried more would be
    asserting on fields the tool never looks at."""
    blob, sig_off = _macho_bytes(body, sig)
    path.write_bytes(blob)
    assert path.stat().st_size == sig_off + len(sig)
    return path, sig_off


def test_an_implausible_sizeofcmds_is_never_allocated(patch_claude, tmp_path):
    """`fh.read(n)` allocates n bytes up front and only then discovers the file
    is shorter, so a header claiming a 256 MiB load-command table would cost
    256 MiB to reject - on a file 48 bytes long. The point of this tool is to
    be pointed at whatever binary is to hand, including ones whose header is
    not what it says, so the size is sanity-checked before it is trusted.

    Measured in-process with tracemalloc: 4,829 bytes peak as the code
    stands, against a 1 MiB ceiling here."""
    tracemalloc = pytest.importorskip("tracemalloc")
    binary = tmp_path / "claude.bin"
    binary.write_bytes(MACHO_MAGIC + bytes(12)
                       + struct.pack("<III", 4, 1 << 28, 0) + bytes(16))

    tracemalloc.start()
    try:
        assert patch_claude.code_signature_range(binary) is None
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()

    assert peak < 1 << 20, f"{peak:,} bytes allocated to reject a 48-byte file"


def test_the_signature_range_is_read_off_the_load_commands(patch_claude, tmp_path):
    binary, sig_off = _signed_macho(tmp_path / "claude.bin", b"a NEEDLE b", b"S" * 40)

    assert patch_claude.code_signature_range(binary) == (sig_off, sig_off + 40)


@pytest.mark.parametrize("name,blob", [
    # The suite's own fixture: real Mach-O magic, a header of zeroes behind it,
    # so ncmds is 0 and there is no command table to read.
    ("no load commands", MACHO_MAGIC + bytes(28) + b"a NEEDLE b"),
    ("not a mach-o", b"\x7fELF" + bytes(28) + b"a NEEDLE b"),
    ("nothing like one", b"#!/bin/sh\necho NEEDLE\n"),
    ("truncated header", MACHO_MAGIC + b"\x00\x01"),
    ("implausible sizeofcmds",
     MACHO_MAGIC + bytes(12) + struct.pack("<III", 4, 1 << 30, 0) + bytes(4)),
    # The case the magic check is actually for: a header that WOULD parse as a
    # Mach-O load-command table if anyone read it as one. Without the magic
    # test this returns a confident range for a file that has no signature.
    ("mach-o shaped, wrong magic",
     b"\x7fELF" + _SIGNED_MACHO_TAIL),
])
def test_a_container_it_cannot_read_yields_no_range_rather_than_a_guess(
        patch_claude, tmp_path, name, blob):
    """None means "I cannot tell", and the caller turns a range into a dropped
    hit. A guessed range would silently refuse to patch a legitimate offset, so
    every shape this tool might be pointed at that is not a thin Mach-O has to
    come back None - and in particular has to come back, rather than raise. A
    tool that dies on `--bin some-elf-binary` before it has even searched would
    be a worse failure than the one this guard exists to prevent."""
    binary = tmp_path / "claude.bin"
    binary.write_bytes(blob)

    assert patch_claude.code_signature_range(binary) is None, name


def test_hits_inside_the_signature_are_dropped_and_reported(tmp_path):
    """On the shipped darwin arm64 binary this is not hypothetical. Measured on
    /tmp/ccmac/package/claude (324,973,552 bytes): its LC_CODE_SIGNATURE covers
    324,320,704..324,973,552, `--old com.anthropic` finds 137 hits, and 2 of
    them - 0x1354BE54 and 0x135E69E5 - are inside it, both the signing
    identifier `com.anthropic.claude-code` stored as a literal C string in the
    CodeDirectory. Before this guard the tool printed `Found 137 occurrence(s);
    patching 137` and, under the default --occurrence all, patched all 137: the
    re-sign that follows rebuilds the superblob and discards 2 of them, and
    --no-sign leaves a CodeDirectory whose own identifier has been rewritten.
    Either way the count reported is not the count that survives."""
    binary, sig_off = _signed_macho(tmp_path / "claude.bin",
                                    b"a NEEDLE b NEEDLE c", b"..NEEDLE..")
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--out", out, "--no-sign")

    assert result.rc == 0, result.out
    assert "skipping 1 of 3 hit(s)" in result.out
    assert "LC_CODE_SIGNATURE" in result.out
    assert f"0x{sig_off + 2:X}" in result.out
    assert "Found 3 occurrence(s); patching 2" in result.out
    # The two body hits are patched; the signature blob comes out byte-identical.
    patched = out.read_bytes()
    assert patched[sig_off:] == b"..NEEDLE.."
    assert patched[:sig_off].endswith(b"a z      b z      c")
    assert len(patched) == len(binary.read_bytes())


def test_occurrence_indexes_the_hits_that_survived_the_signature_filter(tmp_path):
    """The listing and the selector have to agree: --occurrence numbers the
    hits exactly as the tool printed them. The signature blob is always the
    tail of the file, so a dropped signature hit is always the last one - which
    means the observable difference is at the far end. `--occurrence 3` on a
    file with three raw hits has to be out of range and say "found 2"; without
    the filter it would quietly patch the CodeDirectory instead. Index 2 is the
    control: the body hits must not have been renumbered."""
    binary, sig_off = _signed_macho(tmp_path / "claude.bin",
                                    b"a NEEDLE b NEEDLE c", b"..NEEDLE..")
    out = tmp_path / "patched.bin"

    refused = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                     "--occurrence", "3", "--out", out, "--no-sign")

    assert refused.rc != 0
    assert "--occurrence 3 out of range (found 2)" in refused.out
    assert not out.exists()

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--occurrence", "2", "--out", out, "--no-sign")

    assert result.rc == 0, result.out
    assert "Found 3 occurrence(s); patching 1" in result.out
    patched = out.read_bytes()
    assert patched[:sig_off].endswith(b"a NEEDLE b z      c")
    assert patched[sig_off:] == b"..NEEDLE.."


def test_patch_signature_is_the_way_to_say_you_meant_it(tmp_path):
    """Dropping hits silently would be its own trap, so the escape hatch has to
    exist and has to still warn - editing a CodeDirectory is a thing someone
    might genuinely want to do to a binary they are not going to re-sign."""
    binary, sig_off = _signed_macho(tmp_path / "claude.bin",
                                    b"a NEEDLE b", b"..NEEDLE..")
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--out", out, "--no-sign", "--patch-signature")

    assert result.rc == 0, result.out
    assert "--patch-signature given" in result.out
    assert "Found 2 occurrence(s); patching 2" in result.out
    assert out.read_bytes()[sig_off:] == b"..z     .."


def test_every_hit_inside_the_signature_is_a_refusal_not_an_empty_patch(tmp_path):
    """With nothing left to write, exiting 0 over an unmodified file would say
    the patch landed. Refuse, name the reason, and write nothing."""
    binary, _ = _signed_macho(tmp_path / "claude.bin", b"a b c", b"..NEEDLE..")
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--out", out, "--no-sign")

    assert result.rc != 0
    assert "every hit is inside the code-signature blob" in result.out
    assert "--patch-signature" in result.out
    assert not out.exists()


class _CountingOffset(int):
    """A hit offset that counts every comparison and hash performed on it.

    A timing assertion for a quadratic loop is a coin flip on a shared box, so
    the shape is asserted by operation count instead, which is deterministic.
    The two shapes are not close: see the test below."""

    ops = 0

    def __hash__(self):
        _CountingOffset.ops += 1
        return int.__hash__(self)

    def __eq__(self, other):
        _CountingOffset.ops += 1
        return int.__eq__(self, other)

    def __lt__(self, other):
        _CountingOffset.ops += 1
        return int.__lt__(self, other)

    def __le__(self, other):
        _CountingOffset.ops += 1
        return int.__le__(self, other)

    def __gt__(self, other):
        _CountingOffset.ops += 1
        return int.__gt__(self, other)

    def __ge__(self, other):
        _CountingOffset.ops += 1
        return int.__ge__(self, other)


def _reference_partition(hits, signature):
    """What the answer has to be, spelled the slow, obvious way."""
    if signature is None:
        return list(hits), []
    lo, hi = signature
    return ([h for h in hits if not lo <= h < hi],
            [h for h in hits if lo <= h < hi])


def test_the_signature_filter_never_rebuilds_its_set_per_hit(patch_claude):
    """The guard that stops this tool hanging must not itself be the hang.

    The filter this replaced was `[h for h in hits if h not in set(inside)]`,
    which rebuilds the set once per hit: O(len(hits) x len(inside)). Measured
    end to end on synthetic thin Mach-O fixtures whose hits are half inside the
    blob and half outside, `--old NEEDLE --new z --out ... --no-sign`: 4.51 s
    and 4.72 s at 16,000 hits, 16.88 s and 16.90 s at 32,000 - 3.6x the wall
    clock for 2x the hits, against 0.10/0.12 s and 0.14/0.13 s for the one-pass
    form, patched output md5-identical either way.

    Counted rather than timed here. With 4,000 hits split half and half, the
    one-pass form performs 6,000 operations on these offsets (one comparison
    for a hit below the blob, two for a hit inside it) and the set-per-hit form
    performs 8,010,000 - a margin of 1,335x, so the ceiling below can sit two
    orders of magnitude above the real count and still fail the moment the
    quadratic form comes back."""
    n = 4000
    lo, hi = 10_000_000, 20_000_000
    hits = ([_CountingOffset(i * 4) for i in range(n // 2)]
            + [_CountingOffset(lo + i * 4) for i in range(n // 2)])

    _CountingOffset.ops = 0
    outside, inside = patch_claude.partition_signature_hits(hits, (lo, hi))
    ops = _CountingOffset.ops

    assert (outside, inside) == _reference_partition(hits, (lo, hi))
    assert len(outside) == n // 2 and len(inside) == n // 2
    assert ops <= 4 * n, (
        f"{ops:,} operations for {n:,} hits: the filter is doing work per hit "
        f"that scales with the number of hits already found")


@pytest.mark.parametrize("signature", [None, (0, 0), (10, 20), (0, 10 ** 9)])
def test_the_partition_agrees_with_the_obvious_spelling(patch_claude, signature):
    """Speed is worth nothing if the fast form drops a different set of hits.
    `None` is in here because the caller passes it whenever the container was
    not parsed, and an empty range because a zero-length blob must keep every
    hit."""
    hits = [0, 9, 10, 15, 19, 20, 21, 1000]

    assert (patch_claude.partition_signature_hits(hits, signature)
            == _reference_partition(hits, signature))


# --------------------------------------------------------------------------
# what the tool says when it CANNOT find a signature
# --------------------------------------------------------------------------

# Fat/universal magics, from <mach-o/fat.h>. None of them is in MACHO_MAGICS,
# so all four take the "no range" path - which is the point of these tests.
FAT_MAGICS = [
    ("FAT_MAGIC", b"\xca\xfe\xba\xbe"),
    ("FAT_CIGAM", b"\xbe\xba\xfe\xca"),
    ("FAT_MAGIC_64", b"\xca\xfe\xba\xbf"),
    ("FAT_CIGAM_64", b"\xbf\xba\xfe\xca"),
]


@pytest.mark.parametrize("name,magic", FAT_MAGICS)
def test_a_fat_container_gets_no_range_and_a_reason_that_names_it(
        patch_claude, tmp_path, name, magic):
    """A fat binary is the case the module docstring used to be wrong about: it
    is a macOS executable, it is signed, and this tool does not parse it, so
    NOTHING is dropped. The body here is a byte-for-byte thin Mach-O behind the
    fat magic, so the only reason there is no range is the magic - i.e. this
    asserts the tool refuses to guess, and says which shape it refused on."""
    binary = tmp_path / "claude.bin"
    binary.write_bytes(magic + _SIGNED_MACHO_TAIL)

    span, why = patch_claude.signature_scan(binary)

    assert span is None, name
    assert patch_claude.code_signature_range(binary) is None, name
    assert "fat/universal" in why, why
    assert "does not parse" in why, why


@pytest.mark.parametrize("name,blob,fragment", [
    ("elf", b"\x7fELF" + bytes(28) + b"a NEEDLE b", "not a Mach-O"),
    ("empty", b"", "empty file"),
    ("thin, no LC_CODE_SIGNATURE",
     MACHO_MAGIC + bytes(12) + struct.pack("<III", 1, 16, 0) + bytes(4)
     + struct.pack("<IIII", 0x19, 16, 0, 0), "no LC_CODE_SIGNATURE"),
    ("implausible sizeofcmds",
     MACHO_MAGIC + bytes(12) + struct.pack("<III", 4, 1 << 30, 0) + bytes(4),
     "will not read"),
    ("malformed load command",
     MACHO_MAGIC + bytes(12) + struct.pack("<III", 1, 16, 0) + bytes(4)
     + struct.pack("<IIII", 0x1D, 0, 0, 0), "malformed load command"),
])
def test_every_reason_for_no_range_is_a_different_sentence(
        patch_claude, tmp_path, name, blob, fragment):
    """None on its own cannot be reported honestly: "this file has no
    signature" and "I never looked" are the same value. The caller prints this
    string, so each cause has to arrive distinguishable."""
    binary = tmp_path / "claude.bin"
    binary.write_bytes(blob)

    span, why = patch_claude.signature_scan(binary)

    assert span is None, name
    assert fragment in why, f"{name}: {why}"


def test_a_file_that_cannot_be_opened_is_a_reason_not_an_exception(
        patch_claude, tmp_path):
    """main() calls this before it has established anything about the path
    beyond os.path.isfile, and a raise here would be an unhandled traceback in
    place of the tool's own error message."""
    span, why = patch_claude.signature_scan(tmp_path / "not-there")

    assert span is None
    assert "FileNotFoundError" in why


def test_a_container_with_no_range_says_so_and_then_patches_every_hit(tmp_path):
    """The behaviour the docstring now describes, end to end. The fixture is a
    thin Mach-O with a real LC_CODE_SIGNATURE and the fat magic pasted over its
    first four bytes: on the thin version the second hit is dropped (see
    test_hits_inside_the_signature_are_dropped_and_reported), and here the same
    bytes are patched like any other - so the tool has to say that it never
    located a signature at all, rather than printing nothing and leaving the
    two runs indistinguishable except by the hit count."""
    thin, sig_off = _macho_bytes(b"a NEEDLE b", b"..NEEDLE..")
    binary = tmp_path / "claude.bin"
    binary.write_bytes(b"\xca\xfe\xba\xbe" + thin[4:])
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--out", out, "--no-sign")

    assert result.rc == 0, result.out
    assert "no code-signature range" in result.out
    assert "fat/universal" in result.out
    assert "Found 2 occurrence(s); patching 2" in result.out
    assert "skipping" not in result.out
    # the second hit lives in what a thin build of this file would have called
    # its signature blob, and it has been patched
    assert out.read_bytes()[sig_off:] == b"..z     .."


def test_a_signed_thin_binary_gets_no_such_note(tmp_path):
    """The control for the test above: when the range IS found, the note is
    absent, so its presence carries information instead of being boilerplate on
    every run."""
    binary, _ = _signed_macho(tmp_path / "claude.bin", b"a NEEDLE b", b"..zzz..")
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "z",
                    "--out", out, "--no-sign")

    assert result.rc == 0, result.out
    assert "no code-signature range" not in result.out


def test_the_signature_range_of_the_real_binary_is_the_tail_of_the_file(
        patch_claude, real_macho_binary):
    """The synthetic fixtures above are built by the same understanding of the
    format they are testing, so one read of the shipped Mach-O is what says
    that understanding is right. Measured on /tmp/ccmac/package/claude:
    LC_CODE_SIGNATURE = 324,320,704..324,973,552, which is exactly the end of
    the 324,973,552-byte file and sits inside __LINKEDIT (324,124,672..EOF).
    Asserted structurally rather than as those literals, because the fixture is
    whichever darwin tarball the host unpacked."""
    span = patch_claude.code_signature_range(real_macho_binary)

    assert span is not None
    start, end = span
    assert end == os.path.getsize(real_macho_binary)
    assert 0 < start < end


def test_the_linux_elf_binary_gets_no_signature_range(patch_claude, real_elf_binary):
    """The other real container this repo handles. An ELF has no
    LC_CODE_SIGNATURE and no load commands to read, and inventing a range for
    one would drop hits out of a perfectly patchable file."""
    assert patch_claude.code_signature_range(real_elf_binary) is None


def test_the_real_binary_puts_com_anthropic_hits_inside_its_signature(
        patch_claude, real_macho_binary):
    """The measurement the guard exists for, re-run against the real file:
    `--old com.anthropic` really does match inside the CodeDirectory. 137 hits
    with 2 inside the signature when this was written; asserted as "at least
    one, and every one of them is the signing identifier" so it keeps meaning
    the same thing on a different build. Read-only - mmap, no patch run, and
    nothing is written to the binary."""
    span = patch_claude.code_signature_range(real_macho_binary)
    assert span is not None
    with open(real_macho_binary, "rb") as fh:
        blob = mmap.mmap(fh.fileno(), 0, access=mmap.ACCESS_READ)
        try:
            hits = patch_claude.find_all(blob, b"com.anthropic")
            inside = [h for h in hits if span[0] <= h < span[1]]
            assert inside, f"{len(hits)} hits, none inside {span}"
            assert len(inside) < len(hits), "all of them inside is not the shape"
            for off in inside:
                assert blob[off:off + 25] == b"com.anthropic.claude-code"
        finally:
            blob.close()


# --------------------------------------------------------------------------
# preview(): the whole content of the --dry-run rehearsal
# --------------------------------------------------------------------------

def test_the_dry_run_preview_shows_the_bytes_that_are_about_to_be_overwritten(tmp_path):
    """--dry-run is the rehearsal a user runs before an irreversible in-place
    write to a ~300 MB signed binary, and the preview lines are its entire
    content: the offsets and the surrounding text are all the evidence there is
    that --old matched what the user meant. So it has to show the PRE-patch
    bytes.

    Mutant: `blob = patched` before the preview loop in main(). On this fixture
    the pristine tool prints `MACHOMACHO x=("long original text"); tail` and
    the mutant prints `MACHOMACHO x=("short"             ); tail` - the
    post-patch bytes, presented as the bytes about to be replaced, with the
    exit status and every other line unchanged."""
    binary = _binary(tmp_path / "claude.bin",
                     b'MACHOMACHO x=("long original text"); tail')
    original = binary.read_bytes()

    result = _patch("--bin", binary, "--old", '"long original text"',
                    "--new", '"short"', "--in-place", "--dry-run")

    assert result.rc == 0, result.out
    assert 'MACHOMACHO x=("long original text"); tail' in result.out
    assert 'x=("short"' not in result.out
    assert binary.read_bytes() == original


def test_the_preview_of_a_real_write_is_also_the_pre_patch_bytes(tmp_path):
    """The same property on the path that does write. --dry-run and --out share
    one preview loop today; if they are ever split, the rehearsal and the real
    run showing different "before" text is the bug worth catching."""
    binary = _binary(tmp_path / "claude.bin",
                     b'MACHOMACHO x=("long original text"); tail')
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", '"long original text"',
                    "--new", '"short"', "--out", out, "--no-sign")

    assert result.rc == 0, result.out
    assert 'MACHOMACHO x=("long original text"); tail' in result.out
    assert 'x=("short"' not in result.out
    assert out.read_bytes().endswith(b'x=("short"             ); tail')


def test_preview_shows_a_window_on_each_side_and_highlights_only_the_match(
        patch_claude):
    """`width` bytes of context either side, and the colour break has to fall
    exactly on the span that is going to be replaced - that highlight is how a
    reader tells `--old NEEDLE` from `--old NEEDLE ` in a wall of minified JS.
    Asserted on the raw string, escapes included, because stripping the escapes
    is what makes the highlight untestable."""
    blob = b"0123456789NEEDLEabcdefghij"

    text = patch_claude.preview(blob, 10, 6, width=4)

    assert ANSI.sub("", text) == "6789NEEDLEabcd"
    assert patch_claude.YEL + "NEEDLE" + patch_claude.RESET in text


@pytest.mark.parametrize("width", [4, 1000])
def test_preview_clamps_to_the_buffer_at_both_ends(patch_claude, width):
    """A hit closer to offset 0 than `width` is the normal case at the head of
    a Mach-O, and `off - width` there is a negative index. Python reads that as
    an offset from the END of the buffer, so `blob[-2:2]` is empty and the
    context before the match silently disappears - the two widths here are the
    case where the negative index is still in range (width=4 gives -2, and
    `max(0, ...)` is what saves it) and the case where it is past the start
    (width=1000 gives -998, which Python clamps to 0 on its own, so that half
    is a control). The upper end needs no such care: an end index past the
    buffer is already clamped by the slice, and the `min(len(blob), ...)` in
    preview() is there to keep the two ends reading alike."""
    blob = b"abNEEDLEcd"

    text = ANSI.sub("", patch_claude.preview(blob, 2, 6, width=width))

    assert text == "abNEEDLEcd"


def test_preview_keeps_every_hit_on_one_line(patch_claude):
    """One hit is one line. A newline inside the window would split it across
    two and silently push the rest out of alignment with the `[n] offset` line
    above it, so the escaping is part of the output contract."""
    blob = b"a\nb NEEDLE c\r\nd"

    text = ANSI.sub("", patch_claude.preview(blob, 4, 6))

    assert text == "a\\nb NEEDLE c\\r\\nd"
    assert "\n" not in text and "\r" not in text


def test_preview_does_not_choke_on_bytes_that_are_not_text(patch_claude):
    """Every preview on the real binary is a window into a compiled Mach-O, so
    most of what lands either side of a hit is not valid UTF-8. Decoding
    strictly would raise, and the rehearsal would die instead of rehearsing."""
    blob = b"\xff\xfe\x00 NEEDLE \x80\x81"

    text = ANSI.sub("", patch_claude.preview(blob, 4, 6))

    assert "NEEDLE" in text


# --------------------------------------------------------------------------
# a host that cannot sign: the destination must not exist afterwards
# --------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["--out", "--in-place"])
def test_a_host_without_codesign_refuses_before_it_creates_anything(tmp_path, mode):
    """The regression this pins, reproduced on this Linux host before the fix:
    `printf 'HEAD NEEDLE TAIL' > fixture.bin` then `--old NEEDLE --new N --out
    out.bin` ended in an unhandled `FileNotFoundError: 'codesign'` traceback,
    exit 1, and left out.bin holding `HEAD NEEDLE TAIL` - a verbatim UNPATCHED
    copy of the input, under the name the user asked for, with nothing in the
    output saying the destination was not patched. The same command one commit
    earlier had left the correctly patched `HEAD N      TAIL` there.

    Both halves of that matter and both are asserted: no file at the
    destination, and a sentence that names the cause and the way out. The
    traceback assertion is not decoration - `die()` is the only exit that
    prints something a caller can act on."""
    binary = _binary(tmp_path / "claude.bin", b"HEAD NEEDLE TAIL")
    original = binary.read_bytes()
    out = tmp_path / "patched.bin"
    where = [mode] if mode == "--in-place" else [mode, out]

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "N", *where,
                    env=_no_codesign_env(tmp_path))

    assert result.rc != 0
    assert "codesign not found" in result.out
    assert "--no-sign" in result.out
    assert "Traceback" not in result.out
    assert not out.exists()
    # --in-place: neither the input nor its .bak may have been touched either.
    assert binary.read_bytes() == original
    assert not (tmp_path / "claude.bin.bak").exists()


def test_the_same_run_with_no_sign_produces_the_patched_bytes(tmp_path):
    """The other half of the regression, and the reason the refusal above is a
    refusal and not a silent skip: the byte-patching this tool exists for works
    perfectly well on a host with no codesign, and --no-sign is how you ask for
    it. `HEAD NEEDLE TAIL` with --old NEEDLE --new N is the exact fixture the
    regression was found on; `HEAD N      TAIL` is what it used to produce."""
    binary = _binary(tmp_path / "claude.bin", b"HEAD NEEDLE TAIL")
    out = tmp_path / "patched.bin"

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "N",
                    "--out", out, "--no-sign", env=_no_codesign_env(tmp_path))

    assert result.rc == 0, result.out
    assert out.read_bytes().endswith(b"HEAD N      TAIL")


def test_a_dry_run_needs_no_codesign_either(tmp_path):
    """A rehearsal writes nothing and signs nothing, so the pre-flight must sit
    after the --dry-run return. Otherwise the one command that is safe to run
    anywhere becomes the one command that refuses to run off macOS."""
    binary = _binary(tmp_path / "claude.bin", b"HEAD NEEDLE TAIL")

    result = _patch("--bin", binary, "--old", "NEEDLE", "--new", "N",
                    "--in-place", "--dry-run", env=_no_codesign_env(tmp_path))

    assert result.rc == 0, result.out
    assert "dry run - nothing written" in result.out
    assert sorted(q.name for q in tmp_path.iterdir()) == ["claude.bin", "empty-path"]


@pytest.mark.parametrize("mode", ["--out", "--in-place"])
def test_a_failure_reading_signing_metadata_leaves_nothing_at_the_destination(
        patch_claude, tmp_path, monkeypatch, mode):
    """Stronger than the pre-flight above, and the property that actually has
    to hold: the signing metadata is read BEFORE the destination is created, so
    no failure in it - a missing codesign, a permissions error, a codesign that
    dies - can leave a file at --out that is not the patched output. The
    pre-flight only covers the one cause it can predict.

    Driven by making dump_entitlements() raise the same FileNotFoundError
    subprocess raises for a missing binary. `codesign_path` is stubbed present
    so the pre-flight lets the run through and this reaches the ordering."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    original = binary.read_bytes()
    out = tmp_path / "patched.bin"
    monkeypatch.setattr(patch_claude, "codesign_path", lambda: "/usr/bin/codesign")
    monkeypatch.setattr(patch_claude, "dump_entitlements",
                        lambda *a: (_ for _ in ()).throw(
                            FileNotFoundError(2, "No such file or directory", "codesign")))
    where = [mode] if mode == "--in-place" else [mode, str(out)]
    _argv(monkeypatch, binary, *where)

    with pytest.raises(FileNotFoundError):
        patch_claude.main()

    assert not out.exists()
    assert binary.read_bytes() == original
    assert sorted(q.name for q in tmp_path.iterdir()) == ["claude.bin"]


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

def _stub_codesign(module, monkeypatch, verify_rc=0, xattr_rc=0, launch=False):
    """Replace the module's run() and record, for every command, the bytes of
    the file it was pointed at *at the moment it ran*. That snapshot is what
    makes the ordering visible: whether codesign was asked about the original
    binary or about one that had already been overwritten. The entitlements
    plist is snapshotted for the same reason - it lives in a temporary
    directory that is gone by the time the assertions run.

    verify_rc is what the final `codesign -v` returns and xattr_rc what the
    quarantine removal returns; every other codesign call succeeds. launch=True runs anything that is not codesign or xattr for
    real, which is how the --verify path is reachable on a host with neither
    tool - the fixture "binary" there is a shell script."""
    calls = []
    real_run = module.run

    # main() refuses a signing run up front on a host with no codesign, which
    # is every host this suite runs on. Stubbing run() alone leaves that
    # pre-flight looking at the real PATH, so it has to be answered too: these
    # tests are about what the tool does WITH codesign, and the refusal itself
    # is pinned separately below.
    monkeypatch.setattr(module, "codesign_path", lambda: "/usr/bin/codesign")

    def fake_run(cmd, **kw):
        cmd = list(cmd)
        target = cmd[-1]
        ent = cmd[cmd.index("--entitlements") + 1] if "--entitlements" in cmd else None
        calls.append(types.SimpleNamespace(
            cmd=cmd,
            seen=pathlib.Path(target).read_bytes() if os.path.isfile(target) else None,
            ent=pathlib.Path(ent).read_text() if ent and os.path.isfile(ent) else None))
        if cmd[0] not in ("codesign", "xattr"):
            return real_run(cmd, **kw) if launch else _result()
        if cmd[:2] == ["codesign", "-d"] and "--entitlements" in cmd:
            return _result(stdout=ENT_XML)
        if "-dvvv" in cmd:
            return _result(stdout=f"Identifier={IDENTIFIER}\n")
        if cmd[0] == "xattr":
            return _result(returncode=xattr_rc,
                           stderr="" if xattr_rc == 0 else
                           f"xattr: [Errno 93] Attribute not found: '{target}'\n")
        if cmd[:2] == ["codesign", "-v"]:
            return _result(returncode=verify_rc,
                           stderr="" if verify_rc == 0 else
                           f"{target}: invalid signature (code or signature have been modified)\n")
        return _result()

    monkeypatch.setattr(module, "run", fake_run)
    return calls


def _reads(calls):
    return [c for c in calls
            if c.cmd[0] == "codesign" and ("-dvvv" in c.cmd or c.cmd[1] == "-d")]


def test_signing_metadata_is_read_before_the_bytes_are_overwritten(
        patch_claude, tmp_path, monkeypatch):
    """--in-place only, and deliberately so: under --out the source survives
    the write untouched, so both orderings return the same answers and there is
    nothing here to observe. Under --in-place the source *is* the destination,
    and reading afterwards means asking codesign about a file whose signature
    no longer covers its bytes - those two answers are what the re-signature is
    built from. Whether codesign would in fact answer differently is UNVERIFIED
    (no codesign on this host); what this pins is the ordering the tool chose,
    so a revert is visible."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    calls = _stub_codesign(patch_claude, monkeypatch)
    monkeypatch.setattr(sys, "argv", ["patch_claude.py", "--bin", str(binary),
                                      "--old", "OLDSTRING", "--new", "NEW",
                                      "--in-place"])

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


# --------------------------------------------------------------------------
# the post-write tail: quarantine, verification, launch
# --------------------------------------------------------------------------

def _argv(monkeypatch, binary, *rest):
    monkeypatch.setattr(sys, "argv", ["patch_claude.py", "--bin", str(binary),
                                      "--old", "OLDSTRING", "--new", "NEW"]
                        + [str(a) for a in rest])


def test_the_quarantine_xattr_is_dropped_from_the_signed_result(
        patch_claude, tmp_path, monkeypatch):
    """The tool's own comment calls quarantine plus an ad-hoc signature a launch
    block, so the removal is load-bearing and has to actually be issued, at the
    finished file, after the signature is on it. Whether macOS would restore the
    attribute if it were dropped earlier is UNVERIFIED here - neither xattr nor
    codesign exists on this host - but ordering it after the last writer of the
    file is the only ordering that cannot be undone by that writer."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    calls = _stub_codesign(patch_claude, monkeypatch)
    _argv(monkeypatch, binary, "--in-place")

    patch_claude.main()

    cmds = [c.cmd for c in calls]
    dropped = [i for i, c in enumerate(cmds) if c[0] == "xattr"]
    signed = [i for i, c in enumerate(cmds) if "--sign" in c]
    assert len(dropped) == 1 and len(signed) == 1, cmds
    assert cmds[dropped[0]] == ["xattr", "-d", "com.apple.quarantine", str(binary)]
    assert dropped[0] > signed[0], cmds


def test_the_tail_acts_on_the_destination_not_the_source(
        patch_claude, tmp_path, monkeypatch):
    """--out, because under --in-place src IS dest and every argument in the
    tail is trivially right. Split them apart and the two surviving commands -
    the quarantine drop and the final `codesign -v` - have to be shown to name
    the patched copy.

    Both mutations are silent in the direction that matters. Pointed at the
    source, `codesign -v` verifies the untouched original, which still carries
    its valid Developer ID signature: green line, exit 0, and the ad-hoc
    signature on the file the user is about to run was never checked at all.
    Pointed at the source, `xattr -d` strips quarantine off the input and
    leaves it on the output - the ad-hoc-signature-plus-quarantine pairing the
    code comment calls a launch block. Neither is observable on this host
    (no codesign, no xattr), so what is pinned is the argument, plus the bytes
    each command was looking at when it ran."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    dest = tmp_path / "patched.bin"
    calls = _stub_codesign(patch_claude, monkeypatch)
    _argv(monkeypatch, binary, "--out", dest)

    patch_claude.main()

    dropped = [c for c in calls if c.cmd[0] == "xattr"]
    verified = [c for c in calls if c.cmd[:2] == ["codesign", "-v"]]
    assert len(dropped) == 1 and len(verified) == 1, [c.cmd for c in calls]

    assert dropped[0].cmd == ["xattr", "-d", "com.apple.quarantine", str(dest)]
    assert verified[0].cmd == ["codesign", "-v", "--verbose=2", str(dest)]

    # The argument alone could still be a path that happens to spell the
    # destination; these say the file under each command held the patched bytes.
    for call in dropped + verified:
        assert b"NEW" in call.seen and b"OLDSTRING" not in call.seen, call.cmd

    # And the source is neither of them: it still has the original string.
    assert b"OLDSTRING" in binary.read_bytes()


def test_a_file_with_no_quarantine_xattr_is_not_an_error(
        patch_claude, tmp_path, monkeypatch, capsys):
    """`xattr -d` exits non-zero when the attribute is not there, which is the
    normal case for a binary that was never downloaded by a browser. Treating
    that as fatal would fail every ordinary run, so the return code is ignored
    on purpose."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    _stub_codesign(patch_claude, monkeypatch, xattr_rc=1)
    _argv(monkeypatch, binary, "--in-place")

    patch_claude.main()

    assert "Done." in ANSI.sub("", capsys.readouterr().out)


def test_a_failed_signature_verification_is_fatal(
        patch_claude, tmp_path, monkeypatch, capsys):
    """Symmetric with test_a_failed_resign_is_fatal, and for the same reason:
    by this point the patched bytes are the only copy under --in-place. Per the
    module docstring, an invalid signature under the hardened runtime is a
    SIGKILL on launch - macOS behaviour, not checkable on this host. So if
    `codesign -v` rejects the result, exiting 0 tells the caller a ~300 MB
    binary is ready to ship when it will not start."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    _stub_codesign(patch_claude, monkeypatch, verify_rc=1)
    _argv(monkeypatch, binary, "--in-place")

    with pytest.raises(SystemExit) as excinfo:
        patch_claude.main()

    assert excinfo.value.code == 1
    out = ANSI.sub("", capsys.readouterr().out)
    assert "signature verification failed" in out
    assert "Done." not in out


# --verify launches the patched file. That needs a file this host can execute,
# so these two use a shell script rather than the Mach-O-shaped fixture; the
# tool does not parse the container, and every codesign/xattr call is stubbed.
# The `--verify=--check` spelling is not a style choice: argparse refuses a
# value that starts with a dash unless it is attached with `=`. Measured -
# `--verify --version` exits with "argument --verify: expected one argument",
# and --version is the obvious thing to pass, so the help text says so.
LAUNCHABLE = b"""#!/bin/sh
# OLDSTRING
echo "argv: $@"
exit %d
"""


def _script(path, exit_code):
    path.write_bytes(LAUNCHABLE % exit_code)
    path.chmod(0o755)
    return path


def test_verify_launches_the_patched_binary_and_reports_its_output(
        patch_claude, tmp_path, monkeypatch, capsys):
    """--verify exists to answer "does the thing still start", which is the
    question the whole re-signing dance is about. It has to run the patched
    destination - not the source, not nothing at all."""
    binary = _script(tmp_path / "claude.bin", 0)
    out = tmp_path / "patched.bin"
    calls = _stub_codesign(patch_claude, monkeypatch, launch=True)
    _argv(monkeypatch, binary, "--out", out, "--verify=--check")

    patch_claude.main()

    launched = [c.cmd for c in calls if c.cmd[0] not in ("codesign", "xattr")]
    assert launched == [[str(out), "--check"]]
    text = ANSI.sub("", capsys.readouterr().out)
    assert "argv: --check" in text, text
    assert "launched cleanly" in text
    assert b"NEW" in out.read_bytes()


def test_a_binary_that_will_not_launch_is_fatal(
        patch_claude, tmp_path, monkeypatch, capsys):
    """The point of the flag. A patch that produces a file which exits non-zero
    has broken something the signature check cannot see, and reporting success
    would send it on. Exit code 7 is arbitrary; that it is carried into the
    message is what makes the failure diagnosable."""
    binary = _script(tmp_path / "claude.bin", 7)
    out = tmp_path / "patched.bin"
    _stub_codesign(patch_claude, monkeypatch, launch=True)
    _argv(monkeypatch, binary, "--out", out, "--verify=--check")

    with pytest.raises(SystemExit) as excinfo:
        patch_claude.main()

    assert excinfo.value.code == 1
    text = ANSI.sub("", capsys.readouterr().out)
    assert "binary exited 7" in text
    assert "launched cleanly" not in text
    assert "Done." not in text


# --------------------------------------------------------------------------
# splice(): the length invariant as a unit, and the write that follows it
# --------------------------------------------------------------------------

def test_splice_writes_every_hit_and_leaves_its_input_alone(patch_claude):
    """The caller keeps the original buffer for the previews it prints, so a
    splice that mutated in place would show the reader the patched bytes and
    call them the old ones."""
    blob = bytearray(b"a NEEDLE b NEEDLE c")

    out = patch_claude.splice(blob, [2, 11], 6, b"z     ")

    assert bytes(out) == b"a z      b z      c"
    assert bytes(blob) == b"a NEEDLE b NEEDLE c"


@pytest.mark.parametrize("padded,expected", [
    (b"zz", "19 -> 13 bytes"),        # short: every later offset slides left
    (b"zzzzzzzz", "19 -> 23 bytes"),  # long: every later offset slides right
])
def test_splice_refuses_to_return_a_resized_buffer(patch_claude, padded, expected):
    """The invariant this whole tool exists for, stated where it can be tested
    directly instead of only through a CLI path that other guards block first.
    Nothing downstream can repair a shifted offset: the file is the right shape
    and every load command in it points somewhere else."""
    blob = bytearray(b"a NEEDLE b NEEDLE c")

    with pytest.raises(ValueError) as excinfo:
        patch_claude.splice(blob, [2, 11], 6, padded)

    assert "would resize the file" in str(excinfo.value)
    assert expected in str(excinfo.value)


def test_a_refused_splice_costs_the_original_nothing(
        patch_claude, tmp_path, monkeypatch, capsys):
    """splice() has to run before the destination is opened. Under --in-place
    the write is destructive, so a resize caught afterwards - which is where the
    on-disk size check sits - is caught with the original already gone. Pinned
    by making splice() raise: the .bak must not even have been taken yet."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    original = binary.read_bytes()
    monkeypatch.setattr(patch_claude, "splice", lambda *a: (_ for _ in ()).throw(
        ValueError("patch would resize the file")))
    _stub_codesign(patch_claude, monkeypatch)
    _argv(monkeypatch, binary, "--in-place")

    with pytest.raises(SystemExit) as excinfo:
        patch_claude.main()

    assert excinfo.value.code == 1
    assert "would resize the file" in ANSI.sub("", capsys.readouterr().out)
    assert binary.read_bytes() == original
    assert sorted(q.name for q in tmp_path.iterdir()) == ["claude.bin"]


def _truncate_writes_to(module, monkeypatch, dest):
    """Shadow the module's open() so a binary write to `dest` drops half its
    bytes. A module global wins over the builtin, so this reaches the tool's
    open() calls and nothing else. There is no way to make a real filesystem
    short-write on demand here, and the guard is worth nothing untested."""
    real_open = open
    dest = os.path.abspath(str(dest))

    class Short:
        def __init__(self, fh):
            self.fh = fh

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.fh.close()
            return False

        def write(self, data):
            return self.fh.write(data[:len(data) // 2])

    def fake_open(path, mode="r", *args, **kw):
        fh = real_open(path, mode, *args, **kw)
        if os.path.abspath(str(path)) == dest and "w" in mode and "b" in mode:
            return Short(fh)
        return fh

    monkeypatch.setattr(module, "open", fake_open, raising=False)


def test_a_write_that_lands_short_is_not_reported_as_a_patch(
        patch_claude, tmp_path, monkeypatch, capsys):
    """The last-ditch check, after splice() has already guaranteed the buffer.
    What is left for it is the write itself losing bytes - a quota, ENOSPC, a
    filesystem that dropped the tail - without raising. Without it the tool
    prints Patched over a truncated binary and exits 0, and under --in-place
    the input is already gone."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    out = tmp_path / "patched.bin"
    _truncate_writes_to(patch_claude, monkeypatch, out)
    _argv(monkeypatch, binary, "--out", out, "--no-sign")

    with pytest.raises(SystemExit) as excinfo:
        patch_claude.main()

    assert excinfo.value.code == 1
    text = ANSI.sub("", capsys.readouterr().out)
    assert "truncated write" in text
    assert "Patched" not in text
    assert out.stat().st_size < binary.stat().st_size


def test_a_short_write_in_place_is_measured_against_the_buffer_not_the_file(
        patch_claude, tmp_path, monkeypatch, capsys):
    """The same guard as above, under --in-place, which is the only mode where
    it can be got wrong. Under --out the source and the destination are
    different files, so comparing the written size against either one catches a
    short write; under --in-place src IS dest and
    `written != os.path.getsize(src)` is a tautology that can never fire.

    Driven end to end with that mutant in place on this fixture: it printed
    `Patched - size unchanged at 22 bytes` and exited 0, leaving claude.bin
    holding 22 of its 45 bytes with the full 45 in claude.bin.bak - verbatim
    the failure the guard's own comment names. Hence the assertion on the .bak:
    under --in-place the input is already gone by the time this fires, so the
    only thing that makes the message actionable is that the backup is whole."""
    binary = _binary(tmp_path / "claude.bin", b"a OLDSTRING b")
    original = binary.read_bytes()
    _truncate_writes_to(patch_claude, monkeypatch, binary)
    _argv(monkeypatch, binary, "--in-place", "--no-sign")

    with pytest.raises(SystemExit) as excinfo:
        patch_claude.main()

    assert excinfo.value.code == 1
    text = ANSI.sub("", capsys.readouterr().out)
    assert "truncated write" in text
    assert "recover from the .bak" in text
    assert "Patched" not in text
    assert binary.stat().st_size < len(original)
    assert (tmp_path / "claude.bin.bak").read_bytes() == original


# --------------------------------------------------------------------------
# how many copies of a 300 MB binary end up in RAM
# --------------------------------------------------------------------------

# The tool, run in-process by a fresh interpreter under tracemalloc, which
# reports the peak bytes live in Python's allocators during the run.
#
# The obvious measurement - peak RSS of a child process, via fork/posix_spawn
# and wait4 - is not usable from inside a test suite. ru_maxrss is
# max(what this process reached, what it inherited), and CPython's subprocess
# uses vfork, so the child starts out charged with whatever the parent had
# resident: the identical command measured 76.3 MiB launched from a bare shell
# and 101 MiB launched from inside pytest. Subtracting a baseline taken at
# child start does not rescue it either - it hides the allocation under the
# inherited figure, and the same pair then reads 1.0 MiB and 33.0 MiB.
#
# tracemalloc counts allocations rather than pages, so it sees none of that.
# Every byte this tool holds is a Python bytes/bytearray, which is exactly what
# it counts, and it is reproducible to the byte: 66.38 MiB and 98.38 MiB on a
# 33,554,432-byte input, unchanged across three runs each and unchanged with a
# deliberately fat parent process. atexit fires on die()'s sys.exit too.
_RSS_LAUNCHER = """
import atexit, runpy, sys, tracemalloc
report = sys.argv.pop(1)
sys.argv.pop(0)              # drop "-c"; argv[0] is now the tool itself
tracemalloc.start()
atexit.register(lambda: open(report, "w").write(
    str(tracemalloc.get_traced_memory()[1])))
runpy.run_path(sys.argv[0], run_name="__main__")
"""

# Copies of the input the tool may hold at once. Two is the floor for a tool
# that has to keep the pre-patch bytes alive to print the previews while the
# patched buffer exists; the .074 is the tracemalloc bookkeeping itself.
BUDGET = 2.5


def _peak_python_heap_mib(tmp_path, *args):
    """Peak MiB live in Python's allocators over one CLI run."""
    report = tmp_path / "heap.txt"
    proc = subprocess.run(
        [sys.executable, "-c", _RSS_LAUNCHER, str(report), str(TOOL)]
        + [str(a) for a in args],
        capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return int(report.read_text()) / 1024 / 1024


@pytest.mark.parametrize("mode", ["--dry-run", "--out"])
def test_the_binary_is_not_held_in_ram_three_times_over(tmp_path, mode):
    """A byte patcher that needs a gigabyte to change eleven bytes is a bug in
    a tool aimed at a ~325 MB Mach-O. Nothing in main() mutates the buffer it
    reads - splice() takes its own bytearray() copy and preview() only slices -
    so holding it as a bytearray buys nothing and costs a full extra copy at
    the find and another at every preview line.

    Measured on a 33,554,432-byte fixture: 2.074x the input as the code stands,
    3.074x with the buffer read back as `bytearray(fh.read())` and handed to
    find_all/preview through `bytes(...)`. Corroborated outside the suite with
    wait4 peak RSS on a 126,877,716-byte fixture, where the same pair is
    254.2-254.4 MiB against 375.0-375.3 MiB and the --out bytes compare equal
    between them - the third copy buys nothing at all.

    A shape check, not a budget: it fails on a third copy of the file, not on
    a few MiB of drift."""
    pytest.importorskip("tracemalloc")
    size = 32 * 1024 * 1024
    body = bytearray(b"A" * size)
    body[size // 2:size // 2 + 6] = b"NEEDLE"
    binary = tmp_path / "claude.bin"
    binary.write_bytes(bytes(body))
    del body
    where = ["--dry-run"] if mode == "--dry-run" else [
        "--out", str(tmp_path / "patched.bin"), "--no-sign"]

    peak = _peak_python_heap_mib(tmp_path, "--bin", binary, "--old", "NEEDLE",
                                 "--new", "z", *where)

    mib = size / 1024 / 1024
    assert peak < BUDGET * mib, (
        f"{peak:.2f} MiB of Python heap for a {size:,}-byte input "
        f"({peak / mib:.3f}x, budget {BUDGET}x)")
