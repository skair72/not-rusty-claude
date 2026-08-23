"""Tests against real 300 MB Claude binaries. Auto-skipped when absent.

Set NRC_TEST_ELF / NRC_TEST_MACHO to point at them; see tests/conftest.py for
the defaults and docs/runbook.md for where to get a Mach-O one. Without them a
clean host runs the hermetic suite only.

Two kinds of assertion live here and they mean opposite things, so they are
deliberately kept in separate tests rather than interleaved:

  INVARIANTS  - properties of the TOOLS. The pragma is stripped exactly once,
                exactly one IIFE is invoked, no /$bunfs/ reference survives,
                check() is clean. A failure means this repo is broken.
  MEASUREMENTS- facts about a PARTICULAR Claude build: how many /$bunfs/
                literals and build-time file:// URLs its entry module happens
                to contain. A failure means Claude changed. That is the early
                warning this file exists to give, and the numbers are meant to
                be updated when it fires.

Measured on 2026-08-22 against the binaries named below.
"""

import struct

import pytest

pytestmark = pytest.mark.integration

# Counts that are properties of the Claude release, not of the tools.
MEASURED = {
    "elf": {"version": "linux-x64 2.1.222", "assets": 5, "file_urls": 7},
    "macho": {"version": "darwin-arm64 2.1.239", "assets": 9, "file_urls": 8},
}


def _entry_source(extract_bun, path):
    with open(path, "rb") as fh:
        buf = fh.read()
    off, size = extract_bun.find_bun_section(buf)
    payload, mod_off, mod_size, entry = extract_bun.parse_payload(buf[off:off + size])
    size_of = extract_bun.MODULE_RECORD_SIZE
    rec = payload[mod_off + entry * size_of:mod_off + (entry + 1) * size_of]
    _, _, content_off, content_size = struct.unpack_from("<IIII", rec, 0)
    return payload[content_off:content_off + content_size].decode("utf-8", "replace")


def _assert_invariants(postprocess, code):
    out, counts = postprocess.transform(code)

    assert counts["pragma"] == 1, "the `// @bun` pragma block was not stripped"
    assert counts["iife"] == 1, "the trailing IIFE was not invoked"
    assert counts["leftovers"] == [], "a /$bunfs/ reference survived the rewrite"
    assert postprocess.check(out, counts) == []
    return counts


def _assert_no_drift(counts, key, binary):
    """Report EVERY drifted measurement at once, with what to do about it.

    Asserting them one at a time short-circuits: a Claude release that shifts
    both counts shows only the first, so the maintainer fixes one number, re-runs,
    and is told about the next one.
    """
    expected = MEASURED[key]
    drifted = {name: (want, counts[name])
               for name, want in expected.items()
               if name != "version" and counts[name] != want}
    if not drifted:
        return
    lines = ["    %-10s expected %s, measured %s" % (name, want, got)
             for name, (want, got) in sorted(drifted.items())]
    raise AssertionError(
        "%d measured count(s) changed for %s.\n"
        "%s\n"
        "\n"
        "This does NOT mean the tools are broken - the invariants "
        "(pragma/IIFE/leftovers/check) are asserted separately and passed. It "
        "means the Claude build changed, which is what this tripwire is for.\n"
        "To clear it: confirm the new artifact still runs (docs/runbook.md's "
        "smoke test), then update MEASURED[%r] in this file to the measured "
        "values, and the counts in docs/status.md's verification matrix.\n"
        "Binary under test: %s"
        % (len(drifted), expected["version"], "\n".join(lines), key, binary))


def test_real_elf_binary_extracts(extract_bun, real_elf_binary, tmp_path):
    out = tmp_path / "x"
    extract_bun.extract(real_elf_binary, str(out))

    assert (out / "cli.original.js").stat().st_size > 10_000_000
    assets = sorted(p.name for p in (out / "assets").iterdir())
    assert "image-processor.node" in assets
    assert "mermaid.min.js" in assets
    # findings 5a: stored content is ALWAYS raw bytes, whatever the loader id
    # says (these addons are napi, id 10) - so this must be a real ELF
    assert (out / "assets" / "image-processor.node").read_bytes()[:4] == b"\x7fELF"


def test_real_elf_transform_invariants_hold(extract_bun, postprocess, real_elf_binary):
    _assert_invariants(postprocess, _entry_source(extract_bun, real_elf_binary))


def test_real_elf_measured_counts_have_not_drifted(extract_bun, postprocess,
                                                   real_elf_binary):
    counts = _assert_invariants(postprocess, _entry_source(extract_bun, real_elf_binary))

    _assert_no_drift(counts, "elf", real_elf_binary)


def test_real_macho_binary_extracts(extract_bun, real_macho_binary, tmp_path):
    out = tmp_path / "x"
    extract_bun.extract(real_macho_binary, str(out))

    assets = sorted(p.name for p in (out / "assets").iterdir())
    assert "computer-use-swift.node" in assets
    assert "payload.template.html.asset" in assets
    # universal Mach-O magic (0xCAFEBABE big-endian) or thin arm64 (0xFEEDFACF LE)
    head = (out / "assets" / "computer-use-swift.node").read_bytes()[:4]
    assert head in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe")


def test_real_macho_transform_invariants_hold(extract_bun, postprocess,
                                              real_macho_binary):
    _assert_invariants(postprocess, _entry_source(extract_bun, real_macho_binary))


def test_real_macho_measured_counts_have_not_drifted(extract_bun, postprocess,
                                                     real_macho_binary):
    counts = _assert_invariants(postprocess,
                                _entry_source(extract_bun, real_macho_binary))

    _assert_no_drift(counts, "macho", real_macho_binary)
