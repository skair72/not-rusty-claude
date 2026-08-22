import pytest
import fixtures


def test_macho_fixture_is_located_by_existing_extractor(extract_bun):
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"console.log(1)", 1)])
    blob = fixtures.build_macho(payload)

    offset, size = extract_bun.find_bun_section(blob)

    assert blob[offset:offset + 8] == len(payload).to_bytes(8, "little")
    assert blob[offset + 8:offset + 8 + size - 8].endswith(fixtures.TRAILER)


def test_elf_bun_section_is_located(extract_bun):
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"console.log(1)", 1)])
    blob = fixtures.build_elf(payload)

    offset, size = extract_bun.find_bun_section(blob)

    assert blob[offset:offset + 8] == len(payload).to_bytes(8, "little")
    assert blob[offset + 8:offset + 8 + len(payload)].endswith(fixtures.TRAILER)


def test_pe_input_is_refused_with_a_clear_message(extract_bun, capsys):
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = fixtures.build_pe(payload)

    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(blob)

    err = capsys.readouterr().err
    assert "PE" in err
    assert "not supported" in err


def test_unknown_container_magic_is_refused(extract_bun, capsys):
    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(b"\x00\x01\x02\x03" + b"\x00" * 64)

    assert "unrecognized" in capsys.readouterr().err
