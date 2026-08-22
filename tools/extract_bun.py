#!/usr/bin/env /usr/bin/python3
"""
extract_bun.py - pull cli.js + native modules and assets out of a Bun standalone
executable (e.g. the native Claude Code binary), so the JS can be run under a
stock external Bun instead of the signed binary's embedded runtime.

✅ VERIFIED on ~/.local/share/claude/versions/2.1.238 (arm64, macOS 24.6.0):
   extracts cli.original.js (26.8 MB) + 9 assets; all 5 .node come out as real
   Mach-O dylibs. Module names/loaders can change between Claude versions, so
   re-confirm per docs/status.md work item #1 on a new binary.

Only reads the binary; never modifies or signs it. Runs on the stock
/usr/bin/python3 (3.9+) with no node/bun needed.

Format (see docs/bun-section-format.md): the Bun standalone embeds a serialized
module graph in a platform section (Mach-O __BUN,__bun; ELF/PE .bun), ending
with the trailer magic '\\n---- Bun! ----\\n'. This tool implements the Mach-O
and ELF cases, and refuses PE. Entry module -> cli.original.js; napi/base64/file
modules -> assets/<name> written as RAW bytes (the 'base64' loader labels how
Bun exposes the asset to JS, NOT how it is stored — do not decode).

Usage:
  ./extract_bun.py <binary> <out-dir>
"""

import os
import struct
import sys

TRAILER = b"\n---- Bun! ----\n"
OFFSET_STRUCT_SIZE = 32
MODULE_RECORD_SIZE = 52
LOADERS = {0: "jsx", 1: "js", 2: "ts", 3: "tsx", 4: "css", 5: "file",
           6: "json", 7: "toml", 8: "wasm", 9: "napi", 10: "base64",
           11: "dataurl", 12: "text", 13: "bunsh", 14: "sqlite"}

MH_MAGIC_64 = 0xFEEDFACF
ELF_MAGIC_LE = 0x464C457F
PE_MAGIC = b"MZ"


def die(msg):
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(1)


def find_bun_section_macho(buf):
    """Return (raw_offset, raw_size) of the __BUN,__bun section."""
    # mach_header_64: magic(4) cputype(4) cpusubtype(4) filetype(4)
    #                 ncmds(4) sizeofcmds(4) flags(4) reserved(4)
    ncmds = struct.unpack_from("<I", buf, 16)[0]
    off = 32  # load commands begin after the 32-byte header
    LC_SEGMENT_64 = 0x19
    for _ in range(ncmds):
        cmd, cmdsize = struct.unpack_from("<II", buf, off)
        if cmd == LC_SEGMENT_64:
            segname = buf[off + 8:off + 24].split(b"\0", 1)[0].decode("ascii", "replace")
            if segname == "__BUN":
                nsects = struct.unpack_from("<I", buf, off + 0x40)[0]
                s = off + 0x48  # first section_64 record
                for _ in range(nsects):
                    sectname = buf[s:s + 16].split(b"\0", 1)[0].decode("ascii", "replace")
                    if sectname == "__bun":
                        size = struct.unpack_from("<Q", buf, s + 0x28)[0]
                        offset = struct.unpack_from("<I", buf, s + 0x30)[0]
                        return offset, size
                    s += 80  # sizeof(section_64)
        off += cmdsize
    die("Mach-O has no __BUN,__bun section")


def find_bun_section_elf(buf):
    """Return (raw_offset, raw_size) of the .bun section in an ELF64 image.

    The container differs from Mach-O but the payload it wraps is byte-identical
    (see docs/bun-section-format.md): a u64 length prefix, the module graph, and
    the trailer magic.
    """
    if buf[4] != 2 or buf[5] != 1:
        die("only 64-bit little-endian ELF is supported")
    e_shoff = struct.unpack_from("<Q", buf, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", buf, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", buf, 0x3C)[0]
    e_shstrndx = struct.unpack_from("<H", buf, 0x3E)[0]
    if e_shoff == 0 or e_shnum == 0:
        die("ELF has no section headers (stripped?) - cannot locate .bun")

    def shdr(i):
        off = e_shoff + i * e_shentsize
        # sh_name u32, sh_type u32, sh_flags u64, sh_addr u64,
        # sh_offset u64, sh_size u64
        return struct.unpack_from("<IIQQQQ", buf, off)

    _, _, _, _, str_off, str_size = shdr(e_shstrndx)
    strtab = buf[str_off:str_off + str_size]
    for i in range(e_shnum):
        name_off, _, _, _, off, size = shdr(i)
        end = strtab.find(b"\0", name_off)
        if strtab[name_off:end] == b".bun":
            return off, size
    die("ELF has no .bun section")


def find_bun_section(buf):
    if buf[:2] == PE_MAGIC:
        die("PE (Windows) executable detected - extraction is not supported.\n"
            "       The .bun payload format is identical, but running the extracted\n"
            "       JS needs a Windows Bun; see docs/status.md. Only Mach-O and ELF\n"
            "       are supported.")
    magic = struct.unpack_from("<I", buf, 0)[0]
    if magic == MH_MAGIC_64:
        return find_bun_section_macho(buf)
    if magic == ELF_MAGIC_LE:
        return find_bun_section_elf(buf)
    die(f"unrecognized magic 0x{magic:08x} "
        f"(supported: 64-bit little-endian Mach-O and ELF)")


def parse_payload(section):
    """Validate a raw __bun/.bun section and return
    (payload, modules_offset, modules_size, entry_point_id)."""
    payload_size = struct.unpack_from("<Q", section, 0)[0]
    payload = section[8:8 + payload_size]
    if payload[-len(TRAILER):] != TRAILER:
        die("trailer mismatch - not a Bun standalone graph, or wrong offsets")

    start = len(payload) - len(TRAILER) - OFFSET_STRUCT_SIZE
    modules_offset = struct.unpack_from("<I", payload, start + 8)[0]
    modules_size = struct.unpack_from("<I", payload, start + 12)[0]
    entry_point_id = struct.unpack_from("<I", payload, start + 16)[0]

    if modules_size % MODULE_RECORD_SIZE != 0:
        die(f"modules table size {modules_size} not a multiple of {MODULE_RECORD_SIZE}")
    count = modules_size // MODULE_RECORD_SIZE
    if count == 0:
        die("modules table is empty")
    if entry_point_id >= count:
        die(f"entry point id {entry_point_id} out of range (only {count} modules)")
    return payload, modules_offset, modules_size, entry_point_id


def extract(binary, out_dir):
    with open(binary, "rb") as fh:
        buf = fh.read()
    print(f"Size:    {len(buf)/1024/1024:.1f} MB")

    raw_off, raw_size = find_bun_section(buf)
    print(f"Section: offset={raw_off} size={raw_size} ({raw_size/1024/1024:.1f} MB)")
    section = buf[raw_off:raw_off + raw_size]

    payload, modules_offset, modules_size, entry_point_id = parse_payload(section)
    count = modules_size // MODULE_RECORD_SIZE
    print(f"Payload: {len(payload)} bytes, trailer OK")
    print(f"Modules: {count} (entry id={entry_point_id})")

    assets_dir = os.path.join(out_dir, "assets")
    os.makedirs(out_dir, exist_ok=True)
    table = payload[modules_offset:modules_offset + modules_size]

    cli_count = asset_count = shim_count = 0
    for i in range(count):
        rec = table[i * MODULE_RECORD_SIZE:(i + 1) * MODULE_RECORD_SIZE]
        name_off, name_size, content_off, content_size = struct.unpack_from("<IIII", rec, 0)
        loader_id = rec[49]
        name = payload[name_off:name_off + name_size].decode("utf-8", "replace")
        content = payload[content_off:content_off + content_size]
        loader = LOADERS.get(loader_id, f"unknown({loader_id})")
        base = name.replace("\\", "/").split("/")[-1]

        if i == entry_point_id:
            dest = os.path.join(out_dir, "cli.original.js")
            with open(dest, "wb") as fh:
                fh.write(content)
            print(f"  entry   {loader:7} {len(content)/1024/1024:6.2f} MB -> {dest}")
            cli_count += 1
        elif loader in ("napi", "base64", "file"):
            # Native addons (.node) and runtime assets (mermaid, highlight.js,
            # the html template) must land on real disk for cli.js to
            # require/read them once /$bunfs no longer exists. The stored
            # content is ALWAYS the raw bytes - the 'base64' loader label
            # describes how Bun later exposes the asset to JS (as a base64
            # string), NOT how it is stored. Decoding it would corrupt the
            # Mach-O. Write verbatim.
            os.makedirs(assets_dir, exist_ok=True)
            dest = os.path.join(assets_dir, base)
            with open(dest, "wb") as fh:
                fh.write(content)
            kind = "native " if base.endswith(".node") else "asset  "
            print(f"  {kind} {loader:7} {len(content)/1024:7.0f} KB -> {dest}")
            asset_count += 1
        else:
            # tiny js shims (image-processor.js etc.) that load the addons
            shim_count += 1

    print(f"Extracted: {cli_count} cli.js + {asset_count} assets "
          f"({shim_count} loader shims left inlined in cli.js)")
    if cli_count != 1:
        die(f"expected exactly 1 entry-point, got {cli_count}")


def main():
    if len(sys.argv) != 3:
        die("usage: extract_bun.py <binary> <out-dir>")
    extract(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
