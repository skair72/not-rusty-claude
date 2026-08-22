# Project status & how to finish it

This project is **documentation + script backbones**, not a working install.
Extraction of the JS is verified; the "run it under Bun" half is scaffolded from
prior art but **has not been executed or verified**. This file is the map for
completing it.

Read [findings.md](./findings.md) first for the verified facts, then this file
for what's left.

---

## Legend

- ✅ **Verified** — run on a real binary this session; trust it.
- 🟡 **Scaffold** — written from prior art / the format spec, plausible but
  **never executed**. Treat every line as a hypothesis to test.
- ❌ **Not started** — named here so it isn't forgotten.

---

## Component status

| Component | State | Notes |
|---|---|---|
| `tools/extract_bun.py` | ✅ Verified | Parses the module table; extracts `cli.original.js` + all 9 assets as raw bytes; `.node` confirmed as real Mach-O. |
| `docs/findings.md`, `docs/bun-section-format.md` | ✅ Verified | Every ✅ fact observed directly. |
| `tools/postprocess.py` | 🟡 Scaffold | Transforms ported from ClawGod's `post-process.mjs`. **Never run.** Regexes (esp. the `fileURLToPath` one) are guesses against minified code and will likely need adjustment. |
| `scripts/build.sh` | 🟡 Scaffold | Wires the steps together + a Bun version gate. **Never run.** |
| `tools/patch_claude.py` (Approach B) | ✅ Verified | The *byte-patch + re-sign* alternative was verified end-to-end. Not part of the de-rust path, kept for non-JS edits. |
| Running `bun cli.cjs` on Zig 1.3.14 | ❌ Not started | The whole point; needs a Mac with Bun installed. |
| `file`-loader asset rewrites (mermaid/hljs/chart/html) | ❌ Not started | Only `.node` requires are rewritten so far. |

---

## Work items, in order

Each item says **how to verify** and **how to fix** so a future session can pick
up cold.

### 1. Confirm `extract_bun.py` on the target binary 🟡→✅
The extractor is verified on `2.1.238`; module names/loaders can change between
versions ([tweakcc#584](https://github.com/Piebald-AI/tweakcc/issues/584) shows
a rename breaking extractors).

- **Verify:** `tools/extract_bun.py <native> /tmp/x` → expect exactly 1
  `cli.original.js` and several `assets/*`. `file /tmp/x/assets/*.node` → all
  Mach-O.
- **Fix if it breaks:** re-check the module table against
  [bun-section-format.md](./bun-section-format.md); print every module's
  `(loader, name, size)` and adjust which loaders get written to `assets/`.

### 2. Run `postprocess.py` and inspect the output 🟡→✅
Never executed. Confirm each transform actually fired.

- **Verify:**
  ```
  tools/postprocess.py /tmp/x
  head -c 40 /tmp/x/cli.original.cjs      # must start with "(function"
  tail -c 80 /tmp/x/cli.original.cjs      # must end with the (exports,require,...) call
  ```
  Check the printed counts: `.node requires rewired` should be **5** on 2.1.238;
  `IIFE invocations added` must be **1**.
- **Fix if a count is 0:**
  - pragma not stripped → the file didn't start with `//` lines; inspect the
    first bytes and widen the strip regex.
  - `.node` count 0 → the require shape isn't `require('/$bunfs/root/X.node')`;
    grep the raw `cli.original.js` for `bunfs` and `.node` to find the real call
    shape, then update the regex in `postprocess.py` step (2).
  - IIFE count 0 → the file doesn't end in `})`; look at the last ~200 bytes and
    update step (4)'s anchor.
  - `fileURLToPath` count 0 is probably fine (may not appear); the regex is the
    least-certain one — verify against the actual minified identifiers.

### 3. Install Bun 1.3.14 and run it ❌→✅
- **Verify:** per [runbook.md](./runbook.md) §1–§6:
  `claude --version` and `claude -p "hi"` must both work.
- **Expected failure to plan for:** `Expected CommonJS module to have a function
  wrapper` (Bun < 1.3.14, or pragma/IIFE wrong) — see findings §10.

### 4. Handle leftover `/$bunfs/` asset paths ❌→✅
`postprocess.py` prints any `/$bunfs/root/…` still in the output after rewriting.
Each is a `file`-loader asset (mermaid, highlight.js, chart, the HTML template)
that a feature loads at runtime.

- **Verify:** exercise the feature (render a mermaid diagram, syntax
  highlighting) and watch for `ENOENT`/`Cannot find module`.
- **Fix:** add rewrites mapping those `/$bunfs/root/NAME` references to
  `path.join(__dirname,'assets','NAME')`, mirroring the `.node` rewrite. The
  exact call shape (import assertion? `Bun.file`? plain `readFileSync`?) must be
  read out of the minified `cli.js` first — don't guess the mechanism.

### 5. Update-survival & version pinning ❌
- Re-running `build.sh` re-extracts from the newest `versions/` binary.
- **The project's kill switch (findings §10):** if a future Claude is built on a
  canary Bun newer than 1.3.14, its `cli.js` won't run on Zig. Keep the last
  working `~/.not-rusty-claude/extract` and pin that Claude version. Document the
  first version where this happens if it does.

---

## Known unknowns

- **Exact minified call shapes.** All `postprocess.py` regexes target minified
  output that changes every release. They are starting points, not contracts.
- **Universal vs thin addons.** Two `.node` are universal (x86_64+arm64); the
  others thin arm64. Not a problem for arm64 hosts, but note it if porting to
  Intel.
- **Canary API drift.** Whether Anthropic's bundled Bun stays within 1.3.14's
  API surface over time is the single biggest risk and is outside our control.
- **`CLAUDE_CODE_EXECPATH`.** The launcher points it at the native binary for
  shell integrations; whether the extracted CLI needs anything else from the
  original at runtime is unverified.

---

## What NOT to do

- Don't `base64`-decode the `base64`-loader modules — they're raw Mach-O
  (findings §5a).
- Don't re-sign anything for the de-rust path — the signed binary is never run.
  Re-signing only applies to Approach B (`patch_claude.py`).
- Don't assume ClawGod's extractor is a drop-in — it only handles `napi`-loader
  modules and misses the `base64` addons on current builds (findings §5b).
