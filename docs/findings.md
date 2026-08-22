# Findings

What is actually known about Claude Code's native binary and about running its
JavaScript on a **pre-Rust (Zig-era) Bun** instead of the runtime Anthropic
bundles.

> **Verification legend**
> ✅ **executed here** — run on this host (Linux x86_64, Debian 12, glibc 2.36) on 2026-08-22; command + output pasted in [verification-2026-08-22.md](./verification-2026-08-22.md), in its original body or its **2026-08-22 addendum** ·
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
Rust") was opened 2026-05-08 and **merged 2026-05-14T08:09:34Z** ✅ (GitHub API;
an earlier revision of this file said 05-11, which is not a date anything
happened on). At merge time the rewrite was experimental and scoped to Linux
x64 glibc — but that is **no longer current**: **`bun-v1.4.0` was published
2026-08-20T14:07:21Z** ✅, two days before this work, as the first Rust release
targeting all supported platforms. **1.3.14 remains the last Zig release**
(1.3.15 does not exist; the next release is 1.4.0), so the project's premise is
unaffected — but "the Rust rewrite is Linux-x64-only" is stale and must not be
repeated.

> **"Pre-Rust" is right about the rewrite and wrong as literal text.** ✅
> Bun 1.3.14 is not Rust-free: its `.comment` section reads
> `rustc version 1.94.0-nightly (c61a3a44d 2025-12-09)`, and it links vendored
> Rust crates — `lolhtml`, `cssparser-0.36.0`, `encoding_rs-0.8.35`,
> `selectors-0.33.0`. What 1.3.14 predates is the **Zig→Rust rewrite of Bun's
> own core**, verified by property rather than by version number: 1.3.14
> carries 7 Zig source-path strings (`bundler/LinkerGraph.zig`,
> `bundler/OutputFile.zig`, `bundler/bundle_v2.zig`, `js_parser/ast/P.zig`, …)
> and 1.4.0 carries **0**.

**The goal:** run Claude Code's JavaScript on Zig-era Bun (≤ 1.3.14) rather than
on the Rust rewrite. Because Claude Code ships as a Bun *standalone* executable
(the runtime and the app baked into one binary), the embedded runtime cannot be
swapped. Instead we:

1. **Extract** `cli.js` and its assets out of the binary.
2. **Post-process** the JS so it runs outside the standalone sandbox.
3. **Run** it under an external, stock **Bun 1.3.14 (Zig)**.

> **Scope this honestly.** ✅ What the extracted artifact *requires* is an
> **external Bun**. Running it on the Zig one is this project's deliberate
> choice — the whole point — not a technical constraint the artifact imposes.
> Measured: the same `cli.original.cjs` also runs on **Bun 1.4.0, the Rust
> build**, printing `2.1.222 (Claude Code)` with exit 0. 1.3.14 was shown
> *sufficient*, never *necessary*.

The native binary is only ever *read* — not executed, not modified — so code
signing and notarization are irrelevant to this approach. (Contrast with the
byte-patch approach in §7, which does touch the binary and must re-sign.)

Step 3 has now been done for real, on Linux: see §10. What "runs" does and does
not mean is §11 — read it before relying on this.

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

### 5a. Stored content is ALWAYS raw — and this file used to get the reason wrong ✅

**Correction, 2026-08-22. The two paragraphs that used to stand here were
false**, and they were the load-bearing premise of §5b, §8 and the README's
prior-art comparison. They are preserved in the git history; what follows is
what is measured.

**The rule that is true:** the content stored for a module is *always* the raw
bytes. The loader id says how Bun would *present* that module to JS at runtime;
it never describes an encoding applied to the stored payload. So **never decode
anything** — an early port of the extractor base64-decoded `.node` modules and
produced 71-byte "modules". Write bytes verbatim. That rule holds for a genuine
`base64` module too, which is why the extractor still accepts that loader.

**The claim that was false:** this file used to say the `.node` addons *carry
the `base64` loader id*, and built a story around it ("`base64` means expose it
to JS as a base64 string, so of course the bytes are raw"). Measured with a
standalone parser against both shipped binaries, the raw loader byte at record
offset 49 for every `.node` module is **10**, and in Bun 1.3.14's own enum
([`src/bundler/options.zig`](https://raw.githubusercontent.com/oven-sh/bun/bun-v1.3.14/src/bundler/options.zig),
`pub const Loader = enum(u8)`) **10 is `napi`**. There was never a `base64`
addon in any binary this project has looked at.

**Why the mistake happened, because that is the useful part.** The loader table
in [bun-section-format.md](./bun-section-format.md) and in `extract_bun.py` was
**transcribed from prior art rather than read from Bun's source**, and it
omitted `jsonc = 7`. Every id from 7 upward therefore shifted down by one:
byte 10 read as `base64` instead of `napi`. Extraction still worked, purely by
luck — the mislabel landed inside the `{napi, base64, file}` accept-set — so
nothing failed, and the wrong label was then *explained* instead of checked. A
genuine `base64` module (real byte 11) would have been labelled `dataurl`,
missed the accept-set and been **silently dropped**; a `sqlite` module (15)
would have become `unknown(16)`. Fixed in `61957a6`, with
`test_loader_ids_match_bun_1_3_14` pinning the table and
`test_genuine_base64_module_is_written_to_disk` covering the latent bug.

The lesson generalises past this bug: *this repo's governing rule is that every
claim traces to measured evidence, and a plausible story told to explain an
unmeasured number is exactly how that rule gets broken.*

The raw-bytes fact itself is still verified, and asserted permanently by
`tests/test_integration.py`: the bytes stored for
`/$bunfs/root/image-processor.node` in `/usr/bin/claude` begin `\x7fELF`
(`test_real_elf_binary_extracts`); the darwin equivalent asserts the universal
(`0xCAFEBABE`) or thin-arm64 (`0xCFFAEDFE`) magic.

### 5b. What ClawGod actually does and does not extract ✅

**Correction, 2026-08-22.** This section used to read: *"ClawGod's extractor only
writes out modules where `loader === 'napi'`. On every build measured here the
addons are `base64`-loader, so ClawGod would extract zero native modules."*
**That is false**, and it was this project's flagship argument for its own
extractor. It followed directly from §5a's mislabel.

Measured against ClawGod at commit `4401fdb` (2026-08-22):

- ClawGod's own loader table (`install.sh`, `const LOADERS`) is **Bun's**,
  `jsonc: 7` included, `10: 'napi'`. It matched Bun's source all along; ours was
  the one that did not.
- The `.node` addons carry byte 10, so ClawGod labels them `napi` and its
  `else if (m.loader === 'napi')` branch **writes them out** — to
  `vendor/<name>/<arch>-<os>/<name>.node`. It extracts native modules correctly.

The real, measured difference is elsewhere, and it is narrower:

- **`file`-loader assets are dropped.** ClawGod's loop has no `file` branch, so
  `chart.umd.min.js`, `hljsBundle.generated.min.js`, `mermaid.min.js` (and
  darwin's `payload.template.html.asset`) fall to `else { dropped++ }`.
- **Its rewrite is `.node`-only.** `require\(['"](/\$bunfs/root/([\w-]+)\.node)['"]\)`
  matches **2** of the 5 `/$bunfs/` literals in the linux 2.1.222 entry module.
  Measured leftovers after its rewrite: `chart.umd.min.js`,
  `hljsBundle.generated.min.js`, `mermaid.min.js` — still pointing into a
  `/$bunfs` that does not exist at runtime, and never extracted either. That is
  the same defect this project's own ported version had (§6 transform 2).

[`extract_bun.py`](../tools/extract_bun.py) handles `napi`, `base64` and `file`
loaders and writes raw bytes; `postprocess.py` rewrites the literal in any
syntactic position. That is a genuine difference — it just is not the one this
document used to claim.

---

## 6. Post-processing `cli.js` to run outside the standalone ✅

**This section was previously a plan ported from ClawGod's `post-process.mjs`.
It is now a measurement.** `tools/postprocess.py` has been run against both real
binaries; the transforms below are what it actually does, with the counts it
actually produced.

1. **Strip the leading pragma comment lines** (`^(?:\/\/[^\n]*\n)+`, once) —
   necessary **only because transform 4 invokes the wrapper ourselves**. See
   the measured matrix below; the causal story this item carried until
   2026-08-22 ("the pragma line would otherwise make it panic") was wrong.
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

### Why the pragma has to go — the measured 2×2 ✅

The pragma is `// @bun @bytecode @bun-cjs`. It is not decoration: it is what
tells Bun *"this whole file is a CommonJS function wrapper, invoke it for me"*.
Strip it and Bun stops doing that, so you have to invoke the wrapper yourself;
keep it **and** invoke, and the file no longer has the shape Bun expects.

All four combinations, run to completion against the real linux 2.1.222 entry
module (`--version`), on two Bun versions ✅:

| build | Bun 1.3.13 | Bun 1.3.14 |
|---|---|---|
| pragma **stripped** + IIFE invoked — **as shipped** | panic | `2.1.222 (Claude Code)` |
| pragma **kept** + IIFE **not** invoked | `2.1.222 (Claude Code)` | `2.1.222 (Claude Code)` |
| pragma kept + IIFE invoked | panic | **panic** |
| pragma stripped + IIFE not invoked | panic | exit 0, **no output** (nothing ran) |

"panic" is `TypeError: Expected CommonJS module to have a function wrapper.`

So: the pragma **alone** does not make Bun panic — on 1.3.14 *or* on 1.3.13 it
is the configuration that works. The panic comes from pragma **plus** manual
invocation. Stripping is necessary only because this project also invokes.
(Keeping the pragma and not invoking is a viable alternative shape with more
version headroom — §10.)

`postprocess.py`'s `check()` then refuses to write `cli.original.cjs` at all
unless **three** conditions hold — the docstring used to say two:

1. the output starts with `(function`;
2. exactly one IIFE invocation was appended (`counts["iife"] == 1`);
3. it is **not** the case that zero `/$bunfs/` literals were rewritten while
   `assets/` holds files on disk — the "silently asset-less" outcome, which is
   what a wrong VFS prefix (Windows' `B:/~BUN/root/`, [status.md](./status.md)
   § Windows/PE) would produce.

A silently broken output file reaching Bun would surface only as that confusing
panic, so the failure is made loud and early instead.

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

🔎 After post-processing, each output still contains 16 `import.meta.url`
references, **12 textual `fileURLToPath` hits — of which 9 are call sites and 3
are `import { fileURLToPath } from 'node:url'` lines inside embedded script
text** (an earlier revision called all 12 "calls"), and 2 bare `file:///`
literals on linux / 3 on darwin. None of the commands executed in the
verification run hit a problem from them; no stronger claim than that is
available.

### The launcher that no longer exists, and `CLAUDE_CODE_EXECPATH` ✅

The ported design ended with a shell launcher installed on `PATH`:

```bash
export CLAUDE_CODE_EXECPATH="<native-binary>"   # "for shell integrations"
exec "$BUN_BIN" "$INSTALL/cli.cjs" "$@"
```

`scripts/build.sh` **no longer writes any launcher and installs nothing**: a
file named `claude` on `PATH` could shadow a real installation. It prints the
full-path command instead.

**Correction, 2026-08-22.** This section, and
[runbook.md](./runbook.md) § Shell integrations, used to describe the
consequence as *"`CLAUDE_CODE_EXECPATH` is now unset unless you export it
yourself"* and told you to export it. **Exporting it does nothing.** Measured
in the post-processed linux 2.1.222 artifact:

- `process.env.CLAUDE_CODE_EXECPATH` — **0 occurrences**. The CLI never reads
  the variable, in any form.
- The string appears exactly **3** times: once as the constant
  `hNs="CLAUDE_CODE_EXECPATH"`, and twice as an entry in lists of
  environment-variable names the CLI manages for spawned shells and background
  sessions.
- The one place it is *used* is a **write**, in `getEnvironmentOverrides`:
  `c[hNs]=process.execPath`, unconditional — not gated on any
  standalone/install-method check.

So the variable is **write-only from the CLI's point of view**, and the real
consequence is the opposite of the old advice: every shell the CLI spawns
receives `CLAUDE_CODE_EXECPATH=<the bun binary>`, because under this setup
`process.execPath` *is* bun. The generated `find`/`grep` shell functions then
read it back at the shell level:

```bash
function find {
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin='<bundled bfs path>'
  ...
  (exec -a bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"})
}
```

The `[[ -x ]]` guard does not save you: bun *is* executable, so `find` and
`grep` inside a spawned shell resolve to the bun binary invoked with `bfs`/
`ugrep` arguments. Read out of the shipped source, **not observed live** 🔎 —
the snippet above is emitted by a helper whose gate is build-folded true rather
than tied to the standalone check. It belongs to the same family as §11: things
that differ because the process is not a Bun standalone.

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
| [0Chencc/clawgod](https://github.com/0Chencc/clawgod) | ✅ parses table | ✅ `napi` — correctly ✅ | ✅ wrapper + stock bun | ✅ 29 patches | Closest to our goal. Drops `file`-loader assets and rewrites only `.node` requires (§5b) |
| [vicnaum/bun-demincer](https://github.com/vicnaum/bun-demincer) | ✅ + split/deobfuscate/**reassemble** | ✅ | — | — | Most comprehensive decompiler |
| [vibheksoni/unbuned](https://github.com/vibheksoni/unbuned) | ✅ pure-Python, zero-dep | ❌ | — | — | Heuristic; skips native modules **and universal Mach-O** |
| [lafkpages/bun-decompile](https://github.com/lafkpages/bun-decompile) | ✅ + sourcemaps | — | — | — | Web + CLI |
| [@shepherdjerred/bun-decompile](https://www.npmjs.com/package/@shepherdjerred/bun-decompile) | ✅ + AI de-minify | — | — | — | Built to inspect Claude Code CLI |
| [Piebald-AI/tweakcc](https://github.com/Piebald-AI/tweakcc) | ✅ auto-locate | ✅ repacks | ❌ repacks into binary | ✅ themes/prompts/etc. | Byte-patch route; hits the re-sign wall on macOS |

**Correction, 2026-08-22.** This section used to end: *"ClawGod's extractor
misses the `base64` addons (§5b) and its `fileURLToPath` transform matches
nothing on current binaries (§6), and both gaps fail silently."* **Both halves
are false**, and both were inherited from §5a's mislabelled loader table. What
is measured, against ClawGod at commit `4401fdb` and the real linux 2.1.222
entry module ✅:

- ClawGod extracts the native addons **correctly** — its loader enum is Bun's,
  the addons are `napi`, its `napi` branch writes them out (§5b).
- Its `fileURLToPath` transform matches **7 sites**, the same 7 this project
  rewrites. It targets the literal-URL form
  (`[\w$]+\.fileURLToPath("file:///home/runner/work/claude-cli-internal/…")`),
  which is exactly the shape that survives Bun's bundler. It was **this
  project's own scaffolded port** that targeted `fileURLToPath(import.meta.url)`
  and matched 0 (§6) — a defect this repo introduced and then attributed to its
  prior art.

The differences that *are* measured are narrower and specific: ClawGod drops
`file`-loader assets entirely, and its `/$bunfs/` rewrite covers only
`require("….node")` — 2 of the 5 literals on linux — leaving three asset paths
pointing into a filesystem that no longer exists. This repo's two scripts cover
both shapes and write all three loader kinds. That is a real advantage; it is
not the sweeping one this table used to assert. Prefer whichever tool you can
re-measure.

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

**Correction, 2026-08-22 — and it makes the darwin evidence stronger, not
weaker.** This paragraph used to end "only *running* the extracted darwin
JavaScript still needs Apple hardware", and the README, `status.md` and the
verification record all said the darwin artifact "has never been executed".
Measured ✅: it has now, **on this Linux host**, under Linux Bun 1.3.14:

```
$ DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=$(mktemp -d) \
    ~/.bun-1.3.14/bun <darwin-build>/extract/cli.original.cjs --version
2.1.239 (Claude Code)          rc=0
```

The extracted darwin JavaScript boots and runs. What actually needs a Mac is
verifying **macOS-specific behaviour**, and that limit is precise:

- The darwin `.node` addons are Mach-O — `cffaedfe` (thin arm64) for
  `image-processor` / `audio-capture` / `url-handler`, `cafebabe` (universal)
  for `computer-use-swift` / `computer-use-input`. Loading one on Linux fails
  with `ERR_DLOPEN_FAILED … invalid ELF header` ✅. Nothing about the darwin
  native layer can be exercised here.
- `process.platform` is `linux`, so every platform-conditional branch takes the
  Linux path. Nothing that depends on being *on* macOS is exercised.

See [status.md](./status.md) § macOS execution.

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
$ DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
    ~/.bun-1.3.14/bun build/extract/cli.original.cjs mcp list
No MCP servers configured. Use `claude mcp add` to add a server.
(exit 0)
```

**Lead with `mcp list` or `doctor`, not `--version`.** ✅ `--version` is very
nearly vacuous as evidence. Instrumenting the bundle's two lazy-module helpers
(`re` = CommonJS wrapper, 1644 of them; `E` = ESM lazy init, 5104) and counting
how many are actually initialised per command:

| command | lazy modules initialised |
|---|---|
| `--version` | **0 / 6748** (0.0%) — a hardcoded fast path |
| `--help` | 2725 / 6748 |
| `doctor` | 2757 / 6748 |
| `mcp list` | 2761 / 6748 |

`--version` proves the file parses, the CJS wrapper is invoked, and the entry
module's top level runs. It proves **nothing** about Bun's API surface, because
it reaches none of it. It was the README quickstart command and this section's
headline answer; both now lead with `doctor` / `mcp list` instead. `mcp list`
reads config, initialises `.claude.json`, writes a timestamped backup and
dispatches into the MCP subsystem; `doctor` probes the search backend, install
identity and updater state.

**What the review fleet exercised beyond that** 📓 — reproduced here ✅ on
2026-08-22 against a **loopback-only** mock of the Messages API (127.0.0.1, a
throwaway `HOME` and `CLAUDE_CONFIG_DIR`, a fake key; no traffic left the host
and no real account was touched):

- a full agentic loop with SSE streaming — `message_start` /
  `content_block_delta` / `message_stop` — multi-turn tool use, and cost
  accounting, exit 0;
- the **Bash** tool spawning a real subprocess (`echo` + `uname -s` returned
  `Linux` and a live pid);
- the **Read** tool returning a base64 image block (see §11 — this is where the
  equivalence gap shows);
- the Ink **TUI under a pty**: the welcome screen, the theme picker and the
  syntax-highlighted diff preview all render on Bun 1.3.14.

No Bun-API failure anywhere in any of it.

**Scope the answer exactly:**

> Every Bun API reached on the code paths actually exercised is present and
> working in Bun 1.3.14, **for Claude Code 2.1.222, on `linux-x64`.**

It is **not** a permanent guarantee, and "it runs" is not "it behaves the same
as the shipped binary" — that is §11, and the honest answer there is *no*.

### The ≥ 1.3.14 floor is ours, not Claude's ✅

Rebuilding the same entry module in the **pragma-preserving** shape (§6: keep
`// @bun @bytecode @bun-cjs`, do **not** append the IIFE) and running it on
Bun 1.3.13:

```
$ DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=$(mktemp -d) \
    <bun-1.3.13> cli.pragma.cjs --version
2.1.222 (Claude Code)                    rc=0
```

`--help`, `mcp list` and `doctor` also run there. So **Claude Code 2.1.222 has
at least one Bun minor version of headroom below 1.3.14**, and the "Bun ≥
1.3.14" floor is a property of **this project's transform**, not of Claude. The
as-shipped artifact does fail on 1.3.13, but with our own panic, not a missing
API.

### The canary alarm is an ambiguous signal ⚠️

This section used to name `Expected CommonJS module to have a function wrapper`
as *the* failure signature for "Claude now needs a newer Bun". It is not a
clean canary: **this project's own transform produces that exact panic**
whenever the pragma/IIFE shape is wrong (§6's 2×2), and so does simply using a
Bun that is too old. Seeing it tells you the module *shape* was rejected, which
has at least three causes.

A **missing-API error** — a `TypeError` naming a `Bun.*` property that does not
exist — is the unambiguous signal. To tell them apart when a new Claude version
will not run, rebuild in the pragma-preserving shape and try again: if that
runs, the wrapper panic was ours.

**The risk remains real going forward:** if a future Claude build is compiled
against a canary Bun using APIs newer than 1.3.14, its `cli.js` will not run on
Zig, and the only newer Bun is the Rust rewrite. That would defeat the de-rust
goal for that version. Nothing measured so far comes close: what would actually
refute the headline is a Claude release whose `cli.js` fails on 1.3.14 **in the
pragma-preserving build**, with a genuine missing-API error.

Mitigations: pin Claude to the last version that still runs on 1.3.14 (keep its
`build/extract/`), or shim the newer APIs on top of 1.3.14. If it ever happens,
record the first version that breaks here.

### Generalisation: newer Claude builds ✅

An **unmodified** `scripts/build.sh` was pointed at
`@anthropic-ai/claude-code-linux-x64@2.1.240` — 18 releases after the 2.1.222
this document is measured against — downloaded from npm on this host:

```
Modules: 11 (entry id=0)
Extracted: 1 cli.js + 7 assets (3 loader shims left inlined in cli.js)
   incl. NEW clipboard-napi.node (napi) and payload.template.html.asset (file)
/$bunfs/ paths rewired : 7      file:// leaks rewritten: 7      IIFE: 1
```

and the result printed `2.1.240 (Claude Code)` and answered `mcp list` on
**both** Bun 1.3.14 and Bun 1.4.0, exit 0 in all four combinations. The
extractor generalises further than this document previously claimed — which is
also why the integration tests hardcode 2.1.222/2.1.239 counts as a *tripwire*
rather than a contract.

---

## 11. The equivalence gap: this is not the same program ⚠️✅

**New section, 2026-08-22.** Everything above answers *does it run*. This
answers *does it behave the same as the binary Anthropic ships*, and the answer
is **no**. Read this before deciding whether to use this project. The word
`isStandaloneExecutable` appeared **nowhere in this repository** before this
section existed.

Claude Code decides at runtime whether it is a Bun standalone with one
function:

```js
function CE(){return Bun.isStandaloneExecutable===!0}
```

Under an external Bun that property is `undefined` (1.3.14) or `false` (1.4.0)
✅, so `CE()` is **false** and the CLI takes its non-standalone branch at all
**21** call sites. Most of those are correct — the generic self-spawn helper,
for instance, has a proper `{cmd: process.execPath, prefixArgs: [process.argv[1]]}`
branch. Some are not.

### Measured consequences

**1. Native image processing is silently disabled.** The image path is

```js
async function uYe(){ if(Fbo)return Fbo.default;
  if(CE()) try{ …native image-processor… }catch{ console.warn("Native image processor not available, falling back to sharp") }
  …bundled JS sharp… }
```

With `CE()` false the native branch is **never attempted** — not tried and
failed, *unreachable by construction* — and the fallback is a bundled JS sharp
that needs libvips, which is not among the extracted assets. Measured
end-to-end through the mock loop above, reading a 3000×3000 PNG with the
**Read** tool ✅:

| build | tool result |
|---|---|
| as shipped | `is_error: true` — *"Unable to resize image — dimensions exceed the 2000x2000px limit and image processing failed."* |
| same artifact, `Bun.isStandaloneExecutable` forced `true` | a correct **469,774-byte JPEG** (`ff d8 ff e0`), `media_type: image/jpeg` |

The addon itself is fine: `require("assets/image-processor.node")` under Bun
1.3.14 exports `processImage`, `hasClipboardImage`, `readClipboardImage`,
`ImageProcessor`; it reads that PNG's metadata as `{width:3000, height:3000,
format:"png"}` and resizes it to a valid JPEG ✅. Extraction and path rewriting
are not the problem. **The CLI simply never asks for it.**

**2. `exit 0` is not evidence that the asset wiring works.** Both addon loaders
swallow failure:

```js
function FDu(){ if(NDu)return wbo; NDu=!0; try{ wbo=l5l() }catch{ wbo=null } return wbo }
```

A missing, corrupt or wrong-architecture `.node` produces `null` and a degraded
feature, never a non-zero exit. Any claim of the form "it exited 0, so the
assets resolve" is unsound — including in this repo's own earlier evidence.

**3. The seccomp sandbox is entirely disabled.** `function kms(){return CE()}`
gates the whole thing: the `/proc/self/exe` fd, the `applyPath`, the
`MIMALLOC_SCAVENGER` override all early-return. This is a **real security
reduction** versus the native binary, on Linux, and it is silent.

**4. Embedded ripgrep degrades to a system `rg`.** `if(CE())` selects the
embedded mode; otherwise the CLI looks for `rg` on `PATH`. So **`rg` is a de
facto runtime prerequisite** of this setup. Anthropic's own error string for
the miss is *"ripgrep not found on PATH … or use the native claude binary which
embeds it."*

**5. Install identity reports `unknown`.** Which is what makes the auto-updater
hazardous — see [runbook.md](./runbook.md) § Surviving Claude updates. `doctor`,
A/B on the same artifact ✅:

```
as shipped                     forced isStandaloneExecutable=true
  Running: unknown (2.1.222)     Running: native (2.1.222)
  Search: OK (/usr/bin/rg)       Search: OK (bundled)
```

### Do not "fix" this by flipping the flag globally

The obvious patch — define `Bun.isStandaloneExecutable = true` — makes the
image path work and immediately breaks search, because "embedded ripgrep" then
means *"re-exec `process.execPath` with argv0 `rg`"*, and `process.execPath` is
**bun**. Measured with the same `Grep` call through the mock loop ✅:

| build | Grep for a string that exists in the tree |
|---|---|
| as shipped | `hay/a.txt:1:NEEDLE-12345` |
| flag forced true | **`No matches found`** |

Not an error — a *silently wrong answer*. Any shim here must be scoped to the
image-processor call site, and is deliberately **not** implemented yet.

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

# L4: the actual run. DISABLE_AUTOUPDATER=1 is not optional - see runbook.md
# § Surviving Claude updates. The scratch config dir keeps ~/.claude untouched.
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs mcp list

# regression
python3 -m pytest tests/ -q            # 43 passed
```

`scripts/syntax-check.js` is a **secondary** check only: `new Function(source)`
invokes JavaScriptCore's Function-constructor parser, not Bun's module loader,
and the two disagree in both directions (verification record, Step 3). Trust
`bun build --no-bundle` and `postprocess.py`'s own `check()`.
