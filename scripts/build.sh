#!/usr/bin/env bash
#
# build.sh - extract Claude Code's JS from its Bun standalone binary and
# post-process it to run under a stock Zig-era Bun.
#
# Produces artifacts and prints how to run them. Installs NOTHING on PATH:
# creating a `claude` executable could shadow the real one.
#
# Usage:
#   scripts/build.sh [path-to-native-binary]
#
# Env:
#   BUN_BIN   bun to check against (default: `command -v bun`)
#   OUT_DIR   where artifacts land (default: ./build)

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$HERE/build}"
MIN_BUN="1.3.14"   # last Zig release AND the minimum that loads Claude's cli.js

info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# 1. Locate the native binary
NATIVE="${1:-}"
if [ -z "$NATIVE" ]; then
  DATA="${XDG_DATA_HOME:-$HOME/.local/share}"
  NATIVE="$(ls -1d "$DATA"/claude/versions/* 2>/dev/null | sort -V | tail -1 || true)"
fi
if [ -z "$NATIVE" ]; then
  NATIVE="$(command -v claude || true)"
fi
[ -n "$NATIVE" ] && [ -f "$NATIVE" ] || die "native Claude binary not found; pass it as an argument"
info "native binary: $NATIVE"

# 2. Check Bun (advisory - extraction works without it)
BUN_BIN="${BUN_BIN:-$(command -v bun || true)}"
if [ -n "$BUN_BIN" ] && [ -x "$BUN_BIN" ]; then
  BUN_VER="$("$BUN_BIN" --version 2>/dev/null | head -1 | sed 's/-.*//')"
  info "bun: $BUN_VER ($BUN_BIN)"
  if [ "$(printf '%s\n%s\n' "$BUN_VER" "$MIN_BUN" | sort -V | head -1)" != "$MIN_BUN" ]; then
    warn "bun $BUN_VER is below $MIN_BUN; it will panic with"
    warn "'Expected CommonJS module to have a function wrapper'."
  elif [ "$BUN_VER" != "$MIN_BUN" ]; then
    warn "bun $BUN_VER is newer than $MIN_BUN - it may be a post-Zig (Rust) build,"
    warn "which defeats the de-rust goal. Prefer exactly $MIN_BUN."
  fi
else
  warn "bun not found; artifacts will still be built. Install the last Zig release:"
  warn "  curl -fsSL https://bun.sh/install | bash -s \"bun-v$MIN_BUN\""
fi

# 3. Extract into a staging sibling, never over the live artifacts.
#
# A previous version rm -rf'd $WORK before extracting, so a failed rebuild (bad
# binary, a Claude release whose cli.js no longer transforms) destroyed the last
# known-good build - exactly the artifacts the docs tell you to keep as the
# recovery path when a new Claude version will not run. Build beside it and swap
# only after post-processing has succeeded.
WORK="$OUT_DIR/extract"
STAGE="$OUT_DIR/.extract.stage.$$"
PREV="$OUT_DIR/.extract.prev.$$"

cleanup() {
  rm -rf "$STAGE"
  # if we were interrupted between the two moves of the swap, put the old
  # build back rather than leaving the user with nothing
  if [ -d "$PREV" ]; then
    if [ -e "$WORK" ]; then rm -rf "$PREV"; else mv "$PREV" "$WORK"; fi
  fi
}
trap cleanup EXIT

mkdir -p "$OUT_DIR"
rm -rf "$STAGE"
info "extracting cli.js + assets -> $WORK"
"$HERE/tools/extract_bun.py" "$NATIVE" "$STAGE"
[ -f "$STAGE/cli.original.js" ] || die "extraction failed: cli.original.js missing"

# 4. Post-process
info "post-processing cli.js for external Bun"
"$HERE/tools/postprocess.py" "$STAGE"
[ -f "$STAGE/cli.original.cjs" ] || die "post-process failed: cli.original.cjs missing"
[ -f "$STAGE/cli.js" ] || die "post-process failed: cli.js sibling missing"

# 4b. Swap the staged build in. Everything above this line is reversible; if any
# of it failed, the previous $WORK is still untouched on disk.
if [ -e "$WORK" ]; then mv "$WORK" "$PREV"; fi
mv "$STAGE" "$WORK"
rm -rf "$PREV"

# 5. Report - no install
info "artifacts ready:"
printf '      %s\n' "$WORK/cli.original.cjs" "$WORK/assets/"
echo
info "run it with:"
printf '      %s %s --version\n' "${BUN_BIN:-bun}" "$WORK/cli.original.cjs"
echo
warn "Nothing was installed on PATH. Creating a 'claude' launcher could shadow"
warn "your real installation - run the command above by full path instead."
