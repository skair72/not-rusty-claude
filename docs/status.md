# Project status — what is verified, on what, and what is not

`not-rusty-claude` extracts Claude Code's JavaScript out of its Bun *standalone*
executable and runs it under a stock external **Bun 1.3.14** — the last Zig
release before Bun's Zig→Rust rewrite.

**As of 2026-08-22 the pipeline has been run end to end on this host.** The
extracted, post-processed `cli.original.cjs` from the real Linux binary
(Claude Code **2.1.222**) starts under external Bun 1.3.14 and answers
`--version`, `--help`, and `mcp list` — the last of which really reads and
writes config state on disk. That is the first empirical answer to the risk in
[findings.md](./findings.md) §10, and it is a *positive* one **for that version,
on that platform, on those code paths** — nothing wider.

The single evidence record is
[**verification-2026-08-22.md**](./verification-2026-08-22.md): every command
and its pasted output. This file summarises it; that file proves it. Read
[findings.md](./findings.md) for the facts about the binary format itself.

---

## Legend

Statuses distinguish *how* something is known, not how likely it is:

- ✅ **Executed here** — actually run on this host (Linux x86_64, Debian 12,
  glibc 2.36) on 2026-08-22, with command and output pasted in
  [verification-2026-08-22.md](./verification-2026-08-22.md).
- 🔎 **Static check here** — real bytes of a real binary were parsed or
  transformed on this host, or the output was accepted by Bun's own
  parser/transpiler — but **nothing was executed**.
- 🖥️ **Needs hardware we do not have** — the remaining step requires Apple
  Silicon (or Windows). Evaluated and rejected here; see
  [§ macOS execution](#macos-execution-the-one-real-gap).
- ⛔ **Deliberately not implemented** — a scoped-out choice, not an oversight.
- 📓 **Prior-session record** — observed on a Mac on 2026-08-21 against Claude
  Code 2.1.238; **not** re-checked on this host, and not covered by the
  verification record.

There is no "scaffold" status any more. Nothing in `tools/` or `scripts/` is
unrun.

---

## Verification matrix

Columns are the three containers Claude Code ships as. Versions differ because
each artifact is whatever was obtainable here: the Linux binary is the one
installed on this host, the macOS and Windows binaries were downloaded from npm
(see [findings.md](./findings.md) §9).

| Capability | Linux x64 · ELF · 2.1.222 | macOS arm64 · Mach-O · 2.1.239 | Windows x64 · PE · 2.1.239 |
|---|---|---|---|
| Locate the Bun section | ✅ `.bun` @ 86904832 (Step 2) | ✅ `__BUN,__bun` @ 69107712 (Step 3b) | ⛔ tool refuses PE; layout read by hand (see below) |
| Parse payload + module table | ✅ 8 modules, entry id 0 (Step 2) | ✅ 15 modules, entry id 0 (Step 3b) | ⛔ / read by hand: 9 modules, entry id 0 |
| Extract `cli.js` + assets (raw bytes) | ✅ 1 + 5 assets (Step 2) | ✅ 1 + 9 assets (Step 3b) | ⛔ |
| `postprocess.py` transforms | ✅ 5 `/$bunfs/` rewrites, 7 `file://`, 1 IIFE, 0 leftovers (Step 2) | ✅ 9 `/$bunfs/` rewrites, 8 `file://`, 1 IIFE, 0 leftovers (Step 3b) | ⛔ (and the regexes would not match — see below) |
| `scripts/build.sh` end to end | ✅ (Step 2) | ✅ (Step 3b) | ⛔ |
| Output accepted by Bun 1.3.14's parser | 🔎 `bun build --no-bundle`, exit 0 (Step 3) | 🔎 `bun build --no-bundle`, exit 0 (Step 3b) | ⛔ |
| **Runs under external Bun 1.3.14** | ✅ `--version`, `--help`, `mcp list` all exit 0 (Steps 5, 5b) | 🖥️ not executed here — needs Apple Silicon | ⛔ would also need a Windows Bun |
| Runtime asset (`assets/*`) resolution | 🔎 **static only** — paths rewritten and files on disk; no executed command loaded one (Step 4) | 🔎 static only | ⛔ |
| Test suite (31 tests) | ✅ 31 passed, incl. 4 integration tests against the real ELF **and** Mach-O binaries | ✅ same run | — |
| Approach B: byte-patch + re-sign (`tools/patch_claude.py`) | n/a (macOS-only concern) | 📓 verified 2026-08-21 on 2.1.238; not re-checked here | n/a |

Step numbers refer to sections of
[verification-2026-08-22.md](./verification-2026-08-22.md).

The macOS column is worth reading twice: **extraction and post-processing of the
real Mach-O binary are not a projection — they were executed here**, on this
Linux host, against the genuine 325 MB `darwin-arm64` binary. Parsing a
container is byte arithmetic and needs no Mac. Only *running* the resulting
JavaScript does.

---

## macOS execution: the one real gap

The darwin artifact (`cli.original.cjs`, 28,244,063 bytes) was built and checked here,
and Bun 1.3.14's own parser accepts it (`bun build --no-bundle --target=bun`,
exit 0). **It has never been executed.** Nothing below the parser is known: not
that it boots, not that it loads its `.node` addons, not that it resolves a
single Bun API at runtime.

Running it needs Apple hardware. Emulation was evaluated on this host and
rejected on observed evidence, not on preference:

| Route | Blocker observed here |
|---|---|
| QEMU-KVM / `docker-osx` | no `/dev/kvm`; `/proc/cpuinfo` exposes no `vmx`/`svm` |
| Darling (translation layer, no VM) | needs the `darling-mach` kernel module; `/lib/modules` absent and `modprobe` fails even `--privileged` |
| QEMU TCG (pure software) | hours-long boots on shared vCPUs, and it would exercise the darwin-**x64** build, not arm64 |

Apple's licence also restricts macOS virtualization to Apple hardware. So this
gap closes only on a real Mac: run
`scripts/build.sh <native-binary>` there, then the printed
`bun .../cli.original.cjs --version`, and record the result.

---

## Windows / PE

`extract_bun.py` refuses PE input with a message that points here, so here is
the whole picture.

The `win32-x64` build (`@anthropic-ai/claude-code-win32-x64`, `claude.exe`,
337,672,352 bytes, version 2.1.239) **is a Bun standalone like the others**. Its
section table was read directly on this host:

```
.bun   rawoff = 95182336   rawsize = 242479616   vsize = 242479183
```

and the payload behind it has the same shape as the ELF and Mach-O ones — u64
length prefix (`payload_size = 242479175`), module graph, 16-byte trailer
`\n---- Bun! ----\n`, 32-byte offsets struct, 52-byte module records: **9
modules, `entry_point_id = 0`**. So the format work is done; nothing about PE is
mysterious.

**Extraction is deliberately unimplemented (⛔), for two reasons:**

1. **Running the result would additionally need a Windows Bun.** Nothing on this
   host, and no non-Windows host, can close the loop. Shipping an extractor
   whose output nobody here can run is exactly the "plausible but never
   executed" posture this project just spent nine tasks removing.
2. **It is not a one-line change.** On Windows the virtual filesystem prefix is
   different: module names are `B:/~BUN/root/...`, not `/$bunfs/root/...`
   (observed directly in `claude.exe` 2.1.239 — 6 such literals in the entry
   module, and **zero** `/$bunfs/` ones). `postprocess.py`'s `BUNFS_LITERAL`
   regex would therefore rewrite **nothing**, and the output would be silently
   asset-less rather than loudly broken. Anyone implementing PE support must
   generalise that pattern *and* the leftover check first.

Everything else about the PE entry module matches the other platforms: it opens
with the `// @bun @bytecode @bun-cjs` pragma and a CommonJS wrapper, and ends
with the same non-invoked `})`.

> Evidence note: these PE numbers were measured on this host by direct
> read-only byte inspection while writing this document. They are **not** part
> of [verification-2026-08-22.md](./verification-2026-08-22.md), whose scope is
> the Linux end-to-end run and the macOS static checks. Reproduce with
> `npm pack @anthropic-ai/claude-code-win32-x64` and a PE section-header walk
> (see [bun-section-format.md](./bun-section-format.md) §1c).

---

## Remaining work

Ordered. Each says how to verify and how to fix, so a cold session can pick up.

### 1. Re-measure when Claude Code updates

The counts in this repo are facts about **specific versions**, not constants.
Module counts, the entry module's *name*, and the transform counts all differ
between 2.1.222/linux and 2.1.239/darwin already (findings §4). A new Claude
version will change them again.

- **Verify:** re-run `scripts/build.sh <native-binary>` and then
  `python3 -m pytest tests/ -q`. The integration tests hardcode the measured
  counts (8/5/7 for linux-x64 2.1.222, 15/9/8 for darwin-arm64 2.1.239).
- **When they fail:** that failure is the **feature**, not a bug — it is the
  early warning that the binary changed. Re-measure against the new binary,
  update `tests/test_integration.py` and findings §4/§6 with the new numbers,
  and re-run the L3/L4 checks. **Do not loosen the assertions to make them
  pass.**
- **If extraction itself breaks:** print every module's `(loader, name, size)`
  and compare against [bun-section-format.md](./bun-section-format.md). Never
  match the entry module by name — use `entry_point_id` (findings §4).

### 2. Run the darwin artifact on a Mac 🖥️

The only step that cannot be done here. See
[§ macOS execution](#macos-execution-the-one-real-gap).

- **Verify:** on Apple Silicon with Bun 1.3.14 installed,
  `bun <out>/extract/cli.original.cjs --version` should print the version
  string of the binary you extracted from.
- **Expected failure to plan for:** `Expected CommonJS module to have a function
  wrapper` means Bun older than 1.3.14 or a pragma/IIFE problem; a missing-API
  error means findings §10's risk materialised on darwin.

### 3. Verify runtime asset resolution 🔎

Unclosed on **every** platform, including Linux. The rewritten
`require('path').join(__dirname,'assets',…)` expressions exist as text and the
files exist on disk, but no executed command has ever loaded one — proven, not
assumed: with the whole `assets/` directory renamed away, `--version` and
`--help` still exit 0 (verification Step 4).

- **Verify:** exercise a feature that actually needs an asset — syntax
  highlighting (`hljsBundle.generated.min.js`), a mermaid diagram
  (`mermaid.min.js`), image handling (`image-processor.node`) — and watch for
  `ENOENT` / `Cannot find module`.
- **Fix:** the rewrite shape is in `postprocess.py`; confirm the *call* shape in
  the minified source first (`require`? `readFile`? `Bun.file`?) rather than
  guessing, then extend the rewrite.

### 4. Update survival & version pinning

Run the extracted build with `DISABLE_AUTOUPDATER=1`, and never `claude update`
against it. `Bun.isStandaloneExecutable` is undefined under an external Bun, so
Claude's install-method detection reports `unknown` and its updater takes the
npm/bun global-install route — with network that installs a *different,
npm-based* Claude Code on the machine, and it never updates these artifacts.
See [runbook.md](./runbook.md) § Surviving Claude updates. Re-run `build.sh`
against a new native binary to move forward; a failed rebuild now keeps the
previous `build/extract/` intact.

- **The project's kill switch (findings §10):** if a future Claude build is
  compiled against a canary Bun newer than 1.3.14, its `cli.js` will not run on
  Zig at all — the only newer Bun is the Rust rewrite. As of 2.1.222 this has
  **not** happened; that is a measurement, not a guarantee.
- Keep the last working `build/extract/` and pin that Claude version. If it ever
  breaks, record the first version where it broke in findings §10.

---

## Known unknowns

- **Everything past the three executed commands.** No network call, no model API
  request, no interactive TUI, no tool execution, no asset load has ever run
  under Bun 1.3.14 here. `--version`, `--help`, and `mcp list` did.
- **Minified call shapes drift.** `postprocess.py`'s regexes target minified
  output that changes every release. They are measured against two real
  binaries, not contracts.
- **`CLAUDE_CODE_EXECPATH`.** The old design's launcher exported it and no
  longer exists. See [runbook.md](./runbook.md) § Shell integrations for what to
  do instead and what is unknown about it.
- **Universal vs thin addons.** Some darwin `.node` files are universal
  (x86_64 + arm64), others thin arm64; the linux ones are ELF. Matters only if
  you mix architectures.
- **Canary API drift** is outside our control and remains the single biggest
  risk to the project's goal.

---

## What NOT to do

- **Don't loosen the integration tests' hardcoded counts** to make a new Claude
  version pass. They are a tripwire; a failure means "go re-measure".
- **Don't `base64`-decode the `base64`-loader modules** — they are raw
  ELF/Mach-O bytes (findings §5a).
- **Don't match the entry module by name.** It is `/$bunfs/root/cli` on darwin
  and `/$bunfs/root/src/entrypoints/cli.js` on linux. Use `entry_point_id`.
- **Don't install a `claude` launcher on `PATH`.** `build.sh` deliberately
  installs nothing: a launcher named `claude` would shadow a real installation.
- **Don't re-sign anything for the de-rust path** — the signed binary is never
  run. Re-signing only concerns Approach B (`patch_claude.py`).
- **Don't assume ClawGod's extractor is a drop-in** — it only handles
  `napi`-loader modules and misses the `base64` addons on current builds
  (findings §5b).
