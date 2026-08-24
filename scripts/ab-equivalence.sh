#!/usr/bin/env bash
#
# ab-equivalence.sh - run the extracted Claude Code artifact THREE ways against
# the committed loopback mock and print the difference.
#
# The three sides are the same extraction of the same native binary. Two of them
# differ only in whether tools/postprocess.py applied the scoped image shim; the
# third is the patch this project deliberately did NOT make:
#
#   as shipped : NRC_NO_IMAGE_SHIM=1 scripts/build.sh   (no shim: the CLI takes
#                its non-standalone branch and never reaches for
#                image-processor.node - docs/findings.md 11)
#   shimmed    : scripts/build.sh                       (shim applied to the ONE
#                image-processor gate call site)
#   global     : the as-shipped artifact plus
#                `try{Bun.isStandaloneExecutable=true}catch(e){};` injected right
#                after its CJS wrapper header - i.e. the obvious one-line "fix",
#                flipping the flag for every gate site at once.
#
# WHY: docs/findings.md section 11 claims a behaviour flip between the first two
# builds - a Read of an oversized PNG errors on one side and returns a JPEG on
# the other - and claims that the *global* flip buys that same JPEG at the price
# of silently breaking Grep. That second claim is the entire justification for
# the scoped design, so the global side is a case here, not a paragraph: one
# command has to show as-shipped (no image, Grep works), scoped (image works,
# Grep works) and global (image works, Grep BROKEN). The expectations below are
# explicit per side, so if the global flip ever STOPS breaking Grep this script
# fails - the premise the design rests on would have expired and someone needs
# to know.
#
# Everything is loopback and throwaway: 127.0.0.1 mock on an ephemeral port, a
# fake API key, a scratch HOME and CLAUDE_CONFIG_DIR. The real ~/.claude is
# never read or written. /usr/bin/claude is only ever READ (as build input); it
# is never executed - the artifact runs under Bun.
#
# THREE things keep it that way, and each of them was measured by polling
# /proc/<pid>/fd against /proc/net/tcp while a case ran - over every process
# whose ppid chain reaches the artifact, which is not quite the same thing as
# "the whole process tree" (EGRESS below says exactly what escapes it). Each
# was found by that poll, not predicted:
#
#   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
#     WITHOUT it the Bash case opened 6 non-loopback sockets - 5 to
#     160.79.104.10:443 (api.anthropic.com resolves there) and 1 to
#     34.149.66.165:443 (a Google-hosted address, not identified further) -
#     alongside the loopback socket to the mock. WITH it: 0 non-loopback, and
#     nothing left but the loopback connection(s) to the mock. Control: bun
#     alone spinning a 4 s busy loop opened 0 sockets, so it is the CLI's
#     traffic, not bun's.
#   ANTHROPIC_BASE_URL, set for EVERY case including doctor
#     The disable flag is not sufficient on its own. Measured on the doctor case
#     with the flag set but no base URL: 3 non-loopback sockets to
#     160.79.104.10:443, from doctor's Remote Control section. With the base URL
#     pointed at the mock: 0 sockets, and the mock logs no request at all.
#   bun --no-install
#     The one nobody would guess, and it is the AS-SHIPPED side that does it.
#     Measured on the Read case without it: 3 non-loopback sockets to
#     registry.npmjs.org (104.16.3/5/6.34:443) from the as-shipped artifact, and
#     the run's throwaway $HOME left holding a .bun/install/cache with
#     @img/sharp-linux-x64@0.35.3, @img/sharp-libvips-linux-x64@1.3.2,
#     @img/sharp-wasm32@0.35.3, @emnapi/runtime@1.11.3 and tslib@2.8.1 in it.
#     That is Bun's auto-install answering the bundled JS sharp fallback's
#     require for libvips - the fallback findings.md 11 says is unreachable
#     goes shopping for itself. The shimmed and global sides do not: they get
#     the native addon and never ask. With --no-install: 0 non-loopback sockets
#     and every tool_result byte-identical, including the as-shipped Read error
#     - the download does not save that fallback either.
#
# The header of this file used to claim "no traffic leaves the host" while that
# was false, for two independent reasons at once. So the claim is no longer a
# claim: every case now runs under the same poll that found them (see EGRESS
# below), prints its own `egress=` and `egress_guard=` lines, and a non-empty
# egress, a guard that did not finish, or a guard that attributed NO socket at
# all fails the script. That last one is the positive control: an empty egress
# line from a poller that was watching the wrong process is indistinguishable
# from a clean run, so the socket count is reported and checked too.
#
# The INVARIANT is: zero non-loopback sockets on every run, and at least one
# socket attributed to every run that drives a turn. The TOTAL is not an
# invariant and is not quoted here or anywhere else - it moves run to run (the
# CLI opens one or two connections to the mock depending on how it splits the
# turn, and doctor drives no turn at all, so doctor may legitimately open
# none). Each run prints its own count in its `egress_guard=` line; read it
# there rather than comparing against a number in a comment.
#
# PLATFORM: Linux-only, and it refuses to start anywhere else rather than run
# without its own safety net. The egress guard reads /proc/net/tcp and
# /proc/<pid>/fd and there is no portable substitute here, so every dependency
# it needs - bun, node, python3, /proc, timeout(1) - is named by one preflight
# below instead of failing at whichever line comes first. timeout(1) is in that
# list because the shell-watchdog fallback that used to stand in for it is
# gone: the fallback ran only when neither timeout nor gtimeout was on PATH,
# and the host that describes - macOS - is refused two checks earlier for
# having no /proc. It was unreachable code advertised as a portability
# guarantee. A Linux host that somehow lacks timeout(1) is now told so up
# front instead. macOS likewise reaches the preflight and gets one sentence
# about what is missing, instead of dying inside fixture setup on BSD stat's
# `illegal option -- c` (file sizes and md5s go through python3 for the same
# reason).
#
# Usage:
#   scripts/ab-equivalence.sh                       # build all sides, run all cases
#   scripts/ab-equivalence.sh --case read
#   scripts/ab-equivalence.sh --as-shipped some/extract      # reuse a prebuilt tree
#
# --as-shipped / --shimmed skip a build - a few seconds per side (two builds
# timed back to back on this host took 3.9 s and 4.6 s, but this box is shared,
# so treat that as "seconds", not as a number to compare against) - but nothing
# checks that the tree you name was built the way you say. Once scripts/build.sh
# applies the shim by default, build/extract is the SHIMMED side, and handing it
# to --as-shipped compares a tree against itself. The byte-identical warning
# below is the only thing that will tell you.
#
# Options:
#   --case bash|grep|read|doctor|all   which turn(s) to drive     (default: all)
#   --as-shipped DIR            prebuilt extract dir for side A (built NRC_NO_IMAGE_SHIM=1)
#   --shimmed DIR               prebuilt extract dir for side B (built with the shim)
#   --native PATH               native binary to extract from   (default: /usr/bin/claude)
#   --keep                      do not delete the scratch dir on exit
#
# Env:
#   BUN_BIN   bun to run the artifact with (default: $HOME/.bun-1.3.14/bun)

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUN_BIN="${BUN_BIN:-$HOME/.bun-1.3.14/bun}"
MOCK="$REPO/scripts/mock-messages-api.mjs"

CASE="all"
AS_SHIPPED_DIR=""
SHIMMED_DIR=""
NATIVE="${NRC_AB_NATIVE:-/usr/bin/claude}"
KEEP=0

while [ $# -gt 0 ]; do
  case "$1" in
    --case)       CASE="$2"; shift 2 ;;
    --as-shipped) AS_SHIPPED_DIR="$2"; shift 2 ;;
    --shimmed)    SHIMMED_DIR="$2"; shift 2 ;;
    --native)     NATIVE="$2"; shift 2 ;;
    --keep)       KEEP=1; shift ;;
    # awk, not a hardcoded line range: a range silently starts printing code
    # (or stops printing help) the first time this header changes length.
    -h|--help)    awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

info() { printf '\033[36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

# Every run happens from a scratch working directory, so a relative
# --as-shipped would resolve against the wrong cwd. Measured, before this
# existed: `--as-shipped build/extract` produced three cases of
# "(no tool_result)" and an exit 1, with bun's one-line
# `error: Module not found` buried in a stderr file nobody was printing.
abspath() { (cd "$(dirname "$1")" && printf '%s/%s\n' "$(pwd)" "$(basename "$1")"); }

# An unknown case would otherwise reach the mock as `--tool ""`, which is a
# valid text-only turn: the run would "pass" without driving any tool at all.
case "$CASE" in
  all)  CASES="bash grep read doctor" ;;
  bash|grep|read|doctor) CASES="$CASE" ;;
  *) die "unknown --case '$CASE' (want: bash, grep, read, doctor, all)" ;;
esac

# stat(1) and md5sum(1) are not portable - GNU wants `stat -c %s`, BSD/macOS
# `stat -f %z`, and macOS ships `md5`, not `md5sum`. This script already
# requires python3 (the fixture, the summary and the egress guard are all
# python), so one implementation covers both platforms and there is no probe
# left to guess wrong. Measured before this existed, on a macOS-like PATH (a
# BSD `stat` that rejects -c, no md5sum, no timeout): the COMPLETE output of
# `--case bash` was `stat: illegal option -- c` plus a usage line and exit 1,
# from a bare assignment in fixture setup under `set -e` - the script died
# before any of its own dependency checks could say a word.
file_size() { python3 -c 'import os,sys; print(os.path.getsize(sys.argv[1]))' "$1"; }
file_md5()  { python3 -c 'import hashlib,sys
print(hashlib.md5(open(sys.argv[1],"rb").read()).hexdigest())' "$1"; }

# A hanging case must still be bounded, and that bound is timeout(1) - a hard
# requirement checked by the preflight below, not a best effort. gtimeout is
# accepted because coreutils installs it under that name on some hosts; there
# is deliberately no shell-watchdog fallback behind it. The fallback that used
# to be here could only run on a host with neither binary, i.e. a stock macOS,
# and the /proc check below refuses that host before this function is ever
# called - so it was unreachable code that the header and docs/runbook.md were
# both describing as a portability guarantee.
TIMEOUT_BIN=""
if command -v timeout >/dev/null 2>&1; then TIMEOUT_BIN="timeout"
elif command -v gtimeout >/dev/null 2>&1; then TIMEOUT_BIN="gtimeout"; fi

run_timeout() {  # run_timeout <seconds> <cmd...>
  local secs="$1"; shift
  "$TIMEOUT_BIN" "$secs" "$@"
}

# One preflight that names EVERYTHING missing, instead of failing at whichever
# line happens to come first. /proc is in here because the egress guard is the
# mechanism that replaced this file's retracted "no traffic leaves the host"
# claim: running the comparison without it would produce output that looks
# exactly like a clean run.
MISSING=""
if [ ! -x "$BUN_BIN" ]; then MISSING="$MISSING\n  - bun at $BUN_BIN (set BUN_BIN)"; fi
if ! command -v node >/dev/null 2>&1; then MISSING="$MISSING\n  - node (the mock is a node script)"; fi
if ! command -v python3 >/dev/null 2>&1; then MISSING="$MISSING\n  - python3 (fixture, transcript summary, egress guard)"; fi
if [ -z "$TIMEOUT_BIN" ]; then
  MISSING="$MISSING\n  - timeout(1) or gtimeout (the per-case bound; a hung case must not"
  MISSING="$MISSING\n    run until something else on this host kills it)"
fi
if [ ! -r /proc/net/tcp ] || [ ! -d /proc/self/fd ]; then
  MISSING="$MISSING\n  - Linux /proc (/proc/net/tcp and /proc/<pid>/fd): the egress guard reads"
  MISSING="$MISSING\n    them to prove every socket this run opens is loopback. There is no"
  MISSING="$MISSING\n    portable substitute here yet, so this harness is Linux-only."
fi
if [ -n "$MISSING" ]; then
  # shellcheck disable=SC2059
  printf "\033[31merror:\033[0m scripts/ab-equivalence.sh cannot run here. Missing:$MISSING\n" >&2
  exit 1
fi

# if-blocks, not `[ ... ] && x`: under `set -e` a false test at the top level is
# a failing command and would exit the script with status 1 and no message.
if [ -n "$AS_SHIPPED_DIR" ]; then AS_SHIPPED_DIR="$(abspath "$AS_SHIPPED_DIR")"; fi
if [ -n "$SHIMMED_DIR" ]; then SHIMMED_DIR="$(abspath "$SHIMMED_DIR")"; fi

SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/nrc-ab.XXXXXX")"
# Mock PIDs go through files, not a variable: run_case is called inside a
# command substitution, so anything it assigns dies with that subshell and this
# trap would have nothing to kill. Leaking a listening HTTP server out of a
# failed run is exactly the kind of thing that then answers somebody else's
# request an hour later.
cleanup() {
  for pidfile in "$SCRATCH"/*.mockpid "$SCRATCH"/*.egresspid; do
    [ -f "$pidfile" ] || continue
    kill "$(cat "$pidfile")" 2>/dev/null || true
  done
  if [ "$KEEP" = "1" ]; then echo "scratch kept: $SCRATCH"; else rm -rf "$SCRATCH"; fi
}
trap cleanup EXIT

# ---------------------------------------------------------------- fixtures

WORK="$SCRATCH/work"
mkdir -p "$WORK/hay"
printf 'NEEDLE-12345\n' > "$WORK/hay/a.txt"

PNG="$SCRATCH/gradient-3000.png"
# Verbatim from docs/findings.md section 11. The whole point of the Read case
# is that the *input* reproduces, so the fixture is checked - but on its
# DECODED content, not on its file size. The size is a property of the local
# deflate, not of the image: measured on this host, byte-identical scanlines
# (27,003,000 bytes, md5 95adc51dc27c1ad40b52df01793235e2) at level 6 give
# 2,329,429 bytes through python3's zlib 1.2.13 and 2,329,253 through node
# v22's zlib 1.3.1-e00f703. The hard `= 2329429` that used to stand here was
# therefore a `die` in fixture setup - before any case ran - for anyone whose
# python3 links a different-but-correct zlib (zlib-ng-compat, a chromium fork).
PNG_CHECK="$(python3 - "$PNG" <<'EOF'
import hashlib, sys, zlib, struct
W = H = 3000
raw = bytearray()
for y in range(H):
    raw.append(0)                                   # PNG filter: None
    raw += bytes(v for x in range(W)
                 for v in ((x + y) % 256, (x * 2) % 256, (y * 3) % 256))
raw = bytes(raw)
def chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
open(sys.argv[1], "wb").write(
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(raw, 6))
    + chunk(b"IEND", b""))

# Read it back and check what a decoder would see. This is the assertion the
# Read case actually depends on - 3000x3000 is what makes the image oversized,
# and the pixels are what "the same input" means.
blob = open(sys.argv[1], "rb").read()
if blob[:8] != b"\x89PNG\r\n\x1a\n":
    sys.exit("fixture PNG: not a PNG")
pos, idat, ihdr = 8, b"", None
while pos < len(blob):
    ln = struct.unpack(">I", blob[pos:pos + 4])[0]
    tag = blob[pos + 4:pos + 8]
    body = blob[pos + 8:pos + 8 + ln]
    if struct.unpack(">I", blob[pos + 8 + ln:pos + 12 + ln])[0] != zlib.crc32(tag + body):
        sys.exit(f"fixture PNG: CRC mismatch in chunk {tag!r}")
    if tag == b"IHDR":
        ihdr = struct.unpack(">IIBBBBB", body)
    elif tag == b"IDAT":
        idat += body
    pos += 12 + ln
if ihdr[:4] != (W, H, 8, 2):
    sys.exit(f"fixture PNG: header is {ihdr[:4]}, expected {(W, H, 8, 2)}")
back = zlib.decompress(idat)
if back != raw:
    sys.exit(f"fixture PNG: decoded {len(back)} bytes, md5 "
             f"{hashlib.md5(back).hexdigest()}, expected {len(raw)} / "
             f"{hashlib.md5(raw).hexdigest()}")
print(f"PNG_OK {W}x{H} rgb8 raw_bytes={len(raw)} "
      f"raw_md5={hashlib.md5(raw).hexdigest()} file_bytes={len(blob)}")
EOF
)" || die "fixture PNG did not verify (message above); without it the Read case compares nothing"
PNG_BYTES="$(file_size "$PNG")"
# Informational: it moves with the local zlib (see above), so nothing branches
# on it. What was asserted is the decoded image, by the block that just ran.
info "fixture PNG: $PNG_CHECK"
info "  decoded scanlines verified above; the $PNG_BYTES bytes on disk are informational (zlib-dependent)"

# ------------------------------------------------------------------ builds

# Whether the shim exists at all. Until tools/postprocess.py learns
# NRC_NO_IMAGE_SHIM there is only one build to make, and saying so beats
# building two identical trees and calling their sameness a result. The global
# side does not depend on it: it is produced from the as-shipped artifact by
# text injection, so it exists even in a tree where postprocess.py knows nothing
# about any shim.
SHIM_SUPPORTED=0
if grep -q 'NRC_NO_IMAGE_SHIM' "$REPO/tools/postprocess.py"; then SHIM_SUPPORTED=1; fi

# Sets BUILT_DIR rather than echoing it: info() prints to stdout, so a
# $(build_side ...) would capture the progress lines into the path.
BUILT_DIR=""
build_side() {  # build_side <label> <outdir> <shim: yes|no>
  local label="$1" out="$2" shim="$3"
  # ANY non-empty NRC_NO_IMAGE_SHIM opts OUT - both build.sh (`[ -n ... ]`) and
  # postprocess.py (`not os.environ.get(...)`) agree on that rule. So the
  # shimmed side must pass an EMPTY value, never "0". This script passed "0"
  # and therefore built two unshimmed trees: measured, both artifacts came out
  # byte-identical (md5 5e3662ee9e2cfd8143c7a6a1bb0662bb) and the Read case
  # failed on the "shimmed" side with the as-shipped error.
  local noshim=""
  if [ "$shim" = "no" ]; then noshim="1"; fi
  [ -f "$NATIVE" ] || die "native binary not found: $NATIVE (pass --native)"
  info "building '$label' from $NATIVE (NRC_NO_IMAGE_SHIM='$noshim')"
  # OUT_DIR keeps this out of the repo's build/: a comparison run must not
  # clobber the tree the user is otherwise working with.
  OUT_DIR="$out" NRC_NO_IMAGE_SHIM="$noshim" "$REPO/scripts/build.sh" "$NATIVE" >"$out.log" 2>&1 \
    || { sed -n '1,40p' "$out.log" >&2; die "build of '$label' failed; see $out.log"; }
  BUILT_DIR="$out/extract"
}

# The third side. It is NOT a build: it is the as-shipped artifact with one
# statement prepended inside the CJS wrapper, which is exactly what "just set
# the flag" means in practice and is how findings.md 11 measured it. Deriving it
# from the as-shipped artifact rather than from the shimmed one keeps the
# comparison honest - the only difference from side A is the global assignment.
make_global_side() {  # make_global_side <src-extract-dir> <out-dir>
  local src="$1" out="$2"
  rm -rf "$out"
  mkdir -p "$(dirname "$out")"
  # Hardlink the tree: assets/ is tens of MB and exactly one file is rewritten.
  # The hardlink to cli.original.cjs is REMOVED before the injected file is
  # written, so side A's artifact is never modified through it - overwriting a
  # hardlink in place would silently flip the flag on the side we are comparing
  # against. Falls back to a real copy across filesystems.
  cp -al "$src" "$out" 2>/dev/null || cp -a "$src" "$out"
  rm -f "$out/cli.original.cjs"
  python3 - "$src/cli.original.cjs" "$out/cli.original.cjs" <<'EOF'
import sys
src, dst = sys.argv[1], sys.argv[2]
# postprocess.py strips the `// @bun` pragma and hands Bun's CJS loader a file
# that starts with this exact wrapper; injecting after it puts the assignment
# ahead of every gate call while keeping the loader's "starts with (function"
# requirement true. Asserted rather than assumed: a silently un-injected
# artifact would make the global side agree with the as-shipped side and turn
# the whole demonstration into a false negative.
PREFIX = "(function(exports, require, module, __filename, __dirname) {"
INJECT = "try{Bun.isStandaloneExecutable=true}catch(e){};"
data = open(src, encoding="utf-8").read()
if not data.startswith(PREFIX):
    sys.exit("global side: artifact does not start with the CJS wrapper header.\n"
             "  first 80 bytes: " + data[:80])
if INJECT in data:
    sys.exit("global side: the flip is already present in the source artifact")
out = PREFIX + INJECT + data[len(PREFIX):]
open(dst, "w", encoding="utf-8").write(out)
print(f"global side: injected {len(INJECT)} bytes after the CJS wrapper "
      f"({len(data)} -> {len(out)} bytes)")
EOF
}

if [ -z "$AS_SHIPPED_DIR" ]; then
  build_side as-shipped "$SCRATCH/asshipped" no
  AS_SHIPPED_DIR="$BUILT_DIR"
fi
if [ -z "$SHIMMED_DIR" ] && [ "$SHIM_SUPPORTED" = "1" ]; then
  build_side shimmed "$SCRATCH/shimmed" yes
  SHIMMED_DIR="$BUILT_DIR"
fi

A_ART="$AS_SHIPPED_DIR/cli.original.cjs"
[ -f "$A_ART" ] || die "as-shipped artifact missing: $A_ART"
B_ART=""
if [ -n "$SHIMMED_DIR" ]; then
  B_ART="$SHIMMED_DIR/cli.original.cjs"
  [ -f "$B_ART" ] || die "shimmed artifact missing: $B_ART"
fi

info "deriving the globally-flipped side from the as-shipped artifact"
make_global_side "$AS_SHIPPED_DIR" "$SCRATCH/global/extract" | sed 's/^/    /'
G_ART="$SCRATCH/global/extract/cli.original.cjs"
[ -f "$G_ART" ] || die "global artifact missing: $G_ART"

# Identity of what is actually being compared, printed rather than assumed: two
# sides that turn out to be the same bytes would otherwise "agree" for a reason
# that has nothing to do with the shim.
info "bun: $("$BUN_BIN" --version)  node: $(node --version)"
info "as shipped : $A_ART ($(file_size "$A_ART") B, md5 $(file_md5 "$A_ART"))"
if [ -n "$B_ART" ]; then
  info "shimmed    : $B_ART ($(file_size "$B_ART") B, md5 $(file_md5 "$B_ART"))"
  if cmp -s "$A_ART" "$B_ART"; then
    warn "the two artifacts are byte-identical: NRC_NO_IMAGE_SHIM changed nothing."
  fi
else
  warn "no shimmed side: tools/postprocess.py has no NRC_NO_IMAGE_SHIM support yet."
  warn "  running the as-shipped and globally-flipped sides only."
fi
info "global     : $G_ART ($(file_size "$G_ART") B, md5 $(file_md5 "$G_ART"))"

# ------------------------------------------------------------------ runner

# stream-json is a JSONL transcript; this pulls out the one line that matters
# (the tool_result the CLI produced) plus the final result line.
SUMMARIZE="$SCRATCH/summarize.py"
cat > "$SUMMARIZE" <<'EOF'
import base64
import json, sys


def oneline(text):
    """Escape so each field below occupies exactly ONE line.

    Its callers pull these fields back out with `sed -n 's/^tool_result=//p'`,
    which matches only the line that literally begins `tool_result=`. A raw
    newline in a tool_result therefore truncated the value at its first line in
    BOTH the expect/reject check and the SAME/DIFFERS verdict - and this is not
    hypothetical: the Bash case's tool_result is "HELLO-FROM-SUBPROCESS\nLinux"
    (measured), so the "Linux" half was being discarded, and two sides that
    differed only after line 1 would have been reported SAME.
    """
    return (text.replace("\\", "\\\\").replace("\n", "\\n")
                .replace("\r", "\\r").replace("\t", "\\t"))

tool_name = None
summary = "(no tool_result)"
final = "(no result line)"
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        j = json.loads(line)
    except ValueError:
        continue
    t = j.get("type")
    if t == "assistant":
        for b in j.get("message", {}).get("content", []):
            if b.get("type") == "tool_use":
                tool_name = b.get("name")
    elif t == "user":
        content = j.get("message", {}).get("content")
        if isinstance(content, list):
            for b in content:
                if b.get("type") != "tool_result":
                    continue
                err = bool(b.get("is_error"))
                c = b.get("content")
                if isinstance(c, list):
                    parts = []
                    for blk in c:
                        if blk.get("type") == "image":
                            src = blk.get("source", {})
                            data = src.get("data") or ""
                            # decoded length and magic, not the base64 length:
                            # the base64 of a JPEG is not a fact about the JPEG
                            try:
                                raw = base64.b64decode(data)
                            except Exception:
                                raw = b""
                            magic = " ".join(f"{x:02x}" for x in raw[:4])
                            parts.append(
                                f"IMAGE media={src.get('media_type')} "
                                f"decoded_bytes={len(raw)} magic={magic}")
                        else:
                            parts.append(str(blk.get("text"))[:200])
                    body = " | ".join(parts)
                else:
                    body = str(c)[:200]
                summary = f"is_error={err}  {body}"
    elif t == "result":
        final = (f"is_error={j.get('is_error')} num_turns={j.get('num_turns')} "
                 f"result={str(j.get('result'))[:60]!r}")

print(f"tool={oneline(str(tool_name))}")
print(f"tool_result={oneline(summary)}")
print(f"result={oneline(final)}")
EOF

# The loopback-only property, enforced per run instead of asserted in a comment.
# This is how the npm reach-out documented in the header was found, and a header
# is not a mechanism: the previous version of this script claimed "no traffic
# leaves the host" and was wrong about it for two different reasons at once.
#
# Two properties this has to have, and only got in the review that followed:
#
#   It follows the PPID CHAIN, not just the process whose cmdline names the
#   artifact. The Bash tool, `rg`, and any tool-driven network access run in
#   CHILDREN. Measured with the previous version extracted verbatim: a marked
#   process opening a socket to this host's 172.18.0.5:19999 was caught
#   (egress=[172.18.0.5:19999]); the SAME process spawning a child that opened
#   the identical connection reported nothing (egress=[]) while the listener
#   logged the connection both times. With the parent-chain walk below, both
#   are caught, and so is a grandchild - and an unrelated process opening the
#   same socket is still not attributed to this run.
#
#   What a ppid chain cannot follow is a descendant that has been REPARENTED
#   AWAY, and that is a real hole, not a theoretical one. Re-measured on this
#   host with the poller below extracted verbatim and a listener on
#   172.18.0.5:19999, five shapes, one connection each: the marked process
#   itself, a child of it, and a grandchild all came back
#   egress=[172.18.0.5:19999] with sockets=1; a double-forked grandchild whose
#   middle process exits, and the same thing again with setsid(), both came
#   back egress=[] with sockets=0 - while the listener logged the connection in
#   all five. Once the middle process is gone the orphan's ppid is 1 and no
#   chain from it reaches the artifact.
#
#   That is accepted rather than fixed, because the fixes are worse here. The
#   alternatives - attributing by session id or by process group - would take
#   in the harness's OWN session: this script, the node mock, and anything else
#   sharing the shell that started it, which turns the "at least one socket"
#   positive control into noise and the egress line into other people's
#   traffic. What is under test is a CLI driving its own tool subprocesses, and
#   those are ordinary children. A CLI that daemonized itself to phone home
#   would evade this poller; so would a socket opened and closed inside one
#   30 ms gap between polls. Both are MISSES. A hit is still real.
#
#   It fails CLOSED. Being SIGTERMed is the normal end of a case, so "the
#   poller is gone" cannot mean "clean"; it writes a status file and run_case
#   fails any case whose status is not OK. Measured on the previous version:
#   with /proc unreadable it died in 30 ms with an unhandled FileNotFoundError,
#   run_case discarded that exit, and the empty egress file read as a pass.
#
# What it still cannot do is see a socket that opens and closes entirely
# between two 30 ms polls, so a MISS is possible and a hit is real - as long as
# the hit is a real destination, which is why the v6 decoding below matters.
EGRESS="$SCRATCH/egress.py"
cat > "$EGRESS" <<'EOF'
import os, re, signal, socket, sys, time

artifact, outpath, statuspath = sys.argv[1], sys.argv[2], sys.argv[3]
out = open(outpath, "a", buffering=1)
status = open(statuspath, "w", buffering=1)
seen = set()
# Counted, not just filtered: an empty egress file is only evidence if this
# poller was watching the right processes at all. A run that attributes ZERO
# sockets to the artifact's process tree looks exactly like a clean run, and
# every case here opens at least one (the loopback connection to the mock), so
# the caller fails a non-doctor case whose socket count is 0.
egress_count = 0
# backstop: the caller kills this, but if the caller is itself killed, an
# endless /proc scan on a shared box is a bad thing to leave behind. Longer
# than the longest case timeout above (180 s).
deadline = time.time() + 600

# The caller SIGTERMs this poller when the case ends, so "was killed" is the
# NORMAL exit and cannot be used to tell a clean run from a crash. Hence the
# status file: it says OK only on the path that ran the loop to the end. An
# empty or ERROR status makes run_case fail the case. Before this, the poller
# dying on its first iteration (measured: /proc missing -> FileNotFoundError in
# 30 ms) left an empty egress file, which check_side read as "clean" - the
# guard failed OPEN, which is the one way a guard must never fail.
stop = False


def on_signal(signum, frame):
    global stop
    stop = True


for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, on_signal)


def read_text(path):
    try:
        with open(path, "rb") as fh:
            return fh.read().decode("utf8", "replace")
    except OSError:
        # a pid that exited between listdir() and here; not an error
        return None


def inode_table():
    tbl = {}
    for fn, fam in (("/proc/net/tcp", 4), ("/proc/net/tcp6", 6)):
        try:
            lines = open(fn).read().splitlines()[1:]
        except OSError:
            continue
        for ln in lines:
            f = ln.split()
            tbl[f[9]] = (fam, f[2])          # inode -> (family, rem_address)
    return tbl


def ppid_of(pid):
    # Field 4 of /proc/<pid>/stat, parsed from after the LAST ')': field 2 is
    # the comm in parentheses and may itself contain spaces and ')', so a plain
    # split() gets the wrong column for a process whose name is hostile.
    st = read_text(f"/proc/{pid}/stat")
    if st is None:
        return None
    cut = st.rfind(")")
    if cut == -1:
        return None
    rest = st[cut + 1:].split()
    if len(rest) < 2:
        return None
    return rest[1]


def monitored_pids():
    """Every pid whose parent chain reaches a process running the artifact.

    The cmdline filter alone (what this used to do) sees only the bun process
    itself, and the Bash tool, `rg`, and any tool-driven network access run in
    CHILDREN. Measured with the poller extracted verbatim: a marked process
    that opens a socket to this host's 172.18.0.5:19999 is caught, and the same
    process spawning a CHILD that opens the identical connection reports
    nothing while the listener logs the connection either way.

    "Parent chain", and not "process tree": a descendant that has been
    reparented away - a double fork, or setsid with the middle process exiting
    - has ppid 1 and is NOT found. Measured, five shapes, one connection each:
    self, child and grandchild all reported sockets=1 and the connection; the
    double-fork and setsid orphans reported sockets=0 while the listener logged
    their connection anyway. See the block above this heredoc for why that is
    accepted here rather than papered over with a session-id match.

    The root test is "the artifact path appears in this cmdline", so a root is
    any process launched with that path on its command line: the bun process,
    the timeout(1) wrapper in front of it (checked on this host - `timeout 5
    env -i /bin/sleep 3` shows that whole line as its cmdline, and env execs
    into the target rather than staying as a process of its own), and this
    poller, which the caller passes the same path as argv[1]. Everything under
    any of them is watched. Verified by dumping the monitored set mid-run on
    this host with one marked process: exactly two pids, the marked process and
    the poller.
    """
    entries = os.listdir("/proc")     # deliberately NOT guarded: no /proc means
                                      # this guard cannot work at all, and the
                                      # caller must hear about that, not get an
                                      # empty (= clean-looking) result
    pids = [e for e in entries if e.isdigit()]
    parent = {}
    roots = set()
    for pid in pids:
        cmd = read_text(f"/proc/{pid}/cmdline")
        if cmd is None:
            continue
        # the artifact path, so a sibling agent's Claude on the same host is not
        # attributed to this run
        if artifact in cmd:
            roots.add(pid)
        p = ppid_of(pid)
        if p is not None:
            parent[pid] = p
    keep = set(roots)
    for pid in pids:
        chain = []
        cur = pid
        # bounded walk: /proc is read without a lock, so a racing reparent can
        # in principle hand back a cycle, and an unbounded loop here would spin
        # forever holding the CPU on a shared box
        for _ in range(64):
            if cur in keep:
                keep.update(chain)
                break
            chain.append(cur)
            nxt = parent.get(cur)
            if nxt is None or nxt == cur or nxt == "0":
                break
            cur = nxt
    return keep


def addr(fam, hexaddr):
    h, port = hexaddr.split(":")
    port = int(port, 16)
    if fam == 4:
        return ".".join(str(b) for b in bytes.fromhex(h)[::-1]), port
    raw = b"".join(bytes.fromhex(h[i:i + 8])[::-1] for i in range(0, 32, 8))
    if raw[:12] == b"\0" * 10 + b"\xff\xff":       # v4-mapped
        return ".".join(str(b) for b in raw[12:]), port
    # inet_ntop, not raw.hex(): the hex form matched none of the loopback
    # exemptions, so ANY v6 socket with no peer was reported as egress.
    # Reproduced against a process doing only socket(AF_INET6)/bind(("::1",0))/
    # listen(1): the poller printed 00000000000000000000000000000000:0, i.e. it
    # invented traffic leaving the host for a socket bound to v6 loopback.
    return socket.inet_ntop(socket.AF_INET6, raw), port


def is_local(ip):
    # "::" / "0.0.0.0" are what a listening socket's REMOTE address decodes to;
    # they are the absence of a peer, not a destination.
    return ip.startswith("127.") or ip in ("0.0.0.0", "::", "::1")


try:
    while not stop and time.time() < deadline:
        tbl = inode_table()
        for entry in monitored_pids():
            try:
                fds = os.listdir(f"/proc/{entry}/fd")
            except OSError:
                continue
            for fd in fds:
                try:
                    link = os.readlink(f"/proc/{entry}/fd/{fd}")
                except OSError:
                    continue
                m = re.fullmatch(r"socket:\[(\d+)\]", link)
                if not m or m.group(1) in seen or m.group(1) not in tbl:
                    continue
                seen.add(m.group(1))
                fam, rem = tbl[m.group(1)]
                ip, port = addr(fam, rem)
                if not is_local(ip):
                    egress_count += 1
                    out.write(f"{ip}:{port}\n")
        time.sleep(0.03)
except BaseException as exc:                       # noqa: BLE001 - see above
    status.write(f"ERROR {type(exc).__name__}: {exc}\n")
    sys.exit(1)

if stop:
    status.write(f"OK sockets={len(seen)} loopback={len(seen) - egress_count} "
                 f"non_loopback={egress_count}\n")
else:
    status.write("ERROR 600 s backstop reached; the caller never stopped this poller\n")
    sys.exit(1)
EOF

# Which tool each case drives, and what the invocation needs for it to be
# offered at all. Grep is special: measured on 2.1.222, the CLI hides Grep and
# Glob from the tool list unless the invocation opts in (26 tool schemas with
# `--allowedTools Grep,...`, 24 without), and without the opt-in the turn
# "succeeds" with the tool_result "No such tool available: Grep".
case_tool()  { case "$1" in bash) echo bash ;; grep) echo grep ;; read) echo read ;; *) echo none ;; esac; }
case_extra() { case "$1" in grep) echo "--allowedTools Grep,Bash,Read" ;; *) echo "" ;; esac; }
case_input() {
  case "$1" in
    read) printf '{"file_path":"%s"}' "$PNG" ;;
    grep) printf '{"pattern":"NEEDLE-12345","path":"hay","output_mode":"content","-n":true}' ;;
    *)    printf '' ;;
  esac
}
case_prompt() {
  case "$1" in
    bash) echo "run the probe" ;;
    grep) echo "find the needle" ;;
    read) echo "read the image" ;;
  esac
}

run_case() {  # run_case <case> <artifact> <label> ; prints the summary lines
  local kase="$1" artifact="$2" label="$3"
  local tag="$kase.$label"
  local port_file="$SCRATCH/$tag.port"
  local out="$SCRATCH/$tag.jsonl"

  [ -f "$artifact" ] || die "artifact does not exist: $artifact"

  local input; input="$(case_input "$kase")"
  local mock_args=(--tool "$(case_tool "$kase")" --log "$SCRATCH/$tag.mock.log"
                   --ready-file "$port_file")
  if [ -n "$input" ]; then mock_args+=(--tool-input "$input"); fi

  # The doctor case drives no turn and the mock logs nothing for it (measured).
  # It is started anyway so ANTHROPIC_BASE_URL can point somewhere that answers:
  # with no base URL, doctor's Remote Control section opened 3 sockets to
  # Anthropic on :443 even with CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1.
  node "$MOCK" "${mock_args[@]}" >"$SCRATCH/$tag.mock.out" 2>&1 &
  local mock_pid=$!
  echo "$mock_pid" > "$SCRATCH/$tag.mockpid"

  # Wait for the port file rather than sleeping: an ephemeral port is not known
  # until listen() returns, and a fixed sleep is how this kind of harness starts
  # failing on a loaded machine.
  local waited=0
  while [ ! -s "$port_file" ]; do
    sleep 0.1
    waited=$((waited + 1))
    if [ "$waited" -gt 100 ]; then die "mock did not start: $(cat "$SCRATCH/$tag.mock.out")"; fi
  done
  local port; port="$(cat "$port_file")"

  local egress_file="$SCRATCH/$tag.egress"
  local egress_status="$SCRATCH/$tag.egress.status"
  : > "$egress_file"
  rm -f "$egress_status"
  python3 "$EGRESS" "$artifact" "$egress_file" "$egress_status" &
  local egress_pid=$!
  # same reason the mock's pid goes through a file: run_case runs inside a
  # command substitution, so the EXIT trap cannot see this variable, and an
  # abandoned poller is an endless /proc scan nobody notices
  echo "$egress_pid" > "$SCRATCH/$tag.egresspid"

  # A throwaway HOME and CLAUDE_CONFIG_DIR per run: state left by one side must
  # not reach the other, or the comparison stops being about the artifact.
  local home="$SCRATCH/home.$tag"
  mkdir -p "$home/config"

  # env -i: inherited ANTHROPIC_* / CLAUDE_* from the caller's shell would
  # silently retarget this at a real endpoint. Only PATH survives, because the
  # CLI shells out (and, as shipped, needs `rg` from PATH - findings.md 11).
  # `|| rc=$?` rather than a bare call: under `set -e` a non-zero CLI exit would
  # kill the whole comparison, and a non-zero exit is itself a result worth
  # printing next to the other side's.
  local rc=0
  local -a base_env=(
    PATH="/usr/bin:/bin" HOME="$home" CLAUDE_CONFIG_DIR="$home/config"
    DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
    ANTHROPIC_BASE_URL="http://127.0.0.1:$port" ANTHROPIC_API_KEY=sk-ant-fake
  )
  # `bun --no-install` below is the third leg of the loopback-only property, not
  # tidiness: without it the AS-SHIPPED Read case fetched five packages from
  # registry.npmjs.org mid-turn (Bun auto-installing what the JS sharp fallback
  # requires), which is the harness quietly going online in the one case whose
  # whole point is that the fallback does not work. See the header.

  if [ "$kase" = "doctor" ]; then
    # `doctor` is the independent witness that the rewrite stayed scoped:
    # install identity is a DIFFERENT isStandaloneExecutable gate site, and it
    # has to keep answering "unknown" on the shimmed side. It needs no mock
    # turn, so there is no transcript to summarize - the two lines it prints are
    # the result. No pty either: measured, `doctor </dev/null` writes them to a
    # plain pipe.
    ( cd "$WORK" && run_timeout 120 env -i "${base_env[@]}" \
        "$BUN_BIN" --no-install "$artifact" doctor \
        </dev/null >"$out" 2>"$SCRATCH/$tag.err" ) || rc=$?
    local running search
    running="$(sed -n 's/^\(Running: .*\)$/\1/p' "$out" | head -1)"
    search="$(sed -n 's/^\(Search: .*\)$/\1/p' "$out" | head -1)"
    echo "tool=doctor (no API turn; mock started only so nothing can leave the host)"
    echo "tool_result=${running:-(no Running: line)} | ${search:-(no Search: line)}"
    echo "result=(doctor prints no result line)"
  else
    # shellcheck disable=SC2086
    ( cd "$WORK" && run_timeout 180 env -i "${base_env[@]}" \
        "$BUN_BIN" --no-install "$artifact" \
        -p "$(case_prompt "$kase")" --output-format stream-json --verbose \
        --dangerously-skip-permissions $(case_extra "$kase") \
        </dev/null >"$out" 2>"$SCRATCH/$tag.err" ) || rc=$?
    python3 "$SUMMARIZE" "$out"
  fi

  kill "$mock_pid" 2>/dev/null || true
  wait "$mock_pid" 2>/dev/null || true
  rm -f "$SCRATCH/$tag.mockpid"
  # sleep first: the poll interval is 30 ms, and killing the watcher the
  # instant the CLI exits is how a socket opened in its last moments goes
  # unseen.
  sleep 0.2
  kill "$egress_pid" 2>/dev/null || true
  wait "$egress_pid" 2>/dev/null || true
  rm -f "$SCRATCH/$tag.egresspid"

  echo "egress=$(sort -u "$egress_file" | tr '\n' ' ')"
  # The guard's own verdict, not just its findings. An empty egress file means
  # "clean" ONLY if the poller ran to the end and said so; before this line the
  # two were indistinguishable and a poller that crashed on its first
  # iteration passed every case.
  local guard; guard="$(head -1 "$egress_status" 2>/dev/null || true)"
  if [ -z "$guard" ]; then guard="ERROR the egress guard left no status (it crashed, or was killed before it could report)"; fi
  echo "egress_guard=$guard"
  echo "cli_rc=$rc"
  # A turn that produced no tool_result is the failure this harness is most
  # likely to hit (wrong artifact path, a Bun that cannot load it, a tool the
  # CLI does not offer). Print what the process actually said instead of
  # leaving the operator with an empty summary.
  if [ "$kase" != "doctor" ] && ! grep -q '"type":"user"' "$out" 2>/dev/null; then
    echo "cli_stderr=$(head -c 300 "$SCRATCH/$tag.err" | tr '\n' ' ')"
  fi
  if grep -q '^WARN' "$SCRATCH/$tag.mock.log" 2>/dev/null; then
    grep '^WARN' "$SCRATCH/$tag.mock.log" | sed 's/^/mock_/'
  fi
  # A turn that came off the NON-streaming fallback is a different code path
  # from the one a real API run takes, and it is invisible in the transcript:
  # the tool_result is identical. Measured (see the sse() comment in
  # scripts/mock-messages-api.mjs): dropping the SSE `event:` line makes the
  # CLI abandon every stream=true request and re-send it as stream=false, and
  # this harness reported the case as passing. Now it does not.
  if [ -f "$SCRATCH/$tag.mock.log" ]; then
    local fallbacks; fallbacks="$(grep -c 'stream=false' "$SCRATCH/$tag.mock.log" || true)"
    if [ "${fallbacks:-0}" -gt 0 ]; then
      echo "mock_stream_fallback=$fallbacks request(s) were re-sent with stream=false; the turn did not come from the SSE stream"
    fi
  fi
}

# -------------------------------------------------------------------- cases

# What each side is supposed to produce, as documented in docs/findings.md 11.
# Without these the script is a printer, not a check: two sides that both
# silently produced nothing would agree, and "SAME" would be reported as a
# result. Every string below was measured on this host before it was written.
expect_for() {  # expect_for <case> <side>
  case "$1:$2" in
    # The WHOLE tool_result, both lines of it (the mock's Bash probe is
    # `echo HELLO-FROM-SUBPROCESS; uname -s`). The literal \n is what the
    # summarizer's oneline() escaping produces: expecting it is what stops the
    # second line from being silently dropped again. `sed -n 's/^tool_result=//p'`
    # only ever matched the first line, so before this the "Linux" half was
    # discarded from both this check and the SAME/DIFFERS verdict, and two
    # sides differing only after line 1 would have been reported SAME. Naming
    # Linux is safe: the preflight refuses to run this script anywhere else.
    bash:*)        echo "HELLO-FROM-SUBPROCESS\nLinux" ;;
    # Same expectation on the two SHIPPABLE sides, deliberately: a shim that is
    # genuinely scoped to the image call site has to keep the hit.
    grep:asshipped) echo "hay/a.txt:1:NEEDLE-12345" ;;
    grep:shimmed)   echo "hay/a.txt:1:NEEDLE-12345" ;;
    # ...and this is the whole argument for scoping it. The global flip makes
    # "embedded ripgrep" mean "re-exec process.execPath with argv0 rg",
    # process.execPath is bun, and a search for a string that IS there answers
    # "No matches found" - not an error, a wrong answer. Expecting the breakage
    # is deliberate: if a future Bun or Claude makes the global flip harmless,
    # this line goes red, and the premise the scoped design rests on has
    # expired. That is a result, not a bug in the harness.
    grep:global)    echo "No matches found" ;;
    # The documented failure, not merely "an error": the whole claim in
    # findings.md 11 is that this specific message is what an unshimmed build
    # gives back for an oversized image.
    read:asshipped) echo "Unable to resize image" ;;
    read:shimmed)   echo "IMAGE media=image/jpeg" ;;
    # The global flip does buy the working image path. That is why it is
    # tempting, and why the Grep line above has to exist.
    read:global)    echo "IMAGE media=image/jpeg" ;;
    # Install identity is a different gate site. It staying "unknown" on the
    # shimmed side is the positive evidence that the rewrite did not spread;
    # the global side flipping it to "native" is the control that proves this
    # case can tell the two apart at all.
    doctor:asshipped) echo "Running: unknown" ;;
    doctor:shimmed)   echo "Running: unknown" ;;
    doctor:global)    echo "Running: native" ;;
  esac
}

# A string that must NOT appear. The grep:global expectation above is a positive
# match on the breakage; this is the same claim from the other side, so a
# "No matches found" that somehow arrived WITH the hit cannot pass quietly.
reject_for() {  # reject_for <case> <side>
  case "$1:$2" in
    grep:global) echo "NEEDLE-12345" ;;
  esac
}

FAILED=0
check_side() {  # check_side <case> <side> <summary>
  local kase="$1" side="$2" out="$3"
  local want; want="$(expect_for "$kase" "$side")"
  local nope; nope="$(reject_for "$kase" "$side")"
  local got;  got="$(printf '%s\n' "$out" | sed -n 's/^tool_result=//p')"
  if [ -n "$want" ]; then
    case "$got" in
      *"$want"*) echo "    check: OK (contains '$want')" ;;
      *) echo "    check: FAIL (expected '$want')"; FAILED=$((FAILED + 1)) ;;
    esac
  fi
  if [ -n "$nope" ]; then
    case "$got" in
      *"$nope"*) echo "    check: FAIL (must NOT contain '$nope')"; FAILED=$((FAILED + 1)) ;;
      *) echo "    check: OK (does not contain '$nope')" ;;
    esac
  fi
  # Fatal, not a note. A harness whose header promises loopback-only and then
  # opens a socket to the internet mid-run is wrong in the way that is hardest
  # to notice: every case still passes.
  local egress; egress="$(printf '%s\n' "$out" | sed -n 's/^egress=//p')"
  if [ -n "$(printf '%s' "$egress" | tr -d '[:space:]')" ]; then
    echo "    check: FAIL (traffic left the host:$egress)"
    FAILED=$((FAILED + 1))
  fi
  # ...and equally fatal if the guard cannot vouch for that emptiness. An
  # egress file is only evidence when the process that was supposed to fill it
  # ran to the end.
  local guard; guard="$(printf '%s\n' "$out" | sed -n 's/^egress_guard=//p')"
  case "$guard" in
    OK\ *)
      # Positive control on the guard itself, only worth asking once the guard
      # says it finished. Every case that drives a turn talks to the mock over
      # loopback, so a guard that attributed NO socket at all was watching the
      # wrong processes - and that is indistinguishable from a clean run in the
      # egress= line above. The doctor case is exempt: it drives no turn, so
      # opening no socket at all is a legitimate outcome for it. How many
      # sockets any run opens is not fixed and is deliberately not asserted
      # against a constant anywhere - the count is printed in this same
      # egress_guard= line, and what is checked is the invariant: zero
      # non-loopback, and non-zero total wherever a turn was driven.
      local sockets; sockets="$(printf '%s\n' "$guard" | sed -n 's/.*sockets=\([0-9]*\).*/\1/p')"
      if [ "$kase" != "doctor" ] && [ "${sockets:-0}" -eq 0 ]; then
        echo "    check: FAIL (the egress guard saw 0 sockets: it was watching the wrong process, so its empty result means nothing)"
        FAILED=$((FAILED + 1))
      fi ;;
    *) echo "    check: FAIL (egress guard did not report clean: ${guard:-<missing>})"
       FAILED=$((FAILED + 1)) ;;
  esac
  # A pass produced off the mock's non-streaming fallback is not a pass: it
  # exercised a code path the real API run never takes.
  local fallback; fallback="$(printf '%s\n' "$out" | sed -n 's/^mock_stream_fallback=//p')"
  if [ -n "$fallback" ]; then
    echo "    check: FAIL (the CLI abandoned the SSE stream: $fallback)"
    FAILED=$((FAILED + 1))
  fi
}

# The Grep case needs ripgrep on the PATH the runs are given. Measured, with a
# PATH that had every /usr/bin entry except rg: the tool comes back is_error
# with "ripgrep not found on PATH. Install it ... or use the native claude
# binary which embeds it." - which is findings.md 11's point 4 arriving as a
# case failure rather than as an explanation.
if ! PATH="/usr/bin:/bin" command -v rg >/dev/null 2>&1; then
  warn "no rg on /usr/bin:/bin - the Grep case will fail: an extracted build has"
  warn "  no embedded ripgrep and shells out to the system one (findings.md 11)."
fi

echo
for kase in $CASES; do
  echo "=== case: $kase"
  a_out="$(run_case "$kase" "$A_ART" asshipped)"
  echo "--- as shipped"
  echo "$a_out" | sed 's/^/    /'
  check_side "$kase" asshipped "$a_out"
  a_res="$(echo "$a_out" | sed -n 's/^tool_result=//p')"

  b_res=""
  if [ -n "$B_ART" ]; then
    b_out="$(run_case "$kase" "$B_ART" shimmed)"
    echo "--- shimmed (scoped: image gate only)"
    echo "$b_out" | sed 's/^/    /'
    check_side "$kase" shimmed "$b_out"
    b_res="$(echo "$b_out" | sed -n 's/^tool_result=//p')"
  else
    echo "--- shimmed: SKIPPED (no NRC_NO_IMAGE_SHIM support in tools/postprocess.py)"
  fi

  g_out="$(run_case "$kase" "$G_ART" global)"
  echo "--- global (Bun.isStandaloneExecutable=true for every gate)"
  echo "$g_out" | sed 's/^/    /'
  check_side "$kase" global "$g_out"
  g_res="$(echo "$g_out" | sed -n 's/^tool_result=//p')"

  verdict=""
  if [ -n "$B_ART" ]; then
    if [ "$a_res" = "$b_res" ]; then verdict="shimmed vs as-shipped: SAME"
    else verdict="shimmed vs as-shipped: DIFFERS"; fi
    verdict="$verdict  |  "
  fi
  if [ "$a_res" = "$g_res" ]; then verdict="${verdict}global vs as-shipped: SAME"
  else verdict="${verdict}global vs as-shipped: DIFFERS"; fi
  echo "--- verdict: $verdict"
  echo
done

if [ "$FAILED" -gt 0 ]; then
  die "$FAILED expected result(s) did not reproduce"
fi
info "all expected results reproduced"
