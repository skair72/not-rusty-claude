# not-rusty-claude

Run Claude Code on a **pre-Rust (Zig-era) Bun** — the runtime before Bun's
LLM-driven Zig→Rust rewrite — by extracting its JavaScript out of the native
binary and running it under a stock **Bun 1.3.14**. The signed binary is only
ever *read*: never modified, never re-signed.

> **Why.** Claude Code ships as a Bun *standalone* executable (runtime + app in
> one signed Mach-O). Bun is being rewritten from Zig to Rust; 1.3.14 is the
> last Zig release. You can't swap the embedded runtime, so instead we extract
> `cli.js` + assets and run them on an external Zig Bun.

## Status — this is a documented backbone, not a working install

| Piece | State |
|---|---|
| Bun standalone format + extraction (`extract_bun.py`) | ✅ verified on `2.1.238` (arm64, macOS 24.6.0) |
| Post-process (`postprocess.py`) | 🟡 scaffold — ported from prior art, **never executed** |
| `scripts/build.sh` orchestration | 🟡 scaffold — **never executed** end-to-end |
| Run under external Bun 1.3.14 | ❌ not started |

**Start at [`docs/status.md`](docs/status.md)** — it lists exactly what's
verified, what's a scaffold, and the ordered work items (with how-to-verify and
how-to-fix for each) to finish it on a real machine.

## Intended flow (to be completed on the target Mac)

Not yet runnable as-is — the scaffolded steps need verification and likely
fixes. This is the shape it's meant to take:

```bash
# 1. last Zig-era Bun (also the minimum Claude's cli.js needs)
curl -fsSL https://bun.sh/install | bash -s "bun-v1.3.14"
export PATH="$HOME/.bun/bin:$PATH"

# 2. extract → post-process → install a launcher that runs under Bun
scripts/build.sh            # 🟡 scaffold — expect to debug per docs/status.md

# 3. verify
"$HOME/.local/bin/claude" --version
```

Work through [`docs/runbook.md`](docs/runbook.md) step by step (it has the manual
equivalents) rather than assuming `build.sh` runs clean.

## Layout

```
not-rusty-claude/
├── README.md                    you are here
├── docs/
│   ├── status.md                ← START HERE: what's done, what's a scaffold, how to finish
│   ├── findings.md              everything verified
│   ├── bun-section-format.md    byte-level spec of the Bun standalone section
│   └── runbook.md               step-by-step for a fresh Mac
├── tools/
│   ├── extract_bun.py           ✅ extract cli.js + assets from the __BUN section
│   ├── postprocess.py           🟡 scaffold: make cli.js runnable under external Bun
│   └── patch_claude.py          ✅ Approach B: byte-patch + re-sign the binary
└── scripts/
    └── build.sh                 🟡 scaffold: orchestrates extract → postprocess → launcher
```

## Two approaches

**A — Extract & run under Bun (this project's default).** No modification to the
signed binary, so signing/notarization never enter the picture; the JS is plain
text with no length limit. Needs an external Zig Bun. This is the "de-rust" path.

**B — Byte-patch & re-sign** ([`tools/patch_claude.py`](tools/patch_claude.py)).
Edit bytes inside the Mach-O (length-preserving) and ad-hoc re-sign with the
original entitlements + identifier. Verified end-to-end. Downsides: notarization
lost, TCC permissions reset, overwritten by the next update. Use only for edits
that are **not** in the JS layer. Details in
[`docs/findings.md`](docs/findings.md) §7.

## The one risk to know

Anthropic builds Claude with Bun's *canary* channel. Today 1.3.14 (last Zig)
also satisfies Claude's minimum, so it works. But if a future Claude build uses
Bun APIs newer than 1.3.14, its `cli.js` won't run on Zig — and the only newer
Bun is the Rust rewrite. That would defeat the goal for that version. Watch for
`Expected CommonJS module to have a function wrapper` / missing-API errors. Full
discussion in [`docs/findings.md`](docs/findings.md) §10.

## Prior art

[ClawGod](https://github.com/0Chencc/clawgod) (extract + patch + run under Bun),
[bun-demincer](https://github.com/vicnaum/bun-demincer),
[unbuned](https://github.com/vibheksoni/unbuned),
[bun-decompile](https://github.com/lafkpages/bun-decompile),
[tweakcc](https://github.com/Piebald-AI/tweakcc). Comparison and where each falls
short on current builds: [`docs/findings.md`](docs/findings.md) §8.

## Not affiliated with Anthropic. For personal, local use; you run modified code
at your own risk.
