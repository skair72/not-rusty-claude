# Runbook — run Claude Code's JavaScript on Zig-era Bun 1.3.14

Step by step, from a native Claude Code install to `cli.original.cjs` running
under a stock external **Bun 1.3.14** (the last Zig release before Bun's Zig→Rust
rewrite). The native binary is only ever *read* — never modified, not executed by
this pipeline.

Where each platform stands:

- **Linux x64** — the whole path below was executed on 2026-08-22 against Claude
  Code 2.1.222, output pasted in
  [verification-2026-08-22.md](./verification-2026-08-22.md).
- **macOS — Apple Silicon** — steps 1–4 have been run **on** a Mac, reported
  first-hand on 2026-08-24 against that machine's own 2.1.239, ending in
  `mcp list` at rc 0, the interactive TUI and an authenticated session. Not a
  measurement made on this host. Two things that run did **not** cover:
  `scripts/ab-equivalence.sh`, which cannot run on macOS at all, and a `Read` of
  an image large enough to need the native resizer.
- **macOS — Intel** — steps 0–3 were executed *here*, on Linux, against the real
  `darwin-x64` 2.1.241 Mach-O, and step 4 here under **Linux** Bun. No step has
  been run **on** an Intel Mac.
- **Windows** — not supported; extraction refuses PE input by design
  ([status.md](./status.md) § Windows/PE).

[README's macOS section](../README.md#macos) separates all of this command by
command, marking which machine each was run on.

---

## 0. Prerequisites

- A native Claude Code install, or any copy of the native binary:
  - Linux: usually `/usr/bin/claude`, or `~/.local/share/claude/versions/<v>`.
  - macOS: `~/.local/share/claude/versions/<v>`, either architecture.
  - Or download one without installing anything, from Anthropic's own endpoint,
    with the sha256 from its `manifest.json` checked *in the same command flow* —
    the runnable form is [findings.md](./findings.md) §8, and it takes
    `P=darwin-arm64`, `P=darwin-x64`, `P=linux-x64` and five more alike. Save it
    under a name that is **not** `claude`, and do not pipe `install.sh` to `bash`
    — that installs.

  **With an install present you need no argument at all.** `scripts/build.sh`
  probes `${XDG_DATA_HOME:-$HOME/.local/share}/claude/versions/*`, walks the
  entries **newest-first** and takes the first that is at least 1 MiB, naming
  anything it skipped, then falls back to `command -v claude`. Re-measured here
  2026-08-24 against a fake tree of `2.1.9`, `2.1.238` and a 0-byte `2.1.999`: it
  warned about `2.1.999` and chose `2.1.238`. That size check exists because a
  real Mac had exactly such a stub — an interrupted auto-update leaves one, and
  it sorts *newest*, so the old "highest by `sort -V`" rule handed the extractor
  an empty file and the resulting complaint read like a bug in this repo rather
  than a broken install on the host.
- `python3` (stock `/usr/bin/python3` is fine — the tools target 3.9+, no
  third-party packages; the test suite additionally wants `pytest`).
- **`ripgrep` on `PATH`.** Not optional here, though it is optional for a native
  install: the CLI only uses its *embedded* ripgrep when it detects it is a Bun
  standalone, which this build is not ([findings.md](./findings.md) §10).
- This repo checked out. Nothing needs installing from it.

> **A shortcut for the whole sequence.** The `Makefile` wraps steps 1–4 as
> `make setup`, `make binary`, `make build`, `make smoke` and `make test` (`make`
> alone prints the list and changes nothing; `make first-run` is those five in
> order). It installs nothing on `PATH` and creates no file named `claude`. It is
> written in the GNU Make 3.81 / BSD-userland dialect macOS ships and
> `tests/test_makefile.py` enforces that on Linux — but **no target in it has
> been run on a Mac**. The steps below are what it drives, and remain the
> reference.

Every step here works on Linux **and** on Apple Silicon macOS except one, which
is Linux-only by decision: `scripts/ab-equivalence.sh`, the §10 A/B. Its egress
guard reads `/proc/<pid>/fd` against `/proc/net/tcp`, and it refuses to start
where those are unreadable rather than run the comparison with its safety net
silently missing. It also requires `timeout(1)` (or `gtimeout`), `node` for the
loopback mock and `rg` on `PATH`; a preflight names everything missing in one
message.

Locate the binary and confirm it is a Bun standalone:

```bash
# Linux
NATIVE=/usr/bin/claude
readelf -S "$NATIVE" | grep '\.bun'          # expect a .bun section

# macOS
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
NATIVE="$(ls -1d "$DATA"/claude/versions/* | sort -V | tail -1)"
otool -l "$NATIVE" | grep -A2 __BUN          # expect __BUN / __bun
```

> **Safety:** the pipeline reads this file and nothing else. Do not point it at a
> binary you are unwilling to have read, and do not let any step write to it.

---

## 1. Install Bun 1.3.14 — without touching `PATH`

1.3.14 is the **last Zig release**, and the minimum that loads the artifact
**this project builds** (older Bun panics with *"Expected CommonJS module to have
a function wrapper"*). Two honest caveats ✅: that floor is ours, not Claude's —
the same entry module rebuilt in the pragma-preserving shape runs fine on
**1.3.13** — and the artifact does not *require* a Zig Bun at all, since it also
runs on **1.4.0**, the Rust build ([findings.md](./findings.md) §6 and §9).

A plain unpack, no installer script, no rc-file edits, not on `PATH` — exactly
what the verification run did:

```bash
mkdir -p "$HOME/.bun-1.3.14"
curl -fsSL -o /tmp/bun-1.3.14.zip \
  https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-linux-x64.zip
#   macOS arm64:  .../bun-v1.3.14/bun-darwin-aarch64.zip
#     No longer just a URL: the 2026-08-24 Apple Silicon run unzipped this into
#     a home directory, kept it off PATH, and ran the whole pipeline under it.
#   macOS Intel:  .../bun-v1.3.14/bun-darwin-x64.zip
#     (…-x64-baseline.zip on a pre-AVX2 Mac). Both Intel assets are still only
#     URL checks - 302 -> 200 to curl -IL here on 2026-08-24, nothing unzipped.
#     Nothing on THIS host has ever unzipped or run any darwin Bun.
unzip -o -j /tmp/bun-1.3.14.zip 'bun-linux-x64/bun' -d "$HOME/.bun-1.3.14"
chmod +x "$HOME/.bun-1.3.14/bun"
export BUN_BIN="$HOME/.bun-1.3.14/bun"
"$BUN_BIN" --version          # → 1.3.14
```

The upstream installer (`curl -fsSL https://bun.sh/install | bash -s
"bun-v1.3.14"`) also works, but it writes to `~/.bun` and edits shell rc files.
The unpack above keeps this experiment self-contained.

> On a CPU without AVX2, use the `-baseline` build. Bun 1.3.14 is the last
> release built from the Zig codebase on every platform; `bun-v1.4.0` shipped
> 2026-08-20 as the first Rust release targeting all supported platforms
> ([findings.md](./findings.md) §1). "Zig-era" does not mean Rust-free — 1.3.14
> links vendored Rust crates; what it predates is the rewrite of Bun's own core.

---

## 2. Build the artifacts

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh "$NATIVE"
#   OUT_DIR=/somewhere      to put artifacts elsewhere (default: ./build)
#   NRC_NO_IMAGE_SHIM=1     build the "as shipped" artifact, without the scoped
#                           image shim. ANY non-empty value opts out.
```

`build.sh` extracts, post-processes, and then **stops**. It installs nothing — no
launcher, nothing on `PATH` — because a file named `claude` on `PATH` could
shadow a real installation. It prints the exact command to run instead.

Expect output like this (Linux 2.1.222; your numbers will differ per platform and
version — [findings.md](./findings.md) §4 and §6):

```
Modules: 8 (entry id=0)
Extracted: 1 cli.js + 5 assets (2 loader shims left inlined in cli.js)
pragma block stripped  : 1
/$bunfs/ paths rewired : 5
file:// leaks rewritten: 7
IIFE invocations added : 1  (expected 1)
image shim gate        : CE
image shim call sites  : 21 -> 20
image shim applied     : 1  (expected 1)
size: 22960130 -> 22959448 bytes
wrote: .../.extract.stage.NNNN/cli.original.cjs
wrote: .../.extract.stage.NNNN/cli.js  (sibling for Claude's MCP self-spawns)
==> image shim APPLIED: the native image-processor branch is reachable,
==>   which is what the Read tool needs to resize a large image. Every other
==>   isStandaloneExecutable gate (ripgrep, sandbox, updater) stays false.
==> staged build swapped into place -> .../build/extract
```

(Quoted from a run on this host against `/usr/bin/claude`, re-checked 2026-08-24.
Every number belongs to `linux-x64` 2.1.222 — the gate's minified name included.
The darwin builds print a **different** gate name and their own offsets; all
three are tabulated side by side in [findings.md](./findings.md) §6. Do not
expect the name you see to match any of them: it is captured from the module at
build time precisely because it is not a constant. The `wrote:` lines name the
**staging** directory; `build.sh` prints the final paths immediately afterwards.
The `cli.js` sibling is not optional — Claude's own code resolves
`join(__filename,'..','cli.js')` for two MCP self-spawns.)

**Read the counts.** `image shim applied` must be **1** in a default build; `0`
is not a build failure — the artifact is exactly as good as every artifact this
repo shipped before the shim existed — but it means the Read tool will refuse an
oversized image, so the script says so out loud either way and adjusts its
closing list of gaps. `IIFE invocations added` must be exactly 1; if it is not,
`postprocess.py` refuses to write the output rather than handing Bun a file that
fails with a confusing panic. A surviving `/$bunfs/` (or Windows `B:/~BUN/`)
reference is refused the same way. `note: build-machine path still present`
lines *are* informational — string literals containing Anthropic's build paths
that are not `/$bunfs/` references (how many, per binary, is a row of §6's
table). All notes print before `wrote:`, so anything after that line is the
artifact, not a complaint about it.

`build.sh` needs Bun only to *report* its version — extraction and
post-processing are pure Python. Without `BUN_BIN` it warns and continues.

---

## 3. Check the output parses

```bash
"$BUN_BIN" build --no-bundle --target=bun \
  build/extract/cli.original.cjs --outfile=/dev/null
```

This is Bun's **own** parser/transpiler, the same one that will load the file —
the authoritative syntax check (~2 s). `scripts/syntax-check.js` is a faster
secondary check, but it uses JavaScriptCore's `new Function()` parser, which
demonstrably accepts things Bun's loader rejects (verification record, Step 3).
Do not rely on it alone.

---

## 4. Run it — by full path

Start with `doctor` or `mcp list`, **not** `--version`:

```bash
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$BUN_BIN" build/extract/cli.original.cjs doctor
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$BUN_BIN" build/extract/cli.original.cjs mcp list
```

`doctor` prints the version, the search backend, install identity and updater
state; `mcp list` reads config, initialises `.claude.json` and dispatches into
the MCP subsystem. Both initialise thousands of the bundle's lazy modules;
`--version` initialises **0** (a hardcoded fast path), so treat it as a smoke
test only — the per-command counts are [findings.md](./findings.md) §9's table.

The scratch `CLAUDE_CONFIG_DIR` keeps a first run away from your real
`~/.claude`. `DISABLE_AUTOUPDATER=1` keeps the build from trying to update
*itself* down a route that would install a different, npm-based Claude Code on
your machine — see [Surviving Claude updates](#surviving-claude-updates) before
you drop it.

Beyond that: an agentic loop against a **loopback mock** endpoint has been run
here — SSE streaming, multi-turn tool use, the Bash tool spawning a real
subprocess, the Read tool returning an image, and the Ink TUI under a pty — with
no Bun-API failure. No request from **this host's** build has ever gone to
Anthropic; one session elsewhere has (the 2026-08-24 Apple Silicon run), which is
evidence that the path exists, not characterisation of it. And "it runs" is not
"it behaves like the native binary": read [findings.md](./findings.md) **§10**
before real use.

**Under Node instead of Bun.** Node **≥ 24** only — the bundle's `using`
declarations do not parse before it. `make node-deps && make node-run NODE_BIN=<node24>`
runs the artifact under `node --require scripts/bun-shim.cjs`; the command surface is
byte-identical to Bun ([findings.md](./findings.md) §11). The **interactive TUI also
works** — driven through a pty here on 2026-08-26 it painted the banner, took a
keystroke at the theme picker, rendered the syntax preview and reached the login
selector. That was on **Linux**. On macOS it paints nothing at all; see the
troubleshooting row below, which ships the instrument that will say why.

**Run it by full path.** Do not create a `claude` shim on `PATH`; it would shadow
your real installation, and every command in this repo is written to be run by
full path for that reason.

---

## Shell integrations: `CLAUDE_CODE_EXECPATH`

An earlier design installed a launcher on `PATH` that exported
`CLAUDE_CODE_EXECPATH` "for shell integrations". **That launcher is gone and is
not coming back** — it is exactly the `claude`-on-`PATH` shadowing hazard — and
this section used to tell you to export the variable yourself. **Do not: it has
no effect.** Measured in the post-processed artifact,
`process.env.CLAUDE_CODE_EXECPATH` occurs **0** times. The CLI never reads it.

What it actually does is **write** it ✅, in `getEnvironmentOverrides`, without
any standalone/install-method gate:

```js
c["CLAUDE_CODE_EXECPATH"] = process.execPath
```

Under a native install `process.execPath` *is* the `claude` binary. Here it is
**bun** — so every shell the CLI spawns gets `CLAUDE_CODE_EXECPATH=<path to bun>`
in its environment. That matters because of what reads it: the shell functions
the CLI injects into spawned shells for `find` and `grep` 🔎.

```bash
function find {
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin='<bundled bfs path>'
  if [[ ! -x $_cc_bin ]]; then command find ${1+"$@"}; return; fi
  ...
  (exec -a bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"})
}
```

The `[[ -x ]]` fallback does not rescue you: bun *is* executable, so the function
resolves to bun invoked with `bfs`/`ugrep` arguments rather than to the real
`find`/`grep`. Read out of the shipped source; **not observed live**. Same family
as [findings.md](./findings.md) §10, and no supported workaround yet. If `find`
or `grep` misbehave inside a Bash tool call, this is why.

---

## Surviving Claude updates

### ⚠️ Run the extracted build with `DISABLE_AUTOUPDATER=1`

Do not let this build update itself, and do not run `claude update` against it.

Claude decides how it was installed with a `Bun.isStandaloneExecutable` gate.
Under a stock external Bun that property is **undefined**, so the detector never
reaches its `native` case, falls through to npm/global heuristics over
`process.execPath` (which is now *bun*), shells out to `npm config get prefix`,
and ends at `unknown`. **The scoped image shim does not change this, on
purpose** — measured 2026-08-23 with `scripts/ab-equivalence.sh --case doctor`, a
shimmed build still reports `Running: unknown (2.1.222)`, byte-identical to the
as-shipped build's line.

Measured here, in a throwaway `HOME`, with `doctor` alone:

```
Running: unknown (2.1.222)
Path: /home/claude/.bun-1.3.14/bun
Invoked: /tmp/w1rep/extract/cli.original.cjs
Config install method: not set
```

— and that probe alone left `~/.npm/_logs` (three `npm config get prefix` debug
logs) and `~/.bun/install/cache` behind in that `HOME`.

`claude update` continues from the same `unknown` verdict: it prints
`Installation method set to: unknown` and takes the **npm / bun global-install
route**, which with working network installs a *different, npm-based* Claude Code
onto your machine. It can never update these artifacts — nothing in it writes to
`build/extract/`. (That route was exercised in a throwaway `HOME`; it is
deliberately **not** re-run here, because with network it performs the install.)

`DISABLE_AUTOUPDATER` is read by the bundle (`if(te.DISABLE_AUTOUPDATER)`
alongside `DISABLE_UPDATES`) and turns off **background** auto-updates. It does
not stop an explicitly typed `claude update`; only `DISABLE_UPDATES=1` makes that
command refuse outright ("Updates are disabled by your administrator"). Set
`DISABLE_AUTOUPDATER=1` always, and add `DISABLE_UPDATES=1` if you want the
manual command fenced off too. `scripts/build.sh` prints the run command with
`DISABLE_AUTOUPDATER=1` for this reason.

### Moving to a new Claude version

The way forward is always: get the new **native** binary and rebuild. Your
artifacts keep running the version you extracted until you do.

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh "$NATIVE"
python3 -m pytest tests/ -q     # what this prints depends on what the host has;
                                # the per-host counts are README's table alone
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$BUN_BIN" build/extract/cli.original.cjs mcp list   # not --version
```

A failed rebuild is safe: `build.sh` extracts into a staging directory and swaps
it in only after post-processing succeeds, so `build/extract/` still holds the
previous working build if anything goes wrong.

Two things to expect:

1. **The integration tests will fail on a new version.** They assert the measured
   module, asset and transform counts — findings §4 and §6 are where those
   figures are written down. That failure is the **early warning system**:
   re-measure, update the numbers there, and re-run this runbook. Do not relax
   the assertions. The failure tells you which kind it is —
   `…_measured_counts_have_not_drifted` lists **every** changed count at once and
   names the file to update, which is Claude changing and is expected;
   `…_transform_invariants_hold` failing is a different thing entirely (the
   pragma, the IIFE, a surviving `/$bunfs/` reference or `check()` itself), which
   means these tools are broken on the new binary and no number should be updated
   until it is understood.
2. ⚠️ **This is the moment the project can break for good** —
   [findings.md](./findings.md) §9. If the new build was compiled against a
   canary Bun newer than 1.3.14, its `cli.js` will not run on Zig at all. Keep the
   previous working `build/extract/` and pin to that Claude version. A `TypeError`
   naming a missing `Bun.*` property is the real signal; `Expected CommonJS module
   to have a function wrapper` is not, because this project's own transform
   produces it.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Expected CommonJS module to have a function wrapper` | **Ambiguous** — Bun older than 1.3.14, *or* the pragma/IIFE transform did not apply, *or* the pragma was kept **and** the IIFE appended (findings §6's 2×2). Not a reliable "Claude needs a newer Bun" canary | Confirm `bun --version` ≥ 1.3.14; confirm `cli.original.cjs` starts with `(function` and ends with the `(exports, require, module, …)` call |
| `TypeError: … is not a function` naming a `Bun.*` property | **This** is the missing-API signal — findings §9's risk | Pin to an older Claude version, or shim the API |
| Images are refused with *"Unable to resize image…"* | **Not expected in a default build.** It means the artifact predates the shim, was built with `NRC_NO_IMAGE_SHIM` non-empty, or the shim refused on this Claude release | `grep -o 'if(true)try' build/extract/cli.original.cjs \| wc -l` prints **1** for a shimmed build and **0** for an as-shipped one (measured on all three real binaries). If 0, rebuild without the env var and read the `image shim` lines: a refusal names which of three things drifted — the gate **declaration**'s minified shape, the **anchor** string, or the `if(<gate>())try{` branch shape (findings §10). Never "fix" it by flipping `Bun.isStandaloneExecutable` globally: measured, that silently breaks `Grep` |
| `ripgrep not found on PATH` | Embedded ripgrep needs a standalone; this build uses a system `rg` (findings §10) | Install `ripgrep` |
| `postprocess.py` exits non-zero and writes nothing | one of `check()`'s **six** fatal conditions (findings §6) | The error names which. Shape problems (IIFE, `(function`) mean the entry module changed — read its last ~200 bytes and re-measure before editing a regex. A surviving reference means a `/$bunfs/` shape the rewriter does not cover. A missing asset means `extract_bun.py` dropped a loader kind. Zero rewrites with populated assets means a different VFS prefix ([status.md](./status.md) § Windows/PE). Failed shim arithmetic means the one-site rewrite spread, and nothing is written deliberately, because the site it would reach next is embedded ripgrep. `not counted (no gate identified)` is not an error on its own — it is the honest reading when no gate declaration matched |
| `Cannot find module '.../assets/X.node'` | asset not extracted, or its path not rewritten | Should be unreachable from a build that succeeded: `check()` fails when the rewritten code references an asset that is not on disk. If you see it anyway, the artifact and its `assets/` came from different runs — rebuild |
| A mermaid/highlight/chart feature breaks | a `file`-loader asset still referenced via `/$bunfs/`, or an unverified runtime path | The rewritten path shape is verified to work, but no command here has exercised these three features — [status.md](./status.md) remaining work #3 |
| `error: PE (Windows) executable detected` | you pointed the extractor at `claude.exe` | Not supported by design — [status.md](./status.md) § Windows/PE |
| `error: ELF has no section headers (stripped?)` | the binary was stripped | Get an unstripped build; the shipped one is not stripped |
| `input is only 0 bytes` from the extractor, with no path given to `build.sh` | auto-discovery picked a stub: an interrupted `claude` auto-update leaves a 0-byte entry in `versions/`, and it sorts **newest** | Fixed as of 2026-08-24 — discovery walks newest-first, skips anything under 1 MiB and names what it skipped. On an older checkout, pass the working binary explicitly |
| Under **Node on macOS** the TUI paints nothing, sits idle, and Ctrl-C does not exit | **Unknown.** Reported first-hand on Apple Silicon, 2026-08-26. Not reproducible on this Linux host, where the same command drives the TUI through onboarding normally. `Bun.Terminal` is ruled out — startup touches only `semver`, `stringWidth`, `which` and `isStandaloneExecutable` | Use Bun; it works there. To help find the cause, re-run with `scripts/node-trace.cjs` loaded **before** the shim and send the log — see [Diagnosing a Node hang](#diagnosing-a-node-hang) |
| On exit the TUI does not clear or restore the terminal | **Unknown.** Observed once on macOS (Apple Silicon, 2026-08-24), not reproduced, cause not investigated. The native binary does not behave this way | Nothing to do; cosmetic. Recorded in [status.md](./status.md) § Known unknowns so a second sighting has something to attach to — please report one, with the terminal emulator named |

---

## Diagnosing a Node hang

`scripts/node-trace.cjs` is a diagnostic preload. It writes to `NRC_TRACE`
(default `/tmp/nrc-node-trace.log`) and never to stdout, because stdout belongs
to the TUI. Every call it wraps logs both before and after, so **a line with no
matching `<` is the call that never returned**, and a 500 ms heartbeat runs
alongside: ticks continuing means the process is idle and waiting; ticks
stopping means the main thread is blocked. It is unbuffered `fs.writeSync`, so
the last line survives `kill -9`, and its timer is `unref`'d so it cannot keep a
finished process alive. `tests/test_node_trace.py` pins all of that.

Load it **before** the shim, and run these three in order — the first that
misbehaves is the answer:

```bash
cd /path/to/not-rusty-claude
export NODE_PATH="$HOME/.cache/not-rusty-claude/node/node_modules"

# A. do the preloads even complete?
node --require "$PWD/scripts/node-trace.cjs" \
     --require "$PWD/scripts/bun-shim.cjs" -e 'console.log("preloads ok")'

# B. a non-interactive command: if THIS hangs, it is startup, not the TUI
NRC_TRACE=/tmp/nrc-b.log DISABLE_AUTOUPDATER=1 \
  node --require "$PWD/scripts/node-trace.cjs" \
       --require "$PWD/scripts/bun-shim.cjs" \
       build/extract/cli.original.cjs mcp list

# C. the TUI. Leave it in the FOREGROUND; do not background it - a backgrounded
#    TUI gets SIGTTOU and tells you nothing.
NRC_TRACE=/tmp/nrc-c.log DISABLE_AUTOUPDATER=1 \
  node --require "$PWD/scripts/node-trace.cjs" \
       --require "$PWD/scripts/bun-shim.cjs" \
       build/extract/cli.original.cjs
```

While C is stuck, from a **second** terminal, macOS's own sampling profiler
gives the native stack of every thread with no code change at all:

```bash
sample $(pgrep -n -f cli.original.cjs) 5 -f /tmp/nrc-sample.txt
pkill -f cli.original.cjs
```

`/tmp/nrc-c.log` and `/tmp/nrc-sample.txt` together say where it stopped.

For reference, a healthy Linux run reaches first paint at ~1.8 s: `setRawMode`,
then the 710-byte welcome banner, then a `\e[c` device-attributes query, then
the theme picker. A log that ends before the banner never got to the renderer.

---

## Appendix — relocating a native install (no de-rust)

If all you want is to move a **native** install to another machine unchanged:
copy the binary bytes verbatim and place it under
`$XDG_DATA_HOME/claude/versions/<v>`; match the CPU architecture. On macOS the
signature seals the file's bytes, not its path, so it verifies and runs from an
arbitrary directory 📓 — observed on a Mac in a prior session (2026-08-21,
2.1.238), not re-checked here or by the 2026-08-24 run. The install path is not
baked in: it resolves from `XDG_DATA_HOME`.
