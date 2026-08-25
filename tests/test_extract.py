import struct
import pytest
import fixtures


def test_payload_round_trips_names_and_contents(extract_bun):
    modules = [
        ("/$bunfs/root/cli", b"(function(){})", 1),
        ("/$bunfs/root/thing.node", b"\x7fELF\x02\x01raw", 10),
    ]
    payload = fixtures.build_payload(modules)

    parsed, mod_off, mod_size, entry = extract_bun.parse_payload(fixtures._section_bytes(payload))

    assert entry == 0
    assert mod_size // 52 == 2
    table = parsed[mod_off:mod_off + mod_size]
    rec = table[52:104]
    name_off, name_size, content_off, content_size = struct.unpack_from("<IIII", rec, 0)
    assert parsed[name_off:name_off + name_size] == b"/$bunfs/root/thing.node"
    assert parsed[content_off:content_off + content_size] == b"\x7fELF\x02\x01raw"
    assert rec[49] == 10


def test_addon_content_is_written_verbatim_not_decoded(extract_bun, tmp_path):
    """The section stores every module's raw bytes whatever its loader; a loader
    name says how Bun would expose the module to JS, not how it is stored.
    Decoding corrupts the addon (an early version produced 71-byte modules).
    Loader 10 here is napi - what all the shipped .node addons really are."""
    macho_magic = b"\xcf\xfa\xed\xfe" + b"\x00" * 60
    payload = fixtures.build_payload([
        ("/$bunfs/root/cli", b"(function(){})", 1),
        ("/$bunfs/root/addon.node", macho_magic, 10),
    ])
    blob = fixtures.build_elf(payload)
    binary = tmp_path / "claude"
    binary.write_bytes(blob)
    out = tmp_path / "out"

    extract_bun.extract(str(binary), str(out))

    assert (out / "assets" / "addon.node").read_bytes() == macho_magic


def test_bad_trailer_is_rejected(extract_bun, capsys):
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    payload[-1] = 0x00

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(fixtures._section_bytes(bytes(payload)))

    assert "trailer" in capsys.readouterr().err


@pytest.mark.parametrize("modules_size", [53, 54, 103])
def test_misaligned_module_table_is_rejected(extract_bun, capsys, modules_size):
    """Every non-multiple of 52 must be refused, not just one of them.

    This pinned only 53 before, which is remainder 1 - so weakening the guard
    to `% 52 == 1` kept the test green while ACCEPTING 54 (count truncates to
    1 and the trailing 2 bytes vanish in silence). 54 and 103 (remainders 2 and
    51) are the cases that make the assertion about the property rather than
    about one value; asserting the message keeps a different guard firing from
    counting as a pass.
    """
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    struct.pack_into("<I", payload, start + 12, modules_size)

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(fixtures._section_bytes(bytes(payload)))

    assert "not a multiple" in capsys.readouterr().err


# entry_point_id indexes the module table, so `count` itself is the first
# out-of-range value and 99 cannot tell `>= count` from `> count`. Both are
# parametrized here because the far case is what a corrupt field looks like and
# the boundary is what an off-by-one looks like.
@pytest.mark.parametrize("entry_id", [1, 99])
def test_entry_point_id_out_of_range_is_rejected(extract_bun, capsys, entry_id):
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    struct.pack_into("<I", payload, start + 16, entry_id)   # count is 1

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(fixtures._section_bytes(bytes(payload)))

    assert "entry" in capsys.readouterr().err


def test_extract_does_not_claim_success_without_writing_the_entry_module(
        extract_bun, tmp_path, capsys, monkeypatch):
    """The other half of the boundary above: the report must follow the file.

    parse_payload() is stubbed to hand back entry_point_id == count, which is
    exactly the state a relaxed range check (`> count` instead of `>= count`)
    lets through. The loop in extract() then matches no module, so nothing is
    written to cli.original.js - and before this guard the run still printed
    "Extracted: 1 cli.js" and exited 0 (measured on a mutated copy). A silent
    wrong success is the failure this tool exists to prevent, so success is now
    conditional on the entry file existing.
    """
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"(function(){})", 1)])
    binary = tmp_path / "claude"
    binary.write_bytes(fixtures.build_elf(payload))
    out = tmp_path / "out"
    real = extract_bun.parse_payload

    def entry_id_equal_to_count(section):
        parsed, mod_off, mod_size, _ = real(section)
        return parsed, mod_off, mod_size, mod_size // extract_bun.MODULE_RECORD_SIZE

    monkeypatch.setattr(extract_bun, "parse_payload", entry_id_equal_to_count)

    with pytest.raises(SystemExit):
        extract_bun.extract(str(binary), str(out))

    captured = capsys.readouterr()
    assert "no cli.original.js was written" in captured.err
    assert "Extracted:" not in captured.out
    assert not (out / "cli.original.js").exists()


def test_module_table_offset_past_the_payload_is_rejected_cleanly(
        extract_bun, tmp_path, capsys):
    """modules_offset is attacker-controlled and was the one field parse_payload
    never bounded, while it bounds all three of its siblings.

    Slicing past the end of a bytes object yields b"" rather than raising, so
    the bad offset stayed invisible until struct.unpack_from() in extract() hit
    an empty record and raised struct.error - a traceback, on a file this tool
    promises to diagnose. Reproduced against the unfixed tool with
    modules_offset=0xFFFFFF: "struct.error: unpack_from requires a buffer of at
    least 16 bytes ... (actual buffer size is 0)", exit 1.
    """
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    struct.pack_into("<I", payload, start + 8, 0xFFFFFF)   # modules_offset
    binary = tmp_path / "claude"
    binary.write_bytes(fixtures.build_elf(bytes(payload)))

    with pytest.raises(SystemExit):
        extract_bun.extract(str(binary), str(tmp_path / "out"))

    err = capsys.readouterr().err
    assert "error:" in err
    assert "runs past the end" in err


def test_module_table_ending_one_byte_past_the_payload_is_rejected(
        extract_bun, capsys):
    """The boundary of that check: a table whose last byte falls just outside.

    modules_offset is moved forward by 1 from the offset the fixture wrote, so
    modules_offset + modules_size == len(payload) + 1 - the first value that
    must be refused. A check written as `>` instead of `>=`, or one comparing
    only modules_offset to len(payload), accepts this and reads a record that
    is one byte short.
    """
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    modules_offset = struct.unpack_from("<I", payload, start + 8)[0]
    modules_size = struct.unpack_from("<I", payload, start + 12)[0]
    struct.pack_into("<I", payload, start + 8, len(payload) - modules_size + 1)
    assert modules_offset + modules_size <= len(payload)   # the fixture was sane

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(fixtures._section_bytes(bytes(payload)))

    assert "runs past the end" in capsys.readouterr().err


def test_module_table_ending_exactly_at_the_payload_end_is_accepted(
        extract_bun):
    """The other side of the same boundary, so the check cannot be tightened
    into rejecting a legitimate graph whose table runs right up to the end."""
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    modules_size = struct.unpack_from("<I", payload, start + 12)[0]
    struct.pack_into("<I", payload, start + 8, len(payload) - modules_size)

    parsed, mod_off, mod_size, entry = extract_bun.parse_payload(
        fixtures._section_bytes(bytes(payload)))

    assert mod_off + mod_size == len(parsed)


def test_asset_name_containing_a_nul_byte_is_rejected_cleanly(
        extract_bun, tmp_path, capsys):
    """A NUL survives the basename reduction (it is not '/', '\\\\', '', '.' or
    '..') and reaches open(), which raises ValueError("embedded null byte").

    Not a traversal - Python refuses the name before any syscall - but it was
    an uncaught exception, and it fired AFTER the entry module had been
    written, so the run left a half-populated out-dir with a traceback on
    stderr. Reproduced against the unfixed tool with the name 'evil\\x00.node'.
    """
    payload = fixtures.build_payload([
        ("/$bunfs/root/cli", b"(function(){})", 1),
        ("evil\x00.node", b"addon", 10),
    ])
    binary = tmp_path / "claude"
    binary.write_bytes(fixtures.build_elf(payload))
    out = tmp_path / "out"

    with pytest.raises(SystemExit):
        extract_bun.extract(str(binary), str(out))

    err = capsys.readouterr().err
    assert "error:" in err
    assert "unsafe basename" in err
    assert not (out / "assets").exists()


def test_payload_is_delimited_by_its_length_prefix_not_the_section_size(
        extract_bun):
    """A section may be longer than the graph it carries; the u64 prefix is
    what says where the payload ends.

    Every other fixture makes the section exactly 8 + payload_size bytes, so
    the slice could take one byte too many or too few and stay invisible. With
    7 bytes of padding after the payload, a slice of 8+payload_size+1 puts a
    stray 0xAB after the trailer and the trailer check fails instead.
    """
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    section = fixtures._section_bytes(payload, pad=7)
    assert len(section) == 8 + len(payload) + 7

    parsed, _, _, _ = extract_bun.parse_payload(section)

    assert parsed == payload


def test_extraction_ignores_padding_after_the_payload(extract_bun, tmp_path):
    """The same property through the container path, where the section bytes
    come from sh_size rather than from a test calling _section_bytes()."""
    payload = fixtures.build_payload([
        ("/$bunfs/root/cli", b"(function(){})", 1),
        ("/$bunfs/root/addon.node", b"native-bytes", 10),
    ])
    binary = tmp_path / "claude"
    binary.write_bytes(fixtures.build_elf(payload, section_pad=16))
    out = tmp_path / "out"

    extract_bun.extract(str(binary), str(out))

    assert (out / "cli.original.js").read_bytes() == b"(function(){})"
    assert (out / "assets" / "addon.node").read_bytes() == b"native-bytes"


def test_truncated_elf_header_is_rejected_cleanly(extract_bun, capsys):
    """A 32-byte file has a valid-looking class/endianness byte pair but is far
    too short to hold e_shoff/e_shnum/e_shentsize/e_shstrndx (need 64 bytes).
    Before the fix this raised an uncaught struct.error instead of die()."""
    buf = bytes([0x7F, 0x45, 0x4C, 0x46, 2, 1]) + b"\x00" * 26
    assert len(buf) == 32

    with pytest.raises(SystemExit):
        extract_bun.find_bun_section_elf(buf)

    assert "truncated" in capsys.readouterr().err


def test_bogus_section_header_offset_is_rejected_cleanly(extract_bun, capsys):
    """A corrupt/hostile e_shoff that points off the end of the file (here
    2**40) must not reach struct.unpack_from and blow up with struct.error."""
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = bytearray(fixtures.build_elf(payload))
    struct.pack_into("<Q", blob, 0x28, 2**40)   # e_shoff

    with pytest.raises(SystemExit):
        extract_bun.find_bun_section_elf(bytes(blob))

    assert "section header" in capsys.readouterr().err.lower()


@pytest.mark.parametrize("bad_name", ["", ".", "..", "sub/.."])
def test_module_name_reducing_to_unsafe_basename_is_rejected_cleanly(
        extract_bun, tmp_path, capsys, bad_name):
    """A module name whose basename reduces to '', '.' or '..' would make
    extract() try to open assets/<basename> as a file, which IS the assets
    directory (or its parent) - an uncaught IsADirectoryError before the fix.
    The existing `base = name.replace('\\\\','/').split('/')[-1]` reduction
    already prevents path traversal; this only concerns failing cleanly."""
    payload = fixtures.build_payload([
        ("/$bunfs/root/cli", b"(function(){})", 1),
        (bad_name, b"data", 5),   # loader 5 = file, goes through the assets path
    ])
    blob = fixtures.build_elf(payload)
    binary = tmp_path / "claude"
    binary.write_bytes(blob)
    out = tmp_path / "out"

    with pytest.raises(SystemExit):
        extract_bun.extract(str(binary), str(out))

    err = capsys.readouterr().err
    assert "error:" in err
    assert "unsafe basename" in err


# --- loader ids must match Bun's own enum ------------------------------------
#
# src/bundler/options.zig at tag bun-v1.3.14:
#   jsx=0 js=1 ts=2 tsx=3 css=4 file=5 json=6 jsonc=7 toml=8 wasm=9 napi=10
#   base64=11 dataurl=12 text=13 bunsh=14 sqlite=15 sqlite_embedded=16
#   html=17 yaml=18 json5=19 md=20

def test_loader_ids_match_bun_1_3_14(extract_bun):
    assert extract_bun.LOADERS[7] == "jsonc"
    assert extract_bun.LOADERS[9] == "wasm"
    assert extract_bun.LOADERS[10] == "napi"     # what the real .node addons are
    assert extract_bun.LOADERS[11] == "base64"
    assert extract_bun.LOADERS[15] == "sqlite"
    assert extract_bun.LOADERS[20] == "md"


def _extract_one(extract_bun, tmp_path, name, content, loader_id):
    payload = fixtures.build_payload([
        ("/$bunfs/root/cli", b"(function(){})", 1),
        ("/$bunfs/root/" + name, content, loader_id),
    ])
    binary = tmp_path / "claude"
    binary.write_bytes(fixtures.build_elf(payload))
    out = tmp_path / "out"
    extract_bun.extract(str(binary), str(out))
    return out / "assets" / name


def test_genuine_base64_module_is_written_to_disk(extract_bun, tmp_path):
    """The latent bug the off-by-one enum hid: a real base64 module carries
    byte 11, which the old table labelled "dataurl" - not in the accept-set, so
    it fell through the else and was SILENTLY DROPPED. Nothing on either shipped
    binary uses loader 11 today, so no existing test could catch it."""
    blob = b"\x00\x01\x02payload-bytes\xff"

    dest = _extract_one(extract_bun, tmp_path, "thing.bin", blob, 11)

    assert dest.is_file(), "genuine base64 module was dropped"
    assert dest.read_bytes() == blob



# --- basename collisions and unknown loaders ---------------------------------
#
# Both are version-drift blind spots: they cost nothing today (no shipped
# binary has either) and they cost an asset, silently, the day a Claude release
# introduces one.

def _elf_with(tmp_path, modules):
    payload = fixtures.build_payload(
        [("/$bunfs/root/cli", b"(function(){})", 1)] + modules)
    binary = tmp_path / "claude"
    binary.write_bytes(fixtures.build_elf(payload))
    return binary


def test_colliding_basenames_with_different_content_are_fatal(
        extract_bun, tmp_path, capsys):
    """Two modules in different directories share a basename, so both want the
    same assets/<base>. The second used to overwrite the first in silence and
    exit 0. postprocess.py cannot disambiguate them either - BUNFS_LITERAL only
    matches basenames directly under /$bunfs/root/ - so one asset is simply
    lost."""
    binary = _elf_with(tmp_path, [
        ("/$bunfs/root/a/thing.node", b"first-copy", 10),
        ("/$bunfs/root/b/thing.node", b"second-copy", 10),
    ])

    with pytest.raises(SystemExit):
        extract_bun.extract(str(binary), str(tmp_path / "out"))

    err = capsys.readouterr().err
    assert "collides" in err
    assert "thing.node" in err


def test_colliding_basenames_with_identical_content_are_kept(
        extract_bun, tmp_path, capsys):
    """The benign half of the same case: the same bytes embedded twice. There
    is nothing to lose, so keep one copy and say so rather than failing a
    build over it."""
    binary = _elf_with(tmp_path, [
        ("/$bunfs/root/a/thing.node", b"same-bytes", 10),
        ("/$bunfs/root/b/thing.node", b"same-bytes", 10),
    ])
    out = tmp_path / "out"

    extract_bun.extract(str(binary), str(out))

    assert (out / "assets" / "thing.node").read_bytes() == b"same-bytes"
    assert "duplicates" in capsys.readouterr().out


def test_an_unknown_loader_id_is_reported_not_silently_dropped(
        extract_bun, tmp_path, capsys):
    """An id past the end of Bun's enum means the enum has drifted. The module
    is dropped either way, but a drop nobody is told about is how the previous
    off-by-one enum hid for as long as it did."""
    binary = _elf_with(tmp_path, [("/$bunfs/root/mystery.dat", b"bytes", 200)])

    extract_bun.extract(str(binary), str(tmp_path / "out"))

    err = capsys.readouterr().err
    assert "unknown(200)" in err
    assert "DROPPED" in err


def test_a_known_but_unextracted_binary_loader_is_reported(
        extract_bun, tmp_path, capsys):
    """wasm(9) is the live example: the corrected enum moved byte 9 from the
    accept-set to the drop path (it was mislabelled `napi` before). Neither
    shipped binary carries one, so if a future release does, this warning plus
    postprocess.py's referenced-but-never-extracted check are what stand
    between it and a silently asset-less build."""
    binary = _elf_with(tmp_path, [("/$bunfs/root/mod.wasm", b"\x00asm", 9)])

    extract_bun.extract(str(binary), str(tmp_path / "out"))

    err = capsys.readouterr().err
    assert "wasm" in err
    assert "DROPPED" in err


def test_javascript_shims_are_dropped_without_any_warning(
        extract_bun, tmp_path, capsys):
    """The routine case must stay silent, or the warning above is noise: the
    real binaries drop 2 js-loader shims whose contents the bundler has already
    inlined into the entry module."""
    binary = _elf_with(tmp_path, [("/$bunfs/root/image-processor.js", b"//x", 1)])

    extract_bun.extract(str(binary), str(tmp_path / "out"))

    assert capsys.readouterr().err == ""
