# not-rusty-claude

Run Claude Code on a **pre-Rust (Zig-era) Bun** — the runtime before Bun's
LLM-driven Zig→Rust rewrite — by extracting its JavaScript out of the native
binary and running it under a stock **Bun 1.3.14**. The signed binary is only
ever *read*: not modified, not re-signed, not executed by this pipeline.

> **Why.** Claude Code ships as a Bun *standalone* executable — runtime and app
> baked into one signed binary (Mach-O on macOS, ELF on Linux, PE on Windows).
> Bun is being rewritten from Zig to Rust; **1.3.14 is the last Zig release**.
> You cannot swap the embedded runtime, so instead we extract `cli.js` + assets
> and run them on an **external** Bun — and choose the Zig one.
>
> Two precisions, because this repo's rule is that claims trace to
> measurements. *Pre-Rust* describes the **rewrite of Bun's core**, not the
> binary: 1.3.14's `.comment` reads `rustc 1.94.0-nightly` and it links
> vendored Rust crates (`lolhtml`, `cssparser`, `encoding_rs`, `selectors`).
> And 1.3.14 is *sufficient*, not *necessary* — the same artifact also runs on
> Bun 1.4.0, the Rust build. Running on Zig is the **point**, not a
> constraint.

## Status — it works, on Linux, and here is exactly how far that goes

Verified 2026-08-22 on Linux x86_64 (Debian 12, glibc 2.36). Every ✅ below is
a command whose output is pasted in
[`docs/verification-2026-08-22.md`](docs/verification-2026-08-22.md) — except
the image-processing rows, which changed on 2026-08-23 when the scoped shim
landed and are reproducible with `scripts/ab-equivalence.sh` rather than pasted
into that record.

| Piece | `linux-x64` (ELF, 2.1.222) | `darwin-arm64` (Mach-O, 2.1.239) | `win32-x64` (PE, 2.1.239) |
|---|---|---|---|
| Extract `cli.js` + assets (`extract_bun.py`) | ✅ executed | ✅ executed | ⛔ refused by design |
| Post-process (`postprocess.py`) | ✅ executed | ✅ executed | ⛔ |
| `scripts/build.sh` end to end | ✅ executed | ✅ executed | ⛔ |
| Output accepted by Bun 1.3.14's own parser | 🔎 exit 0 | 🔎 exit 0 | ⛔ |
| **Runs under external Bun** | ✅ `doctor`, `mcp list`, `--help`, `--version` — on **1.3.14 *and* 1.4.0** | ✅ the JS boots under **Linux** Bun → `2.1.239 (Claude Code)` | ⛔ would also need a Windows Bun |
| Runtime asset loading | ✅ `image-processor.node` loads, works, **and is now reached by the CLI** — a `Read` of a 3000×3000 PNG returns a JPEG | 🖥️ Mach-O addons cannot load on Linux | ⛔ |
| Behaves the same as the shipped binary | ⚠️ **no** — smaller than it was, see the equivalence gap below | ⚠️ no | ⛔ |

✅ executed here · 🔎 static check only, nothing executed · 🖥️ needs hardware
we do not have · ⚠️ measured difference from the native binary · ⛔ deliberately
not implemented

**The headline result:** the extracted, post-processed `cli.original.cjs` from
the real Linux binary runs under vanilla Bun 1.3.14 and answers `doctor` and
`mcp list` (which really read and write `.claude.json`), plus `--help` and
`--version`. Driven by a **loopback mock** of the Messages API it also completes
a full agentic loop — SSE streaming, multi-turn tool use, the Bash tool spawning
a real subprocess, the Read tool returning a resized image — and renders the Ink
TUI under a pty, with no Bun-API failure anywhere. That is a positive answer, **for Claude Code 2.1.222, on Linux**, to
the risk in [`docs/findings.md`](docs/findings.md) §10. It is a measurement, not
a guarantee.

> **Don't lead with `--version`.** It initialises **0** lazy modules — a
> hardcoded fast path. It proves the file parses and the CJS wrapper is
> invoked, and nothing at all about Bun's API surface. `doctor` and `mcp list`
> initialise thousands; the per-command counts and the bundle's total are
> [`docs/findings.md`](docs/findings.md) §10's table, and are stated there and
> nowhere else.

**⚠️ The equivalence gap — read this before using it.** `Bun.isStandaloneExecutable`
is undefined outside a standalone, so the CLI takes its non-standalone branch
at every site that asks. How many sites that is differs per binary and per
release; the measured numbers are one row of
[`docs/findings.md`](docs/findings.md) §6's table, and §11 records why a
plausible-looking regex undercounts them. Measured consequences: the **seccomp
sandbox is off**, embedded ripgrep becomes a **system `rg`** (so `rg` is a de
facto prerequisite), and install identity reports `unknown`. Both addon loaders
swallow failure, so **exit 0 is not evidence that the asset wiring works**.

**One of those gaps is closed.** `postprocess.py` rewrites the *single* gate
call that guards native image processing, so a default build resizes a large
image instead of erroring. It is scoped to that one call site on purpose:
flipping `Bun.isStandaloneExecutable` globally is measured to make `Grep`
answer `No matches found` for a string that exists — a wrong answer, not an
error. `NRC_NO_IMAGE_SHIM` (any non-empty value) builds the old artifact, and
`scripts/ab-equivalence.sh` reproduces the whole A/B — as-shipped, shimmed and
globally-flipped — against a committed loopback mock. That harness is
**Linux-only** and refuses to start elsewhere: its egress guard reads
`/proc/<pid>/fd` against `/proc/net/tcp`, and running the comparison without
that guard would print output indistinguishable from a clean run. Full detail:
[`docs/findings.md`](docs/findings.md) §11.

**What is honestly not known:** no request from this build has ever gone to
Anthropic — the agentic loop above was a loopback mock. macOS-*specific*
behaviour is unverified: the darwin JS boots here, but its `.node` addons are
Mach-O and cannot load on Linux, and `process.platform` is `linux`. The
[macOS section](#macos) splits that line precisely — what was measured against
the real Mach-O on Linux, and what needs a Mac and has never been run on one.

[`docs/status.md`](docs/status.md) is the full matrix, the Windows/PE picture,
and the remaining work.

## Quickstart

```bash
# 1. the last Zig-era Bun, unpacked (not installed, not on PATH)
mkdir -p "$HOME/.bun-1.3.14"
curl -fsSL -o /tmp/bun.zip \
  https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-linux-x64.zip
unzip -o -j /tmp/bun.zip 'bun-linux-x64/bun' -d "$HOME/.bun-1.3.14"
chmod +x "$HOME/.bun-1.3.14/bun"

# 2. extract + post-process (installs nothing, writes nothing to the binary)
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh /usr/bin/claude

# 3. run it, by full path, with a scratch config dir.
#    DISABLE_AUTOUPDATER=1 is NOT optional - without it this build can try to
#    install a different, npm-based Claude Code onto your machine. See
#    docs/runbook.md § Surviving Claude updates.
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs mcp list
#   → No MCP servers configured. Use `claude mcp add` to add a server.
```

`build.sh` deliberately **installs nothing on `PATH`** — a file named `claude`
there could shadow your real installation. It prints the full-path command
instead. It also writes a one-line `cli.js` beside `cli.original.cjs`, because
Claude's own code resolves a sibling `cli.js` for two MCP self-spawns.

One consequence to know about: under a native install `process.execPath` *is*
`claude`; here it is **bun**, and the CLI unconditionally exports
`CLAUDE_CODE_EXECPATH=<bun>` into every shell it spawns. Setting that variable
yourself does nothing — the CLI never reads it. See
[`docs/runbook.md`](docs/runbook.md) § Shell integrations for what actually
happens.

Step-by-step and troubleshooting: [`docs/runbook.md`](docs/runbook.md). On a
Mac, read the section directly below first — the runbook's steps transfer, but
what has and has not actually been run is set out here.

## macOS

Two levels of confidence, kept apart on purpose, because this project's host is
**Linux x86_64 with no Mac and no way to emulate one.** Re-checked here on
2026-08-24: no `/dev/kvm`, zero `vmx`/`svm` lines in `/proc/cpuinfo`, no
`modprobe` on the host at all, and `unshare` refused (`Operation not
permitted`). An earlier session in this project also recorded `modprobe`
failing for Darling's kernel module even `--privileged`. The emulation routes
were evaluated and rejected on that evidence, not on preference
([`docs/status.md`](docs/status.md) § macOS execution).

So: the first block below was **measured on Linux against the real
`darwin-arm64` Mach-O**, which needs no Mac, because parsing a container and
rewriting JavaScript are byte arithmetic. The second block is what a Mac is
actually for, and **none of it has been run on a Mac by this project** — no
command in it produced output that anyone here has seen. One exception is
recorded rather than hidden: a prior session noted a working Mac run on
2026-08-21 against 2.1.238 (📓 in `docs/status.md`), which is a note, not a
re-checked result, and the bullet that carries it says so. Do not read the
first block as evidence for the second.

### Measured here on Linux ✅ — obtain, extract, shim

Every command in this block was re-run on this host on 2026-08-24.

**1. Get the binary, without a Mac and without installing anything.**

```bash
npm pack @anthropic-ai/claude-code-darwin-arm64
```

Measured today: it returned
`anthropic-ai-claude-code-darwin-arm64-2.1.241.tgz`, 92,295,033 bytes. Note the
version — `npm pack` gives you **whatever is current**, and today that is newer
than the 2.1.239 copy every darwin number in this repo was measured against.
Your counts will differ from the ones below, and that is the release tripwire
working rather than something broken.

**2. Unpack it under a name that is not `claude`.**

```bash
mkdir -p /tmp/ccmac
tar xf anthropic-ai-claude-code-darwin-arm64-*.tgz -C /tmp/ccmac \
    --transform='s|package/claude$|package/claude-darwin-arm64.bin|'
```

The payload inside the tarball is `package/claude`, and a stray file called
`claude` is exactly what can later be found on a `PATH` and shadow a real
installation — so it is renamed as it comes out of the archive, and this repo
never creates that name. Re-run here today against the **2.1.239** tarball this
host already had, so that the rest of this block lines up with the numbers
recorded elsewhere in the repo; the freshly packed 2.1.241 was left unopened.
The extracted file comes out at exactly the size
[`docs/findings.md`](docs/findings.md)'s opening table records for
`darwin-arm64`, and its first four bytes are `cf fa ed fe` — a thin arm64
Mach-O.

**3. Extract and post-process it — on Linux.**

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" OUT_DIR=/tmp/macbuild \
  scripts/build.sh /tmp/ccmac/package/claude-darwin-arm64.bin
```

`extract_bun.py` finds the payload by walking the Mach-O load commands
(`LC_SEGMENT_64`) to the `__BUN,__bun` section — the Mach-O counterpart of the
ELF section-header walk it does on Linux, and byte arithmetic either way
([`docs/bun-section-format.md`](docs/bun-section-format.md)). PE input is
refused, deliberately. Output quoted from that run, against `darwin-arm64`
2.1.239 on 2026-08-24:

```
Section: offset=69107712 size=255007133 (243.2 MB)
Payload: 255007125 bytes, trailer OK
Modules: 15 (entry id=0)
Extracted: 1 cli.js + 9 assets (5 loader shims left inlined in cli.js)
image shim gate        : AE
image shim call sites  : 23 -> 22
image shim applied     : 1  (expected 1)
size: 28244743 -> 28244063 bytes
```

**4. The scoped image shim applies on darwin too**, and only to the one call
site. That is what the two `image shim` lines above are: the gate is minified to
`AE` in this build, its call sites go **23 → 22**, and every other
`Bun.isStandaloneExecutable` gate — ripgrep, sandbox, updater — is left false.
(The Linux binary minifies the gate differently and has a different count; both
platforms are tabulated in [`docs/findings.md`](docs/findings.md) §6, which is
where those figures are recorded.)

**5. The shim is four bytes.** Build the same binary again with
`NRC_NO_IMAGE_SHIM=1` and compare:

```bash
NRC_NO_IMAGE_SHIM=1 BUN_BIN="$HOME/.bun-1.3.14/bun" OUT_DIR=/tmp/macbuild-asshipped \
  scripts/build.sh /tmp/ccmac/package/claude-darwin-arm64.bin
python3 - /tmp/macbuild/extract/cli.original.cjs \
          /tmp/macbuild-asshipped/extract/cli.original.cjs <<'EOF'
import sys
a, b = (open(p, "rb").read() for p in sys.argv[1:3])
d = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
print(len(a), len(b), "differing bytes:", len(d), d)
EOF
```

Measured on 2026-08-24: both artifacts came out at the same length (the `size:`
line above), with **exactly 4 differing bytes**, at offsets
7,104,588–7,104,591 — `if(AE())try{` in the as-shipped artifact against
`if(true)try{` in the shimmed one. The offsets and the gate
name belong to this one build of 2.1.239. So does the *four*: it is the length
of `AE()`, which happens to equal the length of `true`, so the two artifacts
also come out the same size. A release that minified the gate to a one- or
three-character name would change both numbers.

**6. The darwin JavaScript boots — under Linux Bun.**

```bash
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" /tmp/macbuild/extract/cli.original.cjs --version
#   → 2.1.239 (Claude Code)   rc=0   (re-run here 2026-08-24)
```

That is a real execution of the darwin build's JavaScript, and it is
deliberately *not* claimed as evidence about macOS: `process.platform` is
`linux` in that process, and the Mach-O `.node` addons cannot load at all
(`ERR_DLOPEN_FAILED … invalid ELF header`, measured).

### Not verified — never run on a Mac ⛔

Follow these on Apple Silicon if you want; just do not believe them until you
have seen them work, and please report what happens.

- **Bun 1.3.14 for macOS.** The asset is
  `.../bun-v1.3.14/bun-darwin-aarch64.zip` (`curl -I` from here answered
  `HTTP/2 302`, the redirect to GitHub's asset CDN, and `curl -IL` followed it
  to `200`, on 2026-08-24 — that is a URL check and nothing more; nothing on
  this host has unzipped or executed a darwin Bun. An earlier revision of this
  line quoted the `200` without the `-L` that produces it.)
- **Running the artifact on macOS.** Steps 0–4 of
  [`docs/runbook.md`](docs/runbook.md) transfer as written, ending with
  `DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" bun
  <out>/extract/cli.original.cjs mcp list` (`mcp list` or `doctor`, **not**
  `--version`). **Unverified on macOS.** Everything a native install gets from
  being a Bun standalone is still missing there, exactly as on Linux
  ([`docs/findings.md`](docs/findings.md) §11), and none of it has been observed
  on the platform.
- **Whether a darwin `.node` addon loads.** This is the one assertion Linux
  cannot make, and the single most useful thing to send back:
  `bun -e 'console.log(Object.keys(require("<out>/extract/assets/image-processor.node")))'`.
- **`tools/patch_claude.py`'s re-signing (Approach B).** The byte-patching half
  is tested on Linux through `--no-sign`/`--dry-run`; the `codesign` half has
  **never met a real `codesign`** — there is none on this host, so every signing
  path here is exercised only through its refusals. The invocation itself, the
  entitlements/identifier round trip, and whether the re-signed binary launches
  are all unverified. A prior session recorded a working Mac run on 2026-08-21
  against 2.1.238; that is a note (📓 in [`docs/status.md`](docs/status.md)), not
  a re-checked result.
- **`scripts/ab-equivalence.sh` does not run on macOS at all.** That is a
  decision, not an oversight: its egress guard reads `/proc/<pid>/fd` against
  `/proc/net/tcp` to prove every socket a run opens is loopback, there is no
  portable substitute in it yet, and running the comparison without that guard
  would print output indistinguishable from a clean run. The preflight refuses
  up front, naming `/proc` among anything else missing. Verified here on
  2026-08-24 only in part: hiding `bun` and `node` made the preflight name both
  in one message and exit 1 — but the `/proc` branch, the one a Mac takes, could
  not be exercised, because this host will not let `/proc` be hidden (`unshare`
  is refused). What a Mac sees there is read from the script, not observed.

## Layout

```
not-rusty-claude/
├── README.md                       you are here
├── docs/
│   ├── status.md                   ← what is verified on what; the remaining gaps
│   ├── findings.md                 measured facts about the binary and the transforms
│   ├── bun-section-format.md       byte-level spec (Mach-O / ELF / PE containers)
│   ├── runbook.md                  step-by-step, Linux and macOS
│   ├── verification-2026-08-22.md  the evidence record: commands + pasted output
│   └── superpowers/specs/          designs of record, one per change
├── tools/
│   ├── extract_bun.py              extract cli.js + assets from the Bun section
│   ├── postprocess.py              make cli.js runnable under an external Bun,
│   │                               including the scoped image shim (findings §11)
│   └── patch_claude.py             Approach B: byte-patch + re-sign (macOS only)
├── scripts/
│   ├── build.sh                    extract → post-process → print the run command
│   ├── ab-equivalence.sh           the findings §11 A/B: as-shipped vs shimmed vs
│   │                               globally-flipped, through the mock below
│   │                               (Linux-only: its egress guard reads /proc)
│   ├── mock-messages-api.mjs       loopback-only mock of the Messages API
│   └── syntax-check.js             fast secondary syntax check (JSC, not Bun)
└── tests/                          hermetic fixtures + real-binary integration
```

The tools themselves need no third-party packages — stock `python3` (3.9+) is
enough. Running the test suite additionally needs `pytest`: `python3 -m pytest
tests/ -q`. What that prints depends on what the host has, because the
integration tests need a real Claude binary — the two used here are the
`linux-x64` and `darwin-arm64` rows of the table that opens
[`docs/findings.md`](docs/findings.md), with their exact sizes — and they skip
cleanly without one:

**No other file in this repo states these counts.**
[`docs/status.md`](docs/status.md), [`docs/findings.md`](docs/findings.md)'s
appendix and [`docs/runbook.md`](docs/runbook.md) point at this table instead of
repeating it, and nothing else in this README repeats it either. That is
checkable rather than decorative: search these four documents for a pass count,
and every hit outside this table is a quotation of a *past* number that the
surrounding sentence identifies as wrong. (The dated evidence records —
[`docs/verification-2026-08-22.md`](docs/verification-2026-08-22.md) and the
design specs — do paste pass counts, from the suite as it stood on the day each
was written. Those are transcripts of a moment, timestamped as such, not
statements about the suite you are running.)

The claim that stood here before said *"this table is the only place in the repo
that states these counts"* — and it was false as written, in the same change
that introduced it: the layout tree above said `242 tests` and
[`docs/status.md`](docs/status.md) said `242 passed` while calling this table the
one place those counts live. Three copies of one number inside the fix for
duplicating that number. Before that, `docs/findings.md`'s appendix carried four
of these counts and contradicted this table in the very commit that introduced
them (`9c98027`: README `199 passed`, appendix `190 passed`) — by ten at
`18916ca` — under a heading promising that every command below had been run on
this host. The counts also *move*: the four rows below were 242/235/229/226 one
wave ago and are not any more, which is exactly why they must not be written
twice.

The repo's convention, of which this table is the main instance: **a measured
figure is stated in one place, and appears elsewhere only as quoted command
output, labelled with the binary and date that produced it.**

Every row below was re-measured on this host on 2026-08-24, by forcing the row
with the environment variables named underneath. `python3 -m pytest tests/ -q
--collect-only` reports the same total in all four configurations — what the
host has changes the skips, never the collection — and that total is the first
row's number.

| host has | result | how the row was forced |
| --- | --- | --- |
| both binaries + Bun | **259 passed** | nothing set (this host's defaults) |
| ELF binary + Bun, no Mach-O | 252 passed, 7 skipped | `NRC_TEST_MACHO=/nonexistent/macho` |
| Bun only | 246 passed, 13 skipped | …plus `NRC_TEST_ELF=/nonexistent/elf` |
| none of them | 243 passed, 16 skipped | …plus `BUN_BIN=/nonexistent/bun` and a `HOME` with no Bun under it — the command below |

That last row needs the extra care, and for two independent reasons.

`BUN_BIN` is a *first* choice, not an override, so the fixture still falls back
to `~/.bun-1.3.14/bun` and then to `bun` on `PATH`. Measured: `BUN_BIN` pointed
at a nonexistent file with this host's real `HOME` still in place reproduces the
Bun-**only** row above exactly — not the no-Bun one. So `HOME` has to
move too, and `bun` must not be on `PATH` (it is not on this host: the runbook
unpacks 1.3.14 without installing it, so nothing extra was needed here; a host
that does have one has to strip it from `PATH` as well).

And moving `HOME` can take `pytest` with it. Followed literally, "an empty
`HOME`" printed `/usr/bin/python3: No module named pytest` here and exited 1 —
pytest on this host is a `--user` install, under `$HOME/.local`. So put it back
explicitly, derived rather than hardcoded (it resolves to
`/home/claude/.local/lib/python3.11/site-packages` on this host, which is a
fact about this host and not about the recipe):

```bash
# the "none of them" row, as run. $(...) is evaluated before HOME is replaced.
PYTHONPATH="$(python3 -m site --user-site)" \
NRC_TEST_ELF=/nonexistent/elf NRC_TEST_MACHO=/nonexistent/macho \
BUN_BIN=/nonexistent/bun HOME="$(mktemp -d)" \
  python3 -m pytest tests/ -q
#   → the last row of the table above
```

Where pytest is installed system-wide instead, `--user-site` names a directory
that need not exist and the `PYTHONPATH` is a harmless no-op.

Point them at binaries with `NRC_TEST_ELF` (default `/usr/bin/claude`) and
`NRC_TEST_MACHO` (default `/tmp/ccmac/package/claude-darwin-arm64.bin`, the
name [`docs/findings.md`](docs/findings.md)'s appendix unpacks the darwin
tarball under; the older `/tmp/ccmac/package/claude` is still accepted), and at
a Bun with `BUN_BIN`
(default: `~/.bun-1.3.14/bun`, then whatever `bun` is on `PATH`). The
integration tests' hardcoded counts are a deliberate tripwire for the next
Claude release (see [`docs/status.md`](docs/status.md)) — and so is this table:
a stale total here disarms it, because "the number changed" stops meaning
anything.

## Two approaches

**A — Extract & run under Bun (this project's default).** No modification to the
signed binary, so signing and notarization never enter the picture; the JS is
plain text with no length limit. Needs an **external Bun** — using the Zig
1.3.14 is this project's deliberate choice, not a requirement of the artifact,
which also runs on the Rust 1.4.0. This is the "de-rust" path, and the one
verified above.

**B — Byte-patch & re-sign** ([`tools/patch_claude.py`](tools/patch_claude.py)).
Edit bytes inside the Mach-O (length-preserving) and ad-hoc re-sign with the
original entitlements and identifier. Verified end-to-end on a Mac in a prior
session (not re-checked here); its byte-patching half now has a test suite —
`tests/test_patch_claude.py`, **111 tests**, collected on this host on
2026-08-24 — which runs on Linux through `--no-sign` and `--dry-run`. The
re-signing half has still never met a real `codesign`: there is none on this
host (`command -v codesign` finds nothing), so every signing path is exercised
only through its refusals.
Downsides: notarization lost, TCC permissions reset, overwritten by the next
update. Three refusals are worth knowing before you point it at a 325 MB
signed binary: on a host with no `codesign` a signing run stops *before* it
creates anything (it used to leave an unpatched copy of the input under the
`--out` name); `--out <the input>` is refused rather than silently patched in
place without a `.bak`; and hits that land inside the Mach-O code-signature
blob are reported and skipped, because re-signing would discard them and
`--no-sign` would leave the signature corrupt. Use only for edits that are
**not** in the JS layer. Details in [`docs/findings.md`](docs/findings.md) §7.

## The one risk to know

Anthropic builds Claude with Bun's *canary* channel. For 2.1.222 that turned out
fine — 1.3.14 ran it, with room to spare: the same entry module also runs on
**1.3.13** in a pragma-preserving build shape, so the "Bun ≥ 1.3.14" floor is a
property of *this project's transform*, not of Claude. But if a future Claude
build uses Bun APIs newer than 1.3.14, its `cli.js` will not run on Zig, and the
only newer Bun is the Rust rewrite. That defeats the goal for that version.

The signal to watch for is a **missing-API error**. `Expected CommonJS module to
have a function wrapper` is *not* a reliable canary — this project's own
transform produces that exact panic when the pragma/IIFE shape is wrong. Keep
the last working `build/extract/` and pin that Claude version. Full discussion:
[`docs/findings.md`](docs/findings.md) §10.

A related, cheaper surprise: nothing here is a constant across versions or
platforms. The linux build has 8 modules, the darwin build 15, and even the
entry module's *name* differs — which is why extraction keys off
`entry_point_id`, never a name ([`docs/findings.md`](docs/findings.md) §4).

## Prior art

[ClawGod](https://github.com/0Chencc/clawgod) (extract + patch + run under Bun),
[bun-demincer](https://github.com/vicnaum/bun-demincer),
[unbuned](https://github.com/vibheksoni/unbuned),
[bun-decompile](https://github.com/lafkpages/bun-decompile),
[tweakcc](https://github.com/Piebald-AI/tweakcc). Comparison in
[`docs/findings.md`](docs/findings.md) §8 — including a correction: this README
used to claim ClawGod's transforms "silently do nothing on current builds".
Measured, they do not. ClawGod extracts the native addons correctly and its
`fileURLToPath` transform matches all 7 real sites; the narrower, real
differences are that it drops `file`-loader assets and rewrites only
`require("….node")` (§5b).

## Not affiliated with Anthropic. For personal, local use; you run modified code
at your own risk.
