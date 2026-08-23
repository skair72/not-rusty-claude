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
#   NRC_NO_IMAGE_SHIM=1
#             build WITHOUT the scoped isStandaloneExecutable image shim, i.e.
#             exactly what this repo shipped before it existed. It is the "as
#             shipped" half of the A/B in docs/findings.md 11; passed straight
#             through to postprocess.py.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${OUT_DIR:-$HERE/build}"
# Last Zig release, and the minimum Bun that loads the artifact THIS script
# builds. It is not a floor of Claude's: the same entry module runs on 1.3.13
# in a pragma-preserving build shape (docs/findings.md 6 and 10). Nor is Zig
# required - the artifact also runs on 1.4.0, the Rust build. Pinning 1.3.14
# is the project's goal, not a technical constraint.
MIN_BUN="1.3.14"

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
    warn "bun $BUN_VER is newer than $MIN_BUN, so it is a post-Zig (Rust) build."
    warn "The artifact does run there - measured on 1.4.0 - but running on Zig is"
    warn "the point of this project. Prefer exactly $MIN_BUN."
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
#
# stdout is teed so the shim status can be reported here as well. The log lives
# INSIDE $STAGE so it is disposed of by whichever path runs next: the EXIT trap
# rm -rf's $STAGE on failure, and the success path deletes it before the swap -
# nothing of ours is ever left in $OUT_DIR.
info "post-processing cli.js for external Bun"
POST_LOG="$STAGE/.postprocess.log"
"$HERE/tools/postprocess.py" "$STAGE" | tee "$POST_LOG"
[ -f "$STAGE/cli.original.cjs" ] || die "post-process failed: cli.original.cjs missing"
[ -f "$STAGE/cli.js" ] || die "post-process failed: cli.js sibling missing"

# 4a. Say out loud which of the two artifacts this is. Measured on both real
# binaries, the shimmed and unshimmed outputs differ in exactly FOUR bytes
# (`CE()`/`AE()` -> `true`), and that difference only becomes visible when
# someone Reads an image big enough to need resizing - far too late to start
# wondering which build this was.
SHIM_N="$(sed -n 's/^image shim applied *: *\([0-9][0-9]*\).*/\1/p' "$POST_LOG")"
rm -f "$POST_LOG"
# ...and remember it, because the closing summary's list of gaps is only true
# for one of the two builds.
GAPS="image processing, sandbox, ripgrep"
if [ "${SHIM_N:-}" = "1" ]; then
  GAPS="sandbox, ripgrep, install identity"
  info "image shim APPLIED: the native image-processor branch is reachable,"
  info "  which is what the Read tool needs to resize a large image. Every other"
  info "  isStandaloneExecutable gate (ripgrep, sandbox, updater) stays false."
elif [ -n "${NRC_NO_IMAGE_SHIM:-}" ]; then
  warn "image shim NOT APPLIED (NRC_NO_IMAGE_SHIM is set): this is the"
  warn "  'as shipped' build, with the native image-processor branch"
  warn "  unreachable exactly as in every build before the shim existed."
else
  warn "image shim NOT APPLIED: postprocess.py could not find the gate or its"
  warn "  anchor - see its warning above for which. The artifact is otherwise"
  warn "  fine, just with image processing degraded as it was before the shim."
  warn "  Most likely a new Claude release renamed the anchor string."
fi

# 4b. Swap the staged build in. Everything above this line is reversible; if any
# of it failed, the previous $WORK is still untouched on disk.
if [ -e "$WORK" ]; then mv "$WORK" "$PREV"; fi
mv "$STAGE" "$WORK"
rm -rf "$PREV"
info "staged build swapped into place -> $WORK"

# 5. Report - no install
info "artifacts ready:"
printf '      %s\n' "$WORK/cli.original.cjs" "$WORK/cli.js" "$WORK/assets/"
echo
info "run it with ('mcp list' is the smoke test, not '--version'):"
printf '      DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR="$(mktemp -d)" \\\n'
printf '        %s %s mcp list\n' "${BUN_BIN:-bun}" "$WORK/cli.original.cjs"
echo
# Five lines, not the seventeen this used to print. A wall of warnings after
# every successful build is a wall nobody reads, and the two that can cost the
# user something - the updater and the behaviour gap - were buried in it.
warn "keep DISABLE_AUTOUPDATER=1: without it, 'claude update' would install a"
warn "  DIFFERENT, npm-based Claude Code on your machine. Rebuild instead."
warn "not identical to the native binary ($GAPS):"
warn "  read docs/findings.md section 11 before real use."
warn "nothing was installed on PATH - run the command above by full path."
