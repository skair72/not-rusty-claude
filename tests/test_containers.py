import pytest
import fixtures


def test_macho_fixture_is_located_by_existing_extractor(extract_bun):
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"console.log(1)", 1)])
    blob = fixtures.build_macho(payload)

    offset, size = extract_bun.find_bun_section(blob)

    assert blob[offset:offset + 8] == len(payload).to_bytes(8, "little")
    assert blob[offset + 8:offset + 8 + size - 8].endswith(fixtures.TRAILER)
