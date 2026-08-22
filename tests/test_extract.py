import struct
import pytest
import fixtures


def _section(payload):
    return struct.pack("<Q", len(payload)) + payload


def test_payload_round_trips_names_and_contents(extract_bun):
    modules = [
        ("/$bunfs/root/cli", b"(function(){})", 1),
        ("/$bunfs/root/thing.node", b"\x7fELF\x02\x01raw", 10),
    ]
    payload = fixtures.build_payload(modules)

    parsed, mod_off, mod_size, entry = extract_bun.parse_payload(_section(payload))

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
        extract_bun.parse_payload(_section(bytes(payload)))

    assert "trailer" in capsys.readouterr().err


def test_misaligned_module_table_is_rejected(extract_bun, capsys):
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    struct.pack_into("<I", payload, start + 12, 53)   # modules_size not a multiple of 52

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(_section(bytes(payload)))

    assert "not a multiple" in capsys.readouterr().err


def test_entry_point_id_out_of_range_is_rejected(extract_bun, capsys):
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    struct.pack_into("<I", payload, start + 16, 99)

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(_section(bytes(payload)))

    assert "entry" in capsys.readouterr().err
