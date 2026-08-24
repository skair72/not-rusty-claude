# Runbook — run Claude Code's JavaScript on Zig-era Bun 1.3.14

Step by step, from a native Claude Code install to `cli.original.cjs` running
under a stock external **Bun 1.3.14** (the last Zig release before Bun's
Zig→Rust rewrite). The native binary is only ever *read* — never modified, never
re-signed, not executed by this pipeline.

Two platforms, and they are at different levels of confidence:

- **Linux x64** — the whole path below was executed on 2026-08-22 against Claude
  Code 2.1.222, output pasted in
  [verification-2026-08-22.md](./verification-2026-08-22.md).
- **macOS (Apple Silicon)** — steps 0–3 were executed here against the real
  2.1.239 Mach-O binary, and step 4 was executed here *under Linux Bun*: the
  extracted darwin JavaScript boots and prints `2.1.239 (Claude Code)`. No step
  in this runbook has ever been run **on** macOS. The `.node` addons are Mach-O
  and cannot load on Linux, and `process.platform` is `linux` here, so nothing
  macOS-*specific* is exercised — that is what you would be contributing.
  [README's macOS section](../README.md#macos) is the version of this with the
  measured and unverified steps separated command by command; see also
  [status.md](./status.md) § macOS execution.
- **Windows** — not supported; extraction refuses PE input by design
  ([status.md](./status.md) § Windows/PE).

---

## 0. Prerequisites

- A native Claude Code install, or any copy of the native binary:
  - Linux: usually `/usr/bin/claude`, or `~/.local/share/claude/versions/<v>`.
  - macOS: `~/.local/share/claude/versions/<v>` (Apple Silicon).
  - Or download one without installing anything —
    `npm pack @anthropic-ai/claude-code-darwin-arm64` and friends
    ([findings.md](./findings.md) §9).
- `python3` (stock `/usr/bin/python3` is fine — the tools target 3.9+, no
  third-party packages; the test suite additionally wants `pytest`).
- **`ripgrep` on `PATH`.** Not optional here, though it is optional for a
  native install: the CLI only uses its *embedded* ripgrep when it detects it is
  a Bun standalone, which this build is not, so it looks for `rg` on `PATH`
  ([findings.md](./findings.md) §11).
- This repo checked out. Nothing needs installing from it.

Every step in this runbook should work on Linux **and** macOS — with the
standing caveat that none of them has ever been run on macOS
([README](../README.md#macos)) — except one, which is Linux-only by decision:
`scripts/ab-equivalence.sh`, the §11 A/B. Its egress guard reads
`/proc/<pid>/fd` against `/proc/net/tcp`, and the script refuses to start where
those are unreadable rather than run the comparison with its safety net
silently missing. It also requires `timeout(1)` (or `gtimeout`) — a hard
requirement, not a best effort, since a hung case has to be bounded by
something — plus `node` for the loopback mock and `rg` on `PATH`. A preflight
names everything missing in one message rather than dying at whichever line
comes first (checked here on 2026-08-24 by hiding `bun` and `node`: both were
reported together, exit 1; the `/proc` branch — the one a Mac would take —
could not be exercised on this host, which refuses `unshare`).

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

> **Safety:** the pipeline reads this file and nothing else. Do not point it at
> a binary you are unwilling to have read, and do not let any step write to it.

---

## 1. Install Bun 1.3.14 — without touching `PATH`

1.3.14 is the **last Zig release**, and the minimum that loads the artifact
**this project builds** (older Bun panics with *"Expected CommonJS module to
have a function wrapper"*). Two honest caveats ✅:

- That floor is ours, not Claude's. The same entry module, rebuilt in the
  pragma-preserving shape, runs fine on **1.3.13** — see
  [findings.md](./findings.md) §6 and §10.
- The artifact does not *require* a Zig Bun at all; it also runs on **1.4.0**,
  the Rust build. Pinning 1.3.14 is the project's goal, not a technical
  constraint.

Preferred — a plain unpack, no installer script, no rc-file edits, not on
`PATH` (this is exactly what the verification run did):

```bash
mkdir -p "$HOME/.bun-1.3.14"
curl -fsSL -o /tmp/bun-1.3.14.zip \
  https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-linux-x64.zip
#   macOS arm64: .../bun-v1.3.14/bun-darwin-aarch64.zip
unzip -o -j /tmp/bun-1.3.14.zip 'bun-linux-x64/bun' -d "$HOME/.bun-1.3.14"
chmod +x "$HOME/.bun-1.3.14/bun"
export BUN_BIN="$HOME/.bun-1.3.14/bun"
"$BUN_BIN" --version          # → 1.3.14
```

The upstream installer (`curl -fsSL https://bun.sh/install | bash -s
"bun-v1.3.14"`) also works, but it writes to `~/.bun` and edits shell rc files.
The unpack above keeps this experiment entirely self-contained.

> On a CPU without AVX2, use the `-baseline` build instead. Bun 1.3.14 is the
> last release built from the Zig codebase on every platform. **The "the Rust
> rewrite is Linux-x64-only" note that used to sit here is stale:** `bun-v1.4.0`
> shipped 2026-08-20 as the first Rust release targeting all supported
> platforms ([findings.md](./findings.md) §1). ("Zig-era" also does not mean
> Rust-free — 1.3.14 links vendored Rust crates and its `.comment` names
> `rustc`; what it predates is the rewrite of Bun's own core.)

---

## 2. Build the artifacts

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh "$NATIVE"
#   OUT_DIR=/somewhere      to put artifacts elsewhere (default: ./build)
#   NRC_NO_IMAGE_SHIM=1     build the "as shipped" artifact, without the scoped
#                           image shim. ANY non-empty value opts out; leave it
#                           unset (or empty) for the default, shimmed build.
```

`build.sh` extracts, post-processes, and then **stops**. It installs nothing —
no launcher, nothing on `PATH` — because a file named `claude` on `PATH` could
shadow a real installation. It prints the exact command to run instead.

Expect output like this (Linux 2.1.222; your numbers will differ per platform
and version — see [findings.md](./findings.md) §4 and §6):

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

(Quoted from a build run on this host against `/usr/bin/claude`, re-run and
re-checked 2026-08-24. Every number in it belongs to `linux-x64` 2.1.222 — the
gate's minified name included. The darwin-arm64 2.1.239 build prints a
different name and different counts; that transcript is in
[README's macOS section](../README.md#macos), and the two are tabulated side by
side in [findings.md](./findings.md) §6.)

(The `wrote:` lines name the **staging** directory; `build.sh` prints the final
paths in its "artifacts ready" block immediately afterwards. The `cli.js`
sibling is not optional — Claude's own code resolves `join(__filename,'..',
'cli.js')` for two MCP self-spawns.)

**Read the counts.** `image shim applied` must be **1** in a default build.
`0` is not a build failure — the artifact is exactly as good as every artifact
this repo shipped before the shim existed — but it means the Read tool will
refuse an oversized image, so the script says so out loud either way and adjusts
its closing list of gaps to match. `IIFE invocations added` must be exactly 1 —
if it is not,
`postprocess.py` refuses to write the output at all rather than handing Bun a
file that fails with a confusing panic. A surviving `/$bunfs/` (or Windows
`B:/~BUN/`) reference is refused the same way: it used to be a warning printed
after the file had already been written. `note: build-machine path still
present` lines *are* informational: string literals containing Anthropic's
build paths that are not `/$bunfs/` references (how many, per binary, is a row
of [findings.md](./findings.md) §6's table). All notes print before `wrote:`, so
anything after that line is the artifact, not a complaint about it.

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
the MCP subsystem. Both initialise thousands of the bundle's lazy modules, and
that total moves with every Claude release; the per-command counts are
[findings.md](./findings.md) §10's table and live there only.

`--version` also works and prints e.g. `2.1.222 (Claude Code)` — but treat it as
a smoke test only. Measured, it initialises **0** lazy modules (a hardcoded
fast path — the only one of these numbers that is structural rather than a
property of one build): it proves the file parses and the CJS wrapper is invoked,
and nothing whatsoever about Bun's API surface
([findings.md](./findings.md) §10). `--help` renders the full command registry
and initialises about as many modules as the other two.

The scratch `CLAUDE_CONFIG_DIR` keeps a first run away from your real
`~/.claude` — worth doing until you trust the build. `DISABLE_AUTOUPDATER=1`
keeps the build from trying to update *itself* down a route that would install
a different, npm-based Claude Code on your machine — see
[Surviving Claude updates](#surviving-claude-updates) before you drop it.

Beyond that: an agentic loop against a **loopback mock** endpoint has been run
here — SSE streaming, multi-turn tool use, the Bash tool spawning a real
subprocess, the Read tool returning an image, and the Ink TUI under a pty — all
with no Bun-API failure. No request from this build has ever gone to Anthropic.
And "it runs" is not "it behaves like the native binary": read
[findings.md](./findings.md) **§11** before real use.

**Run it by full path.** Do not create a `claude` shim on `PATH`; it would
shadow your real installation, and every command in this repo is written to be
run by full path for that reason.

---

## Shell integrations: `CLAUDE_CODE_EXECPATH`

An earlier design of this project installed a launcher that did:

```bash
export CLAUDE_CODE_EXECPATH="<native-binary>"   # "for shell integrations"
exec "$BUN_BIN" .../cli.original.cjs "$@"
```

**That launcher is gone and is not coming back** (it is exactly the `claude`-on-
`PATH` shadowing hazard).

> **⚠️ Corrected 2026-08-22. This section used to tell you to export
> `CLAUDE_CODE_EXECPATH` yourself. Do not — it has no effect.** Measured in the
> post-processed artifact: `process.env.CLAUDE_CODE_EXECPATH` occurs **0**
> times. The CLI never reads the variable. The advice was wrong.

What the CLI actually does with it ✅ is **write** it. In
`getEnvironmentOverrides` it sets, unconditionally and without any
standalone/install-method gate:

```js
c["CLAUDE_CODE_EXECPATH"] = process.execPath
```

Under a native install `process.execPath` *is* the `claude` binary. Under this
setup it is **bun** — so every shell the CLI spawns gets
`CLAUDE_CODE_EXECPATH=<path to bun>` in its environment.

That matters because of what reads it: the shell functions the CLI injects into
spawned shells for `find` and `grep` 🔎.

```bash
function find {
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin='<bundled bfs path>'
  if [[ ! -x $_cc_bin ]]; then command find ${1+"$@"}; return; fi
  ...
  (exec -a bfs "$_cc_bin" -S dfs -regextype findutils-default ${1+"$@"})
}
```

The `[[ -x ]]` fallback does not rescue you: bun *is* executable, so the
function resolves to bun invoked with `bfs`/`ugrep` arguments rather than to the
real `find`/`grep`. Read out of the shipped source; **not observed live** here.
This is the same family of problem as [findings.md](./findings.md) §11 — things
that differ because this process is not a Bun standalone — and it has no
supported workaround yet. If `find` or `grep` misbehave inside a Bash tool call,
this is why.

---

## Surviving Claude updates

### ⚠️ Run the extracted build with `DISABLE_AUTOUPDATER=1`

Do not let this build update itself, and do not run `claude update` against it.

Claude decides how it was installed with
`function CE(){return Bun.isStandaloneExecutable===!0}`. Under a stock external
Bun that property is **undefined**, so `CE()` is false: the detector never
reaches its `native` case, falls through to npm/global heuristics over
`process.execPath` (which is now *bun*), shells out to `npm config get prefix`,
and ends at `unknown`.

**The scoped image shim does not change this, on purpose.** It rewrites the
image branch's own gate call and nothing else, so install identity is still
decided by a `CE()` that returns false. Measured 2026-08-23 with
`scripts/ab-equivalence.sh --case doctor`: a shimmed build still reports
`Running: unknown (2.1.222)`, byte-identical to the as-shipped build's line.
Keep `DISABLE_AUTOUPDATER=1`.

Measured here, in a throwaway `HOME`, with `doctor` alone:

```
Running: unknown (2.1.222)
Path: /home/claude/.bun-1.3.14/bun
Invoked: /tmp/w1rep/extract/cli.original.cjs
Config install method: not set
```

— and that probe alone left `~/.npm/_logs` (three `npm config get prefix`
debug logs) and `~/.bun/install/cache` behind in that `HOME`.

`claude update` continues from the same `unknown` verdict: it prints
`Installation method set to: unknown` and takes the **npm / bun global-install
route**. With working network that route installs a *different, npm-based*
Claude Code onto your machine. It can never update these artifacts — nothing in
it writes to `build/extract/`. (The npm route was exercised by the review fleet
in a throwaway `HOME`; it is deliberately **not** re-run here, because with
network it performs the install.)

```bash
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$BUN_BIN" build/extract/cli.original.cjs doctor
```

`DISABLE_AUTOUPDATER` is read by the bundle (`if(te.DISABLE_AUTOUPDATER)`
alongside `DISABLE_UPDATES`) and turns off **background** auto-updates. It does
not stop an explicitly typed `claude update`; only `DISABLE_UPDATES=1` makes
that command refuse outright ("Updates are disabled by your administrator").
Set `DISABLE_AUTOUPDATER=1` always, and add `DISABLE_UPDATES=1` if you want the
manual command fenced off too. `scripts/build.sh` prints the run command with
`DISABLE_AUTOUPDATER=1` for this reason.

### Moving to a new Claude version

The way forward is always: get the new **native** binary and rebuild. Your
artifacts keep running the version you extracted until you do.

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh "$NATIVE"
# What this prints depends on what the host has: NRC_TEST_ELF / NRC_TEST_MACHO
# / BUN_BIN choose the real binaries the integration tests need, and the tests
# skip cleanly without them. The four per-host counts are stated in exactly one
# place - README's table - so that they cannot drift apart between files, which
# is how four of them ended up ten too low here. The measured counts INSIDE the
# tests are version-specific and are the release tripwire:
python3 -m pytest tests/ -q
DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$BUN_BIN" build/extract/cli.original.cjs mcp list   # not --version: findings §10
```

A failed rebuild is safe: `build.sh` extracts into a staging directory and swaps
it in only after post-processing succeeds, so `build/extract/` still holds the
previous working build if anything goes wrong.

Two things to expect:

1. **The integration tests will fail on a new version.** They assert the
   measured module, asset and transform counts — findings §4 and §6 are where
   those figures are written down. That failure is the **early warning system**,
   not a defect: re-measure, update the numbers and
   [findings.md](./findings.md) §4/§6,
   and re-run this runbook. Do not relax the assertions.

   The failure tells you which kind it is. `…_measured_counts_have_not_drifted`
   lists **every** changed count at once and names the file to update — that is
   Claude changing, and it is expected. `…_transform_invariants_hold` failing is
   a different thing entirely: the pragma, the IIFE, a surviving `/$bunfs/`
   reference or `check()` itself. That means these tools are broken on the new
   binary, and no number should be updated until it is understood.
2. ⚠️ **This is the moment the project can break for good** —
   [findings.md](./findings.md) §10. If the new build was compiled against a
   canary Bun newer than 1.3.14, its `cli.js` will not run on Zig at all. Keep
   the previous working `build/extract/` and pin to that Claude version. To
   tell that apart from a shape problem: a `TypeError` naming a missing `Bun.*`
   property is the real signal; `Expected CommonJS module to have a function
   wrapper` is not, because this project's own transform produces it.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Expected CommonJS module to have a function wrapper` | **Ambiguous** — Bun older than 1.3.14, *or* the pragma/IIFE transform did not apply, *or* the pragma was kept **and** the IIFE appended (findings §6's 2×2). Not a reliable "Claude needs a newer Bun" canary | Confirm `bun --version` is ≥ 1.3.14; confirm `cli.original.cjs` starts with `(function` and ends with the `(exports, require, module, …)` call |
| `TypeError: … is not a function` naming a `Bun.*` property | **This** is the missing-API signal — findings §10's risk | Pin to an older Claude version, or shim the API |
| Images are refused with *"Unable to resize image…"* | **Not expected in a default build any more.** Since 2026-08-23 `postprocess.py` rewrites the image-processor gate so the native path is reachable (findings §11, *What shipped*). Seeing this means one of three things: the artifact predates the shim; it was built with `NRC_NO_IMAGE_SHIM` set to a non-empty value; or the shim refused in this Claude release, in which case the build printed `image shim NOT APPLIED:` followed by the cause. Three different things drift and they are **not** interchangeable: the gate **declaration**'s minified shape, the anchor string, or the `if(<gate>())try{` branch shape | Check the artifact itself: `grep -o 'if(true)try' build/extract/cli.original.cjs \| wc -l` prints **1** for a shimmed build and **0** for an as-shipped one (measured on linux-x64 2.1.222 and darwin-arm64 2.1.239). If it prints 0, rebuild without `NRC_NO_IMAGE_SHIM` and read the `image shim` lines in the build output. If the build says NOT APPLIED with the env var unset, read the rest of that same line — it names which of the three drifted, and each is re-measured somewhere else (`STANDALONE_DEF`, `IMAGE_ANCHOR`, `_image_site_re` in `tools/postprocess.py`); see findings §11. Checked here on 2026-08-24 by rebuilding a copy of the Linux binary whose 53-byte gate declaration had been replaced in place with an equal-length arrow form: the build named the **declaration** and said the anchor was not implicated, for an artifact whose anchor was still present exactly once. Never "fix" it by flipping `Bun.isStandaloneExecutable` globally: measured, that silently breaks `Grep` |
| `ripgrep not found on PATH` | Embedded ripgrep needs a standalone; this build uses a system `rg` (findings §11) | Install `ripgrep` |
| `postprocess.py` exits non-zero and writes nothing | one of `check()`'s **six** fatal conditions ([findings.md](./findings.md) §6; counted in `tools/postprocess.py` on 2026-08-24): no trailing IIFE; the file does not start with `(function`; a `/$bunfs/` or `B:/~BUN/` reference survived the rewrite; the rewritten code reaches for an `assets/<name>` that was never extracted; **zero** `/$bunfs/` literals were rewritten while `assets/` has files; or the image shim's bookkeeping does not describe the artifact — the before/after gate-call arithmetic does not add up, **or** a rewrite is claimed against a gate that was never identified, in which case nothing counted what the rewrite did and the counts are reported as *unknown* rather than as `0` | The error names which. Shape problems (IIFE, `(function`) mean the entry module changed — read its last ~200 bytes and re-measure before editing a regex. A surviving reference means a `/$bunfs/` shape the rewriter does not cover. A missing asset means `extract_bun.py` dropped a loader kind — check its `LOADERS`/`WRITTEN_LOADERS` against Bun's `src/bundler/options.zig`. Zero rewrites with populated assets means a different VFS prefix ([status.md](./status.md) § Windows/PE). Failed shim arithmetic means the one-site rewrite spread — nothing is written, deliberately, because the site it would reach next is embedded ripgrep and that failure is a wrong answer, not an error. `not counted (no gate identified)` on the `image shim call sites` line is not an error on its own: it is the honest reading when no gate declaration matched, and it is there because `0 -> 0` in its place was a claim about the artifact that measured false |
| `Cannot find module '.../assets/X.node'` | asset not extracted, or its path not rewritten | Should no longer be reachable from a build that succeeded: `check()` fails the build when the rewritten code references an asset that is not on disk. If you see it anyway, the artifact and its `assets/` came from different runs — rebuild |
| A mermaid/highlight/chart feature breaks | a `file`-loader asset still referenced via `/$bunfs/`, or an unverified runtime path | The rewritten path shape is verified to work (`image-processor.node` loads through it), but no command here has exercised these three features — [status.md](./status.md) remaining work #3 |
| `error: PE (Windows) executable detected` | you pointed the extractor at `claude.exe` | Not supported by design — [status.md](./status.md) § Windows/PE |
| `error: ELF has no section headers (stripped?)` | the binary was stripped | Get an unstripped build; the shipped one is not stripped |

---

## Appendix — relocation (no de-rust, no patch)

If all you want is to move a **native** install to another machine unchanged:
copy the binary bytes verbatim and place it under
`$XDG_DATA_HOME/claude/versions/<v>`. On macOS a Mach-O signature seals the
file's bytes, not its path, so it verifies and runs with no re-sign; match the
CPU architecture. Details, and the SIGKILL-on-modify facts behind Approach B,
are in [findings.md](./findings.md) §7 (recorded on a Mac in a prior session,
not re-checked on this host).
