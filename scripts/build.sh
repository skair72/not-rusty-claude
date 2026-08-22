#!/usr/bin/env bash
#
# build.sh - end-to-end "de-rust" build for not-rusty-claude.
#
# Locates the installed native Claude binary, extracts cli.js + assets out of
# its Bun standalone section, post-processes the JS to run outside the sandbox,
# and installs a launcher that runs it under a stock (Zig-era) Bun.
#
# The signed native binary is only READ, never modified or executed. No
# re-signing is involved.
#
# ┌────────────────────────────────────────────────────────────────────────┐
# │ 🟡 SCAFFOLD / BACKBONE — NEVER EXECUTED end-to-end.                      │
# │ Only tools/extract_bun.py (step 3) is verified. Post-process (step 4)   │
# │ and running under Bun (steps 5–6) are unverified. This wires the pieces │
# │ together as the INTENDED flow; expect to fix things as you go.          │
# │ Completion guide: docs/status.md · Manual steps: docs/runbook.md.       │
# └────────────────────────────────────────────────────────────────────────┘
#
# Usage:
#   scripts/build.sh [path-to-native-binary]
#
# Env:
#   BUN_BIN     bun to run under (default: `command -v bun`)
#   INSTALL_DIR where cli.cjs/launcher land (default: ~/.not-rusty-claude)
#   BIN_DIR     where the `claude` launcher goes (default: ~/.local/bin)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$HOME/.not-rusty-claude}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
MIN_BUN="1.3.14"   # last Zig release AND ClawGod's documented minimum

info()  { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()   { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# ── 1. Locate the native binary ────────────────────────────────────────
NATIVE="${1:-}"
if [ -z "$NATIVE" ]; then
  DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
  # newest version dir under versions/
  NATIVE="$(ls -1d "$DATA"/claude/versions/* 2>/dev/null | sort -V | tail -1 || true)"
fi
[ -n "$NATIVE" ] && [ -f "$NATIVE" ] || die "native Claude binary not found; pass it as an argument"
info "native binary: $NATIVE"

# ── 2. Check Bun (Zig-era) ─────────────────────────────────────────────
BUN_BIN="${BUN_BIN:-$(command -v bun || true)}"
if [ -z "$BUN_BIN" ] || [ ! -x "$BUN_BIN" ]; then
  warn "bun not found. Install the last Zig release ($MIN_BUN):"
  warn "  curl -fsSL https://bun.sh/install | bash -s \"bun-v$MIN_BUN\""
  die "install bun $MIN_BUN, then re-run (or set BUN_BIN=...)"
fi
BUN_VER="$("$BUN_BIN" --version 2>/dev/null | head -1 | sed 's/-.*//')"
info "bun: $BUN_VER ($BUN_BIN)"
# lowest of (BUN_VER, MIN_BUN) must be MIN_BUN → BUN_VER >= MIN_BUN
if [ "$(printf '%s\n%s\n' "$BUN_VER" "$MIN_BUN" | sort -V | head -1)" != "$MIN_BUN" ]; then
  warn "bun $BUN_VER is below the required minimum $MIN_BUN."
  warn "Older Bun panics on cli.original.cjs ('Expected CommonJS module to have"
  warn "a function wrapper'). Install bun $MIN_BUN (the last Zig release)."
  die "bun too old"
fi
if [ "$BUN_VER" != "$MIN_BUN" ] && \
   [ "$(printf '%s\n%s\n' "$BUN_VER" "$MIN_BUN" | sort -V | tail -1)" != "$MIN_BUN" ]; then
  warn "bun $BUN_VER is NEWER than $MIN_BUN — it may be a post-Zig (Rust) build,"
  warn "which defeats the de-rust goal. Prefer exactly $MIN_BUN. Continuing anyway."
fi

# ── 3. Extract ─────────────────────────────────────────────────────────
WORK="$INSTALL_DIR/extract"
info "extracting cli.js + assets → $WORK"
rm -rf "$WORK"
mkdir -p "$INSTALL_DIR"
"$HERE/tools/extract_bun.py" "$NATIVE" "$WORK"
[ -f "$WORK/cli.original.js" ] || die "extraction failed: cli.original.js missing"

# ── 4. Post-process ────────────────────────────────────────────────────
info "post-processing cli.js for external Bun"
"$HERE/tools/postprocess.py" "$WORK"
[ -f "$WORK/cli.original.cjs" ] || die "post-process failed: cli.original.cjs missing"

# ── 5. Wrapper (cli.cjs requires the processed cli) ────────────────────
cat > "$INSTALL_DIR/cli.cjs" <<EOF
// not-rusty-claude wrapper — runs Claude Code's JS under a Zig-era Bun.
require('./extract/cli.original.cjs');
EOF

# ── 6. Launcher on PATH ────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
LAUNCH="$BIN_DIR/claude"
cat > "$LAUNCH" <<EOF
#!/bin/bash
# not-rusty-claude launcher
export CLAUDE_CODE_EXECPATH="$NATIVE"
exec "$BUN_BIN" "$INSTALL_DIR/cli.cjs" "\$@"
EOF
chmod +x "$LAUNCH"

info "installed launcher: $LAUNCH"
info "done. Verify with:  \"$LAUNCH\" --version"
echo
warn "This build's run-half is not yet project-verified — confirm --version and"
warn "a real prompt work, then update docs/findings.md §6/§10. See docs/runbook.md."
