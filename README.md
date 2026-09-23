# not-rusty-claude

Run Claude Code on a **pre-Rust (Zig-era) Bun** — the runtime before Bun's
Zig→Rust rewrite — by extracting its JavaScript out of the native binary and
running it under a stock **Bun 1.3.14**. The signed binary is only ever *read*:
never modified, never executed by this pipeline. (`make harness` does run it -
that is what an artifact-vs-native comparison is - in a throwaway `HOME` with a
loopback mock as its API.)

> **Why.** Claude Code ships as a Bun *standalone* executable — runtime and app
> baked into one signed binary (Mach-O on macOS, ELF on Linux, PE on Windows).
> Bun is being rewritten from Zig to Rust; **1.3.14 is the last Zig release**.
> You cannot swap the embedded runtime, so instead we extract `cli.js` + assets
> and run them on an **external** Bun — and choose the Zig one.
>
> Two precisions. *Pre-Rust* describes the **rewrite of Bun's core**, not the
> binary: 1.3.14's `.comment` reads `rustc 1.94.0-nightly` and it links vendored
> Rust crates. And 1.3.14 is *sufficient*, not *necessary* — the same artifact
> also runs on Bun 1.4.0. Running on Zig is the **point**, not a constraint.

## Status — what works, and exactly how far that goes

### Claude Code 2.1.280: a new shape, verified against the native binary ✅

From **2.1.280** Claude Code is no longer one CommonJS file. It is a
**code-split ES-module graph** of 2,196 modules, built on Anthropic's *private*
Bun 1.4.3 (`@anthropic-ai/bun-internal`). That Bun adds a `Bun.ant` namespace
which stock Bun does not have, and Ink cannot draw a single cell without
`Bun.ant.CellSegmenter`. The pipeline now handles both shapes. It tells them
apart by the entry module's own format byte, rewires every
`/$bunfs/root/` reference by context, and puts an entry of its own in front
that installs a `Bun.ant` polyfill. The details are in
[`docs/findings.md`](docs/findings.md) §14.

**How it is verified: `make harness`.** `scripts/harness.py` builds the
artifact, then runs the same scenario through the native binary and the
artifact, and compares what each one did. Measured on this host on 2026-09-22
against `/usr/bin/claude` 2.1.280, under stock Bun 1.3.14, and re-run in full on
2026-09-23 (30 checks, all PASS) once the search checks were added:

| check | artifact vs native |
|---|---|
| build, structure, parse | 138,195 specifiers made relative and 485 paths made runtime-absolute; Bun's own parser accepts all 2,062 modules, and each keeps exactly the import records it had (140,332) |
| text modules | all 84 `require()` to the native string (sha256 + length) |
| `--version`, `--help`, `mcp list`, `mcp add` → `get` → `remove` → `list`, `plugin list`, `auth status` | exit code, stdout **and** stderr equal, at every step |
| `doctor` | equal bar the install-identity and search lines, which are the [equivalence gap](docs/findings.md) |
| the Claude-in-Chrome MCP server, and Claude's own config for it spawned as Claude spawns it | an MCP `initialize` answered identically |
| 9 mock-API agentic turns: text, Bash, Read, Read of a 3000×3000 PNG, Grep, Write, Glob, function hooks, and a Bash `grep`/`find` | tool results equal, and the full request bodies equal once per-run paths, session ids and the random device id are normalised. Native runs these, and the TUI, under its own Glob/Grep opt-in; `search-optin` pins what that opt-in changes on native in a `-p` turn |
| TUI under a pty: onboarding, a REPL turn, a REPL turn full of CJK, emoji, bidi text, a table and a code block | screens identical cell for cell, **styles and hyperlinks included** — bar the randomly chosen spinner verb and the clock, and onboarding compared below its randomly sparkling logo; same requests; the REPL exits 0 with the terminal restored |
| `Bun.ant`, probed inside the native runtime with a real peer process | 27 answers identical |
| `CellSegmenter`, fuzzed against the native class | 0 mismatches (3,000 cases per harness run; 610,000 in development) |

**What it is not.** The same gaps apply as before: the sandbox is off, ripgrep
is the system `rg` and install identity reads `unknown`. Search runs in the
configuration native uses under its own Glob/Grep opt-in, not its default: the **Glob** and
**Grep** tools are offered, and a Bash `grep`/`find` is the system's. Bun has
no embedded ugrep or bfs, and before 2026-09-23 every Bash `grep` printed Bun's
help ([findings §10](docs/findings.md), *Embedded search*). On top of that,
startup is **3–8× slower** than native (`--help` takes 1.2 s against 0.16 s),
because the native runtime runs JSC bytecode that a different WebKit build
cannot load. A code-split build does **not** run under Node yet
([§ Under Node](#under-node-instead-of-bun)). The table below is the record for
the legacy (single-file) builds up to 2.1.241.

Verified on Linux x86_64 (Debian 12, glibc 2.36); commands and output in
[`docs/verification-2026-08-22.md`](docs/verification-2026-08-22.md) and
[`docs/findings.md`](docs/findings.md). The table is measured **on this Linux
host**, `darwin` column included — parsing a Mach-O is byte arithmetic and needs
no Mac. On 2026-08-24 the pipeline was also run **on an Apple Silicon Mac**
([its own block](#2-measured-on-an-apple-silicon-mac)).

| Piece | `linux-x64` (ELF, 2.1.222) | `darwin` (Mach-O): `arm64` 2.1.239 · `x64` 2.1.241 | `win32-x64` (PE, 2.1.239) |
|---|---|---|---|
| Extract `cli.js` + assets (`extract_bun.py`) | ✅ | ✅ **both architectures** · 🍎 also on the Mac, `arm64`, same figures | ⛔ refused by design |
| Post-process (`postprocess.py`) | ✅ | ✅ both · 🍎 also on the Mac, `arm64` | ⛔ |
| `scripts/build.sh` end to end | ✅ | ✅ both · 🍎 also on the Mac, `arm64` | ⛔ |
| Output accepted by Bun 1.3.14's own parser | 🔎 exit 0 | 🔎 exit 0, both | ⛔ |
| **Runs under external Bun** | ✅ `doctor`, `mcp list`, `--help`, `--version` — on **1.3.14 *and* 1.4.0** | ✅ both boot under **Linux** Bun → `2.1.239` / `2.1.241 (Claude Code)`; the `x64` build also answers `mcp list`. 🍎 the `arm64` build also runs **on** a Mac: `mcp list`, the interactive TUI, an authenticated session against a real account | ⛔ would also need a Windows Bun |
| Runtime asset loading | ✅ `image-processor.node` loads, works, **and is reached by the CLI** — a `Read` of a 3000×3000 PNG returns a JPEG | 🖥️ Mach-O addons cannot load on Linux, and the Mac run did **not** close it: the shim applied and image *input* worked, but the resize path was never exercised | ⛔ |
| Behaves the same as the shipped binary | ⚠️ **no** — see the equivalence gap below | ⚠️ no | ⛔ |

✅ executed here · 🍎 executed on the reporting Apple Silicon Mac, 2026-08-24 ·
🔎 static check only · 🖥️ needs hardware we do not have · ⚠️ measured difference
from the native binary · ⛔ deliberately not implemented

**The headline result:** the extracted, post-processed `cli.original.cjs` from
the real Linux binary runs under vanilla Bun 1.3.14 and answers `doctor` and
`mcp list` (which really read and write `.claude.json`), plus `--help` and
`--version`. Driven by a **loopback mock** of the Messages API it also completes
a full agentic loop — SSE streaming, multi-turn tool use, the Bash tool spawning
a real subprocess, the Read tool returning a resized image — and renders the Ink
TUI under a pty, with no Bun-API failure anywhere. A positive answer **for
Claude Code 2.1.222, on Linux** — a measurement, not a guarantee.

> **Don't lead with `--version`.** It initialises **0** lazy modules — a
> hardcoded fast path — so it proves the file parses and nothing at all about
> Bun's API surface. `doctor` and `mcp list` initialise thousands; the
> per-command counts are [`docs/findings.md`](docs/findings.md) §9's table.

**⚠️ The equivalence gap — read this before using it.**
`Bun.isStandaloneExecutable` is undefined outside a standalone, so the CLI takes
its non-standalone branch at every site that asks (how many sites per binary is a
row of [`docs/findings.md`](docs/findings.md) §6's table). Measured consequences:
the **seccomp sandbox is off**, embedded ripgrep becomes a **system `rg`** (so
`rg` is a de facto prerequisite), and install identity reports `unknown`. One
more gap is not a runtime check at all. Claude's embedded bfs/ugrep is switched
on by a build-time constant, so `postprocess.py` switches it off. Glob and Grep
are then offered, and Bash `grep`/`find` are the system's, exactly as native
runs when an `--allowedTools` or `--tools` entry names Glob or Grep. Both
addon loaders swallow failure, so **exit 0 is not evidence that the asset wiring
works**.

**One of those gaps is closed.** `postprocess.py` rewrites the *single* gate
call that guards native image processing, so a default build resizes a large
image instead of erroring — scoped to that one call site because flipping
`Bun.isStandaloneExecutable` globally is measured to make `Grep` answer `No
matches found` for a string that exists: a wrong answer, not an error.
`NRC_NO_IMAGE_SHIM` (any non-empty value) builds the old artifact, bar the
embedded-search gate rewrite, which has no opt-out;
`scripts/ab-equivalence.sh` reproduces the three-way A/B against a committed
loopback mock ([`docs/findings.md`](docs/findings.md) §10, Linux-only).

**What is honestly not known.** No request from **this host's** build has ever
gone to Anthropic: every agentic-loop result here was driven by a loopback mock,
and the one authenticated session happened on the reporting Mac. Unsettled
everywhere: whether a darwin `.node` addon actually loads — the native image
*resize* path, the one thing that would prove it, was never exercised — and the
A/B harness, which cannot run on macOS at all. Intel Macs are untouched:
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
  "$HOME/.bun-1.3.14/bun" build/extract/cli.js mcp list
#   → No MCP servers configured. Use `claude mcp add` to add a server.
#   cli.js is the entry for both shapes: the code-split build's own entry
#   (2.1.280+), or the sibling that requires cli.original.cjs (legacy builds).

# 4. optional: judge the artifact against the native binary, check by check
make harness
```

**Three safety properties, all deliberate.** `build.sh` **installs nothing on
`PATH`** — a file named `claude` there could shadow your real installation, so it
prints the full-path command instead; **no file named `claude` is ever created**,
anywhere, including by the download flow; and the native binary is only ever
**read**. (It does write a one-line `cli.js` beside `cli.original.cjs`, because
Claude's own code resolves a sibling `cli.js` for two MCP self-spawns.)

**No native `claude` on this machine?** Download one — no install, no npm, no
Mac needed. The manifest lists **eight** platforms, `linux-x64` and `linux-arm64`
among them plus `-musl` variants; the runnable fetch → verify →
delete-on-mismatch flow is [`docs/findings.md`](docs/findings.md) §8. The
legacy Linux figures came from the pre-installed `/usr/bin/claude` 2.1.222;
since that became 2.1.280, the legacy-shape tests run on a downloaded 2.1.231
(`~/.cache/not-rusty-claude/claude-linux-x64.bin`), extracted here like any
other.

One consequence to know about: under a native install `process.execPath` *is*
`claude`; here it is **bun**, and the CLI unconditionally exports
`CLAUDE_CODE_EXECPATH=<bun>` into every shell it spawns. Setting that variable
yourself does nothing — the CLI never reads it
([`docs/runbook.md`](docs/runbook.md) § Shell integrations). The shell
functions that ran it as `grep` and `find` are gone from every build since
2026-09-23. On an older artifact, start it with
`--allowedTools='Glob(/nonexistent/**)'`: naming Glob switches Claude's own opt-in on,
and a rule for a directory that does not exist grants nothing. A bare
`--allowedTools=Grep` works too, but it also pre-approves Grep on every path. Step-by-step and
troubleshooting: [`docs/runbook.md`](docs/runbook.md); on a Mac, read the
section below first.

### Under Node instead of Bun

**A code-split build (2.1.280+) does not run under Node yet**, and `make
node-run` refuses one and says why. There are three walls, measured with Node
24.21.0. Node has no `import.meta.require`, which the bundle calls at 445
sites. Node's ES-module resolver ignores `NODE_PATH`, which is how `ws` and
`undici` are supplied. The third is structural: Node refuses to `require()` an
ES module inside an import cycle that is still evaluating
(`ERR_REQUIRE_CYCLE_MODULE`), and Bun allows it. A preload hook gets past the
first two; the third would mean changing module evaluation order. What follows
is the record for legacy builds.

Legacy builds: yes — **Node ≥ 24 only**: the bundle's `using` declarations (ES explicit
resource management) are a parse error before that — `node --check` fails on
22.23.2 and 23.11.1, passes on 24.0.0 and 26.7.0. Node also has no `ws`, no
`undici` and no `Bun` global; the targets below and `scripts/bun-shim.cjs`
supply all three. The two modules come from npm, so they carry the registry's
integrity guarantee rather than a pinned sha256 like the other downloads, and
are installed `--ignore-scripts`.

```bash
make node-deps                          # ws + undici from npm into ~/.cache, not this repo
make node-run NODE_BIN=/path/to/node24  # node --require ./scripts/bun-shim.cjs …
#   → No MCP servers configured. Use `claude mcp add` to add a server.
```

Measured 2026-08-25, Bun 1.3.14 against Node 24.0.0 and 26.7.0: `--version`,
`--help`, `mcp list` and `config ls` print **byte-identical stdout with equal
exit codes**; `doctor` differs in one line, the `Path:` naming the interpreter
actually running. Where the shim cannot match Bun (`spawn`, `file`, `SQL`, …)
it throws naming the API rather than guessing. Detail:
[`docs/findings.md`](docs/findings.md) §11, whose "command surface only" line predates
the interactive runs described next.

The **interactive TUI works too**. Onboarding ✅ was driven through a pty here on
2026-08-26 under Node 24.19.0 — welcome banner, a keystroke at the theme picker, syntax
preview, login selector, all responsive. The **authenticated REPL** 🍎 was confirmed on
Apple Silicon with the reporter's own `~/.claude`, same date. Those are different code
paths and different machines, which is the whole story below.

Finding out why the REPL would not paint took a day. On that Mac it drew nothing,
ignored Ctrl-C and looked idle rather than stuck, while the same machine and binary
rendered onboarding perfectly with a scratch `CLAUDE_CONFIG_DIR`. Seven explanations
were raised and killed by evidence — native addons, `Bun.Terminal`, a fullscreen
renderer, a keychain freeze, MCP servers, a missing `ripgrep`, an eight-version bundle
gap — before `scripts/node-trace.cjs` watched the object the shim installs and named it
in one line: the REPL calls **`Bun.YAML.parse`** and **`Bun.wrapAnsi`**, the shim refused
both, and a React error boundary swallowed every throw. Refusing loudly only helps if
something can hear it.

Both are implemented now, measured against Bun as oracle rather than written from memory
— `wrapAnsi` byte-equal over 4,900 cases, `YAML.parse` matching on 141 of 178 and
refusing 18 by name, with **zero** inputs accepted and parsed differently. A first
version of both passed a smaller corpus and still carried thirteen real defects, found
by review: the corpora were extended until they covered the shapes that had hidden them
(multi-parameter SGR, `#` after an apostrophe, top-level flow collections).

⚠️ **Still refused**, by name rather than by silent misparse: frontmatter using an
anchor, a tag, a complex key, several documents, tab indentation, an explicit block
scalar indent or an over-indented sequence entry. If a TUI will not paint,
`scripts/node-trace.cjs` prints `THREW` with the API and the reason
([`docs/runbook.md`](docs/runbook.md) § Diagnosing a Node hang).

## macOS

**Three levels of confidence, kept apart on purpose:** block 1 was never
evidence for block 2, and block 2 is not evidence for block 3. This repository's
own host is **Linux x86_64 with no Mac and no way to emulate one** — re-checked
2026-08-24: no `/dev/kvm`, no `vmx`/`svm` in `/proc/cpuinfo`, no `modprobe`,
`unshare` refused ([`docs/status.md`](docs/status.md) § macOS execution).

### 1. Measured here on Linux ✅ — obtain, extract, shim

Downloading a file, parsing a container and rewriting JavaScript are byte
arithmetic; none of this needs a Mac. Get the binary from Anthropic's own
endpoint with the checksum verified *inside* the command flow
([`docs/findings.md`](docs/findings.md) §8), using `P=darwin-arm64` for Apple
Silicon or `P=darwin-x64` for an Intel Mac — nothing else changes between them.
Save it under a name that is **not** `claude`.

**Already have Claude Code on that Mac?** Then download nothing: run
`scripts/build.sh` with no argument and it finds the installed binary itself,
skipping the 0-byte stub an interrupted auto-update leaves
([`docs/runbook.md`](docs/runbook.md) § Prerequisites).

**Extract and post-process it — on Linux**, with the same
`BUN_BIN=… OUT_DIR=… scripts/build.sh <mach-o>` as anywhere else.
`extract_bun.py` finds the payload by walking the Mach-O load commands
(`LC_SEGMENT_64`) to the `__BUN,__bun` section — the counterpart of the ELF
section-header walk it does on Linux
([`docs/bun-section-format.md`](docs/bun-section-format.md)). PE is refused.

Both architectures went through the identical command on 2026-08-24 — the
`darwin-x64` 2.1.241 one being the first Mach-O this repo measured that was not
arm64. Same shape, different offsets, a different gate name (`AE` against `Tw`),
the same `23 -> 22` call-site transition and the *same* module and asset counts:
no architecture switch anywhere in the pipeline. `bun build --no-bundle`
accepted both (rc 0), as did the secondary JSC check. Every figure those runs
printed is a column of [`docs/findings.md`](docs/findings.md) §6.

**The scoped image shim applies on darwin too**, and only to the one call site,
leaving every other `Bun.isStandaloneExecutable` gate — ripgrep, sandbox,
updater — false. Rebuild with `NRC_NO_IMAGE_SHIM=1` and the two artifacts come
out at the same length with **exactly 4 differing bytes**: `if(AE())try{` (or
`if(Tw())try{`) against `if(true)try{`. The *four* is the length of `AE()`,
which happens to equal `true`; a release minifying the gate to a one- or
three-character name would change both numbers.

**The darwin JavaScript boots — under Linux Bun.** Both artifacts answer
`mcp list` (`No MCP servers configured…`, rc 0) and print `2.1.241` /
`2.1.239 (Claude Code)`. Real execution of the darwin builds' JavaScript, and
deliberately *not* evidence about macOS: `process.platform` is `linux` there and
the Mach-O `.node` addons cannot load at all (`ERR_DLOPEN_FAILED … invalid ELF
header`, measured).

### 2. Measured on an Apple Silicon Mac

**Reported first-hand on 2026-08-24**, the first time any part of this project
executed on macOS; nothing here was run on this repository's Linux host. The
environment, as reported: Bun **1.3.14** for `darwin-aarch64`, unzipped into a
home directory and **not** on `PATH`; Python **3.14.7**; and that machine's own
installed Claude Code **2.1.239**, whose byte count is **exactly** the one
[`docs/findings.md`](docs/findings.md)'s opening table records for
`darwin-arm64` 2.1.239 — a copy that reached this host by a different route.

1. **The suite ran there, and passed**, with nothing failing and only the
   ELF-only tests skipped — exactly what a host holding a Mach-O binary and no
   ELF one should skip
   ([figures](#the-test-suite-and-its-counts)).

2. **The build matched this host's `darwin-arm64` figures byte for byte** — the
   whole `darwin-arm64` column of [`docs/findings.md`](docs/findings.md) §6, plus
   §2's section offset, section size, payload size, module count and entry id,
   reproduced on another operating system from a separately installed copy. That
   turns the cross-platform argument into a measurement; its one limit is that
   the 4-byte diff needs *two* builds, and only one was run there.

3. **The extractor handled addons it had never seen** — those 9 assets include
   `computer-use-swift.node`, `computer-use-input.node` and `url-handler.node`,
   which the Linux build does not carry. Nothing had to be added, because
   `extract_bun.py` decides what to write from the **loader byte** in each module
   record and never from a filename list
   ([`docs/findings.md`](docs/findings.md) §5a).

4. **What was then run**, in order: `mcp list`, exit 0; the **full interactive
   TUI**, under a real terminal; a session **authenticated against a real account
   with real model inference answering a prompt** — the first Anthropic-facing
   traffic this project has any record of; and an **image attached and described
   by the model**, so image *input* travelled the whole path.

5. **One observed difference from the native binary.** On exit the TUI did not
   clear or restore the terminal. Seen **once**, cosmetic, cause **not
   investigated** — recorded because it was observed, not because it is
   understood.

6. **Three real defects, found only because it ran on a Mac**, all fixed at
   `7e9ecc1`: test helpers set `HOME=OUT_DIR`, so the first macOS process wanting
   a `~/Library` created one inside the directory the test was asserting about;
   `build.sh`'s auto-discovery took `sort -V | tail -1`, and an interrupted
   auto-update had left a **0-byte** `versions/` entry sorting *newer* than the
   working install; and the real-binary fixtures accepted any path that existed,
   so one bad specimen produced seven `SystemExit` tracebacks instead of a
   diagnosis. Each is the same class of bug — a broken *host* presenting as a
   broken *repo* — and none was reachable from Linux.

### 3. Still not verified — on any host ⛔

Everything else in the two blocks above has been executed somewhere;
[`docs/status.md`](docs/status.md) § macOS execution carries the same list with
its consequences.

- **Whether a darwin `.node` addon actually loads**, and with it whether the
  scoped image shim does the job it exists for. The shim demonstrably applied on
  the Mac and image *input* reached the model — but the native path it unlocks is
  what the `Read` tool needs in order to **resize** an image larger than
  2000×2000, and a smaller image never touches it. The single most useful thing
  to send back. The direct probe is
  `bun -e 'console.log(Object.keys(require("<out>/extract/assets/image-processor.node")))'`
  — run it from a **script file** if it prints nothing, because on 1.3.14
  `bun -e` swallows a failing `require()` and exits 0 — and a `Read` of a
  deliberately oversized PNG is the end-to-end version.
- **`scripts/ab-equivalence.sh` on macOS**, which cannot start there by design,
  so the three-way A/B is Linux-only.
- **Anything at all on an Intel Mac.** `darwin-x64` 2.1.241 has been downloaded,
  checksum-verified, extracted, shimmed and booted — every one of those on
  **Linux**. Bun 1.3.14's Intel assets are still only URL checks
  ([`docs/runbook.md`](docs/runbook.md) step 1); nothing on *this* host has ever
  unzipped or executed a darwin Bun.
- **The rest of the equivalence gap on macOS** — sandbox, ripgrep, install
  identity, and search, where a Bash `grep`/`find` is BSD's — and **the `Makefile`**, whose macOS dialect is enforced by
  `tests/test_makefile.py` on Linux but which the Mac run, predating that file,
  never invoked.

## Layout

```
not-rusty-claude/
├── Makefile                        setup → binary → build → smoke → test
├── docs/
│   ├── status.md                   ← what is verified on what; the remaining gaps
│   ├── findings.md                 measured facts about the binary and the transforms
│   ├── bun-section-format.md       byte-level spec (Mach-O / ELF / PE containers)
│   ├── runbook.md                  step-by-step, Linux and macOS
│   ├── verification-2026-08-22.md  the evidence record: commands + pasted output
│   └── superpowers/specs/          designs of record, one per change
├── tools/
│   ├── extract_bun.py              extract cli.js + assets from the Bun section
│   └── postprocess.py              make cli.js runnable under an external Bun,
│                                   plus the scoped image shim (findings §10);
│                                   rewires a code-split tree (findings §14)
├── scripts/
│   ├── build.sh                    extract → post-process → print the run command
│   ├── harness.py                  artifact-vs-native verification (make harness)
│   ├── vtscreen.py                 a small terminal emulator the harness reads TUIs with
│   ├── bun-ant.mjs                 Bun.ant polyfill for the code-split (2.1.280+) builds
│   ├── bun-ant-cell-segmenter.mjs  …its CellSegmenter, fuzzed against the native class
│   ├── bun-shim.cjs                globalThis.Bun stand-in, so Node ≥ 24 can run it
│   ├── trim-config.py              bisect a global config that breaks startup
│   ├── node-trace.cjs              diagnostic preload: what blocked, and where
│   ├── ab-equivalence.sh           the findings §10 A/B (Linux-only: /proc)
│   ├── mock-messages-api.mjs       loopback-only mock of the Messages API
│   └── syntax-check.js             fast secondary syntax check (JSC, not Bun)
└── tests/                          hermetic fixtures + real-binary integration
```

The tools need no third-party packages — stock `python3` (3.9+) is enough; the
test suite additionally needs `pytest`. Neither `make` nor `build.sh` installs
anything on `PATH`, or creates a file named `claude`.

### The test suite and its counts

**No other file in this repo states these counts**, including the Apple Silicon
run's, reconciled below rather than written out twice — the repo's convention
being that **a measured figure is stated in one place, and appears elsewhere
only as quoted command output labelled with the binary and date that produced
it.** These counts *move*, in both directions, as test files are added and removed —
which is exactly why. Every row was re-measured here on 2026-09-23
by forcing it with the variables named beside it; `--collect-only` reports the
same total, **378**, in all six configurations, because what the host has
changes the skips, never the collection. "Binaries" are three now: a legacy
(single-file) ELF, a code-split ELF (2.1.280+) and the Mach-O. The first row
had all three - the cached 2.1.231 download, `/usr/bin/claude` 2.1.280 and
`darwin-arm64` 2.1.239 - plus, for the Node tests, a legacy artifact built from
the 2.1.231 download and named by `NRC_TEST_ARTIFACT`.

| host has | result | how the row was forced |
| --- | --- | --- |
| all three binaries, Bun, Node 24 | **378 passed** | `NRC_TEST_NODE=…/node-v24.21.0-linux-x64/bin/node` (this host's own `node` is 22.23.2) |
| …no Mach-O | 373 passed, 5 skipped | `NRC_TEST_MACHO=/nonexistent/macho` |
| …no ELF | 371 passed, 7 skipped | `NRC_TEST_ELF=/nonexistent/elf NRC_TEST_ESM=/nonexistent/esm` |
| …no binary at all | 366 passed, 12 skipped | all three of those variables at once |
| …and no Bun | 256 passed, 122 skipped | …plus `BUN_BIN=/nonexistent/bun` and a `HOME` with no Bun under it |
| none of them, Node 22 | 227 passed, 151 skipped | …and drop `NRC_TEST_NODE` — the command below |

Every row adds up to 378, and the skips decompose — counted from each run's own
`-rs` skip reasons, not inferred from the totals. **5** tests need the Mach-O
binary, **5** a legacy ELF and **2** a code-split ELF, and the three sets are
disjoint, which is why the fourth row skips exactly 12. Removing Bun while Node
24 is still present skips a further **104**, and moving `HOME` takes
`ws`+`undici` with it for another **6**: 12 + 104 + 6 = 122. Dropping to Node 22
changes which check fires first, so the last row is not the previous one plus
a constant. **63** tests skip for Node ≥ 24, of which **28** also want Bun and
**6** also want `ws`+`undici`, leaving **29** that want only the newer Node; the
other **76** Bun-wanting tests still skip for Bun. 12 + 63 + 76 = 151.

**The Apple Silicon run is not reconcilable to this table, and should not be.**
It reported **257 passed, 6 skipped, 0 failed, 263 collected** — a true
measurement of the tree as it stood on 2026-08-24, whose test set is not
today's. No arithmetic connects 263 to 378 and none is offered. What the Mac run
established is in [§ macOS](#macos); its totals belong to the tree it ran on.

The last two rows need care twice over. `BUN_BIN` is a *first* choice, not an
override — the fixture still falls back to `~/.bun-1.3.14/bun` and then to `bun`
on `PATH`, so pointing it at a nonexistent file while `HOME` stays put
reproduces the Bun-**only** row instead. And moving `HOME` can take `pytest`
with it (here it is a `--user` install), so put it back explicitly:

```bash
# the "none of them" row, as run. $(...) is evaluated before HOME is replaced.
PYTHONPATH="$(python3 -m site --user-site)" \
NRC_TEST_ELF=/nonexistent/elf NRC_TEST_ESM=/nonexistent/esm \
NRC_TEST_MACHO=/nonexistent/macho \
BUN_BIN=/nonexistent/bun HOME="$(mktemp -d)" \
  python3 -m pytest tests/ -q
```

(Where pytest is installed system-wide, `--user-site` names a directory that
need not exist and the `PYTHONPATH` is a harmless no-op.)

Point the tests at binaries with `NRC_TEST_ELF` (a **legacy**-shape ELF; by
default the first of `/usr/bin/claude` and the cached
`~/.cache/not-rusty-claude/claude-linux-x64.bin` whose entry module is CommonJS),
`NRC_TEST_ESM` (a **code-split** ELF, 2.1.280+; default `/usr/bin/claude`) and
`NRC_TEST_MACHO` (default `/tmp/ccmac/package/claude-darwin-arm64.bin`; the older
`/tmp/ccmac/package/claude` is still accepted), and at a Bun with `BUN_BIN`
(default `~/.bun-1.3.14/bun`, then `bun` on `PATH`). The integration tests'
hardcoded counts are a tripwire for the next Claude release
([`docs/status.md`](docs/status.md)) — and so is this table: a stale total here
disarms it, because "the number changed" stops meaning anything.

## The one risk to know

Anthropic builds Claude with Bun's *canary* channel. For 2.1.222 that turned out
fine, with room to spare: the same entry module also runs on **1.3.13** in a
pragma-preserving build shape, so the "Bun ≥ 1.3.14" floor is a property of
*this project's transform*, not of Claude. But if a future Claude build uses Bun
APIs newer than 1.3.14, its `cli.js` will not run on Zig, and the only newer Bun
is the Rust rewrite. That defeats the goal for that version.

The signal to watch for is a **missing-API error**. `Expected CommonJS module to
have a function wrapper` is *not* a reliable canary — this project's own
transform produces that exact panic when the pragma/IIFE shape is wrong. Keep the
last working `build/extract/` and pin that Claude version
([`docs/findings.md`](docs/findings.md) §9). A cheaper surprise: nothing here is
constant across versions or platforms — 8 modules on linux, 15 on darwin, and
even the entry module's *name* differs, which is why extraction keys off
`entry_point_id` ([`docs/findings.md`](docs/findings.md) §4).

## Prior art

[ClawGod](https://github.com/0Chencc/clawgod) (extract + run under Bun),
[bun-demincer](https://github.com/vicnaum/bun-demincer),
[unbuned](https://github.com/vibheksoni/unbuned),
[bun-decompile](https://github.com/lafkpages/bun-decompile),
[tweakcc](https://github.com/Piebald-AI/tweakcc). Comparison in
[`docs/findings.md`](docs/findings.md) §7 — including a correction: this README
used to claim ClawGod's transforms "silently do nothing on current builds";
measured, they do not.

## Not affiliated with Anthropic. For personal, local use; you run modified code
at your own risk.
