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

## Status — it works on Linux and on an Apple Silicon Mac, and here is exactly how far that goes

Verified 2026-08-22 on Linux x86_64 (Debian 12, glibc 2.36). Every ✅ below is
a command whose output is pasted in
[`docs/verification-2026-08-22.md`](docs/verification-2026-08-22.md) — except
the image-processing rows, which changed on 2026-08-23 when the scoped shim
landed and are reproducible with `scripts/ab-equivalence.sh` rather than pasted
into that record.

The table below is measured **on this Linux host**, including its `darwin`
column: parsing a Mach-O is byte arithmetic and needs no Mac. On 2026-08-24 the
pipeline was, for the first time, also run **on a Mac** — a reporting Apple
Silicon host, against that machine's own installed Claude Code 2.1.239. What it
did and did not cover is [its own block](#measured-on-an-apple-silicon-mac--the-first-macos-run)
in the macOS section; the one-line summary is that the build it produced there
matched this host's `darwin-arm64` figures **byte for byte**.

| Piece | `linux-x64` (ELF, 2.1.222) | `darwin` (Mach-O): `arm64` 2.1.239 · `x64` 2.1.241 | `win32-x64` (PE, 2.1.239) |
|---|---|---|---|
| Extract `cli.js` + assets (`extract_bun.py`) | ✅ executed | ✅ executed, **both architectures** · 🍎 also on the Mac, `arm64`, same figures | ⛔ refused by design |
| Post-process (`postprocess.py`) | ✅ executed | ✅ executed, both · 🍎 also on the Mac, `arm64` | ⛔ |
| `scripts/build.sh` end to end | ✅ executed | ✅ executed, both · 🍎 also on the Mac, `arm64` | ⛔ |
| Output accepted by Bun 1.3.14's own parser | 🔎 exit 0 | 🔎 exit 0, both | ⛔ |
| **Runs under external Bun** | ✅ `doctor`, `mcp list`, `--help`, `--version` — on **1.3.14 *and* 1.4.0** | ✅ both boot under **Linux** Bun → `2.1.239` / `2.1.241 (Claude Code)`; the `x64` build also answers `mcp list`. 🍎 the `arm64` build also runs **on** an Apple Silicon Mac: `mcp list`, the interactive TUI, and an authenticated session against a real account | ⛔ would also need a Windows Bun |
| Runtime asset loading | ✅ `image-processor.node` loads, works, **and is now reached by the CLI** — a `Read` of a 3000×3000 PNG returns a JPEG | 🖥️ Mach-O addons cannot load on Linux, so this stays unmeasured — and the Mac run did **not** close it either: the shim applied and image *input* worked there, but the resize path that would exercise `image-processor.node` was not exercised | ⛔ |
| Behaves the same as the shipped binary | ⚠️ **no** — smaller than it was, see the equivalence gap below | ⚠️ no | ⛔ |

✅ executed here · 🍎 executed on the reporting Apple Silicon Mac, 2026-08-24 ·
🔎 static check only, nothing executed · 🖥️ needs hardware we do not have ·
⚠️ measured difference from the native binary · ⛔ deliberately not implemented

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

**What is honestly not known.** Two separate limits, and they moved in
different directions on 2026-08-24.

*Real model traffic.* No request from **this host's** build has ever gone to
Anthropic — every agentic-loop result recorded here was driven by a loopback
mock. That is no longer true of the project as a whole: on the reporting Mac
the extracted build held an authenticated session against a real account and
real model inference answered a prompt. Nothing about that run was measured
here, and none of this host's numbers depend on it.

*macOS-specific behaviour.* Narrower than it was, and still real. The darwin JS
boots here — from the Apple Silicon *and* the Intel binary — but under Linux
its `.node` addons cannot load and `process.platform` is `linux`, so nothing
here exercises the macOS layer. On the Mac it did run: the TUI, `mcp list` and
an authenticated session. What that run still did **not** settle: whether a
darwin `.node` addon actually loads (the native image *resize* path, the one
thing that would have proved it, was never exercised), `tools/patch_claude.py`'s
`codesign` half, and `scripts/ab-equivalence.sh`, which cannot run on macOS at
all. The [macOS section](#macos) draws each of those lines command by command,
and marks which host each was measured on. Intel Macs are untouched by this:
`darwin-x64` has been parsed here and run on nothing.

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

**No native `claude` on this machine?** The download endpoint in the
[macOS section](#macos) is not macOS-only — the manifest lists **eight**
platforms, `linux-x64` and `linux-arm64` among them, plus `-musl` variants of
each. Checked here on 2026-08-24: `curl -I` on
`https://downloads.claude.ai/claude-code-releases/2.1.241/linux-x64/claude`
answered `200` with `Content-Length` 342,636,848, matching that platform's
`size` in the manifest. The same
fetch-manifest → download → `shasum -c` flow applies verbatim with
`P=linux-x64`. Nothing on this host has *extracted* that copy, though: the
Linux figures throughout this repo come from the pre-installed
`/usr/bin/claude` 2.1.222, which is a different release.

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

**Three** levels of confidence now, kept apart on purpose, because they were
measured on two different machines and one of them still does not exist for
this project.

This repository's own host is **Linux x86_64 with no Mac and no way to emulate
one.** Re-checked here on 2026-08-24: no `/dev/kvm`, zero `vmx`/`svm` lines in
`/proc/cpuinfo`, no `modprobe` on the host at all, and `unshare` refused
(`Operation not permitted`). An earlier session in this project also recorded
`modprobe` failing for Darling's kernel module even `--privileged`. The
emulation routes were evaluated and rejected on that evidence, not on
preference ([`docs/status.md`](docs/status.md) § macOS execution).

The three blocks below:

1. **Measured here on Linux** — obtaining, extracting and shimming the real
   darwin Mach-O binaries, `arm64` *and*, since 2026-08-24, `x64`. This needs
   no Mac: downloading a file, parsing a container and rewriting JavaScript are
   byte arithmetic.
2. **Measured on an Apple Silicon Mac** — new on 2026-08-24, and the first time
   any part of this project has executed on macOS. Reported first-hand from
   that machine, not run here. It covers the suite, the build, the smoke test,
   the interactive TUI, an authenticated session and image *input*.
3. **Still not verified anywhere** — a short list now, and each entry says
   precisely why the run above did not reach it.

Read the boundaries between them literally. Block 1 was never evidence for
block 2, and block 2 is not evidence for block 3.

A prior session also noted a working Mac run on 2026-08-21 against 2.1.238
(📓 in `docs/status.md`). That note is superseded as motivation by block 2 and
is kept only as history: it was never re-checked, and nothing below rests on it.

### Measured here on Linux ✅ — obtain, extract, shim

Every command in this block was re-run on this host on 2026-08-24.

**1. Get the binary from Anthropic's download endpoint, and verify it — no Mac,
no install, no npm.** This is the same host and the same paths that
`claude.ai/install.sh` itself uses; the script's own `DOWNLOAD_BASE_URL` is
`https://downloads.claude.ai/claude-code-releases` (read from the script, not
guessed).

```bash
BASE=https://downloads.claude.ai/claude-code-releases
V="$(curl -fsSL "$BASE/latest")"        # 2.1.241 here on 2026-08-24
P=darwin-arm64                          # Apple Silicon. Intel Mac: P=darwin-x64
DEST=/tmp/ccdl/claude-$P.bin            # a name that is NOT `claude` — see below

mkdir -p /tmp/ccdl
curl -fsSL "$BASE/$V/manifest.json" -o /tmp/ccdl/manifest.json
SUM="$(python3 -c 'import json,sys; print(json.load(open("/tmp/ccdl/manifest.json"))["platforms"][sys.argv[1]]["checksum"])' "$P")"

if curl -fsSL "$BASE/$V/$P/claude" -o "$DEST" &&
   printf '%s  %s\n' "$SUM" "$DEST" | shasum -a 256 -c -
then echo "verified $V $P -> $DEST"
else rm -f "$DEST"; echo "download or checksum FAILED; nothing left on disk" >&2
fi
```

**The `if` is the point.** You are about to hand a third of a gigabyte of
unverified bytes to a Mach-O parser that walks load commands and slices at offsets it reads out of
the file. Verification belongs *inside* the flow, deleting the file on
mismatch, not in a step someone skips. Both halves were run here on 2026-08-24:

```
/tmp/ccdl/claude-darwin-arm64.bin: OK
verified 2.1.241 darwin-arm64 -> /tmp/ccdl/claude-darwin-arm64.bin
/tmp/ccdl/claude-darwin-x64.bin: OK
verified 2.1.241 darwin-x64 -> /tmp/ccdl/claude-darwin-x64.bin
```

and the failure path was exercised too, by feeding the same guard a truncated
file and an all-zero checksum: `shasum` printed `FAILED`, the `else` branch ran,
and `ls` afterwards reported the file gone. The sha256 each one matched is in
[`docs/findings.md`](docs/findings.md) §9; the byte sizes are its §1 table.

**Both Mac architectures, one code path.** `P=darwin-arm64` for Apple Silicon,
`P=darwin-x64` for an Intel Mac; nothing else in the flow changes. Checked here
on 2026-08-24, both downloads are **thin** Mach-O — first four bytes
`cf fa ed fe`, `cputype` `0x0100000c` (arm64) and `0x01000007` (x86_64) — not a
fat/universal file, so the extractor's single `LC_SEGMENT_64` walk handles both
without a slice-selection step. That is not generosity: `extract_bun.py`
matches on `MH_MAGIC_64` alone and has no `cputype` branch and no fat-header
path, so a universal `cafebabe` input would be rejected rather than sliced.
Neither download is one. The manifest lists **eight** platforms, each
with a `checksum` and a `size`; for both darwin downloads the `size` field, the
CDN's `Content-Length` and the bytes on disk agreed exactly. Only the
`darwin-x64` figure also equals [`docs/findings.md`](docs/findings.md) §1,
whose row for that platform is the same 2.1.241 build. §1's `darwin-arm64` row
is the **2.1.239** copy, 324,973,552 B, so a `latest` download lands 82,080
bytes away from it — a version difference, not a corrupt file. Cross-referencing
a size across two Claude versions is the exact mistake this repo keeps
retracting; check `size` against the manifest you fetched, never against a
figure recorded for another build.

**Three things worth knowing about this endpoint**, all checked here today:

- **`latest` and `stable` are different pointers, and today they differ** —
  `latest` → `2.1.241`, `stable` → `2.1.231`. Choosing between them is a real
  choice; pin `V` to a literal version if you want a build that does not move
  under you.
- **`claude.zst` exists beside `claude`** and is what the installer tries
  first (both answered `200`). It has its **own** manifest,
  `manifest.zst.json`, with its own checksums — the `checksum` in
  `manifest.json` describes the *decompressed* binary, so verifying a `.zst`
  against it will fail. `Content-Length` here on 2026-08-24 was 64,578,859 B for
  `darwin-arm64/claude.zst` and 69,188,990 B for `darwin-x64/claude.zst`,
  matching the `size` fields in `manifest.zst.json`. This repo takes the plain
  binary: the saving is not worth a second checksum domain and a `zstd`
  dependency, and nothing here has run `zstd -d` on one.
- **Do not pipe `claude.ai/install.sh` to `bash`** if the point is to avoid a
  PATH install. Read from the script itself: it downloads into
  `$HOME/.claude/downloads`, and then runs `"$binary_path" install`, described
  in its own comment as setting up the *launcher and shell integration*. That is
  an install. The flow above is the same download without it.

**2. Never let the file land as `claude`.** The endpoint's path component is
literally `claude` (the manifest's `binary` field is `"claude"` too), so
`curl -O` would drop a file called `claude` in your working directory — exactly
the name that can later be found on a `PATH` and shadow a real installation.
Always `-o` a name of your own choosing, as above. This repo never creates that
name anywhere.

> Steps 3–6 below use two files: the **2.1.239** `darwin-arm64` copy this host
> already had, kept at that version rather than replaced with today's download
> so the arm64 numbers still line up with the rest of the repo, and the
> **2.1.241** `darwin-x64` file step 1 just fetched. The arm64 copy lives at
> `/tmp/ccmac/package/claude-darwin-arm64.bin` — the name the test suite looks
> for by default, `NRC_TEST_MACHO` overrides it. The endpoint gives you
> **whatever is current** — 2.1.241 today — so your counts will differ from the
> ones below, and that is the release tripwire working rather than something
> broken.

**2b. Already have Claude Code on that Mac?** Then there is nothing to download
at all. Run `scripts/build.sh` with no argument: it probes
`${XDG_DATA_HOME:-$HOME/.local/share}/claude/versions/*`, walks the entries
**newest-first** and takes the first *plausible* one — at least 1 MiB, since a
Bun standalone is hundreds of megabytes — naming anything it skipped, and falls
back to `command -v claude` only when that finds nothing. It reads the binary
and never writes to it.

That "plausible" check exists because of the Apple Silicon run. The earlier code
took `sort -V | tail -1` unconditionally, and on that machine an interrupted
auto-update had left a **0-byte** `versions/` entry sorting *newer* than the
working install — so the extractor was handed an empty file and complained about
its size, which reads like a bug in this repo rather than a broken install on the
host. Re-measured here on 2026-08-24 against a three-entry fake tree
(`2.1.9`, `2.1.238`, and a 0-byte `2.1.999`): the script warned that it ignored
`2.1.999`, said in the same breath that a 0-byte entry is what an interrupted
update leaves, and selected `2.1.238`.

**3. Extract and post-process it — on Linux.**

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" OUT_DIR=/tmp/nrc-a64/build \
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

**3b. The same, on the Intel binary — measured here for the first time.** Every
Mach-O number this repo had ever recorded came from `darwin-arm64`. The
`darwin-x64` 2.1.241 download from step 1 went through the identical command,
on 2026-08-24:

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" OUT_DIR=/tmp/nrc-x64/build \
  scripts/build.sh /tmp/ccdl/claude-darwin-x64.bin
```

```
Section: offset=75755520 size=255171495 (243.4 MB)
Payload: 255171487 bytes, trailer OK
Modules: 15 (entry id=0)
Extracted: 1 cli.js + 9 assets (5 loader shims left inlined in cli.js)
image shim gate        : Tw
image shim call sites  : 23 -> 22
image shim applied     : 1  (expected 1)
size: 28245789 -> 28245109 bytes
```

Same shape, different offsets, a different gate name, and the *same* module and
asset counts. Nothing in the pipeline needed an architecture switch: the
container walk is `LC_SEGMENT_64` either way. `bun build --no-bundle` accepted
the result (rc 0), as did the secondary JSC check. Every figure in that
transcript is also a column of [`docs/findings.md`](docs/findings.md) §6, which
is where those numbers live.

**4. The scoped image shim applies on darwin too**, and only to the one call
site. That is what the two `image shim` lines above are: the gate is minified to
`AE` in the arm64 build and `Tw` in the x64 one, its call sites go **23 → 22**
in both, and every other `Bun.isStandaloneExecutable` gate — ripgrep, sandbox,
updater — is left false. (The Linux binary minifies the gate differently again
and has a different count; all three are tabulated in
[`docs/findings.md`](docs/findings.md) §6, which is where those figures are
recorded.)

**5. The shim is four bytes.** Build the same binary again with
`NRC_NO_IMAGE_SHIM=1` and compare:

```bash
NRC_NO_IMAGE_SHIM=1 BUN_BIN="$HOME/.bun-1.3.14/bun" OUT_DIR=/tmp/nrc-a64/noshim \
  scripts/build.sh /tmp/ccmac/package/claude-darwin-arm64.bin
python3 - /tmp/nrc-a64/build/extract/cli.original.cjs \
          /tmp/nrc-a64/noshim/extract/cli.original.cjs <<'EOF'
import sys
a, b = (open(p, "rb").read() for p in sys.argv[1:3])
d = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
print(len(a), len(b), "differing bytes:", len(d), d)
EOF
```

Measured on 2026-08-24: both artifacts came out at the same length (the `size:`
line above), with **exactly 4 differing bytes**, at offsets
7,104,588–7,104,591 — `if(AE())try{` in the as-shipped artifact against
`if(true)try{` in the shimmed one. The same comparison on the `darwin-x64`
2.1.241 pair, run here the same day, also reported **exactly 4** differing
bytes at the same length — at a different place, offsets 7,103,971–7,103,974,
`if(Tw())try{` against `if(true)try{`. The offsets and the gate name belong to
one build each. So does the *four*: it is the length of `AE()` / `Tw()`, which
happens to equal the length of `true`, so the two artifacts also come out the
same size. A release that minified the gate to a one- or three-character name
would change both numbers.

**6. The darwin JavaScript boots — under Linux Bun.**

```bash
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" /tmp/nrc-a64/build/extract/cli.original.cjs --version
#   → 2.1.239 (Claude Code)   rc=0   (re-run here 2026-08-24)
```

**Both of them do.** The `darwin-x64` artifact was taken past `--version` on
2026-08-24, to the smoke test this repo actually recommends:

```bash
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" /tmp/nrc-x64/build/extract/cli.original.cjs mcp list
#   → No MCP servers configured. Use `claude mcp add` to add a server.   rc=0
#   (--version on the same artifact prints 2.1.241 (Claude Code), rc=0)
```

That is a real execution of the darwin builds' JavaScript, and it is
deliberately *not* claimed as evidence about macOS: `process.platform` is
`linux` in those processes, and the Mach-O `.node` addons cannot load at all
(`ERR_DLOPEN_FAILED … invalid ELF header`, measured). An Intel Mac's binary
parses, transforms and boots here exactly as an Apple Silicon one does — which
says nothing about either running *on* a Mac. The block that follows is where
that separate evidence lives, and it covers `arm64` only.

### Measured on an Apple Silicon Mac — the first macOS run

**Reported first-hand from an Apple Silicon Mac on 2026-08-24.** Nothing in this
block was run on this repository's Linux host, and nothing in it is a projection
from one. Read every figure below as *measured there*.

The environment, as reported: Bun **1.3.14** for `darwin-aarch64`, unzipped into
a home directory and **not** placed on `PATH` — the shape
[`docs/runbook.md`](docs/runbook.md) step 1 prescribes; Python **3.14.7**, driven
without a project environment; and that machine's own installed Claude Code
**2.1.239** under `~/.local/share/claude/versions/<version>`, whose byte count
is **exactly** the one the table opening [`docs/findings.md`](docs/findings.md)
records for `darwin-arm64` 2.1.239 — a copy that reached this host by an
entirely different route. The digits are in that table and are not repeated
here.

**1. The suite ran there, and passed**, with nothing failing and only the
ELF-only tests skipped — which is exactly what a host holding a Mach-O binary
and no ELF one should skip. The exact figures, and how they reconcile with this
host's, are in [the per-configuration table](#the-test-suite-and-its-counts):
this repo states a pass count in one place, and that is the place.

**2. The build matched this host's `darwin-arm64` figures byte for byte.** This
is the strongest single result in the repository, because it turns an argument
into a measurement. Every Mach-O number here had been produced by parsing a
darwin binary *on Linux*, defended on the grounds that walking a container is
byte arithmetic and therefore platform-independent. Run on the Mac, against that
machine's own installed copy, the same pipeline printed:

```
Size: 309.9 MB
Section: offset=69107712 size=255007133 (243.2 MB)
Payload: 255007125 bytes, trailer OK
Modules: 15 (entry id=0)
Extracted: 1 cli.js + 9 assets (5 loader shims left inlined in cli.js)
image shim gate        : AE
image shim call sites  : 23 -> 22
image shim applied     : 1  (expected 1)
size: 28244743 -> 28244063 bytes
```

If that block looks like a copy of step 3's transcript above, that is the
result: it is the same output, produced on a different operating system, on a
different machine, from a separately installed copy of the same Claude version.
Compare it field by field with step 3, with
[`docs/findings.md`](docs/findings.md) §3's `darwin-arm64` row and with §6's
`darwin-arm64` column — identical section offset, section size, payload size,
module count, entry id, asset count, gate name, call-site transition and both
artifact sizes. The reasoning was sound, and it is no longer only reasoning.

Two honest limits on that. The `Size:` line is the input binary and rounds to
one decimal, so it is a weaker check than the byte counts beside it. And a
figure this repo derives from *two* builds — the 4-byte diff against an
`NRC_NO_IMAGE_SHIM` artifact — was **not** reproduced there, because only one
build was run.

**3. The extractor handled addons it had never seen.** Those 9 assets include
macOS-only native addons the Linux build does not carry —
`computer-use-swift.node`, `computer-use-input.node` and `url-handler.node`,
alongside `audio-capture.node`, `image-processor.node` and the `file`-loader
assets. Nothing had to be added for them, because `extract_bun.py` decides what
to write from the **loader byte** in each module record and never from a
filename list ([`docs/findings.md`](docs/findings.md) §5a).

**4. What was then run on that Mac**, in order:

- `mcp list` reported that no MCP servers are configured, exit 0 — the smoke
  test this repo recommends over `--version`.
- The **full interactive TUI rendered**, under a real terminal, including its
  startup panels.
- The session was **authenticated against a real account, and real model
  inference answered a prompt.** That is the first Anthropic-facing traffic this
  project has any record of; everything recorded on the Linux host is still a
  loopback mock.
- An **image was attached and the model described its contents**, so image
  *input* travelled the whole path.

**5. One observed difference from the native binary.** On exit, the TUI did not
clear or restore the terminal — the session's prior scrollback stayed on screen.
Seen **once**, cosmetic, and the cause was **not investigated**. It is recorded
here because it was observed, not because it is understood.

**6. Three real defects, found only because it ran on a Mac** — all fixed and
committed at `7e9ecc1`, before the docs you are reading:

1. The **test build helpers set `HOME=OUT_DIR`**, so the first macOS process to
   want a `~/Library` created one inside the directory the test was asserting
   about — which looked exactly like `build.sh` littering the output tree. The
   helpers now hand a build a scratch `HOME` that is a *sibling* of `OUT_DIR`.
2. **`build.sh`'s auto-discovery took `sort -V | tail -1` with no usability
   check**, and a 0-byte stub left by an interrupted auto-update sorted newer
   than the working binary — described in step 2b above.
3. **The `real_elf_binary` / `real_macho_binary` fixtures accepted any path that
   existed.** One bad specimen therefore produced seven `SystemExit` tracebacks
   from inside the extractor instead of a diagnosis. They now check the magic
   bytes and *skip* with a message naming the environment variable to set.

Each is the same class of bug: a broken *host* presenting itself as a broken
*repo*. None of the three was reachable from Linux.

### Still not verified — on any host ⛔

A short list now, and each entry names the run that failed to reach it.
Everything else in the two blocks above has been executed somewhere.

- **Whether a darwin `.node` addon actually loads**, and with it whether the
  scoped image shim does the job it exists for. The Mac run does **not** settle
  this. The shim demonstrably applied there — `image shim call sites : 23 -> 22`,
  `image shim applied : 1` — and image *input* reached the model. But the native
  path that shim unlocks is what the `Read` tool needs in order to **resize** an
  image larger than 2000×2000, and an image small enough not to need resizing
  never touches it. So: the shim applied, image input worked, and **the resize
  path specifically is untested on macOS**. This stays the single most useful
  thing to send back. The direct probe is one line —
  `bun -e 'console.log(Object.keys(require("<out>/extract/assets/image-processor.node")))'`
  — and a `Read` of a deliberately oversized PNG is the end-to-end version.
- **`tools/patch_claude.py`'s re-signing (Approach B).** Untouched by the Mac
  run and unchanged by it: the `codesign` half has **never met a real
  `codesign`**. The byte-patching half is tested on Linux through
  `--no-sign`/`--dry-run` (there is no `codesign` on this host, so every signing
  path here is exercised only through its refusals). The invocation itself, the
  entitlements/identifier round trip, and whether a re-signed binary launches are
  all unverified. A prior session recorded a working Mac run on 2026-08-21
  against 2.1.238; that is a note (📓 in [`docs/status.md`](docs/status.md)), not
  a re-checked result.
- **`scripts/ab-equivalence.sh` does not run on macOS at all**, so the three-way
  A/B remains Linux-only and the Mac run says nothing about it — the script could
  not start there. That is a decision, not an oversight: its egress guard reads
  `/proc/<pid>/fd` against `/proc/net/tcp` to prove every socket a run opens is
  loopback, there is no portable substitute in it yet, and running the comparison
  without that guard would print output indistinguishable from a clean run. The
  preflight refuses up front, naming `/proc` among anything else missing.
  Verified here on 2026-08-24 only in part: hiding `bun` and `node` made the
  preflight name both in one message and exit 1 — but the `/proc` branch, the one
  a Mac takes, could not be exercised, because this host will not let `/proc` be
  hidden (`unshare` is refused). What a Mac sees there is read from the script,
  not observed.
- **Anything at all on an Intel Mac.** `darwin-x64` 2.1.241 has been downloaded,
  checksum-verified, extracted, shimmed and booted — every one of those on
  **Linux**. No Intel Mac has run any of it. Keep the two splits apart: the
  `arm64`/`x64` split is about which binaries have been *parsed* here; the
  Linux/macOS split is about which have been *executed on a Mac*, and there only
  `arm64` has.
- **Bun 1.3.14 on an Intel Mac.** The Apple Silicon asset
  `.../bun-v1.3.14/bun-darwin-aarch64.zip` is no longer a URL check — that is the
  Bun the run above used. The Intel assets
  `.../bun-v1.3.14/bun-darwin-x64.zip` and `bun-darwin-x64-baseline.zip` (for a
  pre-AVX2 Mac) are still only that: all three answered `HTTP/2 302` to `curl -I`
  from here — the redirect to GitHub's asset CDN — and `curl -IL` followed each
  to `200`, on 2026-08-24. Nothing on *this* host has ever unzipped or executed a
  darwin Bun. (An earlier revision of this line quoted the `200` without the `-L`
  that produces it.)
- **The rest of the equivalence gap, on macOS.** Everything a native install gets
  from being a Bun standalone is missing there exactly as it is on Linux
  ([`docs/findings.md`](docs/findings.md) §11) — the seccomp sandbox, embedded
  ripgrep, install identity. The Mac run exercised none of those branches
  deliberately, and `doctor` was not among the commands reported.
- **The `Makefile`.** It is written for macOS's GNU Make 3.81 and a BSD
  userland, and the constraints that make it so are enforced by
  `tests/test_makefile.py` on Linux — but the Mac run predates that file (see
  the count reconciliation below) and did not invoke `make`. No target in it has
  been run on macOS.

## Layout

```
not-rusty-claude/
├── README.md                       you are here
├── Makefile                        one entry point for setup → binary → build →
│                                   smoke → test; installs nothing on PATH and
│                                   creates no file named `claude`. Written in
│                                   the GNU Make 3.81 / BSD-userland dialect
│                                   macOS ships, enforced by tests, and not yet
│                                   run on a Mac
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

### The test suite and its counts

**No other file in this repo states these counts** — including the Apple
Silicon run's, which are reconciled below rather than written out twice.
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
this host. The counts also *move*, and fast: the rows below read 242/235/229/226
two waves ago, 259/252/246/243 one wave ago, and are none of those now — the
suite grew by `tests/test_makefile.py` on 2026-08-24. That is exactly why they
must not be written twice.

The repo's convention, of which this table is the main instance: **a measured
figure is stated in one place, and appears elsewhere only as quoted command
output, labelled with the binary and date that produced it.**

Every row below was re-measured on this host on 2026-08-24 — after
`tests/test_makefile.py` joined the tree — by forcing the row with the
environment variables named beside it. `python3 -m pytest tests/ -q
--collect-only` reports the same total, **273**, in all five configurations:
what the host has changes the skips, never the collection.

| host has | result | how the row was forced |
| --- | --- | --- |
| both binaries + Bun | **273 passed** | nothing set (this host's defaults) |
| ELF binary + Bun, no Mach-O | 266 passed, 7 skipped | `NRC_TEST_MACHO=/nonexistent/macho` |
| Mach-O binary + Bun, no ELF | 267 passed, **6** skipped | `NRC_TEST_ELF=/nonexistent/elf` |
| Bun only | 260 passed, 13 skipped | both of those two variables at once |
| none of them | 257 passed, 16 skipped | …plus `BUN_BIN=/nonexistent/bun` and a `HOME` with no Bun under it — the command below |

Every row adds up to 273, and the skips decompose: **7** tests need the Mach-O
binary, **6** need the ELF one (7 + 6 = 13), and **3** more need only a Bun
(13 + 3 = 16).

**Reconciling the Apple Silicon run's numbers with these.** That machine
reported **257 passed, 6 skipped, 0 failed, 263 collected** — a different total,
and the difference is not a mystery. It ran the branch as **committed**, which
does not yet include `tests/test_makefile.py`; re-collecting the committed tree
here gives exactly **263**, and that file's **10** tests are the whole of the gap
to 273. Its **6** skips are the third row above: a host with a Mach-O binary and
no ELF one skips the six ELF-only tests, which is precisely what this host does
when forced into the same shape. The `257` it reported and the `257` in the last
row above are an unrelated coincidence of two different totals — do not read
them as the same measurement.

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
only through its refusals — **and the 2026-08-24 Apple Silicon run did not
change that**, because it exercised the extract-and-run path only and never
invoked this tool.
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
