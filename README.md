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
[`docs/verification-2026-08-22.md`](docs/verification-2026-08-22.md).

| Piece | `linux-x64` (ELF, 2.1.222) | `darwin-arm64` (Mach-O, 2.1.239) | `win32-x64` (PE, 2.1.239) |
|---|---|---|---|
| Extract `cli.js` + assets (`extract_bun.py`) | ✅ executed | ✅ executed | ⛔ refused by design |
| Post-process (`postprocess.py`) | ✅ executed | ✅ executed | ⛔ |
| `scripts/build.sh` end to end | ✅ executed | ✅ executed | ⛔ |
| Output accepted by Bun 1.3.14's own parser | 🔎 exit 0 | 🔎 exit 0 | ⛔ |
| **Runs under external Bun** | ✅ `doctor`, `mcp list`, `--help`, `--version` — on **1.3.14 *and* 1.4.0** | ✅ the JS boots under **Linux** Bun → `2.1.239 (Claude Code)` | ⛔ would also need a Windows Bun |
| Runtime asset loading | ✅ `image-processor.node` loads and works — but the CLI never asks for it | 🖥️ Mach-O addons cannot load on Linux | ⛔ |
| Behaves the same as the shipped binary | ⚠️ **no** — see the equivalence gap below | ⚠️ no | ⛔ |

✅ executed here · 🔎 static check only, nothing executed · 🖥️ needs hardware
we do not have · ⚠️ measured difference from the native binary · ⛔ deliberately
not implemented

**The headline result:** the extracted, post-processed `cli.original.cjs` from
the real Linux binary runs under vanilla Bun 1.3.14 and answers `doctor` and
`mcp list` (which really read and write `.claude.json`), plus `--help` and
`--version`. Driven by a **loopback mock** of the Messages API it also completes
a full agentic loop — SSE streaming, multi-turn tool use, the Bash tool spawning
a real subprocess — and renders the Ink TUI under a pty, with no Bun-API failure
anywhere. That is a positive answer, **for Claude Code 2.1.222, on Linux**, to
the risk in [`docs/findings.md`](docs/findings.md) §10. It is a measurement, not
a guarantee.

> **Don't lead with `--version`.** It initialises **0 of the bundle's 6748 lazy
> modules** — a hardcoded fast path. It proves the file parses and the CJS
> wrapper is invoked, and nothing at all about Bun's API surface. `doctor` and
> `mcp list` initialise ~2760.

**⚠️ The equivalence gap — read this before using it.** `Bun.isStandaloneExecutable`
is undefined outside a standalone, so the CLI takes its non-standalone branch in
~21 places. Measured consequences: **native image processing is silently
disabled** (a large PNG fails to resize as shipped, and comes back a correct
JPEG when the flag is forced true — the addon itself is fine), the
**seccomp sandbox is off**, embedded ripgrep becomes a **system `rg`** (so `rg`
is a de facto prerequisite), and install identity reports `unknown`. Both addon
loaders swallow failure, so **exit 0 is not evidence that the asset wiring
works**. Full detail: [`docs/findings.md`](docs/findings.md) §11.

**What is honestly not known:** no request from this build has ever gone to
Anthropic — the agentic loop above was a loopback mock. macOS-*specific*
behaviour is unverified: the darwin JS boots here, but its `.node` addons are
Mach-O and cannot load on Linux, and `process.platform` is `linux`.

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

Step-by-step, including the macOS path and troubleshooting:
[`docs/runbook.md`](docs/runbook.md).

## Layout

```
not-rusty-claude/
├── README.md                       you are here
├── docs/
│   ├── status.md                   ← what is verified on what; the remaining gaps
│   ├── findings.md                 measured facts about the binary and the transforms
│   ├── bun-section-format.md       byte-level spec (Mach-O / ELF / PE containers)
│   ├── runbook.md                  step-by-step, Linux and macOS
│   └── verification-2026-08-22.md  the evidence record: commands + pasted output
├── tools/
│   ├── extract_bun.py              extract cli.js + assets from the Bun section
│   ├── postprocess.py              make cli.js runnable under an external Bun
│   └── patch_claude.py             Approach B: byte-patch + re-sign (macOS only)
├── scripts/
│   ├── build.sh                    extract → post-process → print the run command
│   └── syntax-check.js             fast secondary syntax check (JSC, not Bun)
└── tests/                          86 tests: hermetic fixtures + real-binary integration
```

The tools themselves need no third-party packages — stock `python3` (3.9+) is
enough. Running the test suite additionally needs `pytest`: `python3 -m pytest
tests/ -q`. What that prints depends on what the host has, because the
integration tests need a real 300 MB binary and skip cleanly without one:

| host has | result |
| --- | --- |
| both binaries + Bun | **86 passed** |
| ELF binary + Bun, no Mach-O | 83 passed, 3 skipped |
| Bun only | 80 passed, 6 skipped |
| none of them | 77 passed, 9 skipped |

Point them at binaries with `NRC_TEST_ELF` (default `/usr/bin/claude`) and
`NRC_TEST_MACHO` (default `/tmp/ccmac/package/claude-darwin-arm64.bin`, the
name [`docs/findings.md`](docs/findings.md)'s appendix unpacks the darwin
tarball under; the older `/tmp/ccmac/package/claude` is still accepted), and at
a Bun with `BUN_BIN`
(default: `~/.bun-1.3.14/bun`, then whatever `bun` is on `PATH`). The
integration tests' hardcoded counts are a deliberate tripwire for the next
Claude release (see [`docs/status.md`](docs/status.md)).

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
session (not re-checked here). Downsides: notarization lost, TCC permissions
reset, overwritten by the next update. Use only for edits that are **not** in
the JS layer. Details in [`docs/findings.md`](docs/findings.md) §7.

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
