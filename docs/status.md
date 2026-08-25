# Project status — what is verified, on what, and what is not

`not-rusty-claude` extracts Claude Code's JavaScript out of its Bun *standalone*
executable and runs it under a stock external **Bun 1.3.14** — the last Zig
release before Bun's Zig→Rust rewrite.

**As of 2026-08-22 the pipeline has been run end to end on this host**: the
extracted, post-processed `cli.original.cjs` from the real Linux binary (Claude
Code **2.1.222**) starts under external Bun 1.3.14 and answers `doctor`,
`mcp list`, `--help` and `--version`.

**As of 2026-08-24 it has been run end to end on a Mac**, reported first-hand
from an Apple Silicon host against that machine's own installed Claude Code
**2.1.239** — the first time any part of this project executed on macOS, and
*not* a measurement made here. What it covered and the three things it pointedly
did not are in [§ macOS execution](#macos-execution). The strongest single
result: the build it produced there reproduced this host's `darwin-arm64`
figures **byte for byte**.

⚠️ **"It runs" is not "it behaves the same as the shipped binary."** Because
`Bun.isStandaloneExecutable` is not defined outside a standalone, every branch in
the CLI that asks takes its non-standalone path: the seccomp sandbox is off,
embedded ripgrep becomes a system `rg`, and install identity reports `unknown`.
Since 2026-08-23 `postprocess.py` rewrites the *one* gate call that guards native
image processing, so a default build can resize a large image; every other gate
stays false, deliberately, and `NRC_NO_IMAGE_SHIM` set to any non-empty value
builds the old artifact. Read [findings.md](./findings.md) **§10** before relying
on this build.

Lead with `doctor` or `mcp list`, never `--version`: it initialises **0** lazy
modules — a hardcoded fast path — and therefore proves nothing about Bun's API
surface ([findings.md](./findings.md) §9).

[findings.md](./findings.md) holds the facts about the binary format and the
transforms; [verification-2026-08-22.md](./verification-2026-08-22.md) is the
evidence record.

---

## Legend

- ✅ **Executed here** — run on this host (Linux x86_64, Debian 12, glibc 2.36).
- 🍎 **Executed on the reporting Mac** — an Apple Silicon host, 2026-08-24,
  against its own installed 2.1.239. **Not** run here, and kept distinct from ✅
  throughout, because which machine a figure came from is the whole discipline.
- 🔎 **Static check here** — real bytes parsed or transformed, or the output
  accepted by Bun's own parser, but **nothing executed**.
- 🖥️ **Needs hardware we do not have** — Windows, an Intel Mac, or a macOS
  facility the 2026-08-24 run did not exercise.
- ⚠️ **Measured difference from the native binary** ([findings.md](./findings.md) §10).
- ⛔ **Deliberately not implemented.**
- 📓 **Prior-session record** — a Mac, 2026-08-21, 2.1.238; not re-checked here.

Nothing in `tools/` or `scripts/` is unrun.

---

## Verification matrix

Versions differ because each artifact is whatever was obtainable: the Linux
binary is the one installed here; `darwin-x64` came from Anthropic's download
endpoint, checksum-verified 2026-08-24; the `darwin-arm64` and Windows binaries
came from npm ([findings.md](./findings.md) §8). The macOS column covers **both**
Mac architectures — every cell held for `darwin-x64` 2.1.241 as well as for
`darwin-arm64` 2.1.239, differing only in offsets and the minified gate name
([findings.md](./findings.md) §6).

| Capability | Linux x64 · ELF · 2.1.222 | macOS · Mach-O · arm64 2.1.239 + x64 2.1.241 | Windows x64 · PE · 2.1.239 |
|---|---|---|---|
| Locate the Bun section | ✅ `.bun` | ✅ `__BUN,__bun` | ⛔ tool refuses PE; layout read by hand |
| Parse payload + module table | ✅ entry id 0 | ✅ entry id 0 | ⛔ / read by hand, entry id 0 |
| Extract `cli.js` + assets (raw bytes) | ✅ | ✅ | ⛔ |
| `postprocess.py` transforms | ✅ every transform applied, no leftovers | ✅ likewise | ⛔ (and the regexes would not match — see below) |
| `scripts/build.sh` end to end | ✅ | ✅ | ⛔ |
| Output accepted by Bun 1.3.14's parser | 🔎 `bun build --no-bundle`, exit 0 | 🔎 exit 0, both architectures | ⛔ |
| **Runs under external Bun** | ✅ `doctor`, `mcp list`, `--help`, `--version`, all exit 0 on **1.3.14 and 1.4.0** | ✅ both darwin builds boot under **Linux** Bun → `2.1.239` / `2.1.241 (Claude Code)`; the x64 build also answers `mcp list`, rc 0. 🍎 the `arm64` build also runs **on** an Apple Silicon Mac: `mcp list` rc 0, the interactive TUI, an authenticated session against a real account with real model inference answering a prompt. `x64` on an Intel Mac: still nobody | ⛔ would also need a Windows Bun |
| Runtime asset (`assets/*`) resolution | ✅ `image-processor.node` loads and works through the rewritten path — and since the scoped shim the CLI does ask for it: a **Read** of a 3000×3000 PNG comes back a JPEG, end to end through the committed mock | 🔎 static only (Mach-O addons cannot dlopen on Linux) · 🖥️ **still open on macOS**: image *input* worked there, but nothing exercised the **resize** path. No darwin addon has been observed to load, anywhere | ⛔ |
| **Behaves the same as the native binary** | ⚠️ **No.** Sandbox off, ripgrep → system `rg`, install identity `unknown` — all measured, all §10. Native image processing **is** reachable in a default build | ⚠️ same by construction (same gate branches; the shim applies here too) | ⛔ |
| Scoped image shim applied | ✅ 4-byte difference from the unshimmed artifact, same length (re-diffed 2026-08-24) | ✅ likewise on **both** architectures. 🍎 it also applied on the Mac — `23 -> 22`, `applied: 1`, same as here. That it **applied** is measured; that it makes the resize path work is **not** | ⛔ |
| Test suite | ✅ passes with both real binaries and a Bun present (re-run 2026-08-24); the counts are [README's per-configuration table](../README.md#the-test-suite-and-its-counts), the one place in this repo that states them | ✅ same run, against the **`arm64`** copy specifically — `NRC_TEST_MACHO` defaults to it and no test was added for `darwin-x64`, whose results above come from running the pipeline by hand. 🍎 the suite also ran on the Mac, passing with only the ELF-only tests skipped | — |

The integration tests behind the ✅ marks only run when the real binaries are
present; without them the suite skips those tests and still passes, which is why
a bare pass count needs the host stated alongside it. The per-host counts live in
**one** place, README's table, and this row points at it rather than repeating
them — a deliberate change made on 2026-08-24, after the same four counts had
drifted apart across four files. A number that lives in one place cannot drift
against itself.

The macOS column carries two kinds of evidence and they must not be merged:

- **Measured here, on Linux ✅** — extraction, post-processing and execution of
  the real Mach-O binaries' JavaScript, against the genuine `darwin-arm64`
  2.1.239 and `darwin-x64` 2.1.241 files. Obtaining them needed no Mac either.
- **Measured on the reporting Mac 🍎** — the same pipeline, on Apple Silicon,
  against its own installed 2.1.239, printing figures **identical** to this
  host's ([findings.md](./findings.md) §2 and §6). "Parsing a container is byte
  arithmetic and needs no Mac" was a reason to believe the darwin column; it is
  now a thing that was checked on both machines and agreed.
- **Still not measured anywhere 🖥️** — the macOS-specific layer underneath:
  whether a Mach-O `.node` addon loads, and every branch that depends on
  `process.platform === "darwin"`.

[README's macOS section](../README.md#macos) is the user-facing version of all
three splits, command by command, each marked with the machine it ran on.

---

## macOS execution

**The macOS run, in one paragraph.** Reported first-hand from an Apple Silicon
host on 2026-08-24 🍎, against that machine's own installed Claude Code 2.1.239
under `~/.local/share/claude/versions/<version>`, whose byte count equals the one
[findings.md](./findings.md)'s opening table records. Bun 1.3.14
`darwin-aarch64`, unzipped into a home directory and not on `PATH`; Python
3.14.7. The suite ran and passed with only the ELF-only tests skipping; the build
reproduced this host's `darwin-arm64` figures exactly; `mcp list` answered rc 0;
the interactive TUI rendered; the session authenticated against a real account
and real model inference answered a prompt; and an image was attached and
described. The full account, including the three defects that run exposed, is
[README's macOS section](../README.md#macos).

**What that run did *not* establish**, as narrowly as it deserves:

- **The native image resize path.** The shim applied there (`23 -> 22`,
  `applied: 1`) and image *input* reached the model — but the native path the
  shim unlocks is what `Read` needs to **resize** an image over 2000×2000, and a
  smaller image never touches it. So the shim's *purpose* is not confirmed on
  macOS. Only that it applied.
- **Whether any darwin `.node` addon loads at all.** The same gap from the other
  side, and still the single most useful thing to send back. Both addon loaders
  swallow their own failures ([findings.md](./findings.md) §10), so a clean
  session is not evidence either.
- **`scripts/ab-equivalence.sh`.** It cannot start on macOS — its egress guard
  reads `/proc` — so the three-way A/B remains Linux-only.
- **Anything on an Intel Mac.** `darwin-x64` has been parsed, transformed and
  booted here; no Intel Mac has run any of it.
- **`doctor` on macOS**, and with it the install-identity and search lines that
  characterise the equivalence gap. Not among the commands reported.

**One cosmetic difference was observed and is recorded rather than explained**:
on exit, the TUI did not clear or restore the terminal. Seen **once**; the cause
was not investigated, and nothing here should be read as a hypothesis about it.

**What this host cannot say anything about**, precisely:

- **The darwin `.node` addons cannot load here** — `require()`-ing one under
  Linux Bun fails with `ERR_DLOPEN_FAILED … invalid ELF header` ✅ (re-checked on
  the `darwin-x64` `image-processor.node`, 2026-08-24).
- **`process.platform` is `linux`**, so every platform-conditional branch takes
  the Linux path: the `ClaudeCode.app` wrapper, the darwin paths, Keychain, TCC,
  none of it is exercised. On the Mac those branches did take their real path
  inside a session that authenticated and ran; what nobody has done is check any
  of them individually.

**Emulation was evaluated here and rejected on observed evidence**, not on
preference:

| Route | Blocker observed here |
|---|---|
| QEMU-KVM / `docker-osx` | no `/dev/kvm`; `/proc/cpuinfo` exposes no `vmx`/`svm` |
| Darling (translation layer, no VM) | needs the `darling-mach` kernel module; `/lib/modules` absent and `modprobe` fails even `--privileged` |
| QEMU TCG (pure software) | hours-long boots on shared vCPUs, and what it would add is exactly the macOS-native layer a TCG boot is too slow to explore |

Apple's licence also restricts macOS virtualization to Apple hardware. What is
left needs a Mac and a deliberate command, not a whole run — on Apple Silicon,
where the build already works, go straight for the two things 2026-08-24 did not
reach:

```
# 1. does a darwin .node addon actually load?  (from a SCRIPT FILE if it prints
#    nothing: on 1.3.14 `bun -e` swallows a failing require() and exits 0)
bun -e 'console.log(Object.keys(require("<out>/extract/assets/image-processor.node")))'

# 2. does the shim do its job? Read an image larger than 2000x2000 and check
#    that it comes back resized rather than "Unable to resize image..."
```

On an **Intel** Mac everything is still open, including the run itself: build
with `scripts/build.sh <native-binary>` and start with the command it prints.

---

## Windows / PE

`extract_bun.py` refuses PE input with a message that points here.

The `win32-x64` build (`claude.exe`, 2.1.239) **is a Bun standalone like the
others**: its `.bun` section table was read here by hand with a section-header
walk, and the file offset, section size, payload size and module count are the
`win32-x64` row of [findings.md](./findings.md) §2. The payload has the same
shape as the ELF and Mach-O ones — u64 length prefix, module graph, 16-byte
trailer, 32-byte offsets struct, 52-byte module records, `entry_point_id = 0` —
and the section is padded to file alignment, which the other two are not. The
entry module also opens with the `// @bun @bytecode @bun-cjs` pragma and a
CommonJS wrapper and ends with the same non-invoked `})`. Nothing about PE is
mysterious.

**Extraction is deliberately unimplemented (⛔), for two reasons:**

1. **Running the result would additionally need a Windows Bun.** Nothing on this
   host, and no non-Windows host, can close the loop. Shipping an extractor whose
   output nobody here can run is exactly the "plausible but never executed"
   posture this project spent nine tasks removing.
2. **It is not a one-line change.** On Windows the virtual-filesystem prefix is
   `B:/~BUN/root/...`, not `/$bunfs/root/...` — observed directly in
   `claude.exe` 2.1.239: **6** such literals in the entry module, and **zero**
   `/$bunfs/` ones. `postprocess.py`'s `BUNFS_LITERAL` regex would rewrite
   **nothing**, which is a fatal condition in `check()` (zero rewrites with a
   populated `assets/`), so a PE attempt fails **loudly** rather than shipping
   silently asset-less output. The leftover detector covers the `B:/~BUN/` prefix
   too, so it fails loudly twice over. The remaining work is real but bounded:
   generalise `BUNFS_LITERAL`, then find a Windows Bun to run the result on.

> Evidence note: the PE facts here and the numbers behind them were measured on
> this host by read-only byte inspection while writing these documents. They are
> **not** part of [verification-2026-08-22.md](./verification-2026-08-22.md),
> whose scope is the Linux end-to-end run. Reproduce with
> `npm pack @anthropic-ai/claude-code-win32-x64` and a PE section-header walk
> ([bun-section-format.md](./bun-section-format.md) §1c).

---

## Remaining work

### 1. Re-measure when Claude Code updates

The counts in this repo are facts about **specific versions**, not constants.

- **Verify:** re-run `scripts/build.sh <native-binary>` and
  `python3 -m pytest tests/ -q`. The integration tests hardcode the measured
  module, asset and transform counts — the figures findings §4 and §6 tabulate,
  and stated in those two places only.
- **When they fail:** that failure is the **feature** — the early warning that
  the binary changed. Re-measure, update `tests/test_integration.py` and findings
  §4/§6, and re-run the parse and run checks. **Do not loosen the assertions.**
- **If extraction itself breaks:** print every module's `(loader, name, size)`
  and compare against [bun-section-format.md](./bun-section-format.md). Never
  match the entry module by name — use `entry_point_id`.

### 2. Finish the macOS picture 🖥️ — most of it is done

Three things are left, and they are specific: **(a)** does a darwin `.node`
addon load, **(b)** does the shim's branch actually work — `Read` an image over
2000×2000 on that Mac — and **(c)** an Intel Mac at all, with
`bun-darwin-x64.zip` and `P=darwin-x64`, running the whole thing and then (a)
and (b). Keep the two splits apart: the arm64/x64 split is about which *binaries*
have been parsed here; the Linux/macOS split is about which have been *run on a
Mac*, and only `arm64` has.

If that Mac already has Claude Code, there is nothing to download:
`scripts/build.sh` with no argument probes
`${XDG_DATA_HOME:-$HOME/.local/share}/claude/versions/*` newest-first and takes
the first entry of at least 1 MiB, naming anything it skipped, then falls back to
`command -v claude`. The size check exists *because* of the Mac run — an
interrupted auto-update there had left exactly such a 0-byte stub, sorting
newest. Otherwise, findings §8's download endpoint.

**Expected failure to plan for:** `Expected CommonJS module to have a function
wrapper` is **ambiguous** — it means the module *shape* was rejected, which
covers a too-old Bun, a pragma/IIFE problem, *and* this project's own transform.
Rebuild in the pragma-preserving shape to tell them apart (findings §9). Only a
`TypeError` naming a missing `Bun.*` property means findings §9's risk
materialised.

### 3. Close the equivalence gap ⚠️

- **Settled** ✅: `require("<extract>/assets/image-processor.node")` under Bun
  1.3.14 loads and works — it reads a 3000×3000 PNG's metadata and resizes it to
  a valid JPEG. The rewritten `require('path').join(__dirname,'assets',…)` shape
  is correct. The three `file`-loader assets also read back through the same
  shape via `fs/promises.readFile`: 208,522 / 955,678 / 3,312,874 chars, matching
  their extracted sizes.
- **Fixed, for the image half** ✅ (findings §10, *What shipped*): the scoped
  shim, selecting the image branch's own gate call by shape. A **global** flip is
  not the fix and never was — it breaks search silently, which is now a *case* in
  `scripts/ab-equivalence.sh` rather than a paragraph.
- **Still open, deliberately:** the seccomp sandbox, embedded ripgrep and install
  identity stay on their non-standalone branches; findings §10 tabulates why each
  refusal is a refusal. A shimmed build still reports `Running: unknown` and
  `Search: OK (/usr/bin/rg)`, which is the positive evidence that the rewrite did
  not spread.
- **Also settled, and it matters for every "exit 0" claim in this repo:** both
  addon loaders swallow failure, so a missing or broken asset degrades silently.
  **Exit 0 is not evidence that asset wiring works.**
- **Still genuinely unverified:** whether `mermaid.min.js`,
  `hljsBundle.generated.min.js` and `chart.umd.min.js` are read on their real
  feature paths. They resolve; no command here has exercised the feature.

### 4. Update survival & version pinning

Run the extracted build with `DISABLE_AUTOUPDATER=1`, and never `claude update`
against it: install-method detection reports `unknown` and the updater takes the
npm/bun global-install route, which with network installs a *different,
npm-based* Claude Code on the machine and never updates these artifacts. See
[runbook.md](./runbook.md) § Surviving Claude updates. Re-run `build.sh` against
a new native binary to move forward; a failed rebuild keeps the previous
`build/extract/` intact.

**The project's kill switch (findings §9):** if a future Claude build is compiled
against a canary Bun newer than 1.3.14, its `cli.js` will not run on Zig at all.
As of 2.1.222 this has **not** happened — a measurement, not a guarantee. The
floor is softer than it looks: 2.1.222 runs on **1.3.13** in the
pragma-preserving build shape. Keep the last working `build/extract/`, pin that
Claude version, and if it ever breaks record the first version where it broke in
findings §9.

---

## Known unknowns

- **Real model traffic — answered once, on the other machine.** Every
  agentic-loop result recorded *here* was driven by a **loopback mock**, and no
  request has ever gone to Anthropic from this host's build. The Apple Silicon
  run did authenticate against a real account and had real model inference answer
  a prompt 🍎 — one session on one machine, reported rather than measured here.
  Nothing about latency, rate limits, long sessions or error handling against the
  real API has been characterised anywhere.
- **Minified call shapes drift.** `postprocess.py`'s regexes target minified
  output that changes every release. They are measured against real binaries, not
  contracts.
- **The remaining gate branches were read, not exercised.** Of the gate call
  sites findings §6 counts, the shim touches exactly one; four branches have an
  A/B measurement behind them, all reproducible with `scripts/ab-equivalence.sh`.
- **`CLAUDE_CODE_EXECPATH`.** The CLI **never reads** it and unconditionally
  *writes* it as `process.execPath` — now the bun binary — into every spawned
  shell's environment, where the generated `find`/`grep` shell functions fall
  back to it. Read from source, not observed live
  ([runbook.md](./runbook.md) § Shell integrations, findings §6).
- **Universal vs thin addons.** Some darwin `.node` files are universal
  (x86_64 + arm64), others thin arm64; the linux ones are ELF. Matters only if
  you mix architectures.
- **Why the TUI does not restore the terminal on macOS.** Observed once 🍎,
  cosmetic, cause not investigated, not reproducible here.
- **The `Makefile` on macOS.** `tests/test_makefile.py` enforces its GNU Make
  3.81 / BSD-userland constraints as static checks **on Linux**. No target has
  been run on a Mac: the 2026-08-24 run predates that file landing in the tree.
- **Canary API drift** is outside our control and remains the single biggest risk
  to the project's goal.

---

## What NOT to do

- **Don't loosen the integration tests' hardcoded counts** to make a new Claude
  version pass. They are a tripwire; a failure means "go re-measure".
- **Don't decode any module's stored content.** Stored content is *always* raw
  bytes, whatever the loader id says — including a genuine `base64`-loader
  module. The `.node` addons are `napi`-loader (byte 10) and are raw ELF/Mach-O
  (findings §5a).
- **Don't transcribe the loader enum from another extractor.** Read it from Bun's
  `src/bundler/options.zig` at the matching tag. Doing otherwise is how
  `jsonc=7` went missing here and every id from 7 up shifted (findings §5a).
- **Don't lead with `--version`** when checking a build. Use `doctor` or
  `mcp list` (findings §9).
- **Don't read `exit 0` as "the assets resolve".** Both addon loaders swallow
  failure (findings §10).
- **Don't match the entry module by name.** Use `entry_point_id` (findings §4).
- **Don't install a `claude` launcher on `PATH`.** `build.sh` deliberately
  installs nothing: a launcher named `claude` would shadow a real installation.
- **Don't assume ClawGod's extractor is a drop-in** — but not for the reason this
  list used to give. It extracts the native addons **correctly**; what it drops
  is the `file`-loader assets, and its rewrite covers only `require("….node")`
  (findings §5b/§7).
