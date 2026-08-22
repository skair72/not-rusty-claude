# Runbook — de-rust Claude Code on a fresh Mac

Step-by-step to run Claude Code's JavaScript under a **Zig-era Bun (1.3.14)**
instead of Anthropic's bundled runtime. Follow this on the target Apple-Silicon
Mac. The signed native binary is only read — never modified or re-signed.

> Extraction is verified. The **run-under-bun steps (4–6) are not yet
> project-verified** — you are confirming them here. Note any deviation back
> into [findings.md](./findings.md) §6/§10.

---

## 0. Prerequisites

- Apple-Silicon (arm64) macOS with Claude Code already installed the native way
  (`~/.local/share/claude/versions/<v>` exists).
- `python3` (the system `/usr/bin/python3` is fine — the tools target 3.9+).
- This repo checked out on the machine.

Confirm the native install and that it's a Bun standalone:

```bash
DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
NATIVE="$(ls -1d "$DATA"/claude/versions/* | sort -V | tail -1)"
echo "$NATIVE"
otool -l "$NATIVE" | grep -A2 __BUN        # expect a __BUN segment / __bun section
```

---

## 1. Install a Zig-era Bun (1.3.14)

1.3.14 is the **last Zig release** before the Rust rewrite, and also the
**minimum** that can load Claude's `cli.js` (older Bun panics with *"Expected
CommonJS module to have a function wrapper"*). So pin exactly 1.3.14.

```bash
curl -fsSL https://bun.sh/install | bash -s "bun-v1.3.14"
export PATH="$HOME/.bun/bin:$PATH"
bun --version          # → 1.3.14
```

> On macOS arm64 you are on a Zig build regardless (the Rust rewrite is
> Linux-x64-only, experimental). Pinning 1.3.14 guarantees Zig explicitly and
> matches the API level Claude expects.

---

## 2–6. One command (🟡 scaffold — expect to debug)

`build.sh` is a backbone that wires extraction, post-processing, wrapper, and
launcher install together. Only extraction is verified; treat a clean run as
unlikely on the first try and keep [status.md](./status.md) open beside it.

```bash
scripts/build.sh
# or point it at a specific binary:  scripts/build.sh "$NATIVE"
```

Then verify:

```bash
"$HOME/.local/bin/claude" --version      # expect the Claude Code version string
"$HOME/.local/bin/claude" -p "say hi"    # a real prompt through the Zig Bun
```

If both work, the de-rust is functional — update
[findings.md](./findings.md) §6/§10 to mark the run-half **verified ✅**.

---

## What build.sh does (manual equivalent)

If you'd rather run the steps by hand, or `build.sh` fails partway:

```bash
INSTALL="$HOME/.not-rusty-claude"
WORK="$INSTALL/extract"

# 2. extract cli.js + assets (verified)
tools/extract_bun.py "$NATIVE" "$WORK"
#   → $WORK/cli.original.js  +  $WORK/assets/*.node  +  *.js  + file assets

# 3. post-process the JS to run outside the standalone sandbox
tools/postprocess.py "$WORK"
#   → $WORK/cli.original.cjs   (watch the "leftover /$bunfs/" report)

# 4. wrapper
printf "require('./extract/cli.original.cjs');\n" > "$INSTALL/cli.cjs"

# 5. launcher on PATH
cat > "$HOME/.local/bin/claude" <<EOF
#!/bin/bash
export CLAUDE_CODE_EXECPATH="$NATIVE"
exec "$(command -v bun)" "$INSTALL/cli.cjs" "\$@"
EOF
chmod +x "$HOME/.local/bin/claude"

# 6. run
claude --version
```

---

## Surviving Claude updates

Anthropic's auto-update installs a new native binary under
`.../claude/versions/` and repoints its own launcher. Because our `claude`
launcher runs extracted JS instead, it keeps running the **old** extracted
version until you re-run `build.sh`. To move to a new version:

```bash
scripts/build.sh          # re-extracts from the newest versions/ binary
claude --version          # confirm it still runs on Bun 1.3.14
```

⚠️ **This is the moment the project can break** — see §10 of findings. If the
new build was compiled against a canary Bun newer than 1.3.14, `claude
--version` will fail on 1.3.14. If so, keep the previous working extract (it's
under `~/.not-rusty-claude/extract`) and pin to that Claude version.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Expected CommonJS module to have a function wrapper` | Bun older than 1.3.14, or the pragma wasn't stripped | Install bun 1.3.14; re-run postprocess; check `cli.original.cjs` starts with `(function` |
| Missing Bun API / `undefined is not a function` at startup | Claude built against a Bun **newer** than 1.3.14 (§10) | Pin to an older Claude version, or provide a shim |
| `Cannot find module '.../assets/X.node'` | asset not extracted, or path not rewritten | Confirm `assets/X.node` exists; check postprocess `.node requires rewired` count |
| A feature using mermaid/highlight/chart breaks | a `file`-loader asset still referenced via `/$bunfs/` | See postprocess "leftover /$bunfs/" report; add a rewrite for that path |
| `claude` runs the native binary, not ours | `~/.local/bin` not first on PATH, or launcher overwritten by an update | Re-run build.sh; ensure `~/.local/bin` precedes the native launcher on PATH |

---

## Appendix — relocation (no de-rust, no patch)

If all you want is to move the **native** install to another Mac unchanged: copy
the binary bytes verbatim, place it at `$XDG_DATA_HOME/claude/versions/<v>`,
symlink `~/.local/bin/claude` to it. A Mach-O signature is path-independent, so
it verifies and runs with no re-sign. Match the CPU arch (this build is thin
arm64). Details and the SIGKILL-on-modify facts are in
[findings.md](./findings.md) §7.
