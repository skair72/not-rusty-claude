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

