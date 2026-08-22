"""Synthetic Bun standalone containers for hermetic tests.

Builds kilobyte-scale Mach-O / ELF / PE files wrapping a real, well-formed
Bun module graph, so the parser can be tested without a 300 MB binary.
Layouts mirror docs/bun-section-format.md exactly.
"""

import struct

TRAILER = b"\n---- Bun! ----\n"
OFFSET_STRUCT_SIZE = 32
MODULE_RECORD_SIZE = 52


def build_payload(modules, entry=0):
    """modules: list of (name, content_bytes, loader_id). Returns payload bytes.

    Layout: [names+contents blob][modules table][32-byte offsets][trailer]
    """
    blob = bytearray()
    slots = []
    for name, content, loader in modules:
        name_bytes = name.encode("utf-8")
        name_off = len(blob)
        blob += name_bytes
        content_off = len(blob)
        blob += content
        slots.append((name_off, len(name_bytes), content_off, len(content), loader))

    modules_offset = len(blob)
    table = bytearray()
    for name_off, name_size, content_off, content_size, loader in slots:
        rec = bytearray(MODULE_RECORD_SIZE)
        struct.pack_into("<IIII", rec, 0, name_off, name_size, content_off, content_size)
        rec[49] = loader
        table += rec
    blob += table

    offsets = bytearray(OFFSET_STRUCT_SIZE)
    struct.pack_into("<III", offsets, 8, modules_offset, len(table), entry)
    blob += offsets
    blob += TRAILER
    return bytes(blob)


def _section_bytes(payload):
    """A __bun/.bun section is a u64 length prefix followed by the payload."""
    return struct.pack("<Q", len(payload)) + payload


def build_macho(payload):
    """Minimal 64-bit little-endian Mach-O with one LC_SEGMENT_64 __BUN/__bun."""
    section = _section_bytes(payload)
    header_size = 32
    cmdsize = 0x48 + 80          # segment_command_64 + one section_64
    sect_offset = header_size + cmdsize

    hdr = bytearray(header_size)
    struct.pack_into("<I", hdr, 0, 0xFEEDFACF)   # MH_MAGIC_64
    struct.pack_into("<I", hdr, 4, 0x0100000C)   # CPU_TYPE_ARM64
    struct.pack_into("<I", hdr, 12, 2)           # MH_EXECUTE
    struct.pack_into("<I", hdr, 16, 1)           # ncmds
    struct.pack_into("<I", hdr, 20, cmdsize)     # sizeofcmds

    cmd = bytearray(cmdsize)
    struct.pack_into("<II", cmd, 0, 0x19, cmdsize)      # LC_SEGMENT_64
    cmd[8:8 + len(b"__BUN")] = b"__BUN"
    struct.pack_into("<I", cmd, 0x40, 1)                # nsects
    # the single section_64 record lives inside this same command, at +0x48
    cmd[0x48:0x48 + len(b"__bun")] = b"__bun"           # sectname
    cmd[0x58:0x58 + len(b"__BUN")] = b"__BUN"           # section's segname
    struct.pack_into("<Q", cmd, 0x48 + 0x28, len(section))    # size
    struct.pack_into("<I", cmd, 0x48 + 0x30, sect_offset)     # offset

    return bytes(hdr) + bytes(cmd) + section


def build_elf(payload):
    """Minimal ELF64 little-endian with a .bun section.

    Sections: [0] NULL, [1] .shstrtab, [2] .bun
    """
    section = _section_bytes(payload)
    shstrtab = b"\0.shstrtab\0.bun\0"
    name_shstrtab = 1
    name_bun = 11

    ehsize = 64
    shentsize = 64
    bun_off = ehsize
    shstr_off = bun_off + len(section)
    shoff = shstr_off + len(shstrtab)

    e = bytearray(ehsize)
    e[0:4] = b"\x7fELF"
    e[4] = 2      # ELFCLASS64
    e[5] = 1      # ELFDATA2LSB
    e[6] = 1      # EV_CURRENT
    struct.pack_into("<H", e, 0x10, 2)          # e_type ET_EXEC
    struct.pack_into("<H", e, 0x12, 62)         # e_machine x86-64
    struct.pack_into("<I", e, 0x14, 1)          # e_version
    struct.pack_into("<Q", e, 0x28, shoff)      # e_shoff
    struct.pack_into("<H", e, 0x34, ehsize)     # e_ehsize
    struct.pack_into("<H", e, 0x3A, shentsize)  # e_shentsize
    struct.pack_into("<H", e, 0x3C, 3)          # e_shnum
    struct.pack_into("<H", e, 0x3E, 1)          # e_shstrndx

    def shdr(name, sh_type, offset, size):
        s = bytearray(shentsize)
        struct.pack_into("<I", s, 0x00, name)
        struct.pack_into("<I", s, 0x04, sh_type)
        struct.pack_into("<Q", s, 0x18, offset)
        struct.pack_into("<Q", s, 0x20, size)
        return bytes(s)

    headers = (shdr(0, 0, 0, 0)
               + shdr(name_shstrtab, 3, shstr_off, len(shstrtab))   # SHT_STRTAB
               + shdr(name_bun, 1, bun_off, len(section)))          # SHT_PROGBITS

    return bytes(e) + section + shstrtab + headers


def build_pe(payload):
    """Minimal PE/COFF with a .bun section — used only to test refusal."""
    section = _section_bytes(payload)
    e_lfanew = 0x80
    opt_size = 240
    sect_table = e_lfanew + 24 + opt_size
    raw_off = sect_table + 40

    buf = bytearray(raw_off)
    buf[0:2] = b"MZ"
    struct.pack_into("<I", buf, 0x3C, e_lfanew)
    buf[e_lfanew:e_lfanew + 4] = b"PE\0\0"
    struct.pack_into("<H", buf, e_lfanew + 4, 0x8664)      # machine
    struct.pack_into("<H", buf, e_lfanew + 6, 1)           # numberOfSections
    struct.pack_into("<H", buf, e_lfanew + 20, opt_size)   # sizeOfOptionalHeader

    s = bytearray(40)
    s[0:4] = b".bun"
    struct.pack_into("<I", s, 16, len(section))            # sizeOfRawData
    struct.pack_into("<I", s, 20, raw_off)                 # pointerToRawData
    buf[sect_table:sect_table + 40] = s

    return bytes(buf) + section
