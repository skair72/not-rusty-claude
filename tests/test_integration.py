"""Tests against real 300 MB Claude binaries. Auto-skipped when absent.

Counts are facts measured on 2026-08-22, not estimates:
  linux-x64  2.1.222  8 modules, 5 /$bunfs/ literals, 7 file:// leaks
  darwin-arm64 2.1.239 15 modules, 9 /$bunfs/ literals, 8 file:// leaks
Version drift will change them; a mismatch means re-measure and update, which
is the early warning this suite exists to give.
"""

import pytest

pytestmark = pytest.mark.integration


def _entry_source(extract_bun, path):
    with open(path, "rb") as fh:
        buf = fh.read()
    off, size = extract_bun.find_bun_section(buf)
    payload, mod_off, mod_size, entry = extract_bun.parse_payload(buf[off:off + size])
    import struct
    rec = payload[mod_off + entry * 52:mod_off + (entry + 1) * 52]
    _, _, content_off, content_size = struct.unpack_from("<IIII", rec, 0)
    return payload[content_off:content_off + content_size].decode("utf-8", "replace")


def test_real_elf_binary_extracts(extract_bun, real_elf_binary, tmp_path):
    out = tmp_path / "x"
    extract_bun.extract(real_elf_binary, str(out))

    assert (out / "cli.original.js").stat().st_size > 10_000_000
    assets = sorted(p.name for p in (out / "assets").iterdir())
    assert "image-processor.node" in assets
    assert "mermaid.min.js" in assets
    # findings 5a: base64-loader addons are stored raw, so this must be an ELF
    assert (out / "assets" / "image-processor.node").read_bytes()[:4] == b"\x7fELF"


def test_real_elf_transforms_leave_no_bunfs_references(extract_bun, postprocess,
                                                       real_elf_binary):
    code = _entry_source(extract_bun, real_elf_binary)

    out, counts = postprocess.transform(code)

    assert counts["pragma"] == 1
    assert counts["iife"] == 1
    assert counts["assets"] == 5
    assert counts["file_urls"] == 7
    assert counts["leftovers"] == []
    assert postprocess.check(out, counts) == []


def test_real_macho_binary_extracts(extract_bun, real_macho_binary, tmp_path):
    out = tmp_path / "x"
    extract_bun.extract(real_macho_binary, str(out))

    assets = sorted(p.name for p in (out / "assets").iterdir())
    assert "computer-use-swift.node" in assets
    assert "payload.template.html.asset" in assets
    # universal Mach-O magic (0xCAFEBABE big-endian) or thin arm64 (0xFEEDFACF LE)
    head = (out / "assets" / "computer-use-swift.node").read_bytes()[:4]
    assert head in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe")


def test_real_macho_transforms_leave_no_bunfs_references(extract_bun, postprocess,
                                                         real_macho_binary):
    code = _entry_source(extract_bun, real_macho_binary)

    out, counts = postprocess.transform(code)

    assert counts["assets"] == 9
    assert counts["file_urls"] == 8
    assert counts["leftovers"] == []
    assert postprocess.check(out, counts) == []
