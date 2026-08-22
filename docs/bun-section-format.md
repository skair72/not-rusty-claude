# Bun standalone section format

Byte-level reference for the module graph embedded by `bun build --compile`, as
implemented (and verified ✅) by [`tools/extract_bun.py`](../tools/extract_bun.py).
This documents the macOS/Mach-O case; ELF (`.bun`) and PE (`.bun`) place the same
payload in a differently-named section.

All integers are **little-endian**.

---

## 1. Locate the section (Mach-O)

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

Observed on 2.1.238 ✅: `rawOffset = 69107712`, `rawSize = 251304613`.

---

## 2. Section → payload

```
section bytes @ rawOffset, length rawSize:
  +0x00 u64   payload_size
  +0x08       payload            (payload_size bytes)
```

The **payload ends with the trailer magic**:

```
TRAILER = "\n---- Bun! ----\n"   (15 bytes)
assert payload[-15:] == TRAILER
```

Observed ✅: `payload_size = 251304605`, trailer matches.

---

## 3. Payload → offsets struct

Immediately before the trailer sits a fixed **32-byte offsets struct**:

```
start = len(payload) - len(TRAILER) - 32      # = len(payload) - 47
  start +0x08 u32  modules_offset     # offset of the modules table within payload
  start +0x0C u32  modules_size       # total bytes of the modules table
  start +0x10 u32  entry_point_id     # index of the entry module (cli.js)
```

(The remaining fields of the 32-byte struct are other section offsets not needed
for extraction.)

Observed ✅: `modules_offset = 251303776`, `modules_size = 780`,
`entry_point_id = 0`  → `780 / 52 = 15` modules.

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

### Loader enum

```
0 jsx   1 js    2 ts    3 tsx   4 css    5 file   6 json    7 toml
8 wasm  9 napi  10 base64  11 dataurl  12 text  13 bunsh  14 sqlite
```

**Critical:** the **content is always the raw stored bytes**. The loader id
tells Bun how to *present* the module to JS at runtime — e.g. `base64` means
"expose this asset to JS as a base64 string" — it does **not** mean the stored
bytes are base64-encoded. Native `.node` addons on current Claude builds use the
`base64` loader but are stored as raw Mach-O. **Do not decode.** (See
[findings.md](./findings.md) §5a.)

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

---

## 6. Reference: the parse in ~20 lines of Python

```python
import struct
TRAILER = b"\n---- Bun! ----\n"

buf = open(binary, "rb").read()
# ... find __BUN,__bun → (raw_off, raw_size) via load-command walk ...
sec  = buf[raw_off:raw_off + raw_size]
psz  = struct.unpack_from("<Q", sec, 0)[0]
p    = sec[8:8 + psz]
assert p[-len(TRAILER):] == TRAILER

st   = len(p) - len(TRAILER) - 32
mo, msz, eid = struct.unpack_from("<III", p, st + 8)
table = p[mo:mo + msz]

for i in range(msz // 52):
    r = table[i*52:(i+1)*52]
    no, nsz, co, csz = struct.unpack_from("<IIII", r, 0)
    loader_id = r[49]
    name    = p[no:no + nsz].decode("utf-8", "replace")
    content = p[co:co + csz]          # RAW — never base64-decode
    ...
```
