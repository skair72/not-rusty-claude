# Bun standalone section format

Byte-level reference for the module graph embedded by `bun build --compile`, as
implemented by [`tools/extract_bun.py`](../tools/extract_bun.py) and verified
against three real Claude Code binaries.

**The container is the only thing that differs between platforms.** Mach-O
carries the graph in `__BUN,__bun`, ELF and PE in a section named `.bun`, and
inside all three the wrapped payload has an **identical byte layout** — the same
u64 length prefix, the same module-record table, the same 16-byte trailer. All
three were parsed with the same payload code path here (§2–§4); only §1's
section lookup is platform-specific.

All integers are **little-endian**.

---

## 1a. Locate the section — Mach-O (macOS)

The payload lives in the section **`__bun`** inside the segment **`__BUN`**.

Walk the load commands from the 64-bit Mach-O header:

```
mach_header_64 (32 bytes):
  +0x00 u32  magic          0xFEEDFACF (MH_MAGIC_64)
  +0x10 u32  ncmds          number of load commands
  ...
load commands begin at offset 0x20; each is (cmd u32, cmdsize u32, ...)

LC_SEGMENT_64 = 0x19:
  +0x08 char[16]  segname          look for "__BUN"
  +0x40 u32       nsects
  +0x48           first section_64 record

section_64 (80 bytes each):
  +0x00 char[16]  sectname         look for "__bun"
  +0x28 u64       size             → rawSize
  +0x30 u32       offset           → rawOffset (file offset)
```

Observed on `darwin-arm64` 2.1.239: `rawOffset = 69107712`,
`rawSize = 255007133`.

---

## 1b. Locate the section — ELF (Linux)

The payload lives in a section named **`.bun`**. Walk the section-header table:

```
elf64_ehdr:
  +0x04 u8   EI_CLASS       must be 2  (ELFCLASS64)
  +0x05 u8   EI_DATA        must be 1  (little-endian)
  +0x28 u64  e_shoff        section-header table file offset
  +0x3A u16  e_shentsize    size of one section header
  +0x3C u16  e_shnum        number of section headers
  +0x3E u16  e_shstrndx     index of the section-name string table

elf64_shdr (e_shentsize bytes, fields used here):
  +0x00 u32  sh_name        byte offset into the .shstrtab section
  +0x18 u64  sh_offset      → rawOffset (file offset)
  +0x20 u64  sh_size        → rawSize
```

Read the header at index `e_shstrndx` to find the string table, then compare
each section's NUL-terminated name against `.bun`.

Observed on `linux-x64` 2.1.222: `rawOffset = 86904832`, `rawSize = 202513494`.

**Failure mode to handle explicitly:** a fully stripped ELF (`e_shoff == 0` or
`e_shnum == 0`) has no section headers at all, so `.bun` cannot be located this
way. `extract_bun.py` reports that rather than reading garbage. Claude Code's
shipped Linux binary is not stripped.

---

## 1c. Locate the section — PE (Windows) ⛔ not implemented

The `win32-x64` build carries the same graph in a PE section, also named
`.bun`. `extract_bun.py` **refuses PE input by design** (see
[status.md](./status.md) § Windows/PE for why, and for what else would need to
change). The walk, for reference:

```
+0x3C u32   e_lfanew            file offset of the PE signature ("PE\0\0")
COFF header, at e_lfanew + 4:
  +0x02 u16  NumberOfSections
  +0x10 u16  SizeOfOptionalHeader
section table begins at e_lfanew + 4 + 20 + SizeOfOptionalHeader

section header (40 bytes each):
  +0x00 char[8]  Name                 look for ".bun"
  +0x08 u32      VirtualSize
  +0x10 u32      SizeOfRawData        → rawSize  (padded! see below)
  +0x14 u32      PointerToRawData     → rawOffset
```

Observed on `win32-x64` 2.1.239: `rawOffset = 95182336`,
`SizeOfRawData = 242479616`, `VirtualSize = 242479183`.

**PE pads the section to the file alignment.** `SizeOfRawData` here exceeds
`payload_size + 8` by 433 bytes; on ELF and Mach-O the section size matched
`payload_size + 8` exactly. Never derive the payload length from the section
size — always read the u64 length prefix in §2. (`VirtualSize` happens to equal
`payload_size + 8`, but the length prefix is the authoritative field.)

Note also that on Windows the module *names* inside the payload use a different
virtual-filesystem prefix: `B:/~BUN/root/…` rather than `/$bunfs/root/…`. The
payload *structure* is unchanged; only the strings differ.

---

## 2. Section → payload

```
section bytes @ rawOffset, length rawSize:
  +0x00 u64   payload_size
  +0x08       payload            (payload_size bytes)
```

The **payload ends with the trailer magic**:

```
TRAILER = "\n---- Bun! ----\n"   (16 bytes — both newlines count)
assert payload[-16:] == TRAILER
```

Observed:

| Binary | `payload_size` | trailer |
|---|---|---|
| `linux-x64` 2.1.222 | 202513486 | matches |
| `darwin-arm64` 2.1.239 | 255007125 | matches |
| `win32-x64` 2.1.239 | 242479175 | matches |

---

## 3. Payload → offsets struct

Immediately before the trailer sits a fixed **32-byte offsets struct**:

```
start = len(payload) - len(TRAILER) - 32      # = len(payload) - 48
  start +0x08 u32  modules_offset     # offset of the modules table within payload
  start +0x0C u32  modules_size       # total bytes of the modules table
  start +0x10 u32  entry_point_id     # index of the entry module (cli.js)
```

(The remaining fields of the 32-byte struct are other section offsets not needed
for extraction.)

> **Arithmetic note.** Earlier revisions of this document called the trailer 15
> bytes and gave `len(payload) - 47`. Both were wrong: `"\n---- Bun! ----\n"` is
> **16** bytes, so the struct starts at `len(payload) - 48`. The code was never
> affected — it computes `len(TRAILER)` — but anyone reimplementing from the
> prose would have been off by one and read a shifted `modules_offset`.

Observed:

| Binary | `modules_offset` | `modules_size` | `entry_point_id` | modules |
|---|---|---|---|---|
| `linux-x64` 2.1.222 | 202513021 | 416 | 0 | 8 |
| `darwin-arm64` 2.1.239 | 255006296 | 780 | 0 | 15 |
| `win32-x64` 2.1.239 | 242478658 | 468 | 0 | 9 |

---

## 4. Modules table → records

The table is `modules_size / MODULE_RECORD_SIZE` records, **52 bytes** each:

```
module record (52 bytes):
  +0x00 u32  name_offset       # → payload[name_offset : name_offset+name_size]
  +0x04 u32  name_size
  +0x08 u32  content_offset    # → payload[content_offset : +content_size]
  +0x0C u32  content_size
  +0x31 u8   loader_id         # offset 49; see loader enum below
  (other bytes: alignment / flags, unused here)
```

- Name and content are both slices **into the payload** (absolute payload
  offsets, not record-relative).
- The record whose index == `entry_point_id` is the **entry module** = `cli.js`.
  **Use this field.** Do not match on the module's name: it is
  `/$bunfs/root/cli` on darwin-arm64 2.1.239 but
  `/$bunfs/root/src/entrypoints/cli.js` on linux-x64 2.1.222
  ([findings.md](./findings.md) §4).

### Loader enum

```
0 jsx   1 js    2 ts    3 tsx   4 css    5 file   6 json    7 toml
8 wasm  9 napi  10 base64  11 dataurl  12 text  13 bunsh  14 sqlite
```

**Critical:** the **content is always the raw stored bytes**. The loader id
tells Bun how to *present* the module to JS at runtime — e.g. `base64` means
"expose this asset to JS as a base64 string" — it does **not** mean the stored
bytes are base64-encoded. Native `.node` addons on current Claude builds use the
`base64` loader but are stored as raw Mach-O (macOS) or raw ELF (Linux).
**Do not decode.** (See [findings.md](./findings.md) §5a.)

---

## 5. Extraction rules used by `extract_bun.py`

| Module | Action |
|---|---|
| `index == entry_point_id` | write content → `cli.original.js` |
| loader `napi` / `base64` / `file` | write **raw** content → `assets/<basename>` |
| other (`js` loader shims, etc.) | leave inlined in cli.js; skip |

Basenames are taken from the module name after normalizing `\` → `/` and taking
the final path component (e.g. `/$bunfs/root/image-processor.node` →
`image-processor.node`).

Driving this off the loader id — rather than a filename list — is what makes the
same code work across platforms whose module sets differ (8 vs 15 vs 9 modules).

---

## 6. Reference: the parse in ~20 lines of Python

```python
import struct
TRAILER = b"\n---- Bun! ----\n"          # 16 bytes

buf = open(binary, "rb").read()
# ... find the Bun section → (raw_off, raw_size):
#     Mach-O  §1a: __BUN,__bun via the LC_SEGMENT_64 walk
#     ELF     §1b: .bun via the section-header table
#     PE      §1c: .bun via the COFF section table (not implemented)
sec  = buf[raw_off:raw_off + raw_size]
psz  = struct.unpack_from("<Q", sec, 0)[0]
p    = sec[8:8 + psz]                    # length prefix wins over raw_size
assert p[-len(TRAILER):] == TRAILER

st   = len(p) - len(TRAILER) - 32        # == len(p) - 48
mo, msz, eid = struct.unpack_from("<III", p, st + 8)
table = p[mo:mo + msz]

for i in range(msz // 52):
    r = table[i*52:(i+1)*52]
    no, nsz, co, csz = struct.unpack_from("<IIII", r, 0)
    loader_id = r[49]
    name    = p[no:no + nsz].decode("utf-8", "replace")
    content = p[co:co + csz]             # RAW — never base64-decode
    is_entry = (i == eid)                # never match on `name`
    ...
```
