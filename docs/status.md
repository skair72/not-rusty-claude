# Project status — what is verified, on what, and what is not

`not-rusty-claude` extracts Claude Code's JavaScript out of its Bun *standalone*
executable and runs it under a stock external **Bun 1.3.14** — the last Zig
release before Bun's Zig→Rust rewrite.

**As of 2026-08-22 the pipeline has been run end to end on this host.** The
extracted, post-processed `cli.original.cjs` from the real Linux binary
(Claude Code **2.1.222**) starts under external Bun 1.3.14 and answers `doctor`,
`mcp list`, `--help` and `--version`. Lead with `doctor` or `mcp list`: on this
build they initialise ~2760 of the bundle's 6748 lazy modules, whereas
`--version` initialises **0** and therefore proves nothing about Bun's API
surface. (Both numbers are properties of linux-x64 2.1.222, measured; only the
**0** is structural — it is a hardcoded fast path)
([findings.md](./findings.md) §10). That is the first empirical answer to the
risk in §10, and it is a *positive* one **for that version, on that platform,
on those code paths** — nothing wider.

⚠️ **"It runs" is not "it behaves the same as the shipped binary."** Because
`Bun.isStandaloneExecutable` is not defined outside a standalone, every branch
in the CLI that asks takes its non-standalone path — 21 call sites on
`linux-x64` 2.1.222 and 23 on `darwin-arm64` 2.1.239 (the number is a fact
about one binary, re-measured 2026-08-23). The seccomp sandbox is off, embedded
ripgrep becomes a system `rg`, and install identity reports `unknown`.

**One of those consequences is now fixed.** Since 2026-08-23 `postprocess.py`
rewrites the *one* gate call that guards native image processing, so a default
build can resize a large image; every other gate stays false, deliberately, and
`NRC_NO_IMAGE_SHIM` set to any non-empty value builds the old artifact. Read
[findings.md](./findings.md) **§11** — in particular *What shipped: the scoped
shim* — before relying on this build.

The single evidence record is
[**verification-2026-08-22.md**](./verification-2026-08-22.md): every command
and its pasted output. This file summarises it; that file proves it. Read
[findings.md](./findings.md) for the facts about the binary format itself.

---

## Legend

Statuses distinguish *how* something is known, not how likely it is:

- ✅ **Executed here** — actually run on this host (Linux x86_64, Debian 12,
  glibc 2.36) on 2026-08-22, with command and output pasted in
  [verification-2026-08-22.md](./verification-2026-08-22.md) — in its original
  body, or in the **2026-08-22 addendum** appended after the review fleet.
- 🔎 **Static check here** — real bytes of a real binary were parsed or
  transformed on this host, or the output was accepted by Bun's own
  parser/transpiler — but **nothing was executed**.
- 🖥️ **Needs hardware we do not have** — the remaining step requires Apple
  Silicon (or Windows). Evaluated and rejected here; see
  [§ macOS execution](#macos-execution-what-actually-needs-a-mac).
- ⚠️ **Measured difference from the native binary** — it runs, but it does not
  behave the same. See [findings.md](./findings.md) §11.
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
| **Runs under external Bun** | ✅ `doctor`, `mcp list`, `--help`, `--version` all exit 0 on **1.3.14 and 1.4.0** (Steps 5, 5b + addendum) | ✅ the darwin JS boots under **Linux** Bun → `2.1.239 (Claude Code)`; macOS-*specific* behaviour still needs a Mac | ⛔ would also need a Windows Bun |
| Runtime asset (`assets/*`) resolution | ✅ `image-processor.node` loads and works through the rewritten path — and since the scoped shim (2026-08-23) the CLI does ask for it: a **Read** of a 3000×3000 PNG comes back a JPEG, measured end to end through the committed mock ([findings.md](./findings.md) §11) | 🔎 static only (Mach-O addons cannot dlopen on Linux) | ⛔ |
| **Behaves the same as the native binary** | ⚠️ **No.** Seccomp sandbox off, embedded ripgrep → system `rg`, install identity `unknown` — all measured, all §11. Native image processing **is** reachable in a default build since 2026-08-23 (scoped shim, §11) | ⚠️ same by construction (same gate branches; the shim applies here too — `AE`, 23 → 22 call sites) | ⛔ |
| Scoped image shim applied | ✅ `CE`, call sites 21 → 20, artifact 4 bytes from the unshimmed one (2026-08-23) | ✅ `AE`, 23 → 22, likewise 4 bytes — built here, **not** executed on a Mac | ⛔ |
| Test suite | ✅ **200 passed** with both real binaries and a Bun present (187 passed / 13 skipped on a host with neither binary nor Bun — see README's table) | ✅ same run | — |
| Approach B: byte-patch + re-sign (`tools/patch_claude.py`) | n/a (macOS-only concern) | 📓 verified 2026-08-21 on 2.1.238; not re-checked here | n/a |

Step numbers refer to sections of
[verification-2026-08-22.md](./verification-2026-08-22.md).

The integration tests behind the ✅ marks in the "Test suite" row only run when
the real binaries are present: `NRC_TEST_ELF` (default `/usr/bin/claude`) and
`NRC_TEST_MACHO` (default `/tmp/ccmac/package/claude-darwin-arm64.bin`, with
the older `/tmp/ccmac/package/claude` still accepted), plus `BUN_BIN` (default
`~/.bun-1.3.14/bun`, then `bun` on `PATH`) for the tests that actually boot the
artifact. Without them the suite skips those tests and still passes, which is
why a bare "200 passed" needs the host stated alongside it. All four counts
were re-measured on this host on 2026-08-23, after the last test landed, and
they still sum to the 200 tests `--collect-only` reports; README's table says
how each row was forced, including the `PYTHONPATH` the no-Bun row needs on a
host whose pytest is a `--user` install.

The macOS column is worth reading twice: **extraction, post-processing *and*
execution of the real Mach-O binary's JavaScript are not a projection — they
were done here**, on this Linux host, against the genuine 325 MB `darwin-arm64`
binary. Parsing a container is byte arithmetic and needs no Mac, and neither
does running the JS it yields. What needs a Mac is the macOS-specific layer:
the Mach-O `.node` addons, and every branch that depends on
`process.platform === "darwin"`.

---

## macOS execution: what actually needs a Mac

**Corrected 2026-08-22.** This section used to say the darwin artifact "has
never been executed" and that "running it needs Apple hardware". Both are
wrong as stated, and the truth is *better* evidence than the claim they
replaced.

The darwin artifact (`cli.original.cjs`, 28,244,063 bytes) was built and checked
here, Bun 1.3.14's own parser accepts it (`bun build --no-bundle --target=bun`,
exit 0) — **and it has now been executed, on this Linux host** ✅:

```
$ DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=$(mktemp -d) \
    ~/.bun-1.3.14/bun <darwin-build>/extract/cli.original.cjs --version
2.1.239 (Claude Code)          rc=0
```

The JavaScript boots and runs. What still needs a Mac is verifying
**macOS-specific behaviour**, precisely:

- **The darwin `.node` addons cannot load here.** They are Mach-O —
  `cffaedfe` (thin arm64) or `cafebabe` (universal) — and `require()`-ing one
  under Linux Bun fails with `ERR_DLOPEN_FAILED … invalid ELF header` ✅. The
  whole darwin native layer is unexercised.
- **`process.platform` is `linux`.** Every platform-conditional branch takes
  the Linux path, so nothing that depends on running *on* macOS is exercised —
  the `ClaudeCode.app` wrapper, the darwin paths, Keychain, TCC, none of it.
- Everything in [findings.md](./findings.md) §11 applies on macOS too, and is
  likewise unverified there.

Emulation was evaluated on this host and rejected on observed evidence, not on
preference:

| Route | Blocker observed here |
|---|---|
| QEMU-KVM / `docker-osx` | no `/dev/kvm`; `/proc/cpuinfo` exposes no `vmx`/`svm` |
| Darling (translation layer, no VM) | needs the `darling-mach` kernel module; `/lib/modules` absent and `modprobe` fails even `--privileged` |
| QEMU TCG (pure software) | hours-long boots on shared vCPUs, and it would exercise the darwin-**x64** build, not arm64 |

Apple's licence also restricts macOS virtualization to Apple hardware. So the
*remaining* part of this gap closes only on a real Mac: run
`scripts/build.sh <native-binary>` there, then the command it prints —
`DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" bun
.../cli.original.cjs mcp list` (not `--version` — see
[findings.md](./findings.md) §10) — confirm an addon actually loads, and record
the result.

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
   regex would therefore rewrite **nothing**. That outcome — zero rewrites with
   a populated `assets/` — is a fatal condition in `check()`, so it fails
   **loudly** rather than shipping silently asset-less output; that guard is
   what `tools/postprocess.py`'s error message points at this section for.
   (This paragraph used to give "silently asset-less rather than loudly broken"
   as a *reason not to ship PE*; that stopped being true when the guard landed
   in `59d9a98`.) The leftover detector now covers the `B:/~BUN/` prefix too,
   and a surviving reference is fatal in its own right, so a PE attempt fails
   loudly twice over. The remaining work is real but bounded: generalise
   `BUNFS_LITERAL` itself, then find a Windows Bun to run the result on.

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

### 2. Verify macOS-*specific* behaviour on a Mac 🖥️

The darwin JavaScript already boots here under Linux Bun. What is left is the
part that genuinely needs the hardware. See
[§ macOS execution](#macos-execution-what-actually-needs-a-mac).

- **Verify:** on Apple Silicon with Bun 1.3.14, run
  `DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=$(mktemp -d) bun
  <out>/extract/cli.original.cjs mcp list` (not `--version` — it initialises 0
  lazy modules, findings §10), then load a darwin `.node` addon:
  `bun -e 'console.log(Object.keys(require("<out>/extract/assets/image-processor.node")))'`.
  That is the assertion Linux cannot make.
- **Expected failure to plan for:** `Expected CommonJS module to have a function
  wrapper` is **ambiguous** — it means the module *shape* was rejected, which
  covers a too-old Bun, a pragma/IIFE problem, *and* this project's own
  transform. Rebuild in the pragma-preserving shape to tell them apart
  (findings §10). Only a `TypeError` naming a missing `Bun.*` property means
  findings §10's risk materialised.

### 3. Close the equivalence gap ⚠️

**Reframed 2026-08-22. Image half closed 2026-08-23.** This item used to read
"verify runtime asset resolution", on the assumption that nothing was known
about whether the rewritten paths work. That much is now settled in the *good*
direction, a worse problem was found underneath it, and one part of that
problem has since been fixed.

- **Settled** ✅: `require("<extract>/assets/image-processor.node")` under Bun
  1.3.14 loads and works — it reads a 3000×3000 PNG's metadata and resizes it
  to a valid JPEG. The rewritten `require('path').join(__dirname,'assets',…)`
  shape is correct. The three `file`-loader assets also read back through the
  same shape via `fs/promises.readFile`: 208,522 / 955,678 / 3,312,874 chars.
- **The real problem** (findings §11): the CLI *never asked* for the native
  image processor, because that call site is gated on
  `Bun.isStandaloneExecutable`, which is undefined outside a standalone.
  Measured: as shipped, reading a 3000×3000 PNG fails with *"Unable to resize
  image…"*; with the gate true, the same artifact returns a correct JPEG. The
  seccomp sandbox is off, embedded ripgrep degrades to a system `rg`, and
  install identity is `unknown`, for the same reason.
- **Also settled, and it matters for every "exit 0" claim in this repo:** both
  addon loaders swallow failure (`try{…}catch{ …=null }`), so a missing or
  broken asset degrades silently. **Exit 0 is not evidence that asset wiring
  works.**
- **Fixed, for the image half** ✅ (2026-08-23, findings §11 *What shipped*):
  `postprocess.py` rewrites the image branch's own gate call — and only that
  one — to `true`, selecting it by the shape `if(<gate>())try{` in the 400
  bytes before the anchor string `Native image processor not available`.
  Measured: 21 → 20 gate call sites on linux-x64 2.1.222, 23 → 22 on
  darwin-arm64 2.1.239, a 4-byte difference from the unshimmed artifact on
  both. A **global** flip is still not the fix and never was: it breaks search,
  because "embedded ripgrep" then means re-exec `process.execPath` (bun) with
  argv0 `rg`, and a `Grep` for a string that exists returns `No matches found`
  — silently wrong, not an error. That is now a *case* in
  `scripts/ab-equivalence.sh`, not a paragraph: the script builds the
  globally-flipped artifact as a third side and asserts the breakage.
- **Still open, deliberately:** the seccomp sandbox, embedded ripgrep and
  install identity stay on their non-standalone branches. findings §11
  tabulates why each refusal is a refusal. Measured 2026-08-23: a shimmed build
  still reports `Running: unknown` and `Search: OK (/usr/bin/rg)` from
  `doctor`, which is the positive evidence that the rewrite did not spread.
- **Still genuinely unverified:** whether `mermaid.min.js`,
  `hljsBundle.generated.min.js` and `chart.umd.min.js` are read on their real
  feature paths (they are read via `fs/promises.readFile` of the rewritten
  literal, which resolves — but no command here has exercised the feature).

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
  **not** happened; that is a measurement, not a guarantee. Note the floor is
  softer than it looks: 2.1.222 runs on **1.3.13** in the pragma-preserving
  build shape, so "Bun ≥ 1.3.14" is a property of this project's transform, not
  of Claude.
- Keep the last working `build/extract/` and pin that Claude version. If it ever
  breaks, record the first version where it broke in findings §10.

---

## Known unknowns

- **Real model traffic.** Every agentic-loop result recorded here was driven by
  a **loopback mock** of the Messages API. Streaming, multi-turn tool use, the
  Bash tool spawning a subprocess, the Read tool returning an image and the Ink
  TUI under a pty all work on Bun 1.3.14 ✅ — but no request has ever gone to
  Anthropic from this build, and no real account has been touched.
- **Minified call shapes drift.** `postprocess.py`'s regexes target minified
  output that changes every release. They are measured against two real
  binaries, not contracts.
- **The equivalence gap is characterised, and one item of it is closed.**
  findings §11 lists what differs and why. Four branches have an A/B
  measurement behind them, all of them now reproducible in one command
  (`scripts/ab-equivalence.sh`): the image path (closed by the scoped shim),
  `Grep` (the case that shows why a global flip is *not* the fix), `doctor`'s
  install-identity and search lines, and a `Bash` control that must come out
  the same on all three sides. The remaining gate branches were read, not
  exercised — 21 call sites on linux-x64 2.1.222, of which the shim touches
  one.
- **`CLAUDE_CODE_EXECPATH`.** Measured: the CLI **never reads** it (0
  occurrences of `process.env.CLAUDE_CODE_EXECPATH`) and unconditionally
  *writes* it as `process.execPath` — now the bun binary — into every spawned
  shell's environment. The generated `find`/`grep` shell functions fall back to
  it, so they get shadowed onto bun. Read from source, not observed live. See
  [runbook.md](./runbook.md) § Shell integrations and findings §6.
- **Universal vs thin addons.** Some darwin `.node` files are universal
  (x86_64 + arm64), others thin arm64; the linux ones are ELF. Matters only if
  you mix architectures.
- **Canary API drift** is outside our control and remains the single biggest
  risk to the project's goal.

---

## What NOT to do

- **Don't loosen the integration tests' hardcoded counts** to make a new Claude
  version pass. They are a tripwire; a failure means "go re-measure".
- **Don't decode any module's stored content.** Stored content is *always* raw
  bytes, whatever the loader id says — that includes a genuine `base64`-loader
  module. The `.node` addons are `napi`-loader (byte 10) and are raw ELF/Mach-O
  (findings §5a).
- **Don't transcribe the loader enum from another extractor.** Read it from
  Bun's `src/bundler/options.zig` at the matching tag. Doing otherwise is how
  `jsonc=7` went missing here and every id from 7 up shifted (findings §5a).
- **Don't lead with `--version`** when checking a build. It initialises **0**
  lazy modules — a hardcoded fast path — out of 6748 on the build measured
  here. Use `doctor` or `mcp list` (findings §10).
- **Don't read `exit 0` as "the assets resolve".** Both addon loaders swallow
  failure (findings §11).
- **Don't match the entry module by name.** It is `/$bunfs/root/cli` on darwin
  and `/$bunfs/root/src/entrypoints/cli.js` on linux. Use `entry_point_id`.
- **Don't install a `claude` launcher on `PATH`.** `build.sh` deliberately
  installs nothing: a launcher named `claude` would shadow a real installation.
- **Don't re-sign anything for the de-rust path** — the signed binary is never
  run. Re-signing only concerns Approach B (`patch_claude.py`).
- **Don't assume ClawGod's extractor is a drop-in** — but not for the reason
  this list used to give. It extracts the native addons **correctly**; what it
  drops is the `file`-loader assets, and its rewrite covers only
  `require("….node")` (findings §5b/§8).
