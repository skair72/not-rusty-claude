# Findings

Everything verified during investigation of the native Claude Code binary on
macOS, with the goal of running Claude Code on a **pre-Rust (Zig-era) Bun
runtime** instead of whatever runtime Anthropic bundles.

> **Verification legend**
> ✅ observed directly on this machine · 📄 read from source (ClawGod / docs) · ⚠️ inferred, not yet run

All ✅ facts were produced against
`~/.local/share/claude/versions/2.1.238` on macOS 24.6.0 (arm64), 2026-08-21.

---

## 1. What "de-rust" means here

Bun (the JavaScript runtime that Claude Code is compiled with) is being
rewritten from **Zig** to **Rust**, largely with LLM tooling. PR
[oven-sh/bun#30412](https://github.com/oven-sh/bun/pull/30412) ("Rewrite Bun in
Rust") merged 2026-05-11. The rewrite is still experimental and scoped to
**Linux x64 glibc**; stable 1.3.x still ships from the Zig codebase, and
**1.3.14 is documented as the last Zig release**.

**The project goal:** run Claude Code's JavaScript on Zig-era Bun (≤ 1.3.14),
not on the Rust rewrite. Because Claude Code ships as a Bun *standalone*
executable (the runtime and the app baked into one signed Mach-O), we cannot
simply swap the embedded runtime. Instead we:

1. **Extract** `cli.js` and its assets out of the signed binary.
2. **Post-process** the JS so it runs outside the standalone sandbox.
3. **Run** it under an external, stock **Bun 1.3.14 (Zig)**.

The signed binary is only ever *read*, never executed or modified — so code
signing and notarization are irrelevant to this approach. (Contrast with the
byte-patch approach in §7, which does touch the binary and must re-sign.)

---

## 2. The native binary is a Bun standalone ✅

```
file:      ~/.local/share/claude/versions/2.1.238
type:      Mach-O 64-bit executable arm64 (thin, not universal)
size:      321,263,536 bytes
signing:   Developer ID Application: Anthropic PBC (Q6L2SF6YDW), notarized,
           hardened runtime (flags 0x10000), Identifier com.anthropic.claude-code
```

`otool -l` shows a `__BUN` segment containing a `__bun` section — the signature
of a `bun build --compile` standalone. That section holds the serialized Bun
module graph: the app's JS plus its native `.node` addons and file assets.

The install itself is path-independent: the data dir resolves at runtime from
`XDG_DATA_HOME ?? ~/.local/share`, and the only hardcoded path check gates
generation of the `ClaudeCode.app` wrapper (`process.execPath.startsWith(
…/claude/versions/)`), not the CLI. See [runbook.md](./runbook.md) §Relocation.

---

## 3. The Bun standalone format ✅

Full byte-level spec in [bun-section-format.md](./bun-section-format.md). Summary:

- Locate the `__BUN,__bun` Mach-O section → `(rawOffset, rawSize)`.
- Section starts with a **u64 little-endian length prefix**; the payload
  follows and **ends with the trailer magic** `\n---- Bun! ----\n` (15 bytes).
- Just before the trailer sits a **32-byte offsets struct**: at `+8`
  `modules_offset` (u32), `+12` `modules_size` (u32), `+16` `entry_point_id`
  (u32).
- The modules table is `modules_size / 52` records of **52 bytes** each:
  `+0` name offset, `+4` name size, `+8` content offset, `+12` content size,
  `+49` loader id (u8).

Observed on 2.1.238 ✅:

```
Section: __BUN,__bun  offset=69107712  size=251304613 (239.7 MB)
Payload: 251304605 bytes, trailer OK
Modules: 15 (entry id=0)
```

---

## 4. The 15 modules ✅

```
idx loader     size      name
 0  js         26.82 MB  /$bunfs/root/cli                  ← ENTRY (this is cli.js)
 1  js          2.1 KB   /$bunfs/root/image-processor.js   ← loader shim
 2  js          2.1 KB   /$bunfs/root/audio-capture.js     ← loader shim
 3  js          2.1 KB   /$bunfs/root/url-handler.js       ← loader shim
 4  js          2.1 KB   /$bunfs/root/computer-use-swift.js← loader shim
 5  js          2.1 KB   /$bunfs/root/computer-use-input.js← loader shim
 6  base64    1220.1 KB  /$bunfs/root/image-processor.node       ← native (arm64)
 7  base64     859.1 KB  /$bunfs/root/computer-use-swift.node    ← native (universal)
 8  base64    1652.4 KB  /$bunfs/root/computer-use-input.node    ← native (universal)
 9  file       203.6 KB  /$bunfs/root/chart.umd.min.js           ← asset
10  file       962.4 KB  /$bunfs/root/hljsBundle.generated.min.js← asset
11  file      3235.3 KB  /$bunfs/root/mermaid.min.js             ← asset
12  base64     427.8 KB  /$bunfs/root/audio-capture.node         ← native (arm64)
13  file      2177.2 KB  /$bunfs/root/payload.template.html.asset← asset
14  base64     329.0 KB  /$bunfs/root/url-handler.node           ← native (arm64)
```

The entry module (`id=0`) is the 26.8 MB `cli.js`. It opens with the pragma
`// @bun @bytecode @bun-cjs` and a CommonJS wrapper
`(function(exports, require, module, __filename, __dirname) {…`, and ends with a
**non-invoked** IIFE `…})`. See §6 for the post-processing this requires.

`cli.js` directly contains five `require('/$bunfs/root/<name>.node')` calls, so
those native modules must be extracted to real disk and the paths rewritten.

---

## 5. Two gotchas that break naive extractors ✅

### 5a. The `base64` loader stores RAW bytes, not base64 text

The native `.node` modules carry loader id for **`base64`**, but the stored
content is **the raw binary**, not base64-encoded text. The loader name
describes how Bun later exposes the asset *to JS* (as a base64 string), not how
it is stored. Proof: module 6's stored bytes begin `cf fa ed fe` = Mach-O magic
`0xFEEDFACF`, and `file` reports:

```
image-processor.node:     Mach-O 64-bit dynamically linked shared library arm64
computer-use-swift.node:  Mach-O universal binary (x86_64 + arm64)
computer-use-input.node:  Mach-O universal binary (x86_64 + arm64)
```

**Decoding this content as base64 corrupts it** — an early port of the extractor
did exactly that and produced 71-byte "modules." Write the raw bytes verbatim.

### 5b. ClawGod only extracts `napi`-loader modules → misses these

[ClawGod](https://github.com/0Chencc/clawgod)'s extractor only writes out
modules where `loader === 'napi'`. On 2.1.238 the addons are `base64`-loader, so
ClawGod extracts **zero** native modules yet still rewrites the `require()`
paths to point at them — meaning computer-use / audio / image features would
break under ClawGod on this build. Our [`extract_bun.py`](../tools/extract_bun.py)
handles `napi`, `base64`, and `file` loaders and writes raw bytes, so it is
correct here. (Two addons are **universal** Mach-O, which some tools also skip —
see §8.)

---

## 6. Post-processing cli.js to run outside the standalone ⚠️📄

Ported from ClawGod's `post-process.mjs` (📄). Not yet run locally (no Bun
installed on the investigation machine), so treat as the plan to verify on the
target Mac.

1. **Strip the leading pragma comment lines.** Bun's CJS loader needs the file
   to start with `(function`. Remove `/^(?:\/\/[^\n]*\n)+/`.
2. **Rewrite bunfs native requires.**
   `require('/$bunfs/root/X.node')` →
   `require(require('path').join(__dirname, 'assets', 'X.node'))`.
3. **Neutralize build-time `fileURLToPath()` leaks** (paths like
   `/home/runner/.../*.ts` baked in at build) → `__filename`, so relative
   resolutions land next to our `cli.cjs`.
4. **Invoke the IIFE.** Append
   `(exports, require, module, __filename, __dirname)` to the trailing `})` and
   save as `cli.original.cjs`.

**Open item ⚠️:** cli.js also references `file`-loader assets (mermaid, hljs,
chart, the html template) via `/$bunfs/root/…` paths. ClawGod's documented
transforms only cover the `.node` case explicitly. After post-processing, grep
the output for any remaining `/$bunfs/` string — each survivor is an asset path
that still needs a rewrite for the corresponding feature to work. Basic use
(`--version`, chat) does not touch these.

Launcher (📄, ClawGod's shape):

```bash
#!/bin/bash
export CLAUDE_CODE_EXECPATH="<native-binary>.orig"       # for shell integrations
exec "$BUN_BIN" "$HOME/.not-rusty-claude/cli.cjs" "$@"   # cli.cjs requires cli.original.cjs
```

---

## 7. The signature facts (why we don't need them here) ✅

These were verified while evaluating the *byte-patch* alternative, and they
explain why the extract-and-run approach is cleaner.

- **Relocation needs no patch and no re-sign.** A Mach-O signature seals the
  file's bytes, not its path. Copied to an arbitrary directory the binary still
  reports `valid on disk` / `satisfies its Designated Requirement`, and
  `spctl` accepts it as `source=Notarized Developer ID`. It runs from the
  foreign path, and still runs with a `com.apple.quarantine` xattr attached
  (notarization satisfies Gatekeeper).
- **The install path is not baked in** — it resolves from `XDG_DATA_HOME`.
- **The binary never signs anything itself** — the string `codesign` does not
  appear anywhere in it.
- **If you *do* modify bytes, you must re-sign or it is SIGKILLed.** A patched,
  un-re-signed binary exits **137** (SIGKILL) under the hardened runtime. ✅
  Re-signing ad-hoc while preserving the entitlements and identifier makes it
  run again — but notarization is lost (`spctl: rejected`), TCC permissions
  reset, and the next auto-update overwrites it. This is
  [`patch_claude.py`](../tools/patch_claude.py) (Approach B), kept for the case
  where you must edit something *not* in the JS layer.

---

## 8. Ready-made tools (prior art)

| Tool | Extracts JS | Native modules | Runs via Bun | Patches | Notes |
|---|---|---|---|---|---|
| [0Chencc/clawgod](https://github.com/0Chencc/clawgod) | ✅ parses table | ⚠️ `napi` only | ✅ wrapper + stock bun | ✅ 29 patches | Closest to our goal; misses base64 addons (§5b) |
| [vicnaum/bun-demincer](https://github.com/vicnaum/bun-demincer) | ✅ + split/deobfuscate/**reassemble** | ✅ | — | — | Most comprehensive decompiler |
| [vibheksoni/unbuned](https://github.com/vibheksoni/unbuned) | ✅ pure-Python, zero-dep | ❌ | — | — | Heuristic; skips native modules **and universal Mach-O** |
| [lafkpages/bun-decompile](https://github.com/lafkpages/bun-decompile) | ✅ + sourcemaps | — | — | — | Web + CLI |
| [@shepherdjerred/bun-decompile](https://www.npmjs.com/package/@shepherdjerred/bun-decompile) | ✅ + AI de-minify | — | — | — | Built to inspect Claude Code CLI |
| [Piebald-AI/tweakcc](https://github.com/Piebald-AI/tweakcc) | ✅ auto-locate | ✅ repacks | ❌ repacks into binary | ✅ themes/prompts/etc. | Byte-patch route; hits re-sign wall on macOS |

**Fastest ready-made path to our exact goal:** use ClawGod but disable its patch
list — you get its extraction + `cli.cjs` wrapper + stock-bun launcher. Only
caveat is §5b (it may miss the base64 native modules on current builds; our
extractor does not).

---

## 9. The npm shortcut is dead ✅

The tempting "no extraction at all" route — `npm pack @anthropic-ai/claude-code`
and run its `cli.js` under Bun — **no longer works** for current versions. The
npm registry metadata for `@anthropic-ai/claude-code@2.1.238` shows:

```
unpackedSize: 0.2 MB      (was ~13 MB when it shipped a real cli.js)
bin:          { claude: "bin/claude.exe" }
dependencies: []
```

It is now a thin bootstrap that downloads the native binary. Extraction from the
native binary (or from an old pre-native npm version) is the only way to get
`cli.js` today.

---

## 10. Central open question / project risk ⚠️

ClawGod's installer hard-requires **Bun ≥ 1.3.14** (`MIN_BUN_VERSION="1.3.14"`)
and notes: *"Anthropic builds claude-code with Bun's canary channel. Older Bun
panics on cli.original.cjs with 'Expected CommonJS module to have a function
wrapper'."* This is corroborated by
[anthropics/claude-code#45541](https://github.com/anthropics/claude-code/issues/45541).

The tension resolves **for now**: 1.3.14 is simultaneously the last **Zig**
release *and* ClawGod's minimum, so 1.3.14 satisfies both. But:

> **If a future Claude build is compiled against a canary Bun that uses APIs
> newer than 1.3.14, the extracted `cli.js` will not run on Zig 1.3.14** — and
> the only newer Bun is the Rust rewrite. That would defeat the de-rust goal for
> that version.

**Failure signature to watch for:** `Expected CommonJS module to have a function
wrapper`, or missing-API errors, when running `bun cli.cjs --version` on 1.3.14.
Mitigations to explore: pin Claude to the last version that still runs on 1.3.14;
or maintain a shim providing the newer Bun APIs on top of 1.3.14.

---

## Appendix: exact commands used ✅

```bash
# confirm the standalone section exists
otool -l ~/.local/share/claude/versions/2.1.238 | grep -A4 __BUN

# extract (verified)
tools/extract_bun.py ~/.local/share/claude/versions/2.1.238 ./extracted
#   → extracted/cli.original.js (26.8 MB) + extracted/assets/*.node + *.js

# confirm decoded native modules are real Mach-O
file extracted/assets/*.node

# signature facts
codesign -dvvv <binary>                 # Developer ID, notarized, flags 0x10000
codesign -v --verbose=2 <copy>          # valid at any path
spctl -a -vv -t install <copy>          # accepted: Notarized Developer ID
```
