# Project status — what is verified, on what, and what is not

`not-rusty-claude` extracts Claude Code's JavaScript out of its Bun *standalone*
executable and runs it under a stock external **Bun 1.3.14** — the last Zig
release before Bun's Zig→Rust rewrite.

**As of 2026-08-22 the pipeline has been run end to end on this host.** The
extracted, post-processed `cli.original.cjs` from the real Linux binary
(Claude Code **2.1.222**) starts under external Bun 1.3.14 and answers `doctor`,
`mcp list`, `--help` and `--version`.

**And as of 2026-08-24 it has been run end to end on a Mac**, reported
first-hand from an Apple Silicon host against that machine's own installed
Claude Code **2.1.239**. That is the first time any part of this project has
executed on macOS, and it is *not* a measurement made here: read every figure
attributed to it as measured there. What it covered — the suite, the build, the
smoke test, the interactive TUI, an authenticated session and image *input* —
and the three things it pointedly did not — `codesign`, the A/B harness, and the
native image **resize** path — are set out in
[§ macOS execution](#macos-execution-what-a-mac-has-now-shown-and-what-it-has-not).
The single strongest result is in the matrix below: the build it produced there
reproduced this host's `darwin-arm64` figures **byte for byte**. Lead with `doctor` or `mcp list`: on this
build they initialise thousands of the bundle's lazy modules, whereas
`--version` initialises **0** and therefore proves nothing about Bun's API
surface. The per-command counts are properties of linux-x64 2.1.222 and are
tabulated in [findings.md](./findings.md) §10 — the one place that states them,
apart from the pasted transcript they were read off in
[verification-2026-08-22.md](./verification-2026-08-22.md). Only the **0** is
structural: it is a hardcoded fast path. That is the first empirical answer to the
risk in §10, and it is a *positive* one **for that version, on that platform,
on those code paths** — nothing wider.

⚠️ **"It runs" is not "it behaves the same as the shipped binary."** Because
`Bun.isStandaloneExecutable` is not defined outside a standalone, every branch
in the CLI that asks takes its non-standalone path. How many sites that is, per
binary, is one row of [findings.md](./findings.md) §6's measured-counts table —
a fact about one binary of one version, and stated there rather than here so
that the two cannot drift apart. The seccomp sandbox is off, embedded
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
- 🍎 **Executed on the reporting Mac** — run on an Apple Silicon host on
  2026-08-24 and reported first-hand, against that machine's own installed
  Claude Code 2.1.239. **Not** run here, and not covered by this host's
  verification record. Distinguished from ✅ throughout, because which machine a
  figure came from is the whole discipline of this document.
- 🔎 **Static check here** — real bytes of a real binary were parsed or
  transformed on this host, or the output was accepted by Bun's own
  parser/transpiler — but **nothing was executed**.
- 🖥️ **Needs hardware we do not have** — the remaining step requires hardware
  this host cannot reach (Windows, or an Intel Mac), or a macOS facility the
  2026-08-24 run did not exercise. Emulation was evaluated and rejected here;
  see [§ macOS execution](#macos-execution-what-a-mac-has-now-shown-and-what-it-has-not).
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
installed on this host; the `darwin-x64` binary came from Anthropic's own
download endpoint, checksum-verified on 2026-08-24; the `darwin-arm64` and
Windows binaries were obtained earlier from npm (all three routes, and which
one is the documented one, are [findings.md](./findings.md) §9).

The macOS column now covers **both** Mac architectures. `darwin-x64` 2.1.241
was put through the whole pipeline here on 2026-08-24 — first time this repo
has measured a Mach-O that was not arm64 — and every cell below held for it as
well as for `darwin-arm64` 2.1.239. Where the two differ at all, it is in
offsets and the minified gate name, both of which are
[findings.md](./findings.md) §6's table.

| Capability | Linux x64 · ELF · 2.1.222 | macOS · Mach-O · arm64 2.1.239 + x64 2.1.241 | Windows x64 · PE · 2.1.239 |
|---|---|---|---|
| Locate the Bun section | ✅ `.bun` located (Step 2) | ✅ `__BUN,__bun` located (Step 3b; re-done for x64 2026-08-24) | ⛔ tool refuses PE; layout read by hand (see below) |
| Parse payload + module table | ✅ parsed, entry id 0 (Step 2) | ✅ parsed, entry id 0 (Step 3b) | ⛔ / read by hand, entry id 0 |
| Extract `cli.js` + assets (raw bytes) | ✅ (Step 2) | ✅ (Step 3b) | ⛔ |
| `postprocess.py` transforms | ✅ every transform applied, no leftovers (Step 2) | ✅ likewise (Step 3b) | ⛔ (and the regexes would not match — see below) |
| `scripts/build.sh` end to end | ✅ (Step 2) | ✅ (Step 3b) | ⛔ |
| Output accepted by Bun 1.3.14's parser | 🔎 `bun build --no-bundle`, exit 0 (Step 3) | 🔎 `bun build --no-bundle`, exit 0 (Step 3b; and for x64, 2026-08-24) | ⛔ |
| **Runs under external Bun** | ✅ `doctor`, `mcp list`, `--help`, `--version` all exit 0 on **1.3.14 and 1.4.0** (Steps 5, 5b + addendum) | ✅ both darwin builds boot under **Linux** Bun → `2.1.239` / `2.1.241 (Claude Code)`; the x64 build also answers `mcp list`, rc 0 (2026-08-24). 🍎 the `arm64` build also runs **on** an Apple Silicon Mac: `mcp list` rc 0, the interactive TUI, and an authenticated session against a real account with real model inference answering a prompt. `x64` on an Intel Mac: still nobody | ⛔ would also need a Windows Bun |
| Runtime asset (`assets/*`) resolution | ✅ `image-processor.node` loads and works through the rewritten path — and since the scoped shim (2026-08-23) the CLI does ask for it: a **Read** of a 3000×3000 PNG comes back a JPEG, measured end to end through the committed mock ([findings.md](./findings.md) §11) | 🔎 static only (Mach-O addons cannot dlopen on Linux) · 🖥️ **still open on macOS.** The Mac run does not close it: image *input* worked there, but nothing exercised the **resize** path, which is the one that needs `image-processor.node`. No darwin addon has been observed to load, anywhere | ⛔ |
| **Behaves the same as the native binary** | ⚠️ **No.** Seccomp sandbox off, embedded ripgrep → system `rg`, install identity `unknown` — all measured, all §11. Native image processing **is** reachable in a default build since 2026-08-23 (scoped shim, §11) | ⚠️ same by construction (same gate branches; the shim applies here too) | ⛔ |
| Scoped image shim applied | ✅ applied to the one image gate; artifact differs from the unshimmed one by 4 bytes at the same length (re-built and re-diffed 2026-08-24) | ✅ applied likewise on **both** architectures, 4 bytes at the same length each; the transcripts are in [README's macOS section](../README.md#macos). 🍎 it also applied on the Mac, against that machine's own 2.1.239 — `23 -> 22`, `applied: 1`, same as here. That the shim **applied** there is measured; that it makes the resize path work is **not** — see the row above | ⛔ |
| Test suite | ✅ passes with both real binaries and a Bun present (re-run 2026-08-24). The counts — that run's and what a host without them prints instead — are [README's per-configuration table](../README.md#the-test-suite-and-its-counts), the one place in this repo that states them | ✅ same run — but against the **`arm64`** copy specifically: `NRC_TEST_MACHO` defaults to it and no test was added for `darwin-x64`, whose results above come from running the pipeline by hand. 🍎 the suite also ran on the Mac, passing with only the ELF-only tests skipped; its figures and how they reconcile with this host's are in that same README table | — |
| Approach B: byte-patch + re-sign (`tools/patch_claude.py`) | n/a (macOS-only concern) | 📓 signing verified 2026-08-21 on 2.1.238, not re-checked here; the byte-patching half is now covered by `tests/test_patch_claude.py`, which runs on **Linux** through `--no-sign`/`--dry-run`. 🖥️ the 2026-08-24 Mac run did **not** touch this tool: `codesign` has still never been invoked by it | n/a |

Step numbers refer to sections of
[verification-2026-08-22.md](./verification-2026-08-22.md).

The integration tests behind the ✅ marks in the "Test suite" row only run when
the real binaries are present: `NRC_TEST_ELF` (default `/usr/bin/claude`) and
`NRC_TEST_MACHO` (default `/tmp/ccmac/package/claude-darwin-arm64.bin`, with
the older `/tmp/ccmac/package/claude` still accepted), plus `BUN_BIN` (default
`~/.bun-1.3.14/bun`, then `bun` on `PATH`) for the tests that actually boot the
artifact. Without them the suite skips those tests and still passes, which is
why a bare pass count needs the host stated alongside it.

The per-host counts live in **one** place — README's table — and this row
points at it rather than repeating them. That is a deliberate change made on
2026-08-24: the same four counts used to appear in this file, in README, in
[runbook.md](./runbook.md) and in [findings.md](./findings.md)'s appendix; the
appendix's copy sat ten too low from the day it was written until three
reviewers converged on it, and a first attempt at the fix left the headline
figure standing in three places while announcing that it stood in one. A number
that lives in one place cannot drift against itself — and these move: the suite
grew again on 2026-08-24, so every count written down before that date is now
wrong. README states each row and how it was forced, including the `PYTHONPATH`
the no-Bun row needs on a host whose pytest is a `--user` install.

The macOS column is worth reading twice, because it now carries two kinds of
evidence and they must not be merged.

**Measured here, on Linux ✅.** Extraction, post-processing and execution of the
real Mach-O binaries' JavaScript were done on this host, against the genuine
325 MB `darwin-arm64` 2.1.239 binary and the genuine 334 MB `darwin-x64` 2.1.241
one. Nor does *obtaining* a darwin binary need a Mac: `darwin-x64` 2.1.241 and
`darwin-arm64` 2.1.241 were both fetched over HTTPS from Anthropic's download
endpoint and checksum-verified here, and the 2.1.239 `arm64` copy the
measurements use arrived by `npm pack` and is byte-identical to what that
endpoint serves for the same version ([findings.md](./findings.md) §9).

**Measured on the reporting Mac 🍎.** On 2026-08-24 that same pipeline ran on an
Apple Silicon host against its own installed 2.1.239, and the numbers it printed
— section offset, section size, payload size, module count, entry id, asset
count, gate name, `23 -> 22` call sites, and both artifact sizes — are
**identical** to the ones this host measured for `darwin-arm64` 2.1.239
([findings.md](./findings.md) §3 and §6). That is the point of the whole
cross-platform argument arriving as data: "parsing a container is byte
arithmetic and needs no Mac" was a reason to believe the darwin column; it is
now a thing that was checked on both machines and agreed.

**Still not measured anywhere 🖥️.** The macOS-specific layer underneath: whether
a Mach-O `.node` addon actually loads, and every branch that depends on
`process.platform === "darwin"` behaving as it does on a Mac.
[README's macOS section](../README.md#macos) is the user-facing version of all
three splits — the commands, each marked with the machine it ran on.

---

## macOS execution: what a Mac has now shown, and what it has not

**Rewritten 2026-08-24, around a real macOS run.** This section has been wrong
in both directions before. It once said the darwin artifact "has never been
executed" and that "running it needs Apple hardware" — corrected on 2026-08-22,
because the darwin JavaScript does execute here, under Linux Bun. It then said
that nothing had ever run *on* macOS, which was true until 2026-08-24 and is not
any more. Both corrections went the same way: the measurement was better than
the claim it replaced.

**The macOS run, in one paragraph.** Reported first-hand from an Apple Silicon
host on 2026-08-24 🍎, against that machine's own installed Claude Code 2.1.239
under `~/.local/share/claude/versions/<version>`, whose byte count equals the
one [findings.md](./findings.md) §1 records for `darwin-arm64` 2.1.239 — stated
there, not here. Bun 1.3.14
`darwin-aarch64`, unzipped into a home directory and not on `PATH`; Python
3.14.7. The suite ran and passed with only the ELF-only tests skipping; the
build reproduced this host's `darwin-arm64` figures exactly; `mcp list` answered
rc 0; the interactive TUI rendered; the session authenticated against a real
account and real model inference answered a prompt; and an image was attached
and described. The full account, including the three defects that run exposed,
is [README's macOS section](../README.md#macos).

**What that run did *not* establish**, stated as narrowly as it deserves:

- **The native image resize path.** The scoped shim applied there (`23 -> 22`,
  `applied: 1`) and image *input* reached the model — but the native path the
  shim unlocks is what the `Read` tool needs to **resize** an image over
  2000×2000, and an image small enough not to need resizing never touches it.
  So the shim's *purpose* is not confirmed on macOS. Only that it applied.
- **Whether any darwin `.node` addon loads at all.** Same gap, seen from the
  other side, and still the single most useful thing to send back. Both addon
  loaders swallow their own failures ([findings.md](./findings.md) §11), so a
  clean session is not evidence either.
- **`tools/patch_claude.py`'s `codesign` half.** Not invoked by that run.
  Unchanged: it has never met a real `codesign`.
- **`scripts/ab-equivalence.sh`.** It cannot start on macOS — its egress guard
  reads `/proc` — so the three-way A/B remains Linux-only and that run says
  nothing about it.
- **Anything on an Intel Mac.** `darwin-x64` has been parsed, transformed and
  booted here; no Intel Mac has run any of it.
- **`doctor` on macOS**, and with it the install-identity and search lines that
  characterise the equivalence gap. It was not among the commands reported.

**One cosmetic difference was observed and is recorded rather than explained**:
on exit, the TUI did not clear or restore the terminal — the session's prior
scrollback stayed on screen. Seen **once**. The cause was not investigated, and
nothing here should be read as a hypothesis about it.

The darwin artifact (`cli.original.cjs`; its exact size is one row of
[findings.md](./findings.md) §6's table) was built and checked here, Bun
1.3.14's own parser accepts it (`bun build --no-bundle --target=bun`, exit 0) —
**and it has now been executed, on this Linux host** ✅ (re-run 2026-08-24):

```
$ DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=$(mktemp -d) \
    ~/.bun-1.3.14/bun <darwin-build>/extract/cli.original.cjs --version
2.1.239 (Claude Code)          rc=0
```

**And on the Intel build too, 2026-08-24** — the same command against the
`darwin-x64` 2.1.241 artifact printed `2.1.241 (Claude Code)`, rc 0, and
`mcp list` on that artifact answered `No MCP servers configured…`, rc 0. So
"the Mach-O path works here" is no longer a statement about one architecture.

The JavaScript boots and runs *here*. What this host still cannot say anything
about is **macOS-specific behaviour**, precisely:

- **The darwin `.node` addons cannot load here.** They are Mach-O — `cffaedfe`
  (thin: arm64 on the `arm64` build, x86_64 on the `x64` build) or `cafebabe`
  (universal) — and `require()`-ing one under Linux Bun fails with
  `ERR_DLOPEN_FAILED … invalid ELF header` ✅ (re-checked on the `darwin-x64`
  `image-processor.node`, 2026-08-24). The whole darwin native layer is
  unexercised here, on both — and, per the list above, the Mac run did not
  exercise it either.
- **`process.platform` is `linux`.** Every platform-conditional branch takes
  the Linux path, so nothing that depends on running *on* macOS is exercised
  here — the `ClaudeCode.app` wrapper, the darwin paths, Keychain, TCC, none of
  it. On the Mac those branches did take their real path, inside a session that
  authenticated and ran; what nobody has done is check any of them individually.
- Everything in [findings.md](./findings.md) §11 applies on macOS too. The image
  half is the one item the Mac run touched at all, and it touched only the
  *applying* of the shim, not its effect.

Emulation was evaluated on this host and rejected on observed evidence, not on
preference:

| Route | Blocker observed here |
|---|---|
| QEMU-KVM / `docker-osx` | no `/dev/kvm`; `/proc/cpuinfo` exposes no `vmx`/`svm` |
| Darling (translation layer, no VM) | needs the `darling-mach` kernel module; `/lib/modules` absent and `modprobe` fails even `--privileged` |
| QEMU TCG (pure software) | hours-long boots on shared vCPUs. (The old second half of this objection — "and it would exercise the darwin-**x64** build, not arm64" — no longer stands on its own: `darwin-x64` extraction *is* measured here now, and one of the Macs in play is Intel. What emulation would add is the macOS-native layer, which is precisely what a TCG boot is too slow to explore.) |

Apple's licence also restricts macOS virtualization to Apple hardware. Emulation
is therefore still out for *this* host — but it is no longer the only route to
the remaining answers, because a real Mac supplied most of them on 2026-08-24.

What is left needs a Mac and a deliberate command, not a whole run. On Apple
Silicon the build already works, so go straight for the two things the
2026-08-24 session did not reach:

```
# 1. does a darwin .node addon actually load?
bun -e 'console.log(Object.keys(require("<out>/extract/assets/image-processor.node")))'

# 2. does the shim do its job? Read an image larger than 2000x2000 and check
#    that it comes back resized rather than "Unable to resize image..."
```

On an **Intel** Mac, everything is still open, including the run itself: build
with `scripts/build.sh <native-binary>` and start with the command it prints
(`mcp list`, not `--version` — see [findings.md](./findings.md) §10).

---

## Windows / PE

`extract_bun.py` refuses PE input with a message that points here, so here is
the whole picture.

The `win32-x64` build (`@anthropic-ai/claude-code-win32-x64`, `claude.exe`,
version 2.1.239) **is a Bun standalone like the others**. Its `.bun` section
table was read directly on this host, by hand, with a section-header walk; the
file offset, section size, payload size and module count it yielded are the
`win32-x64` row of [findings.md](./findings.md) §3's table, and the binary's
size is its row in the table that opens that document. Both are stated there and
not repeated here. The payload behind the section has the same shape as the ELF
and Mach-O ones — u64 length prefix, module graph, 16-byte trailer
`\n---- Bun! ----\n`, 32-byte offsets struct, 52-byte module records,
`entry_point_id = 0` — and the section is padded to file alignment, which the
other two are not (§3). So the format work is done; nothing about PE is
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

> Evidence note: the PE facts in this section — and the numbers behind them in
> [findings.md](./findings.md) §3 — were measured on this host by direct
> read-only byte inspection while writing these documents. They are **not** part
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
  module, asset and transform counts — the same figures findings §4 and §6
  tabulate, and stated in those two places only.
- **When they fail:** that failure is the **feature**, not a bug — it is the
  early warning that the binary changed. Re-measure against the new binary,
  update `tests/test_integration.py` and findings §4/§6 with the new numbers,
  and re-run the L3/L4 checks. **Do not loosen the assertions to make them
  pass.**
- **If extraction itself breaks:** print every module's `(loader, name, size)`
  and compare against [bun-section-format.md](./bun-section-format.md). Never
  match the entry module by name — use `entry_point_id` (findings §4).

### 2. Finish the macOS picture 🖥️ — most of it is done

**Mostly closed 2026-08-24.** This item used to read "verify macOS-specific
behaviour on a Mac", with nothing at all behind it. An Apple Silicon run has
since covered the suite, the build, the smoke test, the TUI, an authenticated
session and image input 🍎; see
[§ macOS execution](#macos-execution-what-a-mac-has-now-shown-and-what-it-has-not).
Three things are left, and they are specific.

- **Verify (a), the native addon** — on Apple Silicon, where everything else
  already works:
  `bun -e 'console.log(Object.keys(require("<out>/extract/assets/image-processor.node")))'`.
  This is still the assertion Linux cannot make, and the 2026-08-24 run did not
  make it either.
- **Verify (b), the shim's actual effect** — `Read` an image larger than
  2000×2000 on that Mac and check it comes back resized rather than *"Unable to
  resize image…"*. The shim applied there; whether the path it unlocks works on
  macOS is untested.
- **Verify (c), an Intel Mac at all** — with `bun-darwin-x64.zip` and
  `P=darwin-x64`, run the whole thing:
  `DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=$(mktemp -d) bun
  <out>/extract/cli.original.cjs mcp list` (not `--version` — it initialises 0
  lazy modules, findings §10), then (a) and (b). Keep the two splits apart: the
  arm64/x64 split is about which *binaries* have been parsed here; the
  Linux/macOS split is about which have been *run on a Mac*, and only `arm64`
  has.
- **Nothing to download first, if that Mac already has Claude Code**:
  `scripts/build.sh` with no argument probes
  `${XDG_DATA_HOME:-$HOME/.local/share}/claude/versions/*`, walks the entries
  newest-first and takes the first that is at least 1 MiB, naming anything it
  skipped, then falls back to `command -v claude` (re-measured here 2026-08-24
  against a fake tree holding `2.1.9`, `2.1.238` and a 0-byte `2.1.999`: it
  warned about `2.1.999` and chose `2.1.238`). The size check is there *because*
  of the Mac run — an interrupted auto-update on that machine had left exactly
  such a stub, sorting newest. Otherwise, findings §9's download endpoint.
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
  Measured on both real binaries: one gate call site fewer, and a 4-byte
  difference from the unshimmed artifact (per-platform figures: findings §6's
  table). A **global** flip is still not the fix and never was: it breaks search,
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

- **Real model traffic — answered once, on the other machine.** Every
  agentic-loop result recorded *here* was driven by a **loopback mock** of the
  Messages API. Streaming, multi-turn tool use, the Bash tool spawning a
  subprocess, the Read tool returning an image and the Ink TUI under a pty all
  work on Bun 1.3.14 ✅ — and no request has ever gone to Anthropic from this
  host's build. On 2026-08-24 the Apple Silicon run did authenticate against a
  real account and had real model inference answer a prompt, with an image
  attached and described 🍎. That is one session on one machine, reported rather
  than measured here; it does not make any of this host's mock-driven results a
  live-traffic result, and nothing about latency, rate limits, long sessions or
  error handling against the real API has been characterised anywhere.
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
  exercised: of the gate call sites findings §6 counts, the shim touches
  exactly one.
- **`CLAUDE_CODE_EXECPATH`.** Measured: the CLI **never reads** it (0
  occurrences of `process.env.CLAUDE_CODE_EXECPATH`) and unconditionally
  *writes* it as `process.execPath` — now the bun binary — into every spawned
  shell's environment. The generated `find`/`grep` shell functions fall back to
  it, so they get shadowed onto bun. Read from source, not observed live. See
  [runbook.md](./runbook.md) § Shell integrations and findings §6.
- **Universal vs thin addons.** Some darwin `.node` files are universal
  (x86_64 + arm64), others thin arm64; the linux ones are ELF. Matters only if
  you mix architectures.
- **Why the TUI does not restore the terminal on macOS.** Observed **once** on
  the Apple Silicon run 🍎: on exit the TUI left the session's prior scrollback
  on screen rather than clearing or restoring it, which the native binary does
  not do. Cosmetic. The cause was **not investigated**, and no hypothesis about
  it belongs in this repository until someone measures one. Not reproduced,
  because it cannot be reproduced here.
- **The `Makefile` on macOS.** It is written in the GNU Make 3.81 / BSD-userland
  dialect macOS ships, and `tests/test_makefile.py` enforces those constraints
  as static checks **on Linux**. No target in it has been run on a Mac: the
  2026-08-24 run predates that file landing in the tree (which is why its
  collected total is lower than this tree's — see
  [README's table](../README.md#the-test-suite-and-its-counts)).
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
  lazy modules — a hardcoded fast path — out of the thousands the bundle holds
  (findings §10 counts them). Use `doctor` or `mcp list`.
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
