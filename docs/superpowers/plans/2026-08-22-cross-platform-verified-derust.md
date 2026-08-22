# Cross-Platform Extraction + Verified Zig-Bun Run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn `not-rusty-claude` from a never-executed scaffold into a tested, cross-platform tool whose Linux path is verified end-to-end by actually running Claude Code's extracted JavaScript under Bun 1.3.14.

**Architecture:** `extract_bun.py` gains a thin *container layer* (Mach-O `__BUN,__bun` / ELF `.bun` / PE refusal) over the already-correct, format-agnostic *payload layer*. `postprocess.py` is rewritten around call shapes read out of the real minified `cli.js` rather than ported guesses. A hermetic pytest suite builds kilobyte-scale synthetic containers so the parser is tested without network, Bun, or a 300 MB binary.

**Tech Stack:** Python 3.9+ (stdlib only — no pip, no venv), pytest 9.1.1 for tests only, bash for `build.sh`, Bun 1.3.14 (linux-x64) for the end-to-end run.

## Global Constraints

- **Zero runtime dependencies.** `tools/*.py` must run on stock `/usr/bin/python3` (3.9+). stdlib only. pytest is a *test-time* dependency, never imported by the tools.
- **The signed binary is only ever read.** Never modified, never executed, never re-signed.
- **Nothing is installed on `PATH`.** `build.sh` produces artifacts and prints the run command. It must never create or overwrite a `claude` executable — the host's `/usr/bin/claude` is the running session.
- **PE is detection-only.** Refuse with a clear message; extraction is explicitly out of scope.
- **Bun floor is `1.3.14`** — simultaneously the last Zig release and the minimum that loads Claude's `cli.js`.
- **Every count asserted in code must be a measured fact**, not an estimate. Measured on 2026-08-22:
  - linux-x64 2.1.222 (`/usr/bin/claude`): 8 modules, entry name `/$bunfs/root/src/entrypoints/cli.js`, 5 `/$bunfs/` literals, 7 `file://` leaks.
  - darwin-arm64 2.1.239 (npm): 15 modules, entry name `/$bunfs/root/cli`, 9 `/$bunfs/` literals, 8 `file://` leaks.
- **Honesty rule.** If the end-to-end run fails, the failure is the deliverable. Docs record the exact error; no step claims success without pasted command output.

## File Structure

| File | Responsibility |
|---|---|
| `tools/extract_bun.py` *(modify)* | Container location (Mach-O/ELF/PE-refusal) + payload/module-graph parsing + asset writing |
| `tools/postprocess.py` *(rewrite)* | Pure text transforms turning `cli.original.js` into runnable `cli.original.cjs` |
| `scripts/build.sh` *(rewrite)* | Orchestrate extract → postprocess; emit artifacts; install nothing |
| `tests/fixtures.py` *(create)* | Synthetic Bun payload + Mach-O/ELF/PE container builders |
| `tests/conftest.py` *(create)* | pytest markers, real-binary discovery, skip logic |
| `tests/test_containers.py` *(create)* | Container-layer tests: locate `.bun` in each format, refusals |
| `tests/test_extract.py` *(create)* | Payload-layer tests: round-trip, raw-bytes invariant, malformed inputs |
| `tests/test_postprocess.py` *(create)* | Transform tests: pragma, `/$bunfs/` literals, `file://` leaks, IIFE, fail-fast |
| `tests/test_integration.py` *(create)* | Real-binary tests, auto-skipped when binaries absent |
| `docs/*.md` *(update)* | Replace scaffold posture with verified-on-what matrix |

---

### Task 1: Test harness and synthetic fixture builders

Builds the scaffolding every later task tests against, and proves it by round-tripping through the **already-verified** Mach-O extractor — so a green suite here validates the harness, not the new code.

**Files:**
- Create: `tests/fixtures.py`
- Create: `tests/conftest.py`
- Create: `tests/test_containers.py`
- Create: `pytest.ini`

**Interfaces:**
- Consumes: `tools/extract_bun.py`'s existing `find_bun_section(buf) -> (offset, size)`.
- Produces:
  - `fixtures.build_payload(modules, entry=0) -> bytes` where `modules` is `list[tuple[str, bytes, int]]` of `(name, content, loader_id)`
  - `fixtures.build_macho(payload: bytes) -> bytes`
  - `fixtures.build_elf(payload: bytes) -> bytes`
  - `fixtures.build_pe(payload: bytes) -> bytes`
  - `fixtures.TRAILER: bytes`
  - `conftest` fixture `extract_bun` returning the imported `tools/extract_bun.py` module

- [ ] **Step 1: Write the fixture builders**

Create `tests/fixtures.py`:

```python
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
```

- [ ] **Step 2: Write conftest and pytest config**

Create `tests/conftest.py`:

```python
import importlib.util
import os
import pathlib
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name):
    path = ROOT / "tools" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def extract_bun():
    return _load("extract_bun")


@pytest.fixture(scope="session")
def postprocess():
    return _load("postprocess")


def _real(env_var, default):
    path = os.environ.get(env_var, default)
    return path if path and os.path.isfile(path) else None


@pytest.fixture(scope="session")
def real_elf_binary():
    path = _real("NRC_TEST_ELF", "/usr/bin/claude")
    if not path:
        pytest.skip("no ELF Claude binary; set NRC_TEST_ELF")
    return path


@pytest.fixture(scope="session")
def real_macho_binary():
    path = _real("NRC_TEST_MACHO", "/tmp/ccmac/package/claude")
    if not path:
        pytest.skip("no Mach-O Claude binary; set NRC_TEST_MACHO")
    return path
```

Create `pytest.ini`:

```ini
[pytest]
testpaths = tests
markers =
    integration: requires a real 300 MB Claude binary; auto-skipped when absent
```

- [ ] **Step 3: Write the failing harness test**

Create `tests/test_containers.py`:

```python
import pytest
import fixtures


def test_macho_fixture_is_located_by_existing_extractor(extract_bun):
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"console.log(1)", 1)])
    blob = fixtures.build_macho(payload)

    offset, size = extract_bun.find_bun_section(blob)

    assert blob[offset:offset + 8] == len(payload).to_bytes(8, "little")
    assert blob[offset + 8:offset + 8 + size - 8].endswith(fixtures.TRAILER)
```

- [ ] **Step 4: Run it — expect PASS**

Run: `python3 -m pytest tests/test_containers.py -v`
Expected: **PASS**. This is deliberate: it round-trips the synthetic fixture through the *already-verified* Mach-O path, so a pass proves the harness is faithful. If it fails, the fixture builder is wrong — fix `fixtures.build_macho`, not the extractor.

- [ ] **Step 5: Commit**

```bash
git add tests/ pytest.ini
git commit -m "test: hermetic synthetic Mach-O/ELF/PE fixture builders"
```

---

### Task 2: ELF container support

**Files:**
- Modify: `tools/extract_bun.py` (`find_bun_section`, add `find_bun_section_elf`)
- Modify: `tests/test_containers.py`

**Interfaces:**
- Consumes: `fixtures.build_elf(payload) -> bytes` from Task 1.
- Produces: `extract_bun.find_bun_section_elf(buf) -> (offset, size)`; `find_bun_section` now dispatches on magic and returns for ELF instead of dying.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_containers.py`:

```python
def test_elf_bun_section_is_located(extract_bun):
    payload = fixtures.build_payload([("/$bunfs/root/cli", b"console.log(1)", 1)])
    blob = fixtures.build_elf(payload)

    offset, size = extract_bun.find_bun_section(blob)

    assert blob[offset:offset + 8] == len(payload).to_bytes(8, "little")
    assert blob[offset + 8:offset + 8 + len(payload)].endswith(fixtures.TRAILER)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_containers.py::test_elf_bun_section_is_located -v`
Expected: FAIL — `SystemExit: 1` from `die("ELF parsing not ported here; use ClawGod's extract-natives.mjs")`.

- [ ] **Step 3: Write minimal implementation**

In `tools/extract_bun.py`, add above `find_bun_section`:

```python
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
```

Then replace the ELF branch in `find_bun_section`:

```python
    if magic == ELF_MAGIC_LE:
        return find_bun_section_elf(buf)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_containers.py -v`
Expected: PASS (both container tests).

- [ ] **Step 5: Commit**

```bash
git add tools/extract_bun.py tests/test_containers.py
git commit -m "feat: locate the .bun section in ELF containers"
```

---

### Task 3: Input validation — PE refusal, unknown magic, malformed graphs

Groups every "reject bad input clearly" behaviour: a reviewer can accept or reject the whole rejection story at once.

**Files:**
- Modify: `tools/extract_bun.py` (`find_bun_section`, `main`)
- Modify: `tests/test_containers.py`
- Create: `tests/test_extract.py`

**Interfaces:**
- Consumes: `fixtures.build_pe`, `fixtures.build_elf`, `fixtures.build_payload` from Task 1.
- Produces: `extract_bun.parse_payload(section) -> (payload, modules_offset, modules_size, entry_point_id)` — extracted from `main` so tests can drive it without file I/O. `main` calls it.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_containers.py`:

```python
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
```

Create `tests/test_extract.py`:

```python
import struct
import pytest
import fixtures


def _section(payload):
    return struct.pack("<Q", len(payload)) + payload


def test_payload_round_trips_names_and_contents(extract_bun):
    modules = [
        ("/$bunfs/root/cli", b"(function(){})", 1),
        ("/$bunfs/root/thing.node", b"\x7fELF\x02\x01raw", 10),
    ]
    payload = fixtures.build_payload(modules)

    parsed, mod_off, mod_size, entry = extract_bun.parse_payload(_section(payload))

    assert entry == 0
    assert mod_size // 52 == 2
    table = parsed[mod_off:mod_off + mod_size]
    rec = table[52:104]
    name_off, name_size, content_off, content_size = struct.unpack_from("<IIII", rec, 0)
    assert parsed[name_off:name_off + name_size] == b"/$bunfs/root/thing.node"
    assert parsed[content_off:content_off + content_size] == b"\x7fELF\x02\x01raw"
    assert rec[49] == 10


def test_base64_loader_content_is_stored_raw_not_encoded(extract_bun, tmp_path):
    """findings.md 5a: the base64 loader labels how Bun exposes an asset to JS,
    not how it is stored. Decoding corrupts it (once produced 71-byte modules)."""
    macho_magic = b"\xcf\xfa\xed\xfe" + b"\x00" * 60
    payload = fixtures.build_payload([
        ("/$bunfs/root/cli", b"(function(){})", 1),
        ("/$bunfs/root/addon.node", macho_magic, 10),
    ])
    blob = fixtures.build_elf(payload)
    binary = tmp_path / "claude"
    binary.write_bytes(blob)
    out = tmp_path / "out"

    extract_bun.extract(str(binary), str(out))

    assert (out / "assets" / "addon.node").read_bytes() == macho_magic


def test_bad_trailer_is_rejected(extract_bun, capsys):
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    payload[-1] = 0x00

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(_section(bytes(payload)))

    assert "trailer" in capsys.readouterr().err


def test_misaligned_module_table_is_rejected(extract_bun, capsys):
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    struct.pack_into("<I", payload, start + 12, 53)   # modules_size not a multiple of 52

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(_section(bytes(payload)))

    assert "not a multiple" in capsys.readouterr().err


def test_entry_point_id_out_of_range_is_rejected(extract_bun, capsys):
    payload = bytearray(fixtures.build_payload([("/$bunfs/root/cli", b"x", 1)]))
    start = len(payload) - len(fixtures.TRAILER) - 32
    struct.pack_into("<I", payload, start + 16, 99)

    with pytest.raises(SystemExit):
        extract_bun.parse_payload(_section(bytes(payload)))

    assert "entry" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/ -v`
Expected: FAIL — `AttributeError: module 'extract_bun' has no attribute 'parse_payload'` and `'extract'`; the PE test fails with "unrecognized magic" rather than a PE-specific message.

- [ ] **Step 3: Write minimal implementation**

In `tools/extract_bun.py` add the PE magic constant beside the others:

```python
PE_MAGIC = b"MZ"
```

Two stale claims live in this same file and must be corrected here, because
Task 9's staleness grep looks only for "never executed" / "not yet runnable" /
"NEVER EXECUTED" / "SCAFFOLD" and would not catch either one:

- the module docstring's "This tool implements the Mach-O (64-bit
  little-endian) case." — it now implements Mach-O and ELF, and refuses PE.
  Rewrite that sentence accordingly.
- the fallback `die()` message's "(only 64-bit little-endian Mach-O ported)" —
  replace with "(supported: 64-bit little-endian Mach-O and ELF)".

Add the PE branch at the top of `find_bun_section`, before the u32 unpack:

```python
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
```

Extract the payload parsing out of `main` into a reusable function:

```python
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
```

Then restructure `main` so the body becomes a callable `extract(binary, out_dir)` and `main` only parses argv:

```python
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

    # ... existing per-module loop, unchanged, writing cli.original.js and assets/


def main():
    if len(sys.argv) != 3:
        die("usage: extract_bun.py <binary> <out-dir>")
    extract(sys.argv[1], sys.argv[2])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: PASS — all container and extract tests.

- [ ] **Step 5: Commit**

```bash
git add tools/extract_bun.py tests/
git commit -m "feat: refuse PE input clearly and validate malformed module graphs"
```

---

### Task 4: Rewrite the `/$bunfs/` path transform

The core correctness fix. The scaffold rewrote only `require('…​.node')` — 2 of 5 references on linux, 5 of 9 on darwin — silently leaving every `file`-loader asset pointing into a filesystem that no longer exists.

**Files:**
- Modify: `tools/postprocess.py`
- Create: `tests/test_postprocess.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `postprocess.transform(code: str) -> (str, dict)` where the dict has integer keys `pragma`, `assets`, `file_urls`, `iife` and list keys `leftovers` and `build_paths`. `postprocess.main` reads/writes files and reports.

- [ ] **Step 1: Write the failing test**

Create `tests/test_postprocess.py`:

```python
import pytest


NODE_REQUIRE = 'var l5l=re(function(A,q){q.exports=require("/$bunfs/root/image-processor.node")});'
ASSET_CONST = 'var _qo="/$bunfs/root/chart.umd.min.js";'


def test_node_require_is_rewritten_to_the_assets_dir(postprocess):
    out, counts = postprocess.transform(NODE_REQUIRE)

    assert "/$bunfs/" not in out
    assert "require('path').join(__dirname,'assets',\"image-processor.node\")" in out
    assert counts["assets"] == 1


def test_file_asset_string_constant_is_rewritten(postprocess):
    """The mechanism is a plain string read by fs/promises.readFile, not a
    require - the scaffold's .node-only regex missed all of these."""
    out, counts = postprocess.transform(ASSET_CONST)

    assert "/$bunfs/" not in out
    assert out.startswith("var _qo=require('path').join(__dirname,'assets',")
    assert counts["assets"] == 1


def test_both_shapes_are_counted_together(postprocess):
    out, counts = postprocess.transform(NODE_REQUIRE + ASSET_CONST)

    assert counts["assets"] == 2
    assert counts["leftovers"] == []


def test_single_quoted_bunfs_literals_are_handled(postprocess):
    out, counts = postprocess.transform("var x='/$bunfs/root/mermaid.min.js';")

    assert counts["assets"] == 1
    assert "/$bunfs/" not in out
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_postprocess.py -v`
Expected: FAIL — `AttributeError: module 'postprocess' has no attribute 'transform'`.

- [ ] **Step 3: Write minimal implementation**

First delete the module docstring's scaffold banner — the box reading
`🟡 SCAFFOLD / BACKBONE — NEVER EXECUTED` and the paragraph under it. It stops
being true in this task, and Task 9 Step 5 greps for exactly that wording.
Keep the "What it does" list, updating it to match the transforms below.

Then replace the transform section of `tools/postprocess.py` with a pure
function. Add at module level:

```python
import json

# A /$bunfs/root/<name> string literal. This single pattern covers BOTH shapes
# observed in the real minified cli.js (see docs/findings.md 6):
#   require("/$bunfs/root/image-processor.node")   -> native addon
#   var _qo="/$bunfs/root/chart.umd.min.js"        -> file asset read via
#                                                     fs/promises.readFile
# The .node case simply becomes a dynamic require of an absolute path.
BUNFS_LITERAL = re.compile(r"""(['"])/\$bunfs/root/([\w.\-]+)\1""")
LEFTOVER_BUNFS = re.compile(r"/\$bunfs/root/[\w.\-]*")


def _asset_expr(match):
    name = match.group(2)
    return "require('path').join(__dirname,'assets'," + json.dumps(name) + ")"


def transform(code):
    """Pure text transform. Returns (new_code, counts)."""
    counts = {}
    code, counts["pragma"] = re.subn(r"^(?:\/\/[^\n]*\n)+", "", code, count=1)
    code, counts["assets"] = BUNFS_LITERAL.subn(_asset_expr, code)
    counts["file_urls"] = 0
    counts["iife"] = 0
    counts["leftovers"] = sorted(set(LEFTOVER_BUNFS.findall(code)))
    return code, counts
```

(`file_urls` and `iife` are filled in by Task 5; they are declared here so the
returned shape is stable for every caller.)

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_postprocess.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add tools/postprocess.py tests/test_postprocess.py
git commit -m "fix: rewrite every /\$bunfs/ literal, not just .node requires"
```

---

### Task 5: Pragma, `file://` build-path leaks, IIFE invocation, and fail-fast

**Files:**
- Modify: `tools/postprocess.py`
- Modify: `tests/test_postprocess.py`

**Interfaces:**
- Consumes: `postprocess.transform(code) -> (str, dict)` from Task 4.
- Produces: the same `transform` with `pragma`, `file_urls`, `iife` populated and `build_paths` added, plus `postprocess.check(code, counts) -> list[str]` returning fatal error strings (empty when the output is sound).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_postprocess.py`:

```python
REAL_HEAD = "// @bun @bytecode @bun-cjs\n(function(exports, require, module, __filename, __dirname) {"
REAL_TAIL = 'r("cli_after_main_complete")}PSE();})\n'
LEAK = ('function qpy(e){let r=ole.dirname(nwu.fileURLToPath('
        '"file:///home/runner/work/claude-cli-internal/claude-cli-internal/'
        'src/utils/computerUse/setup.ts"));return r}')


def test_pragma_is_stripped_so_the_file_starts_with_the_wrapper(postprocess):
    out, counts = postprocess.transform(REAL_HEAD + REAL_TAIL)

    assert counts["pragma"] == 1
    assert out.startswith("(function(exports, require, module, __filename, __dirname)")


def test_trailing_iife_is_invoked(postprocess):
    out, counts = postprocess.transform(REAL_HEAD + REAL_TAIL)

    assert counts["iife"] == 1
    assert out.rstrip().endswith("})(exports, require, module, __filename, __dirname)")


def test_baked_in_build_machine_file_url_is_rewritten(postprocess):
    """Bun's bundler resolved import.meta.url into a literal file:// URL of the
    build machine. The namespace prefix must be consumed too, or the result is
    the syntax error `nwu.__filename`."""
    out, counts = postprocess.transform(LEAK)

    assert counts["file_urls"] == 1
    assert "/home/runner/" not in out
    assert "nwu.__filename" not in out
    assert "ole.dirname(__filename)" in out


def test_iife_free_input_is_reported_as_fatal(postprocess):
    out, counts = postprocess.transform("(function(){return 1}")

    errors = postprocess.check(out, counts)

    assert errors
    assert any("IIFE" in e for e in errors)


def test_sound_output_reports_no_errors(postprocess):
    out, counts = postprocess.transform(REAL_HEAD + REAL_TAIL)

    assert postprocess.check(out, counts) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_postprocess.py -v`
Expected: FAIL — `counts["iife"]` and `counts["file_urls"]` are hardcoded `0`; `postprocess.check` does not exist.

- [ ] **Step 3: Write minimal implementation**

Add to `tools/postprocess.py`:

```python
# Bun's bundler resolves import.meta.url at build time into a literal file://
# URL of the build machine, e.g.
#   nwu.fileURLToPath("file:///home/runner/work/.../setup.ts")
# The optional `ns.` / `(0, ns.fn)` callee prefix must be consumed as well,
# otherwise the replacement yields the syntax error `nwu.__filename`.
FILE_URL_LEAK = re.compile(
    r"(?:\(0,\s*[\w$]+\.fileURLToPath\)|(?:[\w$]+\.)?fileURLToPath)"
    r"\((['\"])file://[^'\"]*\1\)"
)
BUILD_PATH_LEAK = re.compile(r"""['"](/home/runner/[^'"]*)['"]""")
```

Update `transform` so the placeholder counts are real:

```python
    code, counts["file_urls"] = FILE_URL_LEAK.subn("__filename", code)
    code, counts["iife"] = re.subn(
        r"\}\)\s*$",
        "})(exports, require, module, __filename, __dirname)",
        code)
    counts["build_paths"] = sorted(set(BUILD_PATH_LEAK.findall(code)))
    counts["leftovers"] = sorted(set(LEFTOVER_BUNFS.findall(code)))
    return code, counts
```

Order matters: strip pragma, rewrite `/$bunfs/` literals, rewrite `file://`
leaks, *then* append the IIFE invocation — appending first would break the
`\}\)\s*$` anchor.

Add the validator:

```python
def check(code, counts):
    """Return a list of fatal problems; empty means the output should load."""
    errors = []
    if not code.startswith("(function"):
        errors.append("output does not start with '(function' - Bun's CJS loader "
                      "will panic with 'Expected CommonJS module to have a "
                      "function wrapper'")
    if counts["iife"] != 1:
        errors.append("expected exactly 1 trailing IIFE to invoke, found "
                      + str(counts["iife"])
                      + " - the file does not end in '})'")
    return errors
```

Rewrite `main`'s tail to fail loudly instead of warning:

```python
    code, counts = transform(code)
    errors = check(code, counts)

    print(f"pragma lines stripped  : {counts['pragma']}")
    print(f"/$bunfs/ paths rewired : {counts['assets']}")
    print(f"file:// leaks rewritten: {counts['file_urls']}")
    print(f"IIFE invocations added : {counts['iife']}  (expected 1)")
    print(f"size: {orig_len} -> {len(code)} bytes")

    if errors:
        for e in errors:
            sys.stderr.write(f"error: {e}\n")
        sys.exit(1)

    with open(out, "w", encoding="utf-8") as fh:
        fh.write(code)
    print(f"wrote: {out}")

    for name in counts["leftovers"]:
        sys.stderr.write(f"warning: leftover bunfs reference: {name}\n")
    for path in counts["build_paths"]:
        sys.stderr.write(f"note: build-machine path still present: {path}\n")

    assets_dir = os.path.join(d, "assets")
    if os.path.isdir(assets_dir):
        for entry in sorted(os.listdir(assets_dir)):
            if entry not in code:
                sys.stderr.write(f"note: extracted asset never referenced: {entry}\n")
```

**Important:** the file must not be written when `errors` is non-empty — a
silently-broken `cli.cjs` reaching Bun is exactly the failure this task removes.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: PASS — all tests across all files.

- [ ] **Step 5: Commit**

```bash
git add tools/postprocess.py tests/test_postprocess.py
git commit -m "fix: rewrite baked-in file:// build paths and fail fast on broken output"
```

---

### Task 6: Integration tests against the real binaries

**Files:**
- Create: `tests/test_integration.py`

**Interfaces:**
- Consumes: `extract_bun.extract`, `extract_bun.find_bun_section`, `postprocess.transform` from Tasks 3–5; `real_elf_binary` / `real_macho_binary` fixtures from Task 1.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the tests**

Create `tests/test_integration.py`:

```python
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
```

- [ ] **Step 2: Run the integration tests**

Run: `python3 -m pytest tests/test_integration.py -v -m integration`
Expected: PASS for the ELF tests. The Mach-O tests skip unless the darwin binary is present; fetch it with:

```bash
mkdir -p /tmp/ccmac && cd /tmp/ccmac && \
  curl -sSL -o mac.tgz "https://registry.npmjs.org/@anthropic-ai/claude-code-darwin-arm64/-/claude-code-darwin-arm64-2.1.239.tgz" && \
  tar xzf mac.tgz
```

- [ ] **Step 3: Run the full suite**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS, zero skips when both binaries are present.

- [ ] **Step 4: Commit**

```bash
git add tests/test_integration.py
git commit -m "test: integration coverage against real ELF and Mach-O binaries"
```

---

### Task 7: Rewrite `build.sh` — cross-platform, artifacts only

**Files:**
- Modify: `scripts/build.sh`

**Interfaces:**
- Consumes: `tools/extract_bun.py <binary> <out-dir>`, `tools/postprocess.py <extract-dir>`.
- Produces: `$OUT_DIR/extract/cli.original.cjs` + `$OUT_DIR/extract/assets/`; prints the exact `bun` command to run it. No `PATH` mutation, no launcher file.

- [ ] **Step 1: Rewrite the script**

Replace `scripts/build.sh` in full:

```bash
#!/usr/bin/env bash
#
# build.sh - extract Claude Code's JS from its Bun standalone binary and
# post-process it to run under a stock Zig-era Bun.
#
# Produces artifacts and prints how to run them. Installs NOTHING on PATH:
# creating a `claude` executable could shadow the real one.
#
# Usage:
#   scripts/build.sh [path-to-native-binary]
#
# Env:
#   BUN_BIN   bun to check against (default: `command -v bun`)
#   OUT_DIR   where artifacts land (default: ./build)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$HERE/build}"
MIN_BUN="1.3.14"   # last Zig release AND the minimum that loads Claude's cli.js

info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Locate the native binary
NATIVE="${1:-}"
if [ -z "$NATIVE" ]; then
  DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
  NATIVE="$(ls -1d "$DATA"/claude/versions/* 2>/dev/null | sort -V | tail -1 || true)"
fi
if [ -z "$NATIVE" ]; then
  NATIVE="$(command -v claude || true)"
fi
[ -n "$NATIVE" ] && [ -f "$NATIVE" ] || die "native Claude binary not found; pass it as an argument"
info "native binary: $NATIVE"

# 2. Check Bun (advisory - extraction works without it)
BUN_BIN="${BUN_BIN:-$(command -v bun || true)}"
if [ -n "$BUN_BIN" ] && [ -x "$BUN_BIN" ]; then
  BUN_VER="$("$BUN_BIN" --version 2>/dev/null | head -1 | sed 's/-.*//')"
  info "bun: $BUN_VER ($BUN_BIN)"
  if [ "$(printf '%s\n%s\n' "$BUN_VER" "$MIN_BUN" | sort -V | head -1)" != "$MIN_BUN" ]; then
    warn "bun $BUN_VER is below $MIN_BUN; it will panic with"
    warn "'Expected CommonJS module to have a function wrapper'."
  elif [ "$BUN_VER" != "$MIN_BUN" ]; then
    warn "bun $BUN_VER is newer than $MIN_BUN - it may be a post-Zig (Rust) build,"
    warn "which defeats the de-rust goal. Prefer exactly $MIN_BUN."
  fi
else
  warn "bun not found; artifacts will still be built. Install the last Zig release:"
  warn "  curl -fsSL https://bun.sh/install | bash -s \"bun-v$MIN_BUN\""
fi

# 3. Extract
WORK="$OUT_DIR/extract"
info "extracting cli.js + assets -> $WORK"
rm -rf "$WORK"
mkdir -p "$OUT_DIR"
"$HERE/tools/extract_bun.py" "$NATIVE" "$WORK"
[ -f "$WORK/cli.original.js" ] || die "extraction failed: cli.original.js missing"

# 4. Post-process
info "post-processing cli.js for external Bun"
"$HERE/tools/postprocess.py" "$WORK"
[ -f "$WORK/cli.original.cjs" ] || die "post-process failed: cli.original.cjs missing"

# 5. Report - no install
info "artifacts ready:"
printf '      %s\n' "$WORK/cli.original.cjs" "$WORK/assets/"
echo
info "run it with:"
printf '      %s %s --version\n' "${BUN_BIN:-bun}" "$WORK/cli.original.cjs"
echo
warn "Nothing was installed on PATH. Creating a 'claude' launcher could shadow"
warn "your real installation - run the command above by full path instead."
```

- [ ] **Step 2: Make `postprocess.py` executable**

`tools/postprocess.py` ships as `-rw-r--r--` while `extract_bun.py` is `-rwxr-xr-x`.
`build.sh` invokes both directly, so the build dies with "Permission denied"
without this:

```bash
chmod +x tools/postprocess.py
git update-index --chmod=+x tools/postprocess.py
ls -l tools/postprocess.py
```

Expected: mode `-rwxr-xr-x`.

- [ ] **Step 3: Run it**

Run: `scripts/build.sh /usr/bin/claude`
Expected: extraction prints 8 modules; post-process prints `/$bunfs/ paths rewired : 5`, `file:// leaks rewritten: 7`, `IIFE invocations added : 1`; exit 0; no file created under `~/.local/bin`.

- [ ] **Step 4: Confirm nothing was installed**

Run: `ls -la ~/.local/bin/claude 2>&1; command -v claude`
Expected: `No such file or directory` for the first; `command -v claude` still resolves to `/usr/bin/claude`.

- [ ] **Step 5: Commit**

```bash
git add scripts/build.sh tools/postprocess.py
git commit -m "feat: cross-platform build.sh that emits artifacts and installs nothing"
```

---

### Task 8: Install Bun 1.3.14 and run the verification ladder

The payoff: answers `findings.md` §10 empirically. **Do not skip a rung, and do not paraphrase output — paste it.**

**Files:**
- Create: `docs/verification-2026-08-22.md`

**Interfaces:**
- Consumes: `scripts/build.sh` from Task 7.
- Produces: a verification record with pasted command output, consumed by Task 9's doc rewrite.

- [ ] **Step 1: Install Bun 1.3.14 without mutating the shell profile**

Do **not** use `curl https://bun.sh/install | bash`. That installer
unconditionally appends `export BUN_INSTALL=` / `export PATH=` to the first
writable of `~/.bash_profile`, `~/.bashrc` or `~/.zshrc` with no opt-out, and
runs `bun completions` — both violate this plan's "changes nothing on the box"
constraint. Fetch the release archive directly instead:

```bash
mkdir -p "$HOME/.bun-1.3.14"
curl -fsSL -o /tmp/bun-1.3.14.zip \
  https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-linux-x64.zip
unzip -o -j /tmp/bun-1.3.14.zip 'bun-linux-x64/bun' -d "$HOME/.bun-1.3.14"
chmod +x "$HOME/.bun-1.3.14/bun"
"$HOME/.bun-1.3.14/bun" --version
```

Expected: `1.3.14`. Nothing is added to `PATH` and no rc file is touched.

If the binary dies with `Illegal instruction`, this host lacks AVX2 — use the
`bun-linux-x64-baseline.zip` asset instead. (Checked on this host: AVX2 is
present, so the standard build is correct.)

- [ ] **Step 2: L1+L2 — build from the real ELF binary**

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh /usr/bin/claude
```

Expected: 8 modules; `/$bunfs/ paths rewired : 5`; `file:// leaks rewritten: 7`; `IIFE invocations added : 1`; no leftover warnings.

- [ ] **Step 3: L3 — syntactic validity of the CJS wrapper**

**Do not use `node --check`.** Claude's `cli.js` uses ES explicit resource
management — 30 `using x =` and 5 `await using x =` declarations on linux
(31 and 12 on darwin). Node 22 rejects `using` outright, and rejects
`await using` *even with* `--js-explicit-resource-management`. That failure is
inherent to the source — it reproduces on the untransformed `cli.original.js` —
so a `SyntaxError: Unexpected identifier` from node says nothing about our
transforms and must never be mistaken for one.

Use Bun, whose parser accepts the syntax. `new Function(src)` compiles without
executing:

Create `scripts/syntax-check.js`:

```javascript
// Compile-only syntax check. new Function() parses the source and throws on a
// syntax error, but never runs it — so this is safe on a 23 MB CLI bundle.
// Runs under Bun because Node rejects the `using` / `await using` declarations
// the real cli.js contains.
const fs = require("fs");

const target = process.argv[2];
if (!target) {
  console.error("usage: bun scripts/syntax-check.js <file>");
  process.exit(2);
}

try {
  new Function(fs.readFileSync(target, "utf8"));
  console.log("SYNTAX OK");
} catch (err) {
  console.error("SYNTAX FAIL:", err.message);
  process.exit(1);
}
```

```bash
"$HOME/.bun-1.3.14/bun" scripts/syntax-check.js build/extract/cli.original.cjs
```

Expected: `SYNTAX OK`. This catches a broken pragma-strip or IIFE append
without executing anything.

- [ ] **Step 3b: L3 on the darwin output — the macOS path's real check**

Parsing is platform-independent, so the Linux Bun can syntax-check the *darwin*
extraction even though it cannot run it. This is the strongest verification the
macOS path can get on this host, and it is stronger than the `node --check` the
plan originally specified:

```bash
OUT_DIR=/tmp/macbuild scripts/build.sh /tmp/ccmac/package/claude
"$HOME/.bun-1.3.14/bun" scripts/syntax-check.js /tmp/macbuild/extract/cli.original.cjs
```

Expected: post-process reports `/$bunfs/ paths rewired : 9` and
`file:// leaks rewritten: 8`, then `SYNTAX OK`.

- [ ] **Step 4: L5 — rewritten asset paths resolve**

```bash
ls -la build/extract/assets/
grep -o "require('path').join(__dirname,'assets'" build/extract/cli.original.cjs | wc -l
```

Expected: 5 assets listed; count `5`.

Use `grep -o … | wc -l`, not `grep -c`. `grep -c` counts *matching lines*, and
minified code puts several rewrites on one line — it reports 4 here, not 5.

- [ ] **Step 5: L4 — the actual run under Zig-era Bun**

Run it against a scratch config dir so the live session's `~/.claude` state is
never touched:

```bash
CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs --version
```

Expected: `2.1.222 (Claude Code)`.

**If it fails**, that is the finding, not a blocker. Capture the complete error and classify it:
- `Expected CommonJS module to have a function wrapper` → the wrapper transform is wrong; re-check Step 3 and the tail of `cli.original.js`.
- `SyntaxError: Unexpected identifier` near a `using` declaration → you ran the check under Node, not Bun. Re-read Step 3; this is not a transform bug.
- A missing Bun API / `undefined is not a function` → **findings §10 has materialised**: Claude 2.1.222 needs a Bun newer than the last Zig release. Record the exact API and stop; do not work around it.
- `Cannot find module '.../assets/X.node'` → an asset was not extracted; re-check Step 4.

- [ ] **Step 6: Record the evidence**

Create `docs/verification-2026-08-22.md` containing, for each rung, the exact command and its **pasted, unedited** output, plus a header stating host (`Linux x86_64`, glibc 2.36), Claude version, and Bun version. State plainly which rungs passed and which are unavailable on this host.

- [ ] **Step 7: Commit**

```bash
git add docs/verification-2026-08-22.md
git commit -m "docs: record the end-to-end verification run on Bun 1.3.14"
```

---

### Task 9: Rewrite the documentation against measured reality

**Files:**
- Modify: `docs/status.md`, `docs/findings.md`, `docs/bun-section-format.md`, `docs/runbook.md`, `README.md`

**Interfaces:**
- Consumes: `docs/verification-2026-08-22.md` from Task 8. Every ✅ claimed must trace to a pasted output there.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Rewrite `docs/status.md`**

Replace the scaffold map with a verified-on-what matrix: component × platform × evidence. Delete work items 1, 2 and 4 (now done); rewrite item 3 as "verified on Linux, pending on macOS"; keep item 5 (update survival). Every row cites the verification doc.

**Add a Windows/PE section.** `find_bun_section`'s refusal message tells the
user to "see docs/status.md", and today this document says nothing about
Windows. State: the win32-x64 build is a PE carrying the same `.bun` section
(confirmed: `.bun` at rawoff=95182336 in `claude.exe` 2.1.239), the payload
format is identical, extraction is deliberately unimplemented, and running the
extracted JS would additionally need a Windows Bun. Without this the error
message points at nothing.

- [ ] **Step 2: Update `docs/findings.md`**

- §4: state that the module list is **per-platform and per-version** — 15 on darwin-arm64 2.1.239 vs 8 on linux-x64 2.1.222 — and that the entry module *name* differs (`/$bunfs/root/cli` vs `/$bunfs/root/src/entrypoints/cli.js`), so extractors must use `entry_point_id`, never a name match.
- §5a: note the invariant was re-confirmed on ELF (stored bytes begin `\x7fELF`).
- §6: replace the ported-from-ClawGod transform list with the measured one, including the correction that Bun bakes `import.meta.url` into literal `file://` URLs, so `fileURLToPath(import.meta.url)` never appears and the scaffold's regex matched **0** occurrences on both binaries.
- §9: the npm bootstrap is *not* a dead end for extraction — per-platform native binaries ship as `@anthropic-ai/claude-code-<platform>` optional dependencies, which is how a Mach-O binary was obtained without a Mac.
- §10: answer it with the Task 8 result.

- [ ] **Step 3: Update `docs/bun-section-format.md`**

Add an ELF section-header walk and a PE note beside the existing Mach-O walk, keeping the payload spec as the shared core. State that all three containers were confirmed to wrap a byte-identical payload.

**Fix a pre-existing arithmetic error while you are here:** the doc calls the
trailer `"\n---- Bun! ----\n"` **15 bytes** and derives
`start = len(payload) - 47`. It is **16 bytes**, so the offset is
`len(payload) - 48`. The code is unaffected (it uses `len(TRAILER)`), but the
prose is wrong and would mislead anyone reimplementing from the spec.

- [ ] **Step 4: Update `docs/runbook.md` and `README.md`**

Runbook: Linux and macOS paths; verification by full path with nothing installed. README: replace the status table with the verified matrix and drop "not yet runnable as-is".

Also record a known behavioural difference: the old design's launcher exported
`CLAUDE_CODE_EXECPATH=<native binary>` "for shell integrations" (findings §6).
`build.sh` no longer writes a launcher, so anyone running `cli.original.cjs`
directly for real use — not just `--version` — should export it themselves.
Document the variable and why it exists; do not reintroduce the launcher.

- [ ] **Step 5: Verify no stale claims remain**

Run: `grep -rn --exclude-dir=superpowers "never executed\|not yet runnable\|NEVER EXECUTED\|SCAFFOLD" docs/ README.md tools/ scripts/`
Expected: no output. Any hit is a stale claim to fix.

`--exclude-dir=superpowers` is required: `docs/superpowers/` holds this plan and
the design spec, which describe the scaffold state as **historical record**.
Those must not be scrubbed — they are the account of what was true before.

- [ ] **Step 6: Run the full suite one last time**

Run: `python3 -m pytest tests/ -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add docs/ README.md
git commit -m "docs: replace scaffold posture with measured, verified status"
```
