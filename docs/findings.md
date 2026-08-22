# Findings

What is actually known about Claude Code's native binary and about running its
JavaScript on a **pre-Rust (Zig-era) Bun** instead of the runtime Anthropic
bundles.

> **Verification legend**
> ✅ **executed here** — run on this host (Linux x86_64, Debian 12, glibc 2.36) on 2026-08-22; command + output pasted in [verification-2026-08-22.md](./verification-2026-08-22.md) ·
> 🔎 **static check here** — real bytes parsed/transformed, or the output accepted by Bun's own parser, but **nothing executed** ·
> 🖥️ **needs hardware we do not have** ·
> ⛔ **deliberately not implemented** ·
> 📓 **prior-session record** — observed on a Mac on 2026-08-21 against 2.1.238, *not* re-checked here ·
> 📄 read from source (ClawGod / upstream docs), not measured

**The three binaries these findings are measured against:**

| Platform | Container | Version | Size | How obtained |
|---|---|---|---|---|
| `linux-x64` | ELF | **2.1.222** | 289,467,400 B | `/usr/bin/claude`, pre-installed on this host (read-only; not executed by this project) |
| `darwin-arm64` | Mach-O (thin arm64) | **2.1.239** | 324,973,552 B | `npm pack @anthropic-ai/claude-code-darwin-arm64` (§9) |
| `win32-x64` | PE | **2.1.239** | 337,672,352 B | `npm pack @anthropic-ai/claude-code-win32-x64` (§9) |

Where a number differs between platforms or versions, **both** are given. That
is the single most important lesson of this round: almost nothing here is a
constant.

---

## 1. What "de-rust" means here

Bun (the JavaScript runtime Claude Code is compiled with) is being rewritten
from **Zig** to **Rust**, largely with LLM tooling. PR
[oven-sh/bun#30412](https://github.com/oven-sh/bun/pull/30412) ("Rewrite Bun in
Rust") merged 2026-05-11. The rewrite is experimental and scoped to **Linux x64
glibc**; stable 1.3.x still ships from the Zig codebase, and **1.3.14 is
documented as the last Zig release**. (1.3.15 does not exist; the next release
is 1.4.0.)

**The goal:** run Claude Code's JavaScript on Zig-era Bun (≤ 1.3.14), not on the
Rust rewrite. Because Claude Code ships as a Bun *standalone* executable (the
runtime and the app baked into one binary), the embedded runtime cannot be
swapped. Instead we:

1. **Extract** `cli.js` and its assets out of the binary.
2. **Post-process** the JS so it runs outside the standalone sandbox.
3. **Run** it under an external, stock **Bun 1.3.14 (Zig)**.

The native binary is only ever *read* — not executed, not modified — so code
signing and notarization are irrelevant to this approach. (Contrast with the
byte-patch approach in §7, which does touch the binary and must re-sign.)

Step 3 has now been done for real, on Linux: see §10.

---

## 2. The native binary is a Bun standalone — on all three platforms ✅🔎

Each build embeds a serialized Bun module graph in a platform-specific section.
Only the container differs; the payload inside has the same layout everywhere
(full spec: [bun-section-format.md](./bun-section-format.md)).

| Platform | Section | File offset | Section size | Payload size | Modules |
|---|---|---|---|---|---|
| `linux-x64` 2.1.222 ✅ | `.bun` (ELF) | 86904832 | 202513494 | 202513486 | 8 |
| `darwin-arm64` 2.1.239 ✅ | `__BUN,__bun` (Mach-O) | 69107712 | 255007133 | 255007125 | 15 |
| `win32-x64` 2.1.239 🔎 | `.bun` (PE) | 95182336 | 242479616 | 242479175 | 9 |

(The ELF and Mach-O rows are from `build.sh` runs pasted in the verification
record, Steps 2 and 3b. The PE row was read by hand with a section-header walk —
`extract_bun.py` refuses PE by design; see [status.md](./status.md) § Windows/PE.)

Note the PE section is **padded**: `rawsize` 242479616 exceeds `payload_size + 8`
by 433 bytes of file-alignment padding, whereas on ELF and Mach-O the section
size equals `payload_size + 8` exactly. Always trust the u64 length prefix, not
the section size.

The macOS install is path-independent 📓: the data dir resolves at runtime from
`XDG_DATA_HOME ?? ~/.local/share`, and the only hardcoded path check gates
generation of the `ClaudeCode.app` wrapper
(`process.execPath.startsWith(…/claude/versions/)`), not the CLI. See
[runbook.md](./runbook.md) § Appendix.

---

## 3. The Bun standalone format ✅

Full byte-level spec in [bun-section-format.md](./bun-section-format.md).
Summary:

- Locate the container's Bun section → `(rawOffset, rawSize)`.
- The section starts with a **u64 little-endian length prefix**; the payload
  follows and **ends with the trailer magic** `\n---- Bun! ----\n` — **16
  bytes** (the leading and trailing newlines are part of it).
- Just before the trailer sits a **32-byte offsets struct**: at `+8`
  `modules_offset` (u32), `+12` `modules_size` (u32), `+16` `entry_point_id`
  (u32).
- The modules table is `modules_size / 52` records of **52 bytes** each:
  `+0` name offset, `+4` name size, `+8` content offset, `+12` content size,
  `+49` loader id (u8).

Confirmed on all three containers. `tools/extract_bun.py` implements Mach-O and
ELF and refuses PE with a message pointing at [status.md](./status.md).

---

## 4. The module list is per-platform AND per-version ✅

**This is a correction.** An earlier version of this document listed "the 15
modules" as if the graph were fixed. It is not. Two builds three patch versions
apart differ in module count, in module *contents*, and — the trap — in the
entry module's **name**.

**`linux-x64` 2.1.222 — 8 modules** ✅

```
idx loader     size      name
 0  js        21.90 MB   /$bunfs/root/src/entrypoints/cli.js  ← ENTRY (entry_point_id = 0)
 1  js         2.1 KB    /$bunfs/root/image-processor.js      ← loader shim
 2  js         2.1 KB    /$bunfs/root/audio-capture.js        ← loader shim
 3  base64  1430.4 KB    /$bunfs/root/image-processor.node    ← native (ELF)
 4  base64   480.6 KB    /$bunfs/root/audio-capture.node      ← native (ELF)
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
 6  base64  1220.1 KB    /$bunfs/root/image-processor.node    ← native (arm64)
 7  base64   859.1 KB    /$bunfs/root/computer-use-swift.node ← native (universal)
 8  base64  1652.4 KB    /$bunfs/root/computer-use-input.node ← native (universal)
 9  file     203.6 KB    /$bunfs/root/chart.umd.min.js        ← asset
10  file     962.4 KB    /$bunfs/root/hljsBundle.generated.min.js ← asset
11  file    3235.3 KB    /$bunfs/root/mermaid.min.js          ← asset
12  base64   427.8 KB    /$bunfs/root/audio-capture.node      ← native (arm64)
13  file    2177.2 KB    /$bunfs/root/payload.template.html.asset ← asset
14  base64   329.0 KB    /$bunfs/root/url-handler.node        ← native (arm64)
```

(`win32-x64` 2.1.239 has **9** modules 🔎 — the linux set plus
`payload.template.html.asset` — and its names use the `B:/~BUN/root/` prefix
instead of `/$bunfs/root/`. Its entry module is `B:/~BUN/root/cli`: it follows
**darwin's** short name, not linux's `src/entrypoints/cli.js` — a third data
point for the rule below. See [status.md](./status.md) § Windows/PE.)

**Consequences, all of them practical:**

- **Never identify the entry module by name.** It is `/$bunfs/root/cli` on
  darwin and `/$bunfs/root/src/entrypoints/cli.js` on linux. Only
  `entry_point_id` is reliable — it is the field the format provides for exactly
  this purpose, and it was `0` on all three containers (which is *not* a licence
  to hardcode `0` either).
- **Never hardcode a module count or an asset list.** Linux 2.1.222 has no
  `computer-use-*`, no `url-handler`, no HTML template; darwin has all four.
  Extraction must be driven by the loader id (`napi`/`base64`/`file` → write to
  `assets/`), never by a filename allow-list.
- **The platform difference is not only naming.** `image-processor.node` is
  1430 KB on linux and 1220 KB on darwin, and they are different object formats.
  There is no cross-platform artifact here; extract per platform.

The entry module on **all three** platforms opens with the pragma
`// @bun @bytecode @bun-cjs` followed by a CommonJS wrapper
`(function(exports, require, module, __filename, __dirname) {…`, and ends with a
**non-invoked** `})`. §6 is the post-processing that requires.

---

## 5. Two gotchas that break naive extractors

### 5a. The `base64` loader stores RAW bytes, not base64 text

The native `.node` modules carry the loader id for **`base64`**, but the stored
content is **the raw binary**. The loader name describes how Bun later exposes
the asset *to JS* (as a base64 string), not how it is stored.

Originally proven on Mach-O (module bytes begin `cf fa ed fe` = `MH_MAGIC_64`),
and **re-confirmed on ELF** ✅: the bytes stored for
`/$bunfs/root/image-processor.node` in `/usr/bin/claude` begin `\x7fELF`. This
is asserted permanently by `tests/test_integration.py`
(`test_real_elf_binary_extracts`), and the darwin equivalent asserts the
universal (`0xCAFEBABE`) or thin-arm64 (`0xFEEDFACF`) magic.

**Decoding this content as base64 corrupts it** — an early port of the extractor
did exactly that and produced 71-byte "modules." Write the raw bytes verbatim.

### 5b. ClawGod only extracts `napi`-loader modules → misses these 📄

[ClawGod](https://github.com/0Chencc/clawgod)'s extractor only writes out
modules where `loader === 'napi'`. On every build measured here the addons are
`base64`-loader, so ClawGod would extract **zero** native modules while still
rewriting the `require()` paths to point at them — meaning the image / audio /
computer-use features would break. [`extract_bun.py`](../tools/extract_bun.py)
handles `napi`, `base64`, and `file` loaders and writes raw bytes.

---

## 6. Post-processing `cli.js` to run outside the standalone ✅

**This section was previously a plan ported from ClawGod's `post-process.mjs`.
It is now a measurement.** `tools/postprocess.py` has been run against both real
binaries; the transforms below are what it actually does, with the counts it
actually produced.

1. **Strip the leading pragma comment lines** (`^(?:\/\/[^\n]*\n)+`, once).
   Bun's CJS loader needs the file to start with `(function`; the pragma line
   would otherwise make it panic with *"Expected CommonJS module to have a
   function wrapper"*.
2. **Rewrite every `/$bunfs/root/<name>` string literal** — regardless of
   syntactic position — to
   `require('path').join(__dirname,'assets',"<name>")`. This is the fix for a
   real defect in the ported version, which only rewrote
   `require("/$bunfs/root/X.node")` call sites. The literals appear in **two**
   shapes in the real minified code: as a `require()` argument (native addons)
   *and* as a bare string constant later read through `fs/promises.readFile`
   (file-loader assets such as `chart.umd.min.js`). Rewriting only the first
   shape leaves the second silently pointing into a `/$bunfs` that no longer
   exists.
3. **Rewrite build-time `file://` leaks to `__filename`** (see below).
4. **Append the CJS IIFE invocation** `(exports, require, module, __filename,
   __dirname)` to the trailing `})`.
5. **Report** any leftover `/$bunfs/` reference, any surviving `/home/runner/…`
   build-machine path, and any extracted asset the code never mentions.

`postprocess.py`'s `check()` then refuses to write `cli.original.cjs` at all
unless the output starts with `(function` **and** has exactly one appended IIFE
invocation. A silently broken output file reaching Bun would surface only as
that confusing panic, so the failure is made loud and early instead.

### The measured counts ✅

| | `linux-x64` 2.1.222 | `darwin-arm64` 2.1.239 |
|---|---|---|
| pragma block stripped | 1 | 1 |
| `/$bunfs/` literals rewritten | **5** | **9** |
| `file://` leaks rewritten | **7** | **8** |
| IIFE invocations added | 1 | 1 |
| leftover `/$bunfs/` references | **0** | **0** |
| build-machine path notes (informational) | 3 | 3 |
| never-referenced extracted assets | 0 | 0 |
| size | 22,960,130 → 22,959,448 B | 28,244,743 → 28,244,063 B |

The rewrite count equals the extracted-asset count on both platforms (5 and 9),
and no "extracted asset never referenced" note was emitted, so every asset
written to disk is referenced by exactly one rewritten literal.

### The `fileURLToPath` correction — why the ported regex found nothing

The scaffolded transform targeted
`(0, ns.fileURLToPath)(…import.meta.url)`. Measured against both real binaries,
that pattern matches **0 occurrences**. The reason is worth stating exactly,
because "0" invited the wrong conclusion (that the transform was unnecessary):

> **Bun's bundler resolves `import.meta.url` at build time.** What survives into
> the shipped `cli.js` is not an `import.meta.url` expression but a **literal
> `file://` URL of Anthropic's build machine** — e.g.
> `fileURLToPath("file:///home/runner/work/claude-cli-internal/…")`.

So the leak is real and common (7 on linux, 8 on darwin) — it simply never looks
like the pattern that was being searched for. The current regex matches the
literal-URL form, and must also consume an optional `ns.` / `(0, ns.fn)` callee
prefix: replacing only the argument would produce `ns.__filename`, a syntax
error that the check step is there to catch.

Precision, since the numbers matter 🔎: the *substring*
`fileURLToPath(import.meta.url)` does occur (twice in each binary), and a wider
`fileURLToPath(…import.meta.url)` match finds **5 sites per binary**. **Every
one of them sits inside a string literal, not in executable code** — confirmed
by dumping the surrounding bytes at each site:

- **3 sites** are inside an embedded `.mjs` **script source carried as text**
  (design/build tooling: `scriptsShaFor()`, `package-build.mjs`,
  `storybook/http-serve.mjs`). The doubled escaping is the tell — `\\u2014` and
  `\\${…}` inside the block — and the win32 build's copy of the same block
  preserves Windows line endings as `\r` escape sequences (20,932 occurrences of
  `\r` before a newline in that entry module, against 143 on linux). Text being
  carried, not code being run.
- **2 sites** — exactly the two exact-substring hits — are inside embedded
  **Markdown documentation about ESM**: one in prose explaining that
  `__dirname` does not exist in ES modules, one inside a fenced `typescript`
  example block.

The same holds for all 16 bare `import.meta.url` occurrences: every one is
inside staged script text or documentation. Rewriting any of them would corrupt
embedded text rather than fix a path, so they are **left alone** — and that is
why the real leak count is 7/8 and not higher.

🔎 After post-processing, 16 `import.meta.url` references, 12 `fileURLToPath`
calls, and 2–3 bare `file:///` literals remain in each output. None of the
commands executed in the verification run hit a problem from them; no stronger
claim than that is available.

### The launcher that no longer exists

The ported design ended with a shell launcher installed on `PATH`:

```bash
export CLAUDE_CODE_EXECPATH="<native-binary>"   # "for shell integrations"
exec "$BUN_BIN" "$INSTALL/cli.cjs" "$@"
```

`scripts/build.sh` **no longer writes any launcher and installs nothing**: a
file named `claude` on `PATH` could shadow a real installation. It prints the
full-path command instead. The behavioural consequence — `CLAUDE_CODE_EXECPATH`
is now unset unless you export it yourself — is documented in
[runbook.md](./runbook.md) § Shell integrations.

---

## 7. The signature facts (why we don't need them here) 📓

Verified on a Mac on 2026-08-21 against 2.1.238, while evaluating the
*byte-patch* alternative. **Not re-checked on this host** (they need `codesign`
and `spctl`), and not part of the verification record. They explain why the
extract-and-run approach is the cleaner one.

- **Relocation needs no patch and no re-sign.** A Mach-O signature seals the
  file's bytes, not its path. Copied to an arbitrary directory the binary still
  reports `valid on disk` / `satisfies its Designated Requirement`, and `spctl`
  accepts it as `source=Notarized Developer ID`. It runs from the foreign path,
  and still runs with a `com.apple.quarantine` xattr attached.
- **The install path is not baked in** — it resolves from `XDG_DATA_HOME`.
- **The binary never signs anything itself** — the string `codesign` does not
  appear anywhere in it.
- **If you *do* modify bytes, you must re-sign or it is SIGKILLed.** A patched,
  un-re-signed binary exits **137** under the hardened runtime. Re-signing
  ad-hoc while preserving entitlements and identifier makes it run again — but
  notarization is lost (`spctl: rejected`), TCC permissions reset, and the next
  auto-update overwrites it. That is
  [`patch_claude.py`](../tools/patch_claude.py) (Approach B), kept for edits
  that are **not** in the JS layer.

---

## 8. Ready-made tools (prior art) 📄

| Tool | Extracts JS | Native modules | Runs via Bun | Patches | Notes |
|---|---|---|---|---|---|
| [0Chencc/clawgod](https://github.com/0Chencc/clawgod) | ✅ parses table | ⚠️ `napi` only | ✅ wrapper + stock bun | ✅ 29 patches | Closest to our goal; misses base64 addons (§5b) |
| [vicnaum/bun-demincer](https://github.com/vicnaum/bun-demincer) | ✅ + split/deobfuscate/**reassemble** | ✅ | — | — | Most comprehensive decompiler |
| [vibheksoni/unbuned](https://github.com/vibheksoni/unbuned) | ✅ pure-Python, zero-dep | ❌ | — | — | Heuristic; skips native modules **and universal Mach-O** |
| [lafkpages/bun-decompile](https://github.com/lafkpages/bun-decompile) | ✅ + sourcemaps | — | — | — | Web + CLI |
| [@shepherdjerred/bun-decompile](https://www.npmjs.com/package/@shepherdjerred/bun-decompile) | ✅ + AI de-minify | — | — | — | Built to inspect Claude Code CLI |
| [Piebald-AI/tweakcc](https://github.com/Piebald-AI/tweakcc) | ✅ auto-locate | ✅ repacks | ❌ repacks into binary | ✅ themes/prompts/etc. | Byte-patch route; hits the re-sign wall on macOS |

These remain useful references, but the fastest path to *this* project's goal is
now this repo's own two scripts, because they are the ones measured against
current builds: ClawGod's extractor misses the `base64` addons (§5b) and its
`fileURLToPath` transform matches nothing on current binaries (§6), and both
gaps fail silently.

---

## 9. The npm route: dead for `cli.js`, alive for the native binaries ✅

**This is a correction.** This section previously read "the npm shortcut is
dead," full stop. Half of that is right, and the useful half was missing.

**Dead:** `npm pack @anthropic-ai/claude-code` no longer yields a runnable
`cli.js`. The published package is a thin bootstrap that downloads a native
binary 📓:

```
unpackedSize: 0.2 MB      (was ~13 MB when it shipped a real cli.js)
bin:          { claude: "bin/claude.exe" }
dependencies: []
```

**Alive, and genuinely useful:** the native builds themselves are published as
per-platform **optional dependencies**, one npm package per platform:

```
@anthropic-ai/claude-code-darwin-arm64     → package/claude      (Mach-O)
@anthropic-ai/claude-code-win32-x64        → package/claude.exe  (PE)
@anthropic-ai/claude-code-<os>-<arch>      → …
```

`npm pack @anthropic-ai/claude-code-darwin-arm64` delivered a genuine 325 MB
Mach-O arm64 binary in about 1.3 seconds — **on Linux, with no Mac involved**,
and the `win32-x64` package delivered the PE the same way. Each tarball's
`package.json` carries the exact version, `os`, and `cpu`, so the artifact
identifies itself.

This is what made cross-platform verification possible at all. Extraction is
byte arithmetic over a file; it does not care what OS can *execute* that file.
The Mach-O extraction results throughout this document were produced this way.
Only **running** the extracted darwin JavaScript still needs Apple hardware
(🖥️, [status.md](./status.md) § macOS execution).

---

## 10. The central risk — answered for 2.1.222, still open in general ✅

ClawGod's installer hard-requires **Bun ≥ 1.3.14** (`MIN_BUN_VERSION="1.3.14"`)
and notes 📄: *"Anthropic builds claude-code with Bun's canary channel. Older Bun
panics on cli.original.cjs with 'Expected CommonJS module to have a function
wrapper'."* Corroborated by
[anthropics/claude-code#45541](https://github.com/anthropics/claude-code/issues/45541).

1.3.14 is simultaneously the last **Zig** release *and* that stated minimum — so
it satisfies both, in theory. The open question was whether that still holds in
practice for a *current* build, given that the only Bun newer than 1.3.14 is the
Rust rewrite.

**It does, measured, for Claude Code 2.1.222 on Linux** ✅:

```
$ CLAUDE_CONFIG_DIR="$(mktemp -d)" ~/.bun-1.3.14/bun build/extract/cli.original.cjs --version
2.1.222 (Claude Code)
(exit 0)
```

`--help` (the complete option and command registry) and `mcp list` were run the
same way and also exited 0. `mcp list` is the load-bearing one: it does not
parse-and-exit, it reads config, initializes `.claude.json`, writes a timestamped
backup, and dispatches into the MCP subsystem — all under Bun 1.3.14, with no
error. Full transcripts: [verification-2026-08-22.md](./verification-2026-08-22.md)
Steps 5 and 5b.

**Scope this answer exactly:**

> Every Bun API reached on the code paths actually exercised (`--version`,
> `--help`, `mcp list`) is present and working in Bun 1.3.14, **for Claude Code
> 2.1.222, on `linux-x64`.**

It is one version, on one platform, on three code paths. It is **not** a
permanent guarantee. Unexercised and therefore unknown: network and model API
calls, interactive TUI rendering, tool execution, and native asset loading (no
executed command loaded an asset — proven by renaming `assets/` away and seeing
`--version` and `--help` still succeed). The darwin artifact was not executed at
any point.

**The risk remains real going forward:** if a future Claude build is compiled
against a canary Bun using APIs newer than 1.3.14, its `cli.js` will not run on
Zig, and the only newer Bun is the Rust rewrite. That would defeat the de-rust
goal for that version.

**Failure signature to watch for:** `Expected CommonJS module to have a function
wrapper`, or a missing-API error, when running `bun cli.original.cjs --version`
on 1.3.14. Mitigations: pin Claude to the last version that still runs on
1.3.14 (keep its `build/extract/`), or shim the newer APIs on top of 1.3.14.
If it ever happens, record the first version that breaks here.

---

## Appendix: exact commands used ✅

Every command below was run on this host. `/usr/bin/claude` was only ever read.

```bash
# Bun 1.3.14, installed WITHOUT touching PATH or any rc file
curl -fsSL -o /tmp/bun-1.3.14.zip \
  https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-linux-x64.zip
unzip -o -j /tmp/bun-1.3.14.zip 'bun-linux-x64/bun' -d "$HOME/.bun-1.3.14"

# extract + post-process, Linux ELF
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh /usr/bin/claude

# the same pipeline against the real macOS binary, on Linux
npm pack @anthropic-ai/claude-code-darwin-arm64                    # → a .tgz
mkdir -p /tmp/ccmac && tar xf anthropic-ai-claude-code-darwin-arm64-*.tgz -C /tmp/ccmac
#   → /tmp/ccmac/package/claude, a 325 MB Mach-O arm64 binary
OUT_DIR=/tmp/macbuild scripts/build.sh /tmp/ccmac/package/claude

# L3: Bun's own parser (primary), then the faster JSC check (secondary)
"$HOME/.bun-1.3.14/bun" build --no-bundle --target=bun \
  build/extract/cli.original.cjs --outfile=/dev/null
"$HOME/.bun-1.3.14/bun" scripts/syntax-check.js build/extract/cli.original.cjs

# L4: the actual run, with a scratch config dir so ~/.claude is never touched
CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs --version

# regression
python3 -m pytest tests/ -q            # 31 passed
```

`scripts/syntax-check.js` is a **secondary** check only: `new Function(source)`
invokes JavaScriptCore's Function-constructor parser, not Bun's module loader,
and the two disagree in both directions (verification record, Step 3). Trust
`bun build --no-bundle` and `postprocess.py`'s own `check()`.
