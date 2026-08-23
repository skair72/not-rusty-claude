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


def test_macho_absurd_ncmds_is_refused_before_the_walk_begins(extract_bun, capsys):
    """The sanity cap, on its own.

    This test used to build its fixture with cmdsize=0 as well, so the
    cmdsize guard fired first and the cap was never reached - and it asserted
    only "load command", a substring BOTH messages contain, so deleting
    MAX_LOAD_COMMANDS entirely left it green. Every load command here is
    well-formed; the only thing wrong with the file is a header claiming more
    commands than any Mach-O has, and the message asserted below is produced by
    nothing else.

    The cap is load-bearing, not belt-and-braces: with well-formed 8-byte
    commands the walk is bounded by file size, so a crafted ~126 MB file costs
    ~4 s without the cap and 0 s with it, scaling linearly with size. A fixture
    large enough to show that would be a slow test; asserting the guard that
    prevents it is the same property, cheaply.
    """
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = _macho_with(payload, ncmds=2**32 - 1, rename_bun=True)

    started = time.monotonic()
    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(bytes(blob))

    assert "load commands (max" in capsys.readouterr().err
    assert time.monotonic() - started < 2.0


def test_macho_load_command_table_ending_mid_command_is_rejected_cleanly(
        extract_bun, capsys):
    """ncmds promises a command the file does not contain even the header of.

    Not the same as the cmdsize/EOF checks below: this one fires before the
    8-byte cmd/cmdsize pair is read at all. Without it, struct.unpack_from
    raises a raw struct.error - the exact class of failure this tool's contract
    says it does not have.
    """
    blob = bytearray(32 + 4)
    struct.pack_into("<I", blob, 0, 0xFEEDFACF)   # MH_MAGIC_64
    struct.pack_into("<I", blob, 16, 1)           # ncmds: one command...
    # ...of which only 4 bytes exist.

    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(bytes(blob))

    err = capsys.readouterr().err
    assert "past end of file" in err
    assert "error:" in err


def test_macho_segment_command_too_short_to_hold_a_section_table_is_skipped(
        extract_bun, capsys):
    """An LC_SEGMENT_64 whose cmdsize is smaller than segment_command_64 itself.

    nsects lives at +0x40 and the section records at +0x48, so reading them out
    of a 24-byte command means reading whatever follows it - or past EOF, which
    is what happens here: without the `cmdsize >= SEGMENT_64_HEADER` guard this
    input raises struct.error at offset 96 on a 56-byte buffer. With it, the
    malformed command is skipped and the file is refused for what it actually
    is: a Mach-O with no usable __BUN section.
    """
    blob = bytearray(32 + 24)
    struct.pack_into("<I", blob, 0, 0xFEEDFACF)
    struct.pack_into("<I", blob, 16, 1)                  # ncmds
    struct.pack_into("<II", blob, 32, 0x19, 24)          # LC_SEGMENT_64, cmdsize=24
    blob[32 + 8:32 + 13] = b"__BUN"                      # segname says __BUN

    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(bytes(blob))

    assert "no __BUN,__bun section" in capsys.readouterr().err


def test_macho_section_walk_stays_inside_its_own_load_command(extract_bun, capsys):
    """The section bound must be the COMMAND's end, not the file's.

    Both bounds refuse the same malformed inputs with the same message, so no
    assertion on an error can tell them apart. This input distinguishes them by
    what a too-loose bound would *accept*: a section_64 record planted in the
    bytes after the load command, which a file-bounded walk reads and believes
    - returning an attacker-chosen offset and size - and a command-bounded walk
    never reaches.
    """
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = bytearray(_macho_with(payload, nsects=2, rename_bun=True))
    cmd_end = 32 + 0x48 + 80          # the one section_64 record the command holds
    planted = bytearray(80)
    planted[0:5] = b"__bun"                                   # sectname
    planted[16:21] = b"__BUN"                                 # segname
    struct.pack_into("<Q", planted, 0x28, 0xDEAD)             # size
    struct.pack_into("<I", planted, 0x30, 0xBEEF)             # offset
    blob[cmd_end:cmd_end] = planted

    with pytest.raises(SystemExit):
        extract_bun.find_bun_section(bytes(blob))

    assert "past the end" in capsys.readouterr().err


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


def test_elf_section_header_entry_too_small_is_rejected_cleanly(extract_bun, capsys):
    """e_shentsize below the 40 bytes shdr() unpacks.

    The table-extends-past-EOF check multiplies e_shnum by e_shentsize, so a
    small enough entry size satisfies it while each individual read still runs
    off the end: here the table is declared to end exactly at EOF, and reading
    one 40-byte header at its start already goes past. Raw struct.error without
    this guard.
    """
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)])
    blob = bytearray(fixtures.build_elf(payload))
    struct.pack_into("<Q", blob, 0x28, len(blob) - 8)   # e_shoff: 8 bytes from EOF
    struct.pack_into("<H", blob, 0x3A, 8)               # e_shentsize
    struct.pack_into("<H", blob, 0x3C, 1)               # e_shnum
    struct.pack_into("<H", blob, 0x3E, 0)               # e_shstrndx

    with pytest.raises(SystemExit):
        extract_bun.find_bun_section_elf(bytes(blob))

    assert "too small" in capsys.readouterr().err


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
