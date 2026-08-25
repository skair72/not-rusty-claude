# Design — cross-platform extraction with an end-to-end verified Linux run

**Date:** 2026-08-22
**Status:** approved, pre-implementation
**Supersedes the "🟡 scaffold" posture of** [`docs/status.md`](../../status.md)

---

## 1. Problem

`not-rusty-claude` extracts Claude Code's JavaScript out of its Bun *standalone*
executable and runs it under a stock **Bun 1.3.14** — the last Zig release before
Bun's Zig→Rust rewrite. Today the repo is a documented backbone:

| Half | State before this work |
|---|---|
| Mach-O extraction | ✅ verified on macOS 2.1.238 |
| `postprocess.py` | 🟡 scaffold, **never executed** |
| `scripts/build.sh` | 🟡 scaffold, **never executed** |
| Run under external Bun | ❌ not started — "the whole point" |
| ELF / PE containers | ❌ `extract_bun.py` dies on non-Mach-O input |

Every remaining work item was gated on "needs a Mac with Bun installed". That
gate turns out to be false for all but one step.

## 2. What changed: the gate was wrong

Directly observed on the implementation host (Linux x86_64, no macOS tooling):

- **`/usr/bin/claude` is a Bun standalone ELF** — Claude Code 2.1.222, 289 MB,
  `.bun` section at offset 86904832, 8 modules, entry
  `/$bunfs/root/src/entrypoints/cli.js`. A live fixture for the *whole* pipeline
  including the run step.
- **The real darwin binary is a plain npm download.** Native binaries ship as
  per-platform optional dependencies — `npm pack
  @anthropic-ai/claude-code-darwin-arm64` yields a genuine 325 MB Mach-O in ~1.3 s.
  Extraction is byte-parsing, so **Mach-O is verifiable without a Mac**;
  `tools/extract_bun.py` already parses it correctly (15 modules, 1 cli.js +
  9 assets).
- **The win32 build is a PE** carrying the same `.bun` section.
- **Bun 1.3.14 linux-x64 is downloadable** (HTTP 200); 1.3.15 does not exist and
  the next release is 1.4.0, corroborating 1.3.14 as the end of the 1.3 line.

Consequence: **all three container formats and both text-transform halves are
verifiable here.** Only *executing* darwin JS needs real Apple hardware.

### 2.1 macOS emulation was evaluated and rejected — with evidence

| Route | Blocker (observed) |
|---|---|
| `docker-osx` / QEMU-KVM | `/dev/kvm` absent; `/proc/cpuinfo` exposes no `vmx`/`svm`; `kvm_intel`/`kvm_amd` nested params absent |
| Darling (no-VM translation layer) | needs the `darling-mach` LKM; `/lib/modules` absent and `modprobe` fails (rc=1) even `--privileged` |
| QEMU TCG software emulation | hours-to-boot on 5 shared vCPUs, exercises only the darwin-**x64** build, and Apple's licence restricts macOS virtualization to Apple hardware |

Rejected. The macOS *extraction* half is verified against the real binary
anyway, which is where the risk actually lived.

## 3. Goals / non-goals

**Goals**
1. `extract_bun.py` handles Mach-O and ELF; refuses PE with a clear message.
2. `postprocess.py` works, driven by call shapes *read out of* the real binaries.
3. `build.sh` runs clean end-to-end on Linux and macOS, producing artifacts.
4. A real `bun-1.3.14 cli.cjs --version` run on Linux, answering findings §10.
5. A test suite that does not depend on network, Bun, or a 300 MB binary.
6. Docs that state exactly what is verified, on what, and what is not.

**Non-goals**
- PE *extraction* (detection only — explicitly scoped out).
- Installing anything on `PATH`. `build.sh` emits artifacts and prints the run
  command; it must never shadow the system `claude`.
- Deobfuscating or modifying Claude's application behaviour.

## 4. Architecture

Keep the repo's existing shape: standalone, zero-dependency scripts runnable on
stock `python3` (3.9+). No package, no `pip install`. Two layers inside
`extract_bun.py`:

```
container layer     bytes -> (payload_offset, payload_size)
                    ├── Mach-O   __BUN,__bun     (verified; unchanged logic)
                    ├── ELF      .bun            (new)
                    └── PE       .bun            (detected, refused)
                                   │
payload layer       identical for all three containers
                    u64 length prefix · trailer "\n---- Bun! ----\n"
                    · 32-byte offsets struct · N × 52-byte module records
```

The split is justified by observation, not aesthetics: all three containers wrap
a byte-identical module graph. The payload layer is already correct and stays
untouched apart from validation.

`postprocess.py` remains a separate pure text-transform script — it shares no
state with extraction and is independently testable.

## 5. The postprocess transforms

`status.md` item #4 required the asset mechanism be read out of the minified
code rather than guessed. It was:

```js
var _qo="/$bunfs/root/chart.umd.min.js";
function xob(){
  return Uyp.readFile(bqo.isAbsolute(_qo) ? _qo
       : bqo.join("/home/runner/work/claude-cli-internal/claude-cli-internal/src/frame", _qo), "utf8")
}
```

A plain **string constant** consumed by `fs/promises.readFile` — not an import
assertion, not `Bun.file`. Since `/$bunfs/root/…` is absolute, `isAbsolute()` is
true and the string is read verbatim.

This collapses two planned transforms into one:

| # | Transform | Rationale |
|---|---|---|
| 1 | strip leading `//` pragma lines | Bun's CJS loader requires the file to start with `(function`. Observed head: `// @bun @bytecode @bun-cjs\n(function(exports, require, module, __filename, __dirname) {` |
| 2 | **string literal** `"/$bunfs/root/<name>"` → `require('path').join(__dirname,'assets','<name>')` | Handles the `readFile` assets **and** the `require("….node")` addons uniformly — the latter simply become dynamic requires of an absolute path. 5 occurrences on 2.1.222. |
| 3 | `fileURLToPath(import.meta.url)` → `__filename` | Kept, but its true hit count is reported rather than assumed; if it is 0 on both real binaries the docs say so instead of implying it fired. |
| 4 | invoke the trailing IIFE | Observed tail: `…PSE();})\n` → append `(exports, require, module, __filename, __dirname)` |
| 5 | report leftover `/$bunfs/` references | Expected: **0** after transform 2. |

`__dirname` inside the wrapper resolves to the directory of `cli.original.cjs`,
which is exactly where `assets/` sits — so the rewritten paths are correct by
construction.

## 6. Error handling

The current scaffold only *warns* on a bad transform count and still writes
output, so a silently-broken `cli.cjs` reaches Bun and surfaces as the confusing
`Expected CommonJS module to have a function wrapper` panic. Changing to
fail-fast:

- **Fatal, non-zero exit:** output does not start with `(function`; IIFE
  invocation count ≠ 1.
- **Warn:** leftover `/$bunfs/` references; `fileURLToPath` count 0.
- **Extractor validation:** container magic, trailer magic, `modules_size % 52`,
  `entry_point_id` in range, exactly one entry module.
- **PE input:** explicit "PE detected — extraction not supported" rather than
  today's raw unrecognized-magic error.

## 7. Testing

**Unit (fast, hermetic — no network, no Bun, no 300 MB binary).** Synthetic
fixture builders construct minimal Mach-O / ELF / PE files wrapping a hand-built
module graph, a few KB each:

- round-trip: build a graph → extract → contents and names match exactly
- the §5a invariant: a `base64`-loader module's stored bytes are written
  **verbatim**, never base64-decoded (the bug that once produced 71-byte modules)
- rejection paths: bad trailer, `modules_size` not a multiple of 52,
  `entry_point_id` out of range, PE input, unknown container magic
- postprocess: pragma stripping, both `/$bunfs/` shapes (the `require(...)` addon and the bare string constant), IIFE invocation,
  fatal-on-malformed-tail

**Integration (marked, auto-skipped when the binary is absent).** Runs the real
extractor against `/usr/bin/claude` and, when present, the downloaded darwin
binary; asserts the module tables observed in §2.

## 8. Verification ladder — the deliverable evidence

| Rung | Linux / ELF | macOS / Mach-O |
|---|---|---|
| L1 extract → module table matches | run here | run here (2.1.239) |
| L2 postprocess → counts, 0 leftovers | run here | run here |
| L3 `node --check` — CJS wrapper is syntactically valid | run here | run here |
| L4 **`bun-1.3.14 cli.cjs --version` → `2.1.222 (Claude Code)`** | **run here** | needs a Mac |
| L5 rewritten asset paths exist on disk | run here | run here |

L3 matters for the darwin path specifically: a broken pragma-strip or IIFE
append is *the* failure mode, and syntactic validity catches it without
executing anything. What remains unverifiable on darwin is runtime behaviour, and
the docs will say precisely that — a one-command verifier ships for whoever has
a Mac.

## 9. Outcome handling for findings §10

L4 answers the project's stated central risk — *does a current Claude `cli.js`
still run on the last Zig Bun?* — empirically, for the first time. Both outcomes
are deliverable:

- **Runs:** §10 is marked resolved-as-of 2.1.222 with the evidence.
- **Fails:** the exact error is the finding. Docs record the failing version, the
  error, and whether it is a missing-API or wrapper problem. The project's premise
  being falsified for current builds is a legitimate, documented result — not a
  failure to be papered over.

## 10. Documentation changes

- `status.md` — replace the scaffold map with a verified-on-what matrix.
- `findings.md` — add the Linux/ELF section; correct §4 (module lists are
  per-platform: 15 on darwin-arm64 2.1.239 vs 8 on linux-x64 2.1.222, and the
  entry module *name* differs); record the §5a confirmation on ELF; answer §10.
- `bun-section-format.md` — add ELF and PE container location; keep the payload
  spec as the shared core.
- `runbook.md` — Linux and macOS paths; no-install verification.
- `README.md` — status table reflecting reality.

## 11. Risks

| Risk | Mitigation |
|---|---|
| `cli.js` needs Bun APIs newer than 1.3.14 | This is findings §10 and is the thing being measured; §9 covers both outcomes |
| Dynamic `require(path.join(…))` behaves differently from a literal require | L4 exercises it directly; the `.node` addons load at startup |
| Minified identifiers change every release | Transforms target *structure* (string literals, file head/tail), not identifiers; counts are asserted so drift fails loudly |
| Accidentally shadowing the system `claude` | `build.sh` installs nothing; verification uses absolute paths only |
| 300 MB binaries in tests | Unit tests use KB-scale synthetic fixtures; integration tests auto-skip |
