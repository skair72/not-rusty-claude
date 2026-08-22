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
  2.1.239 Mach-O binary; **step 4 (actually running it) has never been done by
  this project** and is what you would be contributing. See
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
  dependencies).
- This repo checked out. Nothing needs installing from it.

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

1.3.14 is both the **last Zig release** and the **minimum** that can load
Claude's `cli.js` (older Bun panics with *"Expected CommonJS module to have a
function wrapper"*). Pin exactly 1.3.14: anything newer risks being a Rust
build, which defeats the point.

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

> On a CPU without AVX2, use the `-baseline` build instead. Bun 1.3.14 is a Zig
> build on every platform regardless — the Rust rewrite is experimental and
> Linux-x64-only.

---

## 2. Build the artifacts

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh "$NATIVE"
#   OUT_DIR=/somewhere  to put artifacts elsewhere (default: ./build)
```

`build.sh` extracts, post-processes, and then **stops**. It installs nothing —
no launcher, nothing on `PATH` — because a file named `claude` on `PATH` could
shadow a real installation. It prints the exact command to run instead.

Expect output like this (Linux 2.1.222; your numbers will differ per platform
and version — see [findings.md](./findings.md) §4 and §6):

```
Modules: 8 (entry id=0)
Extracted: 1 cli.js + 5 assets (2 loader shims left inlined in cli.js)
pragma lines stripped  : 1
/$bunfs/ paths rewired : 5
file:// leaks rewritten: 7
IIFE invocations added : 1  (expected 1)
wrote: .../build/extract/cli.original.cjs
```

**Read the counts.** `IIFE invocations added` must be exactly 1 — if it is not,
`postprocess.py` refuses to write the output at all rather than handing Bun a
file that fails with a confusing panic. `warning: leftover bunfs reference`
lines mean an asset path was not rewritten. `note: build-machine path still
present` lines are informational: string literals containing Anthropic's build
paths that are not `/$bunfs/` references (3 of them on both binaries measured).

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

```bash
CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$BUN_BIN" build/extract/cli.original.cjs --version
```

Expected: the version string of the binary you extracted from, e.g.
`2.1.222 (Claude Code)`, exit 0.

The scratch `CLAUDE_CONFIG_DIR` keeps a first run away from your real
`~/.claude` — worth doing until you trust the build. Deeper smoke tests that
were verified on Linux:

```bash
CLAUDE_CONFIG_DIR="$(mktemp -d)" "$BUN_BIN" build/extract/cli.original.cjs --help
CLAUDE_CONFIG_DIR="$(mktemp -d)" "$BUN_BIN" build/extract/cli.original.cjs mcp list
```

`--help` renders the full command registry; `mcp list` actually reads and writes
config state, which is the deepest path verified so far. Beyond that — real
prompts, TUI, tools, asset loading — nothing is verified; you are exploring.

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
`PATH` shadowing hazard). The behavioural consequence is worth knowing:
`CLAUDE_CODE_EXECPATH` is now simply unset unless you set it yourself.

What is known 🔎: the variable name appears in the extracted CLI among the
environment variables it propagates to spawned shells and background sessions,
alongside the shell-integration code. Under a normal native install the running
executable *is* `claude`; under this setup `process.execPath` is the **Bun
binary**, so anything that re-invokes "the Claude executable" from that path
would get bare Bun instead.

What is not known: none of the commands verified here (`--version`, `--help`,
`mcp list`) needed it — they all exited 0 with it unset — so its exact runtime
semantics were never exercised. If you use `cli.original.cjs` for real work and
something involving shell snapshots or spawned sessions misbehaves, set it
yourself for that invocation:

```bash
CLAUDE_CODE_EXECPATH="$NATIVE" \
  "$BUN_BIN" build/extract/cli.original.cjs "$@"
```

---

## Surviving Claude updates

Anthropic's auto-update installs a new native binary and repoints its own
launcher. Your extracted artifacts do not follow it — they keep running the
version you extracted until you rebuild:

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh "$NATIVE"
python3 -m pytest tests/ -q        # counts are version-specific; see below
CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$BUN_BIN" build/extract/cli.original.cjs --version
```

Two things to expect:

1. **The integration tests will fail on a new version.** They assert the
   measured module and transform counts (8/5/7 for linux-x64 2.1.222, 15/9/8 for
   darwin-arm64 2.1.239). That failure is the **early warning system**, not a
   defect: re-measure, update the numbers and [findings.md](./findings.md) §4/§6,
   and re-run this runbook. Do not relax the assertions.
2. ⚠️ **This is the moment the project can break for good** —
   [findings.md](./findings.md) §10. If the new build was compiled against a
   canary Bun newer than 1.3.14, its `cli.js` will not run on Zig at all. Keep
   the previous working `build/extract/` and pin to that Claude version.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Expected CommonJS module to have a function wrapper` | Bun older than 1.3.14, or the pragma/IIFE transform did not apply | Confirm `bun --version` is 1.3.14; confirm `cli.original.cjs` starts with `(function` and ends with the `(exports, require, module, …)` call |
| `postprocess.py` exits non-zero and writes nothing | its `check()` found no trailing IIFE, or the file does not start with `(function` | Read the last ~200 bytes of `cli.original.js`; the file shape changed — re-measure before editing the regex |
| Missing Bun API / `undefined is not a function` at startup | Claude built against a Bun **newer** than 1.3.14 (§10) | Pin to an older Claude version, or shim the API |
| `Cannot find module '.../assets/X.node'` | asset not extracted, or its path not rewritten | Confirm `assets/X.node` exists; check the `/$bunfs/ paths rewired` count and any leftover-bunfs warning |
| A mermaid/highlight/chart feature breaks | a `file`-loader asset still referenced via `/$bunfs/`, or an unverified runtime path | Runtime asset resolution is **unverified** on every platform ([status.md](./status.md) remaining work #3) — this is a known open gap, not a misconfiguration |
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
