import struct
import time

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


# --- Mach-O fixture fidelity -------------------------------------------------
#
# The real darwin-arm64 binary has 21 load commands with __BUN at #5, behind
# four commands of DIFFERENT sizes, and its __TEXT segment carries 11 sections.
# The default fixture has exactly one of each, so neither the variable-size
# command walk nor the 80-byte section stride was ever exercised: a parser that
# ignored cmdsize, or one that used the wrong section stride, passed anyway.

def test_bun_section_is_found_behind_variable_size_load_commands(extract_bun):
    """cmdsize must actually drive the walk. With four decoys of sizes
    72/952/232/712 - the real binary's - a fixed stride lands mid-command."""
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"console.log(1)", 1)])
    blob = fixtures.build_macho(
        payload, decoy_commands=fixtures.REAL_MACHO_DECOY_CMDSIZES)

    offset, size = extract_bun.find_bun_section(blob)

    assert blob[offset:offset + 8] == len(payload).to_bytes(8, "little")
    assert blob[offset + 8:offset + 8 + len(payload)].endswith(fixtures.TRAILER)


def test_bun_section_is_found_behind_decoy_sections(extract_bun):
    """sizeof(section_64) is 80. With __bun as the 11th record - the depth
    __TEXT reaches on the real binary - any other stride misses it."""
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"console.log(1)", 1)])
    blob = fixtures.build_macho(payload, decoy_sections=10)

    offset, size = extract_bun.find_bun_section(blob)

    assert blob[offset:offset + 8] == len(payload).to_bytes(8, "little")
    assert blob[offset + 8:offset + 8 + len(payload)].endswith(fixtures.TRAILER)


def test_bun_section_is_found_with_both_kinds_of_decoy(extract_bun):
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"console.log(1)", 1)])
    blob = fixtures.build_macho(
        payload, decoy_commands=fixtures.REAL_MACHO_DECOY_CMDSIZES,
        decoy_sections=10)

    offset, size = extract_bun.find_bun_section(blob)

    assert blob[offset:offset + 8] == len(payload).to_bytes(8, "little")


# --- Mach-O bounds checks ----------------------------------------------------
#
# Every count and offset in a Mach-O header is attacker-controlled. The ELF
# path has rejected out-of-range values since it was written; this one did not,
# and two shapes turned a malformed input into a ~10-and-~17-minute wait:
# cmdsize=0 (the walk stops advancing and re-reads the same bytes ncmds times)
# and a huge nsects with no __bun (slicing past EOF yields b"" with no error).
# Counts here are kept small so the tests stay fast; what is asserted is the
# specific guard that fired, since an unguarded parser reaches the same generic
# "no __BUN,__bun section" death, just very much later.

def _macho_with(payload, ncmds=None, cmdsize=None, nsects=None, rename_bun=False):
    blob = bytearray(fixtures.build_macho(payload))
    if ncmds is not None:
        struct.pack_into("<I", blob, 16, ncmds)
    if cmdsize is not None:
        struct.pack_into("<I", blob, 36, cmdsize)     # first command's cmdsize
    if nsects is not None:
        struct.pack_into("<I", blob, 32 + 0x40, nsects)
    if rename_bun:
        blob[32 + 0x48:32 + 0x48 + 16] = b"__nope" + b"\0" * 10
    return bytes(blob)


def test_macho_zero_cmdsize_cannot_stall_the_load_command_walk(extract_bun, capsys):
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = _macho_with(payload, ncmds=5000, cmdsize=0)

    started = time.monotonic()
    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(bytes(blob))

    assert "cmdsize=0" in capsys.readouterr().err
    assert time.monotonic() - started < 2.0


def test_macho_absurd_ncmds_is_refused_immediately(extract_bun, capsys):
    """ncmds is a u32. Walking 2**32-1 commands took ~10 minutes measured;
    it must now cost nothing."""
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = _macho_with(payload, ncmds=2**32 - 1, cmdsize=0)

    started = time.monotonic()
    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(bytes(blob))

    assert "load command" in capsys.readouterr().err
    assert time.monotonic() - started < 2.0


def test_macho_absurd_nsects_is_refused_immediately(extract_bun, capsys):
    """The other DoS shape: a __BUN segment claiming more sections than the
    file could hold. buf[s:s+16] past EOF returns b"" instead of raising, so
    the loop ran to completion - ~17 minutes at u32 scale."""
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = _macho_with(payload, nsects=2**32 - 1, rename_bun=True)

    started = time.monotonic()
    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(bytes(blob))

    assert "sections" in capsys.readouterr().err
    assert time.monotonic() - started < 2.0


def test_macho_section_table_running_past_its_command_is_rejected(extract_bun, capsys):
    """nsects below the sanity cap, but more records than the command holds,
    and no __bun among them - so the walk runs off the end of its own command
    and into the payload. Slicing past EOF returns b"" rather than raising,
    which is what let the unbounded version keep going."""
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = _macho_with(payload, nsects=64, rename_bun=True)

    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(bytes(blob))

    assert "past the end" in capsys.readouterr().err


def test_macho_load_command_running_past_eof_is_rejected(extract_bun, capsys):
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = _macho_with(payload, ncmds=2, cmdsize=2**31)

    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(bytes(blob))

    assert "past end of file" in capsys.readouterr().err


def test_truncated_macho_header_is_rejected_cleanly(extract_bun, capsys):
    """Only the magic, nothing else. Raised a raw struct.error before."""
    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(b"\xcf\xfa\xed\xfe" + b"\x00" * 8)

    assert "truncated" in capsys.readouterr().err


# --- die() at the outer boundaries -------------------------------------------
#
# The tools' contract is that malformed input exits with `error: ...`, never a
# traceback. These are the entry points where that was still not true.

def test_empty_input_is_rejected_cleanly(extract_bun, capsys):
    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(b"")

    assert "error:" in capsys.readouterr().err


def test_tiny_input_is_rejected_cleanly(extract_bun, capsys):
    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(b"\x7fEL")

    assert "too short" in capsys.readouterr().err


def test_stub_section_is_rejected_cleanly(extract_bun, capsys):
    """parse_payload on four zero bytes: not even a u64 length prefix."""
    with pytest.raises(SystemExit):
        extract_bun.parse_payload(b"\0" * 4)

    assert "too short" in capsys.readouterr().err


def test_payload_shorter_than_its_own_footer_is_rejected_cleanly(extract_bun, capsys):
    """A length prefix that is well-formed but describes a payload too small to
    hold the offset struct and trailer it is supposed to end with."""
    section = struct.pack("<Q", 4) + b"\0" * 60

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(section)

    assert "too short" in capsys.readouterr().err


def test_elf_string_table_index_out_of_range_is_rejected_cleanly(extract_bun, capsys):
    """e_shstrndx indexes the section table before any loop bound applies to
    it - the one field the earlier bounds check did not cover."""
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = bytearray(fixtures.build_elf(payload))
    struct.pack_into("<H", blob, 0x3E, 999)   # e_shstrndx, only 3 sections

    with pytest.raises(SystemExit):
        extract_bun.find_bun_section_elf(bytes(blob))

    assert "out of range" in capsys.readouterr().err


def test_empty_module_table_is_rejected_with_its_own_message(extract_bun, capsys):
    """count == 0 is reachable and dies BEFORE the entry-point range check, so
    the message a user sees is 'modules table is empty' rather than the far
    more confusing 'entry point id 0 out of range (only 0 modules)'. Pinning
    the message is what keeps that branch from being deleted as redundant."""
    payload = fixtures.build_payload([])

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(fixtures._section_bytes(payload))

    assert "modules table is empty" in capsys.readouterr().err
