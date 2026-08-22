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


def test_base64_loader_content_is_stored_raw_not_encoded(extract_bun, tmp_path):
    """findings.md 5a: the base64 loader labels how Bun exposes an asset to JS,
    not how it is stored. Decoding corrupts it (once produced 71-byte modules)."""
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


def test_misaligned_module_table_is_rejected(extract_bun, capsys):
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    struct.pack_into("<I", payload, start + 12, 53)   # modules_size not a multiple of 52

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(fixtures._section_bytes(bytes(payload)))

    assert "not a multiple" in capsys.readouterr().err


def test_entry_point_id_out_of_range_is_rejected(extract_bun, capsys):
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    struct.pack_into("<I", payload, start + 16, 99)

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(fixtures._section_bytes(bytes(payload)))

    assert "entry" in capsys.readouterr().err


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
