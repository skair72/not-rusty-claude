#!/usr/bin/env python3
"""
extract_bun.py - pull cli.js + native modules and assets out of a Bun standalone
executable (e.g. the native Claude Code binary), so the JS can be run under a
stock external Bun instead of the signed binary's embedded runtime.

Both the Mach-O and ELF code paths here have been run against real Claude Code
binaries; see docs/status.md's verification matrix for current per-platform
counts and status - this comment does not restate them so they cannot drift out
of sync. Module names/loaders can change between Claude versions, so re-measure
per docs/status.md's "Remaining work" item 1 on a new binary.

Only reads the binary; never modifies or signs it. Runs on the stock
/usr/bin/python3 (3.9+) with no node/bun needed.

Format (see docs/bun-section-format.md): the Bun standalone embeds a serialized
module graph in a platform section (Mach-O __BUN,__bun; ELF/PE .bun), ending
with the trailer magic '\\n---- Bun! ----\\n'. This tool implements the Mach-O
and ELF cases, and refuses PE. Entry module -> cli.original.js; napi/base64/file
modules -> assets/<name> written as RAW bytes. Every .node addon on both shipped
binaries carries loader id 10 = napi. Ids are Bun's, see LOADERS below.

Usage:
  ./extract_bun.py <binary> <out-dir>
"""

import os
import struct
import sys

TRAILER = b"\n---- Bun! ----\n"
OFFSET_STRUCT_SIZE = 32
MODULE_RECORD_SIZE = 52
# Bun's own loader enum, transcribed from src/bundler/options.zig at tag
# bun-v1.3.14. Get this wrong and modules are mislabelled: an earlier table
# here omitted jsonc=7, shifting every id from 7 up by one. That table called
# the .node addons (real id 10, napi) "base64", which happened to be in the
# accept-set below so extraction still worked - while a GENUINE base64 module
# (real id 11) was labelled "dataurl", missed the accept-set, and would have
# been silently dropped. Re-check this table against Bun's source if the
# extractor is ever pointed at a binary built by a different Bun version.
LOADERS = {0: "jsx", 1: "js", 2: "ts", 3: "tsx", 4: "css", 5: "file",
           6: "json", 7: "jsonc", 8: "toml", 9: "wasm", 10: "napi",
           11: "base64", 12: "dataurl", 13: "text", 14: "bunsh", 15: "sqlite",
           16: "sqlite_embedded", 17: "html", 18: "yaml", 19: "json5",
           20: "md"}

# Loaders whose modules must land on real disk under assets/ because the entry
# module reaches for them at RUNTIME - native addons (napi) and assets read
# back through fs (file, base64). Everything else is a JS shim the bundler has
# already inlined into the entry module. Kept as a named constant so the test
# suite can assert one written kind per entry rather than restating the tuple.
WRITTEN_LOADERS = ("napi", "base64", "file")

# Loaders that are genuine JavaScript, i.e. the ones legitimately inlined into
# the entry module and dropped here. Anything else being dropped is a signal,
# not a routine event - see the warning in extract().
JS_LOADERS = ("js", "jsx", "ts", "tsx")

# Sanity ceilings for Mach-O header counts. A real Mach-O has tens of load
# commands and tens of sections; these are ~1000x that, so they reject the
# u32-scale values a corrupt or hostile header supplies without constraining
# any plausible real binary. Without them a load command claiming ncmds=2**32-1
# takes ~10 minutes to walk (measured) instead of dying immediately.
MAX_LOAD_COMMANDS = 10000
MAX_SECTIONS = 10000

MH_MAGIC_64 = 0xFEEDFACF
ELF_MAGIC_LE = 0x464C457F
PE_MAGIC = b"MZ"


def die(msg):
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(1)


def find_bun_section_macho(buf):
    """Return (raw_offset, raw_size) of the __BUN,__bun section.

    Every count and offset here comes straight out of the file, so all of them
    are attacker-controlled on an untrusted input. The bounds checks are not
    decoration: without them a header with cmdsize=0 makes `off` stop advancing
    and re-reads the same bytes ncmds times, and a huge nsects walks past EOF
    where slicing quietly yields b"" instead of raising - measured at ~10 and
    ~17 minutes respectively for u32-scale counts. The ELF path opposite has
    had these checks since it was written; this one predates them.
    """
    # mach_header_64: magic(4) cputype(4) cpusubtype(4) filetype(4)
    #                 ncmds(4) sizeofcmds(4) flags(4) reserved(4)
    HEADER_SIZE = 32
    SECTION_64_SIZE = 80
    SEGMENT_64_HEADER = 0x48   # section_64 records start here, inside the command
    if len(buf) < HEADER_SIZE:
        die(f"Mach-O header truncated - file is only {len(buf)} bytes "
            f"(need at least {HEADER_SIZE} for the fixed header)")
    ncmds = struct.unpack_from("<I", buf, 16)[0]
    if ncmds > MAX_LOAD_COMMANDS:
        die(f"Mach-O claims {ncmds} load commands (max {MAX_LOAD_COMMANDS}) "
            "- corrupt or hostile header")
    off = HEADER_SIZE  # load commands begin after the 32-byte header
    LC_SEGMENT_64 = 0x19
    for _ in range(ncmds):
        if off + 8 > len(buf):
            die("Mach-O load command table extends past end of file "
                "(truncated or corrupt binary)")
        cmd, cmdsize = struct.unpack_from("<II", buf, off)
        if cmdsize == 0:
            die("Mach-O load command has cmdsize=0 - the walk cannot advance "
                "(corrupt or hostile binary)")
        if off + cmdsize > len(buf):
            die("Mach-O load command extends past end of file "
                "(truncated or corrupt binary)")
        if cmd == LC_SEGMENT_64 and cmdsize >= SEGMENT_64_HEADER:
            segname = buf[off + 8:off + 24].split(b"\0", 1)[0].decode("ascii", "replace")
            if segname == "__BUN":
                nsects = struct.unpack_from("<I", buf, off + 0x40)[0]
                if nsects > MAX_SECTIONS:
                    die(f"Mach-O __BUN segment claims {nsects} sections "
                        f"(max {MAX_SECTIONS}) - corrupt or hostile header")
                s = off + SEGMENT_64_HEADER  # first section_64 record
                for _ in range(nsects):
                    # section_64 records live inside the load command; a
                    # well-formed one never runs past its own cmdsize.
                    if s + SECTION_64_SIZE > min(off + cmdsize, len(buf)):
                        die("Mach-O __BUN section table extends past the end "
                            "of its load command (truncated or corrupt binary)")
                    sectname = buf[s:s + 16].split(b"\0", 1)[0].decode("ascii", "replace")
                    if sectname == "__bun":
                        size = struct.unpack_from("<Q", buf, s + 0x28)[0]
                        offset = struct.unpack_from("<I", buf, s + 0x30)[0]
                        return offset, size
                    s += SECTION_64_SIZE
        off += cmdsize
    die("Mach-O has no __BUN,__bun section")


def find_bun_section_elf(buf):
    """Return (raw_offset, raw_size) of the .bun section in an ELF64 image.

    The container differs from Mach-O but the payload it wraps is byte-identical
    (see docs/bun-section-format.md): a u64 length prefix, the module graph, and
    the trailer magic.
    """
    if len(buf) < 0x40:
        die(f"ELF header truncated - file is only {len(buf)} bytes "
            "(need at least 64 for the fixed header)")
    if buf[4] != 2 or buf[5] != 1:
        die("only 64-bit little-endian ELF is supported")
    e_shoff = struct.unpack_from("<Q", buf, 0x28)[0]
    e_shentsize = struct.unpack_from("<H", buf, 0x3A)[0]
    e_shnum = struct.unpack_from("<H", buf, 0x3C)[0]
    e_shstrndx = struct.unpack_from("<H", buf, 0x3E)[0]
    if e_shoff == 0 or e_shnum == 0:
        die("ELF has no section headers (stripped?) - cannot locate .bun")
    if e_shoff + e_shnum * e_shentsize > len(buf):
        die("ELF section header table extends past end of file "
            "(truncated or corrupt binary)")
    if e_shentsize < 40:
        die(f"ELF section header entry size {e_shentsize} is too small "
            "(need at least 40 bytes to hold sh_offset/sh_size)")
    if e_shstrndx >= e_shnum:
        # shdr() below indexes the table with e_shstrndx before any loop
        # bound applies to it, so an out-of-range value reaches
        # struct.unpack_from as a raw offset. Same class the check above
        # closes, one field further on.
        die(f"ELF section name string table index {e_shstrndx} is out of "
            f"range (only {e_shnum} sections)")

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
    if len(buf) < 4:
        die(f"input is only {len(buf)} bytes - too short to hold a container "
            "magic, let alone a Bun standalone")
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
    # The smallest conceivable well-formed section: u64 length prefix, the
    # 32-byte offset struct and the trailer. Anything shorter cannot be
    # unpacked at all, and reached struct.error rather than die() before.
    minimum = 8 + OFFSET_STRUCT_SIZE + len(TRAILER)
    if len(section) < minimum:
        die(f"section is only {len(section)} bytes - too short to hold a Bun "
            f"module graph (need at least {minimum})")
    payload_size = struct.unpack_from("<Q", section, 0)[0]
    payload = section[8:8 + payload_size]
    if len(payload) < OFFSET_STRUCT_SIZE + len(TRAILER):
        die(f"payload is only {len(payload)} bytes - too short to hold the "
            "offset struct and trailer (truncated section?)")
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

    asset_count = shim_count = 0
    written = {}    # basename -> content already written under assets/
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
        elif loader in WRITTEN_LOADERS:
            # Native addons (.node, loader napi) and runtime assets (mermaid,
            # highlight.js, the html template - loader file) must land on real
            # disk for cli.js to require/read them once /$bunfs no longer
            # exists. Whatever the loader, the section stores the module's RAW
            # bytes; a loader name describes how Bun would later expose the
            # module to JS (base64 = as a base64 string), NOT how it is stored.
            # Decoding would corrupt the payload. Write verbatim.
            if base in ("", ".", ".."):
                # base is already reduced to a basename (see above), so this
                # is never a traversal hole - just a name that would collide
                # with the assets/ directory itself and raise
                # IsADirectoryError from open() below. Fail cleanly instead.
                die(f"module {i} ({name!r}) reduces to an unsafe basename "
                    f"{base!r} - refusing to write it under assets/")
            if base in written:
                # Two modules in different directories share a basename, so
                # both want the same assets/<base>. The second used to
                # overwrite the first in silence, exit 0, and ship an artifact
                # missing an asset. postprocess.py could not disambiguate them
                # either: BUNFS_LITERAL only matches basenames directly under
                # /$bunfs/root/, so both references rewrite to the same path.
                if written[base] == content:
                    print(f"  note: module {i} ({name!r}) duplicates "
                          f"assets/{base} byte-for-byte; keeping one copy")
                    continue
                die(f"module {i} ({name!r}) collides with an already-written "
                    f"assets/{base} whose contents differ - one of the two "
                    "would be silently lost")
            os.makedirs(assets_dir, exist_ok=True)
            dest = os.path.join(assets_dir, base)
            written[base] = content
            with open(dest, "wb") as fh:
                fh.write(content)
            kind = "native " if base.endswith(".node") else "asset  "
            print(f"  {kind} {loader:7} {len(content)/1024:7.0f} KB -> {dest}")
            asset_count += 1
        else:
            # tiny js shims (image-processor.js etc.) that load the addons.
            # Anything that is NOT JavaScript is being dropped on the floor:
            # either Bun's loader enum has drifted (a new id we do not know)
            # or this binary carries a kind we have never seen embedded, e.g.
            # wasm(9) or sqlite(15). Neither is fatal here - if the entry
            # module actually references it, postprocess.py's
            # referenced-but-never-extracted check stops the build - but it
            # must not pass in silence, because a silent drop is precisely how
            # the old off-by-one enum hid a mislabelled kind.
            if loader not in JS_LOADERS:
                sys.stderr.write(
                    f"warning: module {i} ({name!r}) has loader "
                    f"{loader} (id {loader_id}), which is neither JavaScript "
                    "to inline nor a kind we extract - it was DROPPED. Check "
                    "LOADERS against Bun's src/bundler/options.zig.\n")
            shim_count += 1

    # No cli_count guard: exactly one module has i == entry_point_id, and
    # parse_payload() has already rejected entry_point_id >= count, so the
    # entry branch fires exactly once by construction.
    print(f"Extracted: 1 cli.js + {asset_count} assets "
          f"({shim_count} loader shims left inlined in cli.js)")


def main():
    if len(sys.argv) != 3:
        die("usage: extract_bun.py <binary> <out-dir>")
    extract(sys.argv[1], sys.argv[2])


if __name__ == "__main__":
    main()
