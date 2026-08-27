# Findings

What is measured about Claude Code's native binary, and about running its
JavaScript on a **pre-Rust (Zig-era) Bun** instead of the runtime Anthropic
bundles.

> **Legend.** ✅ executed on **this host** (Linux x86_64, Debian 12, glibc
> 2.36) · 🍎 executed on the **reporting Apple Silicon Mac**, 2026-08-24,
> against its own installed 2.1.239 — reported first-hand, *not* measured here ·
> 🔎 real bytes parsed or the output accepted by Bun's parser, nothing executed ·
> 🖥️ needs hardware we do not have · ⛔ deliberately not implemented ·
> 📓 prior-session record (a Mac, 2026-08-21, 2.1.238), not re-checked ·
> 📄 read from source, not measured.
>
> Commands and pasted output: [verification-2026-08-22.md](./verification-2026-08-22.md).

**The four binaries these findings are measured against:**

| Platform | Container | Version | Size | How obtained |
|---|---|---|---|---|
| `linux-x64` | ELF | **2.1.222** | 289,467,400 B | `/usr/bin/claude`, pre-installed here (read-only, never executed) |
| `darwin-arm64` | Mach-O (thin arm64) | **2.1.239** | 324,973,552 B | `npm pack @anthropic-ai/claude-code-darwin-arm64` (§8) |
| `darwin-x64` | Mach-O (thin x86_64) | **2.1.241** | 333,784,816 B | first-party download endpoint, checksum-verified (§8) |
| `win32-x64` | PE | **2.1.239** | 337,672,352 B | `npm pack @anthropic-ai/claude-code-win32-x64` (§8) |

The `darwin-arm64` size was corroborated from the Mac 🍎: that machine's own
installed 2.1.239, under `~/.local/share/claude/versions/<version>`, is
**324,973,552 bytes** — two acquisition routes, two operating systems, one
number.

**Almost nothing here is a constant.** Where a figure differs between platforms
or versions, both are given.

---

## 1. What "de-rust" means here

Bun is being rewritten from **Zig** to **Rust**. PR
[oven-sh/bun#30412](https://github.com/oven-sh/bun/pull/30412) ("Rewrite Bun in
Rust") was opened 2026-05-08 and **merged 2026-05-14T08:09:34Z** ✅ (GitHub
API). **`bun-v1.4.0` was published 2026-08-20T14:07:21Z** ✅ as the first Rust
release targeting all supported platforms. **1.3.14 remains the last Zig
release** (1.3.15 does not exist).

> **"Pre-Rust" is right about the rewrite and wrong as literal text.** ✅
> Bun 1.3.14 is not Rust-free: its `.comment` reads
> `rustc version 1.94.0-nightly (c61a3a44d 2025-12-09)` and it links vendored
> Rust crates — `lolhtml`, `cssparser-0.36.0`, `encoding_rs-0.8.35`,
> `selectors-0.33.0`. What it predates is the Zig→Rust rewrite of Bun's **own
> core**, verified by property rather than version: 1.3.14 carries **4** Zig
> source-path strings — `bundler/LinkerGraph.zig`, `bundler/OutputFile.zig`,
> `bundler/bundle_v2.zig`, `js_parser/ast/P.zig` — and 1.4.0 carries **0**.
> (`strings -n 6 bun | grep '\.zig' | sort -u` returns **7** lines on 1.3.14;
> three are embedded JavaScript using an identifier spelled `newResolver.zig`,
> not source paths.)

Claude Code ships as a Bun *standalone* executable, so the embedded runtime
cannot be swapped. Instead: **extract** `cli.js` and its assets, **post-process**
the JS so it runs outside the standalone sandbox, and **run** it under an
external, stock Bun 1.3.14.

> **Scope this honestly.** ✅ The artifact *requires* an external Bun. Running
> it on the Zig one is this project's deliberate choice — the whole point — not
> a constraint the artifact imposes: the same `cli.original.cjs` also runs on
> **Bun 1.4.0**, the Rust build, printing `2.1.222 (Claude Code)`, exit 0.
> 1.3.14 was shown *sufficient*, never *necessary*.

The native binary is only ever *read*. What "runs" does and does not mean is
§10 — read it before relying on this.

---

## 2. The native binary is a Bun standalone — on all three platforms ✅🔎

Each build embeds a serialized Bun module graph in a platform-specific section.
Only the container differs; the payload layout is identical everywhere (spec:
[bun-section-format.md](./bun-section-format.md)).

| Platform | Section | File offset | Section size | Payload size | Modules |
|---|---|---|---|---|---|
| `linux-x64` 2.1.222 ✅ | `.bun` (ELF) | 86904832 | 202513494 | 202513486 | 8 |
| `darwin-arm64` 2.1.239 ✅🍎 | `__BUN,__bun` (Mach-O) | 69107712 | 255007133 | 255007125 | 15 |
| `darwin-x64` 2.1.241 ✅ | `__BUN,__bun` (Mach-O) | 75755520 | 255171495 | 255171487 | 15 |
| `win32-x64` 2.1.239 🔎 | `.bun` (PE) | 95182336 | 242479616 | 242479175 | 9 |

The PE row was read by hand with a section-header walk; `extract_bun.py` refuses
PE by design ([status.md](./status.md) § Windows/PE).

**The `darwin-arm64` row carries 🍎, and that is this document's strongest
single result.** Every Mach-O figure here was produced by parsing a darwin
binary *on Linux*, on the argument that walking a container is byte arithmetic
and therefore platform-independent. On 2026-08-24 the same pipeline ran on an
Apple Silicon Mac against that machine's own 2.1.239 and printed the same four
numbers, the same asset count and the same shim figures (§6). The argument was
right; it is no longer only an argument. It shows the *container parse* is
platform-independent — nothing about the macOS layer underneath
([status.md](./status.md) § macOS execution).

The PE section is **padded**: `rawsize` exceeds `payload_size + 8` by 433 bytes
of file alignment, whereas on ELF and Mach-O the two are equal. Always trust the
u64 length prefix, not the section size.

The macOS install is path-independent 📓: the data dir resolves at runtime from
`XDG_DATA_HOME ?? ~/.local/share`, and the only hardcoded path check gates
generation of the `ClaudeCode.app` wrapper
(`process.execPath.startsWith(…/claude/versions/)`), not the CLI.

---

## 3. The Bun standalone format ✅

Byte-level spec: [bun-section-format.md](./bun-section-format.md). Summary:
locate the container's Bun section → a **u64 little-endian length prefix**, then
the payload, ending with the **16-byte** trailer `\n---- Bun! ----\n`. Just
before the trailer sits a **32-byte offsets struct** (`+8` `modules_offset`,
`+12` `modules_size`, `+16` `entry_point_id`); the modules table is
`modules_size / 52` records of **52 bytes** (`+0` name offset, `+4` name size,
`+8` content offset, `+12` content size, `+49` loader id).

Confirmed on all three containers.

---

## 4. The module list is per-platform AND per-version ✅

Two builds three patch versions apart differ in module count, in module
*contents*, and — the trap — in the entry module's **name**.

**`linux-x64` 2.1.222 — 8 modules** ✅

```
idx loader     size      name
 0  js        21.90 MB   /$bunfs/root/src/entrypoints/cli.js  ← ENTRY (entry_point_id = 0)
 1  js         2.1 KB    /$bunfs/root/image-processor.js      ← loader shim
 2  js         2.1 KB    /$bunfs/root/audio-capture.js        ← loader shim
 3  napi    1430.4 KB    /$bunfs/root/image-processor.node    ← native (ELF)
 4  napi     480.6 KB    /$bunfs/root/audio-capture.node      ← native (ELF)
 5  file     203.6 KB    /$bunfs/root/chart.umd.min.js        ← asset
 6  file     962.4 KB    /$bunfs/root/hljsBundle.generated.min.js ← asset
 7  file    3235.3 KB    /$bunfs/root/mermaid.min.js          ← asset
```

**`darwin-arm64` 2.1.239 — 15 modules** ✅

```
idx loader     size      name
 0  js        26.94 MB   /$bunfs/root/cli                     ← ENTRY (entry_point_id = 0)
 1  js         2.1 KB    /$bunfs/root/image-processor.js      ← loader shim
 2  js         2.1 KB    /$bunfs/root/audio-capture.js        ← loader shim
 3  js         2.1 KB    /$bunfs/root/url-handler.js          ← loader shim
 4  js         2.1 KB    /$bunfs/root/computer-use-swift.js   ← loader shim
 5  js         2.1 KB    /$bunfs/root/computer-use-input.js   ← loader shim
 6  napi    1220.1 KB    /$bunfs/root/image-processor.node    ← native (arm64)
 7  napi     859.1 KB    /$bunfs/root/computer-use-swift.node ← native (universal)
 8  napi    1652.4 KB    /$bunfs/root/computer-use-input.node ← native (universal)
 9  file     203.6 KB    /$bunfs/root/chart.umd.min.js        ← asset
10  file     962.4 KB    /$bunfs/root/hljsBundle.generated.min.js ← asset
11  file    3235.3 KB    /$bunfs/root/mermaid.min.js          ← asset
12  napi     427.8 KB    /$bunfs/root/audio-capture.node      ← native (arm64)
13  file    2177.2 KB    /$bunfs/root/payload.template.html.asset ← asset
14  napi     329.0 KB    /$bunfs/root/url-handler.node        ← native (arm64)
```

`win32-x64` 2.1.239 has **9** modules 🔎 — the linux set plus
`payload.template.html.asset` — and its names use the `B:/~BUN/root/` prefix.
Its entry module is `B:/~BUN/root/cli`: it follows **darwin's** short name, not
linux's.

**Consequences, all practical:**

- **Never identify the entry module by name.** Only `entry_point_id` is
  reliable — `0` on all three containers, which is *not* a licence to hardcode
  `0` either.
- **Never hardcode a module count or an asset list.** Extraction must be driven
  by the loader id (`napi`/`base64`/`file` → write to `assets/`).
- **The platform difference is not only naming.** `image-processor.node` is
  1430 KB on linux and 1220 KB on darwin, in different object formats. Extract
  per platform.

The entry module on **all three** platforms opens with the pragma
`// @bun @bytecode @bun-cjs` followed by a CommonJS wrapper
`(function(exports, require, module, __filename, __dirname) {…`, and ends with a
**non-invoked** `})`. §6 is the post-processing that requires.

---

## 5. Two gotchas that break naive extractors

### 5a. Stored content is ALWAYS raw ✅

The content stored for a module is *always* the raw bytes. The loader id says
how Bun would *present* that module to JS at runtime; it never describes an
encoding applied to the stored payload. So **never decode anything** — an early
port of the extractor base64-decoded `.node` modules and produced 71-byte
"modules". Write bytes verbatim, including for a genuine `base64` module, which
is why the extractor still accepts that loader.

Measured with a standalone parser against both shipped binaries: the raw loader
byte at record offset 49 for every `.node` module is **10**, and in Bun
1.3.14's own enum
([`src/bundler/options.zig`](https://raw.githubusercontent.com/oven-sh/bun/bun-v1.3.14/src/bundler/options.zig),
`pub const Loader = enum(u8)`) **10 is `napi`**. There has never been a
`base64` addon in any binary this project has looked at.

> **Correction — this file called those addons `base64` until 2026-08-22.** The
> loader table had been transcribed from prior art instead of read from Bun's
> source, and it omitted `jsonc = 7`, shifting every id from 7 upward down by
> one. Extraction still worked by luck: the mislabel landed inside the
> `{napi, base64, file}` accept-set. A genuine `base64` module (real byte 11)
> would have been labelled `dataurl`, missed the accept-set and been **silently
> dropped**; a `sqlite` module (15) would have fallen off the end of that
> 15-entry table and been reported as `unknown(15)`. Fixed in `61957a6`, with
> `test_loader_ids_match_bun_1_3_14` pinning the table and
> `test_genuine_base64_module_is_written_to_disk` covering the latent bug. The
> lesson generalises: **a plausible story told to explain an unmeasured number
> is how this repo's evidence rule gets broken.**

The raw-bytes fact is asserted permanently by `tests/test_integration.py`: the
bytes stored for `/$bunfs/root/image-processor.node` in `/usr/bin/claude` begin
`\x7fELF` (`test_real_elf_binary_extracts`); the darwin equivalent asserts the
universal (`0xCAFEBABE`) or thin-arm64 (`0xCFFAEDFE`) magic.

### 5b. What ClawGod actually does and does not extract ✅

Measured against ClawGod at commit `4401fdb` (2026-08-22):

- Its loader table (`install.sh`, `const LOADERS`) is **Bun's**, `jsonc: 7`
  included, `10: 'napi'`. It matched Bun's source all along; ours did not.
- The `.node` addons carry byte 10, so ClawGod's `napi` branch **writes them
  out**, to `vendor/<name>/<arch>-<os>/<name>.node`. It extracts native modules
  correctly. (This document previously claimed it extracted **zero** — false,
  and it followed directly from 5a's mislabel.)

The real, measured differences are narrower:

- **`file`-loader assets are dropped.** Its loop has no `file` branch, so
  `chart.umd.min.js`, `hljsBundle.generated.min.js`, `mermaid.min.js` (and
  darwin's `payload.template.html.asset`) fall to `else { dropped++ }`.
- **Its rewrite is `.node`-only.** `require\(['"](/\$bunfs/root/([\w-]+)\.node)['"]\)`
  matches **2** of the 5 `/$bunfs/` literals in the linux 2.1.222 entry module,
  leaving the three asset paths pointing into a `/$bunfs` that does not exist at
  runtime — and never extracted either. That is the same defect this project's
  own ported version had (§6 transform 2).

[`extract_bun.py`](../tools/extract_bun.py) handles `napi`, `base64` and `file`
loaders and writes raw bytes; `postprocess.py` rewrites the literal in any
syntactic position.

---

## 6. Post-processing `cli.js` to run outside the standalone ✅

`tools/postprocess.py` has been run against all three real binaries; these are
the transforms it applies, with the counts it produced.

1. **Strip the leading pragma comment lines** (`^(?:\/\/[^\n]*\n)+`, once) —
   necessary **only because transform 4 invokes the wrapper ourselves** (see the
   2×2 below).
2. **Rewrite every `/$bunfs/root/<name>` string literal**, regardless of
   syntactic position, to `require('path').join(__dirname,'assets',"<name>")`.
   The literals appear in **two** shapes in the real minified code: as a
   `require()` argument (native addons) *and* as a bare string constant later
   read through `fs/promises.readFile` (file-loader assets). Rewriting only the
   first leaves the second silently pointing into a `/$bunfs` that no longer
   exists.
3. **Rewrite build-time `file://` leaks to `__filename`** (see below).
4. **Append the CJS IIFE invocation** `(exports, require, module, __filename,
   __dirname)` to the trailing `})`.
5. **Refuse** the build if any `/$bunfs/` (or `B:/~BUN/`) reference survived, or
   if the rewritten code references an asset that is not on disk; **report** any
   surviving `/home/runner/…` build-machine path and any extracted asset the
   code never mentions. Reports print before the file is written.

### Why the pragma has to go — the measured 2×2 ✅

The pragma `// @bun @bytecode @bun-cjs` is what tells Bun *"this file is a
CommonJS function wrapper, invoke it for me"*. All four combinations, run to
completion against the real linux 2.1.222 entry module (`--version`), on two Bun
versions ✅:

| build | Bun 1.3.13 | Bun 1.3.14 |
|---|---|---|
| pragma **stripped** + IIFE invoked — **as shipped** | panic | `2.1.222 (Claude Code)` |
| pragma **kept** + IIFE **not** invoked | `2.1.222 (Claude Code)` | `2.1.222 (Claude Code)` |
| pragma kept + IIFE invoked | panic | **panic** |
| pragma stripped + IIFE not invoked | panic | exit 0, **no output** (nothing ran) |

"panic" is `TypeError: Expected CommonJS module to have a function wrapper.`
So the pragma **alone** does not make Bun panic; the panic comes from pragma
**plus** manual invocation. Stripping is necessary only because this project
also invokes. Keeping the pragma and not invoking is a viable alternative shape
with more version headroom (§9).

### What `check()` refuses to write ✅

`postprocess.py` refuses to write `cli.original.cjs` at all unless **six**
conditions hold (counted by AST in `tools/postprocess.py` on 2026-08-24: six
conditions, eight `errors.append` sites — the sixth reports its three failure
shapes separately):

1. the output starts with `(function`;
2. exactly one IIFE invocation was appended (`counts["iife"] == 1`; the pattern
   is `$`-anchored, so more than one is not reachable);
3. **no `/$bunfs/` — or Windows `B:/~BUN/` — reference survived the rewrite.**
   The old leftover detector demanded the same `root/<basename>` shape the
   rewriter handles, making it blind in precisely the cases the rewriter could
   not handle; it was demonstrably vacuous, since making the pattern unmatchable
   left the suite green;
4. **every `assets/<name>` the rewritten code will reach for at runtime is a
   file the extractor actually wrote** — the direction that catches a whole
   loader kind falling out of the accept-set (a live risk since the enum
   correction moved byte 9, `wasm`, from written to dropped);
5. it is **not** the case that zero `/$bunfs/` literals were rewritten while
   `assets/` holds files — the "silently asset-less" outcome a wrong VFS prefix
   (Windows' `B:/~BUN/root/`) would produce;
6. **the image shim's bookkeeping adds up** (§10): rewriting one gate call site
   leaves exactly one fewer `<gate>()` call, and rewriting none leaves the count
   untouched. Fatal rather than a note, because a text rewrite that *spread*
   would take the ripgrep gate with it, and that gate's failure mode is a wrong
   answer rather than an error. It counts how many sites moved, never which —
   hence selection by shape (§10).

A silently broken output reaching Bun would surface only as that confusing
panic, or — for a missing asset — as nothing at all, because both of Claude's
addon loaders swallow their own failures. So the failure is made loud and early.

### The measured counts ✅

| | `linux-x64` 2.1.222 | `darwin-arm64` 2.1.239 | `darwin-x64` 2.1.241 |
|---|---|---|---|
| pragma block stripped | 1 | 1 | 1 |
| `/$bunfs/` literals rewritten | **5** | **9** | **9** |
| `file://` leaks rewritten | **7** | **8** | **8** |
| IIFE invocations added | 1 | 1 | 1 |
| leftover `/$bunfs/` references | **0** | **0** | **0** |
| build-machine path notes (informational) | 3 | 3 | 3 |
| never-referenced extracted assets | 0 | 0 | 0 |
| image shim gate | `CE` | `AE` | `Tw` |
| image shim gate call sites, before → after | **21 → 20** | **23 → 22** | **23 → 22** |
| image shim applied | 1 | 1 | 1 |
| size | 22,960,130 → 22,959,448 B | 28,244,743 → 28,244,063 B | 28,245,789 → 28,245,109 B |
| shimmed vs `NRC_NO_IMAGE_SHIM` artifact | **4** bytes differ, at 4,337,061 | **4** bytes differ, at 7,104,588 | **4** bytes differ, at 7,103,971 |

Every row was re-measured on this host on **2026-08-24** by rebuilding all three
binaries from the real files, and the byte-diff row by rebuilding each with and
without `NRC_NO_IMAGE_SHIM=1`. **This table is where these figures live**:
README, [status.md](./status.md) and [runbook.md](./runbook.md) point at it and
quote build output rather than restating numbers.

The **`darwin-arm64` column was independently reproduced on a Mac** 🍎 on the
same date — gate `AE`, `23 -> 22`, `applied: 1`, `28244743 -> 28244063` bytes:
every figure in that column that a single build prints. The byte-diff row was
**not** re-run there (it needs a second build with `NRC_NO_IMAGE_SHIM` set), and
neither was anything in the other two columns.

The two darwin columns are the same release family at different versions —
2.1.239 for `arm64` because that is the copy the rest of this document uses,
2.1.241 for `x64` because that is what the endpoint served (§8). They agree on
every count and disagree on every offset, which is what a per-build minifier
should look like.

The rewrite count equals the extracted-asset count on all three builds (5, 9 and
9) with no "asset never referenced" note, so every asset written to disk is
referenced by exactly one rewritten literal.

The four differing bytes are `CE()`/`AE()`/`Tw()` → `true`. The size is
unchanged by the shim because all three minified gate names happen to be two
characters long — which is also why the shim cannot be spotted from a size. The
only cheap way to tell the two artifacts apart is the build log.

### The `fileURLToPath` correction — why the ported regex found nothing ✅

The scaffolded transform targeted `(0, ns.fileURLToPath)(…import.meta.url)`.
Measured against the real binaries, that pattern matches **0 occurrences** — and
"0" invited the wrong conclusion, that the transform was unnecessary:

> **Bun's bundler resolves `import.meta.url` at build time.** What survives into
> the shipped `cli.js` is not an `import.meta.url` expression but a **literal
> `file://` URL of Anthropic's build machine** — e.g.
> `fileURLToPath("file:///home/runner/work/claude-cli-internal/…")`.

So the leak is real and common (7 on linux, 8 on darwin); it simply never looks
like the pattern that was searched for. The current regex matches the
literal-URL form and must also consume an optional `ns.` / `(0, ns.fn)` callee
prefix: replacing only the argument would produce `ns.__filename`, a syntax
error.

Precision, since the numbers matter 🔎: the substring
`fileURLToPath(import.meta.url)` does occur (twice per binary), and a wider
`fileURLToPath(…import.meta.url)` match finds **5 sites per binary**. **Every
one sits inside a string literal, not in executable code** — confirmed by
dumping the surrounding bytes:

- **3 sites** are inside an embedded `.mjs` **script source carried as text**
  (`scriptsShaFor()`, `package-build.mjs`, `storybook/http-serve.mjs`). The
  doubled escaping is the tell — `\\u2014` and `\\${…}` — and the win32 build's
  copy preserves Windows line endings as `\r` escape sequences (20,932
  occurrences before a newline in that entry module, against 143 on linux).
- **2 sites** — the two exact-substring hits — are inside embedded **Markdown
  documentation about ESM**.

The same holds for all 16 bare `import.meta.url` occurrences. Rewriting any of
them would corrupt embedded text rather than fix a path, so they are left alone;
that is why the real leak count is 7/8 and not higher.

🔎 After post-processing, each output still contains 16 `import.meta.url`
references, **12 textual `fileURLToPath` hits — 9 call sites and 3
`import { fileURLToPath } from 'node:url'` lines inside embedded script text** —
and 2 bare `file:///` literals on linux, 3 on darwin. No command in the
verification run hit a problem from them; no stronger claim is available.

### `CLAUDE_CODE_EXECPATH` is write-only ✅

`scripts/build.sh` writes **no launcher and installs nothing**: a file named
`claude` on `PATH` could shadow a real installation. It prints the full-path
command instead. The consequence is *not* that you should export
`CLAUDE_CODE_EXECPATH` yourself — measured in the post-processed linux 2.1.222
artifact:

- `process.env.CLAUDE_CODE_EXECPATH` — **0 occurrences**. The CLI never reads
  the variable, in any form.
- The string appears exactly **3** times: the constant
  `hNs="CLAUDE_CODE_EXECPATH"`, and two entries in lists of environment-variable
  names the CLI manages for spawned shells and background sessions.
- The one place it is *used* is a **write**, in `getEnvironmentOverrides`:
  `c[hNs]=process.execPath`, unconditional, not gated on any standalone check.

So every shell the CLI spawns receives `CLAUDE_CODE_EXECPATH=<the bun binary>`,
because here `process.execPath` *is* bun. The generated `find`/`grep` shell
functions read it back at the shell level:

```bash
function find {
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin='<bundled bfs path>'
  ...
  (exec -a bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"})
}
```

The `[[ -x ]]` guard does not save you: bun *is* executable, so `find` and
`grep` inside a spawned shell resolve to bun invoked with `bfs`/`ugrep`
arguments. Read out of the shipped source, **not observed live** 🔎. Same family
as §10: things that differ because the process is not a Bun standalone.

---

## 7. Ready-made tools (prior art) 📄

| Tool | Extracts JS | Native modules | Runs via Bun | Notes |
|---|---|---|---|---|
| [0Chencc/clawgod](https://github.com/0Chencc/clawgod) | ✅ parses table | ✅ `napi` — correctly ✅ | ✅ wrapper + stock bun | Closest to our goal; 40 patches. Drops `file`-loader assets and rewrites only `.node` requires (§5b) |
| [vicnaum/bun-demincer](https://github.com/vicnaum/bun-demincer) | ✅ + split/deobfuscate/**reassemble** | ✅ | — | Most comprehensive decompiler |
| [vibheksoni/unbuned](https://github.com/vibheksoni/unbuned) | ✅ pure-Python, zero-dep | ❌ | — | Heuristic; skips native modules **and universal Mach-O** |
| [lafkpages/bun-decompile](https://github.com/lafkpages/bun-decompile) | ✅ + sourcemaps | — | — | Web + CLI |
| [@shepherdjerred/bun-decompile](https://www.npmjs.com/package/@shepherdjerred/bun-decompile) | ✅ + AI de-minify | — | — | Built to inspect Claude Code CLI |
| [Piebald-AI/tweakcc](https://github.com/Piebald-AI/tweakcc) | ✅ auto-locate | ✅ repacks | ❌ repacks into the binary | Themes/prompts; edits the binary rather than running the JS externally |

This section used to assert that ClawGod misses the addons and that its
`fileURLToPath` transform matches nothing. **Both are false** (§5b): it extracts
the addons correctly, and its transform matches **7 sites**, the same 7 this
project rewrites — it was *this project's own* scaffolded port that matched 0.
The measured differences are the narrow ones in §5b. Prefer whichever tool you
can re-measure.

**And it is ahead of this project on the equivalence gap (§10).** ✅ At the same
commit, ClawGod's patch list already contains
`'Bun.isStandaloneExecutable → true'` — the exact global flip §10 shows restores
native image processing — *and*
`'Restore Glob/Grep tools (un-inline EMBEDDED_SEARCH_TOOLS)'`, which checks that
real `bfs`/`ugrep` binaries exist on `PATH` before claiming embedded search.
That second patch is the mitigation for the trap §10 documents. Prior art
addressed both halves before this project noticed either. The patch count is
`grep -cE '^    name: ' install.sh` at `4401fdb` = **40** (this table said 29
until 2026-08-23).

---

## 8. Getting the native binaries — without a Mac, and without installing ✅

### The first-party download endpoint — the documented route ✅

The base URL is read from `claude.ai/install.sh` itself, as the value of
`DOWNLOAD_BASE_URL`: `https://downloads.claude.ai/claude-code-releases`.

**The runnable flow, with verification *inside* it** — run here on 2026-08-24
for both `P=darwin-arm64` and `P=darwin-x64`. You are about to hand a third of a
gigabyte of unverified bytes to a Mach-O parser that slices at offsets it reads
out of the file, so the `if` is the point: it deletes the file on mismatch
rather than leaving it for a step someone skips.

```bash
BASE=https://downloads.claude.ai/claude-code-releases
V="$(curl -fsSL "$BASE/latest")"        # 2.1.241 here on 2026-08-24
P=darwin-arm64                          # Apple Silicon. Intel Mac: darwin-x64.
                                        # Linux: linux-x64 — same flow verbatim
DEST=/tmp/ccdl/claude-$P.bin            # a name that is NOT `claude`

mkdir -p /tmp/ccdl
curl -fsSL "$BASE/$V/manifest.json" -o /tmp/ccdl/manifest.json
SUM="$(python3 -c 'import json,sys; print(json.load(open("/tmp/ccdl/manifest.json"))["platforms"][sys.argv[1]]["checksum"])' "$P")"

if curl -fsSL "$BASE/$V/$P/claude" -o "$DEST" &&
   printf '%s  %s\n' "$SUM" "$DEST" | shasum -a 256 -c -
then echo "verified $V $P -> $DEST"
else rm -f "$DEST"; echo "download or checksum FAILED; nothing left on disk" >&2
fi
```

```
/tmp/ccdl/claude-darwin-arm64.bin: OK
verified 2.1.241 darwin-arm64 -> /tmp/ccdl/claude-darwin-arm64.bin
/tmp/ccdl/claude-darwin-x64.bin: OK
verified 2.1.241 darwin-x64 -> /tmp/ccdl/claude-darwin-x64.bin
```

The failure path was exercised too, with a truncated file and an all-zero
checksum: `shasum` printed `FAILED`, the `else` branch ran, and the file was
gone afterwards.

**Never let the file land as `claude`.** The endpoint's last path component is
literally `claude` (the manifest's `binary` field is `"claude"` too), so
`curl -O` would drop a file by that name in your working directory — exactly the
name that can later be found on a `PATH` and shadow a real installation. Always
`-o` a name of your own. This repo never creates that name anywhere.

Measured on this host on **2026-08-24** (Linux; no Mac, no npm, no account, no
token):

| Path | What came back |
|---|---|
| `/latest` | `2.1.241` |
| `/stable` | `2.1.231` |
| `/<v>/manifest.json` | JSON: `version`, `commit`, `buildDate`, `platforms`, `sdkCompat` |
| `/<v>/<platform>/claude` | `200` — checked for `darwin-arm64`, `darwin-x64`, `linux-x64` |
| `/<v>/<platform>/claude.zst` | `200` — same three |
| `/<v>/manifest.zst.json` | `200` — a *second* manifest, for the compressed files |

(The other five platforms were read out of `manifest.json` but not requested.)

`latest` and `stable` **are different pointers and today they do not agree** —
2.1.241 vs 2.1.231. Pinning `<v>` to a literal version is a third choice.

`manifest.json` for 2.1.241 carried
`commit c87e2742fc9ad269ec8920460d00a091b1e410f0`,
`buildDate 2026-08-22T23:04:33Z`, and a `platforms` object with **eight** keys —
`darwin-arm64`, `darwin-x64`, `linux-arm64`, `linux-arm64-musl`, `linux-x64`,
`linux-x64-musl`, `win32-arm64`, `win32-x64` — each exactly `binary` (always
`claude`), `checksum` (sha256) and `size`.

| Platform | `checksum` (sha256, 2.1.241) |
|---|---|
| `darwin-arm64` | `1495eb7c42d3b4451f5f1cd38b6d498d22a4a38c802bc2be5c1cf1795e64820d` |
| `darwin-x64` | `cf01b8cace66485ef5b476f14d96f69af61194a38c3df8412a80eb8f1316c10d` |

**Those two lines are the only place in this repo that states a binary
checksum.** Both files were downloaded here and both matched; the on-disk size
and the CDN's `Content-Length` both equalled the manifest's `size` — *for the
version actually fetched*. That is §1's table for `darwin-x64` (both 2.1.241)
but **not** for `darwin-arm64`, whose §1 row is the 2.1.239 copy at 324,973,552
B, 82,080 bytes below 2.1.241's 325,055,632 B. Cross-referencing a size across
two Claude versions is the exact mistake this repo keeps retracting: check
`size` against the manifest you fetched, never against a figure recorded for
another build.

Two more traps:

- **The `.zst` files have their own checksums.** `manifest.json`'s `checksum` is
  of the *decompressed* binary; `install.sh` fetches `manifest.zst.json` for the
  compressed one. `Content-Length` here was 64,578,859 B
  (`darwin-arm64/claude.zst`) and 69,188,990 B (`darwin-x64/claude.zst`),
  matching that second manifest's `size` fields. This repo takes the plain
  `claude`; nothing here has run `zstd -d` on one.
- **Do not pipe `install.sh` to `bash`** if the goal is to avoid installing.
  Read from the script: it downloads into `$HOME/.claude/downloads` and then
  runs `"$binary_path" install`, which its own comment describes as setting up
  the launcher and shell integration.

**Both Mac architectures are thin Mach-O.** First four bytes `cf fa ed fe` on
both, `cputype` `0x0100000c` (arm64) and `0x01000007` (x86_64) — no
fat/universal header, so `extract_bun.py`'s single `LC_SEGMENT_64` walk handles
both with no slice-selection step. That is not generosity: it matches on
`MH_MAGIC_64` alone, with no `cputype` branch and no fat-header path, so a
universal `cafebabe` input would be **rejected** rather than sliced. Neither
download is one.

**On Linux too.** The same eight-platform manifest covers `linux-x64` and
`linux-arm64` (and `-musl` builds of each). Checked only as far as `curl -I` on
`/2.1.241/linux-x64/claude` → `200`, `Content-Length` 342,636,848 = the manifest
`size`. Nothing on this host has extracted that copy: the Linux figures here
come from the pre-installed `/usr/bin/claude` 2.1.222.

### The npm route: dead for `cli.js`, alive for the native binaries ✅

Kept because it is where this repo's `darwin-arm64` 2.1.239 and `win32-x64`
2.1.239 measurements came from — not because it is recommended.

**Dead:** `npm pack @anthropic-ai/claude-code` no longer yields a runnable
`cli.js`. The published package is a thin bootstrap that downloads a native
binary 📓: `unpackedSize: 0.2 MB` (was ~13 MB when it shipped a real `cli.js`),
`bin: { claude: "bin/claude.exe" }`, no dependencies.

**Alive:** the native builds are published as per-platform optional
dependencies, one npm package each —
`@anthropic-ai/claude-code-darwin-arm64` → `package/claude` (Mach-O),
`@anthropic-ai/claude-code-win32-x64` → `package/claude.exe` (PE), and so on.
`npm pack @anthropic-ai/claude-code-darwin-arm64` delivered a genuine 325 MB
Mach-O arm64 binary in about 1.3 seconds — **on Linux, with no Mac involved**.
Each tarball's `package.json` carries the exact version, `os` and `cpu`.

**It gives you the current release, not this document's.** Re-run 2026-08-24, it
returned `anthropic-ai-claude-code-darwin-arm64-2.1.241.tgz` (92,295,033 bytes)
— two releases past the 2.1.239 copy every darwin measurement here was taken
against. Check the `version` in the tarball's `package.json` before comparing
counts; a mismatch is the release tripwire working
([status.md](./status.md) § Remaining work #1), not a broken tool.

**The two routes deliver one artifact.** Measured 2026-08-24: the
`package/claude` payload inside `@anthropic-ai/claude-code-darwin-arm64` 2.1.241
and the file served by the endpoint for `darwin-arm64` 2.1.241 have the **same
sha256** — the `darwin-arm64` value in the checksum table above — and the same
size. Byte-identical, both hashed here.

### What this host can and cannot do with a darwin binary ✅

The extracted darwin JavaScript **boots here, under Linux Bun 1.3.14**, on both
Mac architectures: `--version` prints `2.1.239 (Claude Code)` / `2.1.241 (Claude
Code)` and the `x64` build also answers `mcp list`
(`No MCP servers configured…`), rc 0 throughout. Extraction is byte arithmetic
over a file; it does not care what OS can *execute* that file.

The limit here is precise:

- **The darwin `.node` addons cannot load.** They are Mach-O — `cffaedfe` (thin:
  arm64 on the `arm64` build, x86_64 on the `x64` build) for `image-processor` /
  `audio-capture` / `url-handler`, `cafebabe` (universal) for
  `computer-use-swift` / `computer-use-input`. Loading one under Linux fails
  with `ERR_DLOPEN_FAILED … invalid ELF header` ✅ (re-checked 2026-08-24
  against the `darwin-x64` `image-processor.node`).
- **`process.platform` is `linux`**, so every platform-conditional branch takes
  the Linux path.

What has and has not been run *on* a Mac is
[status.md](./status.md) § macOS execution.

---

## 9. The central risk — answered for 2.1.222, still open in general ✅

ClawGod's installer hard-requires **Bun ≥ 1.3.14** (`MIN_BUN_VERSION="1.3.14"`)
and notes 📄: *"Anthropic builds claude-code with Bun's canary channel. Older Bun
panics on cli.original.cjs with 'Expected CommonJS module to have a function
wrapper'."* Corroborated by
[anthropics/claude-code#45541](https://github.com/anthropics/claude-code/issues/45541).
1.3.14 is both the last **Zig** release and that stated minimum. The open
question was whether that still holds for a *current* build.

**It does, measured, for Claude Code 2.1.222 on Linux** ✅:

```
$ DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
    ~/.bun-1.3.14/bun build/extract/cli.original.cjs mcp list
No MCP servers configured. Use `claude mcp add` to add a server.
(exit 0)
```

**Lead with `mcp list` or `doctor`, not `--version`.** ✅ Instrumenting the
bundle's two lazy-module helpers (`re` = CommonJS wrapper, 1644 of them; `E` =
ESM lazy init, 5104) and counting how many are actually initialised:

| command | lazy modules initialised |
|---|---|
| `--version` | **0 / 6748** (0.0%) — a hardcoded fast path |
| `--help` | 2725 / 6748 |
| `doctor` | 2757 / 6748 |
| `mcp list` | 2761 / 6748 |

`--version` proves the file parses, the CJS wrapper is invoked and the entry
module's top level runs. It proves **nothing** about Bun's API surface, because
it reaches none of it. `mcp list` reads config, initialises `.claude.json`,
writes a timestamped backup and dispatches into the MCP subsystem; `doctor`
probes the search backend, install identity and updater state.

**What else was exercised** ✅, on 2026-08-22, against a **loopback-only** mock
of the Messages API (127.0.0.1, throwaway `HOME` and `CLAUDE_CONFIG_DIR`, a fake
key; no traffic left the host, no real account touched):

- a full agentic loop with SSE streaming — `message_start` /
  `content_block_delta` / `message_stop` — multi-turn tool use and cost
  accounting, exit 0;
- the **Bash** tool spawning a real subprocess (`echo` + `uname -s` returned
  `Linux` and a live pid);
- the **Read** tool returning a base64 image block (§10 — where the gap shows);
- the Ink **TUI under a pty**: welcome screen, theme picker and
  syntax-highlighted diff preview, all rendering on Bun 1.3.14.

No Bun-API failure anywhere in any of it.

> Every Bun API reached on the code paths actually exercised is present and
> working in Bun 1.3.14, **for Claude Code 2.1.222, on `linux-x64`.**

That is not a permanent guarantee, and "it runs" is not "it behaves the same as
the shipped binary" — §10, where the honest answer is *no*.

### The ≥ 1.3.14 floor is ours, not Claude's ✅

Rebuilt in the **pragma-preserving** shape (keep the pragma, do **not** append
the IIFE) and run on Bun 1.3.13, the same entry module prints
`2.1.222 (Claude Code)`, rc 0 — and `--help`, `mcp list` and `doctor` also run
there. So 2.1.222 has at least one Bun minor version of headroom below 1.3.14,
and the "Bun ≥ 1.3.14" floor is a property of **this project's transform**. The
as-shipped artifact does fail on 1.3.13, but with our own panic, not a missing
API.

### The canary alarm is an ambiguous signal ⚠️

`Expected CommonJS module to have a function wrapper` is **not** a clean canary
for "Claude now needs a newer Bun": this project's own transform produces that
exact panic whenever the pragma/IIFE shape is wrong (the 2×2 in §6), and so does
a Bun that is simply too old. It tells you the module *shape* was rejected,
which has at least three causes.

A **missing-API error** — a `TypeError` naming a `Bun.*` property that does not
exist — is the unambiguous signal. To tell them apart, rebuild in the
pragma-preserving shape and try again: if that runs, the wrapper panic was ours.

**The risk remains real going forward:** if a future Claude build uses Bun APIs
newer than 1.3.14, its `cli.js` will not run on Zig, and the only newer Bun is
the Rust rewrite. Nothing measured so far comes close. Mitigations: pin Claude
to the last version that still runs on 1.3.14 (keep its `build/extract/`), or
shim the newer APIs. If it ever happens, record the first version that breaks
here.

### Generalisation: newer Claude builds ✅

An **unmodified** `scripts/build.sh` was pointed at
`@anthropic-ai/claude-code-linux-x64@2.1.240` — 18 releases after 2.1.222:

```
Modules: 11 (entry id=0)
Extracted: 1 cli.js + 7 assets (3 loader shims left inlined in cli.js)
   incl. NEW clipboard-napi.node (napi) and payload.template.html.asset (file)
/$bunfs/ paths rewired : 7      file:// leaks rewritten: 7      IIFE: 1
```

The result printed `2.1.240 (Claude Code)` and answered `mcp list` on **both**
Bun 1.3.14 and Bun 1.4.0, exit 0 in all four combinations. The extractor
generalises further than this document once claimed — which is also why the
integration tests hardcode 2.1.222/2.1.239 counts as a *tripwire* rather than a
contract.

---

## 10. The equivalence gap: this is not the same program ⚠️✅

Everything above answers *does it run*. This answers *does it behave the same as
the binary Anthropic ships*, and the answer is still **no** — though one of the
consequences is now fixed in a default build. Read this before deciding whether
to use this project.

- **Closed** (default builds, since 2026-08-23): native image processing — see
  *What shipped: the scoped shim*.
- **Open, deliberately**: the seccomp sandbox, embedded ripgrep, install
  identity. Each is a refusal with a reason, tabulated below.
- **Open, and not a gate at all**: both addon loaders swallow their own
  failures, so `exit 0` is not evidence that the asset wiring works.

Claude Code decides at runtime whether it is a Bun standalone with one function:

```js
function CE(){return Bun.isStandaloneExecutable===!0}                  // linux-x64 2.1.222
function AE(){return typeof Bun<"u"&&Bun.isStandaloneExecutable===!0}  // darwin-arm64 2.1.239
```

Under an external Bun that property is `undefined` (1.3.14) or `false` (1.4.0)
✅, so the gate is **false** and the CLI takes its non-standalone branch at every
call site. **How many sites that is is a fact about one binary, not about
Claude** — the call-site row of §6's table (21 on linux-x64 2.1.222, 23 on both
darwin builds, declaration excluded). Everything below is measured on the linux
build unless it says otherwise.

> **A trap for anyone re-deriving the count.** A `\b`-style word boundary
> over-counts: a bare `CE\(\)` search finds **26** hits on linux-x64 2.1.222 —
> 21 real call sites, 4 substrings of `isGCE()` / `_checkIsGCE()`, and the
> declaration `function CE(){`, which contains `CE()` too. (`AE\(\)` finds 26 on
> darwin by a different composition: 23 call sites, the declaration, and the
> tails of `function IAE()` and `function jAE()`.) The obvious fix is a
> lookbehind, and the obvious lookbehind is wrong: excluding a preceding `.`
> as well (`(?<![\w$.])`) drops the count to **18** on linux and **20** on
> darwin, and 18 was briefly reported as the true figure. It is not — the three
> excluded sites are real, and they look like this:
>
> ```js
> let e=process.execPath,r=[...CE()?[e]:[e,process.argv[1]]];
> ```
>
> — where the dot is the tail of a **spread**, not a member access. There are
> exactly 3 such sites in each binary. `postprocess.py` uses `(?<![\w$])` for
> that reason: dropping those three would leave the shim's safety invariant
> blind to a rewrite that spread into them. **21 is right.**

Most sites are correct — the generic self-spawn helper has a proper
`{cmd: process.execPath, prefixArgs: [process.argv[1]]}` branch. Some are not.

### Measured consequences

**1. Native image processing was silently disabled.** The image path is

```js
async function uYe(){ if(Fbo)return Fbo.default;
  if(CE()) try{ …native image-processor… }catch{ console.warn("Native image processor not available, falling back to sharp") }
  …bundled JS sharp… }
```

With `CE()` false the native branch is **never attempted** — not tried and
failed, *unreachable by construction* — and the fallback is a bundled JS sharp
that needs libvips, which is not among the extracted assets. This half is now
closed in a default build; the A/B that establishes it is below.

**2. `exit 0` is not evidence that the asset wiring works.** Both addon loaders
swallow failure:

```js
function FDu(){ if(NDu)return wbo; NDu=!0; try{ wbo=l5l() }catch{ wbo=null } return wbo }
```

A missing, corrupt or wrong-architecture `.node` produces `null` and a degraded
feature, never a non-zero exit. Any claim of the form "it exited 0, so the assets
resolve" is unsound — including in this repo's own earlier evidence.

**3. The seccomp sandbox is entirely disabled.** `function kms(){return CE()}`
gates the whole thing: the `/proc/self/exe` fd, the `applyPath`, the
`MIMALLOC_SCAVENGER` override all early-return. A **real security reduction**
versus the native binary, on Linux, and a silent one.

**4. Embedded ripgrep degrades to a system `rg`.** `if(CE())` selects the
embedded mode; otherwise the CLI looks for `rg` on `PATH`. So **`rg` is a de
facto runtime prerequisite** here. Anthropic's own error string for the miss is
*"ripgrep not found on PATH … or use the native claude binary which embeds
it."*

**5. Install identity reports `unknown`**, which is what makes the auto-updater
hazardous — [runbook.md](./runbook.md) § Surviving Claude updates.

### The addon itself is fine ✅

Generate a deterministic 3000×3000 PNG with stock `python3`:

```bash
python3 - /tmp/gradient-3000.png <<'EOF'
import sys, zlib, struct
W = H = 3000
raw = bytearray()
for y in range(H):
    raw.append(0)                                   # PNG filter: None
    raw += bytes(v for x in range(W)
                 for v in ((x + y) % 256, (x * 2) % 256, (y * 3) % 256))
def chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
open(sys.argv[1], "wb").write(
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    + chunk(b"IEND", b""))
EOF
```

Identify that file by its **decoded** content — 3000×3000 rgb8, 27,003,000 raw
bytes, md5 `95adc51dc27c1ad40b52df01793235e2` — which is what the A/B harness
asserts. Its size on disk (2,329,429 bytes here) is a property of the local
zlib, not of the image: the same scanlines deflate at level 6 to a
2,329,372-byte IDAT through this `python3`'s zlib 1.2.13 and a 2,329,196-byte
one through node v22's zlib 1.3.1-e00f703 — 2,329,429 and 2,329,253 bytes on
disk once the 57 bytes of PNG framing are added. A hard size assertion there was
a `die` in fixture setup for anyone whose `python3` links a
different-but-correct zlib.

Then drive the extracted addon directly, the way the CLI's wrapper does
(`processImage` → `metadata`/`resize`/`jpeg`/`toBuffer`):

```bash
cat > /tmp/imgprobe.cjs <<'EOF'
const fs = require("fs");
const addon = require(process.argv[2]);
(async () => {
  console.log("exports:", Object.keys(addon).join(", "));
  const buf = fs.readFileSync(process.argv[3]);
  let img = await addon.processImage(buf);
  console.log("metadata:", JSON.stringify(await img.metadata()));
  img.dispose?.();
  img = await addon.processImage(buf);
  img.resize(2000, 2000, { fit: "inside", withoutEnlargement: true });
  img.jpeg(80);
  const out = await img.toBuffer();
  console.log("jpeg bytes:", out.length,
    "magic:", [...out.slice(0, 4)].map(x => x.toString(16).padStart(2, "0")).join(" "));
})();
EOF
# absolute path: the probe require()s this argument, and a bare specifier is
# resolved relative to the SCRIPT, not the shell's cwd - "./" does not help
"$HOME/.bun-1.3.14/bun" /tmp/imgprobe.cjs \
  "$PWD/build/extract/assets/image-processor.node" /tmp/gradient-3000.png
```

Measured 2026-08-23 on **both** Bun 1.3.14 and 1.4.0, identical output:

```
exports: processImage, hasClipboardImage, readClipboardImage, ImageProcessor
metadata: {"width":3000,"height":3000,"format":"png"}
jpeg bytes: 919331 magic: ff d8 ff e0
```

> **Use a script file, not `bun -e`.** ✅ On 1.3.14, `bun -e` with a failing
> `require()` of a native addon prints nothing and exits 0 — the dlopen failure
> is swallowed. (1.4.0 does report it.) There, exit 0 means "the expression was
> evaluated", not "the addon loaded".

Extraction and path rewriting are not the problem. **The CLI simply never asked
for it** — which is what the scoped shim changes, and only for that one gate.

### What shipped: the scoped shim ✅

**Implemented 2026-08-23** (`tools/postprocess.py`; design of record:
[`docs/superpowers/specs/2026-08-23-scoped-image-shim-design.md`](./superpowers/specs/2026-08-23-scoped-image-shim-design.md)).

**What it is.** One more text rewrite in `postprocess.py`'s existing pass: the
image branch's own gate call is replaced by the literal `true`. Nothing is
defined, patched or monkey-patched at runtime, and `Bun.isStandaloneExecutable`
itself is never touched.

**What it is scoped to.** Exactly one call site, chosen by **shape**: the
branch's own guard `if(<gate>())try{`, searched for in the 400 bytes before the
anchor string `Native image processor not available`. Measured on both entry
modules: the anchor occurs **once** in each file, the `if(<gate>())try{` shape
occurs **once** in each whole file, and the guard starts **132** bytes before
the anchor in both.

**What stays false, and why.** Every other gate site — the count drops by
exactly one and the artifact differs from the unshimmed one by exactly **4**
bytes (per-platform figures: §6's table, not repeated here so they cannot
disagree with it).

| gate site | why it stays false |
|---|---|
| embedded ripgrep | flipping it is the measured `No matches found` below — a wrong answer, not an error |
| seccomp sandbox (`kms`) | it would arm a sandbox whose `/proc/self/exe` is `bun`; unverified, and a wrong sandbox is worse than a documented missing one |
| install identity / updater | `DISABLE_AUTOUPDATER=1` already covers the hazard, and reporting `native` would make the updater's story *less* true |
| the two MCP self-spawns | the non-standalone branch is correct, and the `cli.js` sibling (§6) already serves it |
| telemetry `is_native_binary` | reporting `native` would be a lie |

**Why not the nearest call.** The first version rewrote the last gate call
before the anchor. A reviewer broke it with an entry module whose image function
has lost its own `if(<gate>())` but kept the anchor: the nearest preceding gate
call is then **embedded ripgrep's**, and rewriting *that* passes every
arithmetic check, because the count invariant asks how many sites moved and
never which. The build would have shipped clean with `Grep` silently wrong.
Hence selection by shape, and hence
`tests/test_image_shim.py::test_a_lost_image_guard_does_not_hand_the_rewrite_to_ripgrep`,
that exploit kept as a test. For scale: the nearest *other* gate call is 506,792
bytes away on linux-x64 2.1.222 and 1,732,905 bytes away on darwin-arm64
2.1.239, so the 400-byte window is not what keeps ripgrep safe — the shape is.

**Why not the first declaration, either.** Selecting the *site* by shape still
left the gate's *name* selected by position (the first declaration in file
order). An entry module declaring two differently named gates, `ZZ` before the
real `CE`, with the image branch correctly on `if(CE())try{` and an
`if(ZZ())try{` in the window, made `ZZ` "the gate": its branch was rewritten,
`applied` reported 1, the arithmetic balanced, `check()` returned clean — and
image processing was still off. The shim now refuses a file that declares more
than one *distinct* gate name (two declarations of the same name are not an
ambiguity), the same way it already refused a duplicated anchor.

**Three names, three modules — and what that does and does not show.** Measured
2026-08-23/24: exactly one declaration in each real entry module, and a
**different name in each** — `CE` in the 22,960,130-byte linux-x64 2.1.222
module, `AE` in the 28,244,743-byte darwin-arm64 2.1.239 one, `Tw` in the
28,245,789-byte darwin-x64 2.1.241 one. That is the strongest evidence that the
name must be *captured* from the module rather than hard-coded. But the three
samples differ in **both** platform and version, so they do not on their own show
that the name varies *by platform*; a per-build minifier renaming every release
would produce the same table. What is established is that the name is not a
constant across the binaries this project handles, which is all the transform
needs.

**It applies on macOS, and that is all that is known there** 🍎. On the Apple
Silicon host the shim selected gate `AE`, moved the call sites `23 -> 22` and
reported `applied: 1` — so the *selection* logic works against a real binary on
a real Mac. What was not observed there is the branch it unlocks doing anything.

**When it does not apply.** A renamed anchor or a restructured function is a
**warning, not a build failure**: the artifact degrades to exactly what this
repo shipped before the shim existed. `postprocess.py` prints the gate name, the
before → after counts and the applied count either way, and `build.sh` prints an
explicit verdict and adjusts its closing list of gaps.

**The refusal names *which* thing drifted** — the gate **declaration**'s
minified shape, the **anchor** string, or the `if(<gate>())try{` branch shape —
as `image shim not applied : …` on stdout, which `build.sh` quotes instead of
guessing. It used to guess, and the guess was measurably wrong: reproduced here
2026-08-24 against a copy of `/usr/bin/claude` whose single 53-byte gate
declaration (one occurrence, at offset 260,565,233) was replaced in place with
an equal-length arrow form, anchor untouched. The old build exited 0 blaming
"a new Claude release renamed the anchor string" — for an artifact that still
held the anchor exactly once — and printed `image shim call sites  : 0 -> 0`
for an entry module with **21** live `CE()` call sites. The new build names the
declaration, says the anchor is not implicated, and prints
`image shim call sites  : not counted (no gate identified)`: `0` is not a
synonym for "unknown", it is a claim about the artifact, and that claim measured
false. `check()` now treats a rewrite *claimed* against a gate nobody identified
as the same fatal bookkeeping failure as arithmetic that does not balance.

**Opting out.** `NRC_NO_IMAGE_SHIM` set to **any non-empty value** builds the
"as shipped" artifact. Both `build.sh` (`[ -n … ]`) and `postprocess.py` (a
truthiness test) use that one rule; they have to agree, or a shim that genuinely
*failed* gets announced as a deliberate choice — the one wording that stops
anyone looking. Verified 2026-08-23 with `NRC_NO_IMAGE_SHIM=false` and
`=yes` on both real binaries, and pinned by
`tests/test_build_script.py::test_any_non_empty_opt_out_value_is_an_opt_out_here_too`.

### Do not "fix" this by flipping the flag globally

Defining `Bun.isStandaloneExecutable = true` makes the image path work and
immediately breaks search, because "embedded ripgrep" then means *"re-exec
`process.execPath` with argv0 `rg`"* — and `process.execPath` is **bun**. Not an
error: a *silently wrong answer*. That is why the harness carries the global flip
as a **third side** and asserts the breakage. If a future Bun or Claude ever
makes the global flip harmless, that assertion goes red — which is a result, not
a bug: the premise this design rests on would have expired.

### The A/B, and how to reproduce it ✅

```bash
# builds three sides from /usr/bin/claude and drives each through the
# loopback mock: as-shipped, shimmed, and the globally-flipped artifact
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/ab-equivalence.sh
scripts/ab-equivalence.sh --case grep                        # one case at a time
scripts/ab-equivalence.sh --as-shipped OUT_A/extract \
                          --shimmed    OUT_B/extract         # reuse two builds
```

Four cases × three sides, on `linux-x64` 2.1.222 under Bun 1.3.14 (node v22.23.2
for the mock), re-run in full 2026-08-24 — twelve runs, `all expected results
reproduced`, exit 0:

| case | as shipped | shimmed (default build) | globally flipped |
|---|---|---|---|
| `read` (3000×3000 PNG) | `is_error: true` — *"Unable to resize image — dimensions exceed the 2000x2000px limit and image processing failed."* | JPEG, `media_type: image/jpeg`, magic `ff d8 ff e0` | the same JPEG |
| `grep` (string that exists) | `hay/a.txt:1:NEEDLE-12345` | `hay/a.txt:1:NEEDLE-12345` | **`No matches found`** |
| `doctor` | `Running: unknown (2.1.222)`, `Search: OK (/usr/bin/rg)` | `Running: unknown (2.1.222)`, `Search: OK (/usr/bin/rg)` | `Running: native (2.1.222)`, `Search: OK (bundled)` |
| `bash` (control) | `HELLO-FROM-SUBPROCESS\nLinux` | `HELLO-FROM-SUBPROCESS\nLinux` | `HELLO-FROM-SUBPROCESS\nLinux` |

The `doctor` middle column is the point: the shim moved the image gate and
**nothing else**, so install identity — a different gate site — still answers
`unknown` and search still uses the system `rg`. The right-hand column is the
control that proves the case can tell them apart at all. The `bash` row is the
other control: a case that comes out the same on all three sides is what tells
you the harness is comparing artifacts rather than comparing failures. Its `\n`
is literal and load-bearing — the harness escapes a tool_result onto one line
and the comparison pulls it back with `sed -n 's/^tool_result=//p'`, so before
the escaping existed the `Linux` half was dropped from both the expectation
check and the SAME/DIFFERS verdict.

The `read` row's shimmed and globally-flipped sides are the same JPEG:
`IMAGE media=image/jpeg decoded_bytes=494476 magic=ff d8 ff e0`, from the
3000×3000 fixture above (2026-08-24). The **flip** is what reproduces across
hosts; the JPEG's size is a property of this addon build. An earlier table
quoted a size taken against a source PNG that was not in the repo, an
independent A/B measured a different number for the same qualitative result,
and that number was retracted — hence the rule: **byte counts are quoted here
only when the input is reproducible.**

**This table is `linux-x64` only, and the macOS run did not add a row to it**
🍎. On the Mac an image was attached and described, so image *input* works there.
But this table is about **resizing**, and an image small enough not to need
resizing never reaches the native branch. The honest statement: the shim applied
on macOS, image input worked on macOS, and **the resize path specifically is
untested there**. Do not let "an image reached the model" stand in for this
table.

**The harness is Linux-only** and refuses to start elsewhere. It needs `node`
for the mock, `rg` on `PATH` (an extracted build has no embedded ripgrep — point
4 arriving as a case failure rather than as an explanation), and `timeout(1)` or
`gtimeout` as a hard preflight requirement. Its egress guard reads
`/proc/<pid>/fd` against `/proc/net/tcp`, there is no portable substitute in it
yet, and running the comparison with its safety net silently missing would print
output indistinguishable from a clean run. A preflight names every missing
prerequisite in one message rather than dying at whichever line comes first
(checked 2026-08-24 with `bun` and `node` both hidden: both reported together,
exit 1; the `/proc` branch cannot be exercised on this host, which refuses
`unshare`, so what a Mac sees there is read from the script, not observed).
Nothing else in the script is Linux-specific — sizes and md5s go through
`python3` rather than `stat -c` / `md5sum`.

The guard polls the whole process tree, parent chain walked, so a socket opened
by the Bash tool's subprocess or by `rg` is attributed to the run that spawned
it. **The invariant is: zero non-loopback sockets on every run, and at least one
socket attributed to every run that drives an API turn.** *How many* sockets a
run opens is not an invariant and is deliberately written down nowhere — it
moves from run to run, and three write-ups of that number in this repo ended up
disagreeing with each other. The harness prints its own count on every
`egress_guard=` line (`sockets=… loopback=… non_loopback=…`), where it is true.
All twelve runs of 2026-08-24 had empty `egress=` lines and `egress_guard= OK`.

Three further failure conditions, each because an empty `egress=` line was
otherwise indistinguishable from a guard that was not working: the poller not
reporting that it ran to the end (being SIGTERMed is the *normal* end of a case);
the poller attributing **zero** sockets to a turn-driving case, meaning it was
watching the wrong processes; and the mock's log showing a `stream=false`
request, meaning the CLI abandoned the SSE stream for the non-streaming fallback
— a path a real API run never takes.

---

## 11. Running the artifact under Node instead of Bun ✅

Measured 2026-08-25, `linux-x64` 2.1.231 artifact, Bun 1.3.14 as the oracle.
`scripts/bun-shim.cjs` loads via `node --require`; `make node-run` wires it up.

**The parse boundary is Node 24, and it is hard.** The bundle has **33** `using`
declarations — ES explicit resource management. (A grep says 35; each match was
classified by breaking the keyword and re-parsing, and the two that still parse
are prose in the `NotebookEdit` help string.) `node --check` exits 1 on 22.23.2
and 23.11.1 (`SyntaxError: Unexpected identifier`), 0 on 24.0.0, 24.19.0,
25.0.0, 25.9.0 and 26.7.0; V8 13.6 arrives with the major, not with a patch.

**Of five non-builtin bare specifiers, Node needs two to start.** Real
`require`/`import` calls, string content excluded by re-parsing each one (a
textual scan says 17): `ws`, `undici`, `bun:ffi`, `bun:jsc`, `node-fetch` — Bun
provides all five, Node none. `make node-deps` puts the two that loading needs
in `~/.cache/not-rusty-claude/node/node_modules`, never in this checkout, never
globally — on npm's registry integrity, not a pinned sha256 like `make setup`,
hence `--ignore-scripts`. `bun:ffi` and `bun:jsc` sit inside a `try`/`catch`
(`bun:ffi`'s also behind a `!== "macos"` return); ⚠️ `node-fetch` does not — an
SDK fallback no tested command reaches, `ERR_MODULE_NOT_FOUND` under Node.

**The `Bun` global: 44 property references in code, 24 distinct** (every
occurrence re-parsed: that drops two a grep counts from inside strings and adds
the `globalThis.Bun.which` it misses). The shim covers **25** names — the extra,
`Bun.stdin`, occurs only in a doc string. It implements **seven** —
`stringWidth`, `stripANSI`, `hash`, `which`, `semver.order`, `deepEquals`, `gc`
— each pinned by a differential test against Bun in
`tests/test_node_runtime.py`. Two more arrived on 2026-08-26, `wrapAnsi` and
`YAML.parse`, because the interactive REPL calls both and a refusal there was
being swallowed by a React error boundary: nine implemented, with their own
corpora in `tests/test_wrap_ansi.py` and `tests/test_yaml_parse.py`. Nine
throw, naming the API (`spawn`, `file`, `serve`, …), and `YAML.parse` still
refuses the constructs it could not match; **seven** stay deliberately *undefined* (`Terminal`,
`WebView`, `JSONL`, `version`, `isStandaloneExecutable`, `stdin`) because the
bundle feature-detects them — a plausible-looking stub is the §10 failure mode
— and `Bun.ant`, for the opposite reason: it is not feature-detected, it is
patched into the Bun inside the shipped binary, and `typeof Bun.ant` is
`"undefined"` in stock Bun 1.3.14. Defining it was the one place the shim
claimed a surface the oracle lacks; all three call sites are bare
`Bun.ant.x(…)` inside `try`/`catch`, so leaving it out throws the same
`TypeError` Bun throws, at the same place.

**What was compared, and what was not.** Same artifact, throwaway `HOME` and
`CLAUDE_CONFIG_DIR` per side, Bun 1.3.14 against Node 24.0.0 and 26.7.0:
`--version` (22 B), `--help` (16,890 B), `mcp list` (65 B) and `config ls`
(35 B, exit 1 both) give byte-identical stdout and equal exit codes; `doctor`
(973 B) differs in one line, `Path:`, naming the interpreter actually running.
⚠️ **No agentic session — a real conversation with tool use — has ever been run
under Node**, here or anywhere. The *interactive* path has, as of 2026-08-26:
onboarding through a pty on Linux, and the authenticated REPL on Apple Silicon
with a real config. Getting there is what `wrapAnsi` and `YAML.parse` were
implemented for.

---

## 12. `Bun.wrapAnsi` answers like Bun, but not in Bun's time ⚠️

Measured 2026-08-27 on this Linux host, `scripts/wrap-bench.cjs` under both
runtimes (`make wrap-bench`), COLS=100. The differential fuzzing proves the
shim gives the same *answers* as Bun; nothing in the suite proves it gives
them in time, and it does not.

Read the columns as a ratio, never as absolute milliseconds: the host and its
load move both together, so only Bun-vs-shim on the *same* run carries across
machines.

| case | Bun 1.3.14 | node + shim | ratio |
|---|---|---|---|
| plain 40-line frame (5,739 ch) | 0.34 ms | 17.9 ms | 52x |
| plain code line (156 ch) | 0.009 ms | 0.33 ms | 37x |
| plain paragraph, 120 words | 0.058 ms | 4.36 ms | 75x |
| gating line, 20 words (180 ch) | 0.011 ms | 4.01 ms | 365x |
| gating line, 60 words (523 ch) | 0.030 ms | 36.3 ms | 1,200x |
| gating line, 120 words (1,042 ch) | 0.062 ms | 147 ms | 2,400x |
| gating line, 240 words (2,080 ch) | 0.118 ms | 610 ms | 5,200x |

**The split is a cliff, not a gradient.** A "plain" line carries SGRs and
nothing else. A "gating" line carries one non-SGR CSI — a cursor query, an
erase-line, anything a TUI emits — and that single escape is enough to make
every row of the line eligible for the midline whitespace collapse.

**Plain lines are linear-ish and merely slow.** `wrap_lineCanGate` proves no
row can qualify (a row qualifies only on a non-SGR CSI or an ST-terminated
OSC 8 at its leading edge, and rows are built from the line's own escapes), so
the whole collapse pass is skipped in one O(n) scan. This early-out landed as
`b85f9ac` and took the 40-line frame from 120 ms to 17 ms.

**Gating lines are quadratic.** `wrap_collapseMidlineRuns` must know which
*row* each candidate SGR lands on, because the gate is per-row — so it rebuilds
the rows from scratch, once per escape cluster, over a growing prefix. Bun
stays linear across the same inputs (0.011 → 0.118 ms for 11.6x the length);
the shim goes 4.0 → 610 ms, 152x. A single 2 KB gating line is over half a
second of blocked main thread.

**This is the word-model tax, and it is the same root cause as the remaining
divergences.** This shim splits a line into words and reconstructs separator
spaces during placement; Bun almost certainly walks characters once, carrying
per-row state as it goes. A streaming walk would not need to rebuild anything
to answer "which row am I on" — it would already know. Both the 38 residual
`wrapAnsi` divergences and this quadratic are that one mismatch, seen from two
sides. Patching either further without the rewrite is buying inches.

**Not yet load-bearing, but do not assume that holds.** No agentic session has
run under Node at all (§11), so nothing has profiled a real render loop. The
plain-line numbers are the common case and are survivable; whether real Claude
Code output puts non-SGR CSIs on long lines is unmeasured, and that question
decides whether this is a footnote or a blocker.

---

## 13. An OSC 8 hyperlink whose uri contains the letter `m` wraps differently ⚠️

Measured 2026-08-27 against Bun 1.3.14, found while auditing a stale comment
rather than by fuzzing — the generated grammar never puts a whitespace run
directly behind a leading OSC 8, so all 800,000 cases are blind to it.

A leading escape does not shelter the whitespace behind it from the per-row
leading trim, with one documented exception: a non-SGR CSI shelters a **tab**,
and only a tab. Sweeping that rule again turned up two things the original
sweep had recorded wrongly.

**An ST-terminated OSC 8 shelters the tab too**, opener or closer, empty uri or
not. The original sweep recorded both OSC 8 forms as non-sheltering, which is
true only of the BEL form. Fixed: the shelter test is now literally
`wrap_rowGateQualifies`'s test, and not by coincidence — the same two escape
kinds that let a row gate its whitespace collapse are the ones that shelter a
tab at its leading edge. Pinned by two new corpus cases.

**The BEL form shelters if and only if its uri contains the letter `m`.** Not
implemented. Reproduce with `ESC ]8;; <uri> BEL " \t ab"` at any width:

| uri | Bun keeps the tab? |
|---|---|
| `z` | no |
| `m` | **yes** |
| `zzzzzzzz` | no |
| `zzzmzzzz` | **yes** |
| `abcdefgh` | no |
| `abcdefgm` | **yes** |
| `http://x` | no |
| `https://x.example` | **yes** |

One character decides it, anywhere in the uri, at any length including one.
`m` is the final byte of an SGR (`ESC [ 0 m`), so the likely mechanism is an
OSC scan that also accepts a CSI final byte as a terminator — Bun ending the
"escape" at the `m` and treating what follows as something else. This was
first mistaken for a length rule, then for a `://` rule, then for the
substring `exam`; each held over a dozen samples and then broke. Single-
character bisection is what settled it, and the result reproduces in a fresh
process, from a file and from `bun -e`, so it is neither harness state nor
transpiler cache.

**Deliberately not matched.** The shim's contract is byte-equality with Bun,
which in principle includes Bun's bugs — but reimplementing this one means
deciding how far bug-compatibility goes, and that is a design call rather than
a fix. It affects zero of the 800,000 fuzzed cases and no plausible real uri
policy. Recorded so the next person to sweep this rule does not spend the
afternoon rediscovering it. The corpus deliberately pins the ST behavior and
deliberately omits the BEL one, with a comment saying why.

---

## Appendix: exact commands used ✅

Every command below was run on **this host**. `/usr/bin/claude` was only ever
read. Nothing from the 2026-08-24 Apple Silicon run 🍎 is in this block,
deliberately: that run's commands live in
[README's macOS section](../README.md#macos), attributed to the machine that
executed them.

```bash
# Bun 1.3.14, installed WITHOUT touching PATH or any rc file
curl -fsSL -o /tmp/bun-1.3.14.zip \
  https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-linux-x64.zip
unzip -o -j /tmp/bun-1.3.14.zip 'bun-linux-x64/bun' -d "$HOME/.bun-1.3.14"

# extract + post-process, Linux ELF
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh /usr/bin/claude

# a darwin binary without a Mac: the endpoint flow above (§8), run for both
# P=darwin-arm64 and P=darwin-x64, then the same pipeline against it
OUT_DIR=/tmp/nrc-x64/build scripts/build.sh /tmp/ccdl/claude-darwin-x64.bin

# how §1's darwin-arm64 2.1.239 and win32-x64 2.1.239 copies were obtained,
# historically. Kept for provenance, not as a recommended route: `npm pack`
# gives whatever is CURRENT (2.1.241 on 2026-08-24), and that payload is
# byte-identical to the endpoint download.
npm pack @anthropic-ai/claude-code-darwin-arm64                    # → a .tgz
mkdir -p /tmp/ccmac
tar xf anthropic-ai-claude-code-darwin-arm64-*.tgz -C /tmp/ccmac \
    --transform='s|package/claude$|package/claude-darwin-arm64.bin|'
#   → /tmp/ccmac/package/claude-darwin-arm64.bin, where the test suite looks
#     for it by default; NRC_TEST_MACHO overrides
OUT_DIR=/tmp/macbuild scripts/build.sh /tmp/ccmac/package/claude-darwin-arm64.bin

# Bun's own parser (primary), then the faster JSC check (secondary)
"$HOME/.bun-1.3.14/bun" build --no-bundle --target=bun \
  build/extract/cli.original.cjs --outfile=/dev/null
"$HOME/.bun-1.3.14/bun" scripts/syntax-check.js build/extract/cli.original.cjs

# the actual run. DISABLE_AUTOUPDATER=1 is not optional - see runbook.md
# § Surviving Claude updates. The scratch config dir keeps ~/.claude untouched.
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs mcp list

# regression. What this prints depends on what the host has; NRC_TEST_ELF /
# NRC_TEST_MACHO / BUN_BIN override the paths. The per-configuration counts are
# NOT repeated here - README's table is the single place that states them.
python3 -m pytest tests/ -q

# the equivalence A/B, three sides through the committed loopback mock
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/ab-equivalence.sh
```

`scripts/syntax-check.js` is a **secondary** check only: `new Function(source)`
invokes JavaScriptCore's Function-constructor parser, not Bun's module loader,
and the two disagree in both directions
([verification-2026-08-22.md](./verification-2026-08-22.md), Step 3). Trust
`bun build --no-bundle` and `postprocess.py`'s own `check()`.
