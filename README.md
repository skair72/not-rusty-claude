# not-rusty-claude

Run Claude Code on a **pre-Rust (Zig-era) Bun** — the runtime before Bun's
LLM-driven Zig→Rust rewrite — by extracting its JavaScript out of the native
binary and running it under a stock **Bun 1.3.14**. The signed binary is only
ever *read*: not modified, not re-signed, not executed by this pipeline.

> **Why.** Claude Code ships as a Bun *standalone* executable — runtime and app
> baked into one signed binary (Mach-O on macOS, ELF on Linux, PE on Windows).
> Bun is being rewritten from Zig to Rust; 1.3.14 is the last Zig release. You
> cannot swap the embedded runtime, so instead we extract `cli.js` + assets and
> run them on an external Zig Bun.

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
| **Runs under external Bun 1.3.14** | ✅ `--version`, `--help`, `mcp list` | 🖥️ needs a Mac | ⛔ would also need a Windows Bun |
| Runtime asset loading | 🔎 unverified | 🔎 unverified | ⛔ |

✅ executed here · 🔎 static check only, nothing executed · 🖥️ needs hardware
we do not have · ⛔ deliberately not implemented

**The headline result:** the extracted, post-processed `cli.original.cjs` from
the real Linux binary starts under vanilla Bun 1.3.14 and answers `--version`,
`--help`, and `mcp list` — the last of which really reads and writes
`.claude.json`. That is a positive answer, **for Claude Code 2.1.222, on Linux,
on those code paths**, to the risk in
[`docs/findings.md`](docs/findings.md) §10. It is a measurement, not a
guarantee.

**What is honestly not known:** the macOS artifact has never been executed (that
needs Apple hardware; emulation was evaluated and rejected here, with evidence).
No executed command has ever loaded a runtime asset — proven, not assumed, by
renaming `assets/` away and watching `--version` and `--help` still succeed. No
network call, model request, TUI render, or tool execution has been exercised.

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

# 3. run it, by full path, with a scratch config dir
CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs --version
#   → 2.1.222 (Claude Code)
```

`build.sh` deliberately **installs nothing on `PATH`** — a file named `claude`
there could shadow your real installation. It prints the full-path command
instead. One consequence to know about: the old design's launcher exported
`CLAUDE_CODE_EXECPATH`; see [`docs/runbook.md`](docs/runbook.md) § Shell
integrations for when to set it yourself.

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
└── tests/                          22 tests: hermetic fixtures + real-binary integration
```

Everything runs on stock `python3` (3.9+) with no dependencies. `python3 -m
pytest tests/ -q` → 22 passed. The integration tests need the real binaries and
skip cleanly without them; their hardcoded counts are a deliberate tripwire for
the next Claude release (see [`docs/status.md`](docs/status.md)).

## Two approaches

**A — Extract & run under Bun (this project's default).** No modification to the
signed binary, so signing and notarization never enter the picture; the JS is
plain text with no length limit. Needs an external Zig Bun. This is the
"de-rust" path, and the one verified above.

**B — Byte-patch & re-sign** ([`tools/patch_claude.py`](tools/patch_claude.py)).
Edit bytes inside the Mach-O (length-preserving) and ad-hoc re-sign with the
original entitlements and identifier. Verified end-to-end on a Mac in a prior
session (not re-checked here). Downsides: notarization lost, TCC permissions
reset, overwritten by the next update. Use only for edits that are **not** in
the JS layer. Details in [`docs/findings.md`](docs/findings.md) §7.

## The one risk to know

Anthropic builds Claude with Bun's *canary* channel. For 2.1.222 that turned out
fine — 1.3.14 ran it — but if a future Claude build uses Bun APIs newer than
1.3.14, its `cli.js` will not run on Zig, and the only newer Bun is the Rust
rewrite. That defeats the goal for that version. Watch for `Expected CommonJS
module to have a function wrapper` and missing-API errors, keep the last working
`build/extract/`, and pin that Claude version. Full discussion:
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
[tweakcc](https://github.com/Piebald-AI/tweakcc). Comparison, and the two places
ClawGod's transforms silently do nothing on current builds:
[`docs/findings.md`](docs/findings.md) §8.

## Not affiliated with Anthropic. For personal, local use; you run modified code
at your own risk.
