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
# /proc/<pid>/fd (over the whole process tree) against /proc/net/tcp while a
# case ran. Each was found by that poll, not predicted:
#
#   CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
#     WITHOUT it the Bash case opened 6 non-loopback sockets - 5 to
#     160.79.104.10:443 (api.anthropic.com resolves there) and 1 to
#     34.149.66.165:443 (a Google-hosted address, not identified further) -
#     alongside the one loopback socket to the mock. WITH it: 1 socket total, 0
#     non-loopback. Control: bun alone spinning a 4 s busy loop opened 0
#     sockets, so it is the CLI's traffic, not bun's.
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
# below), prints its own `egress=` line, and a non-empty one FAILS the script.
# Measured across a full run - four cases, three sides, twelve runs - all twelve
# egress lines are empty, and a manual poll over the same run saw 17 sockets,
# all of them loopback to the mock. The loopback count wanders between runs (16
# in the run before it); the zero is the invariant.
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

[ -x "$BUN_BIN" ] || die "bun not found at $BUN_BIN (set BUN_BIN)"
command -v node >/dev/null 2>&1 || die "node not found; the mock needs it"

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
# Verbatim from docs/findings.md section 11. Deterministic on stock python3:
# measured 2,329,429 bytes, md5 78f7dbdc56b15d269b02a6439d3c5d36. The size is
# asserted below because the whole point of the Read case is that the *input*
# reproduces - the retracted number in that section was retracted precisely
# because its source image did not.
python3 - "$PNG" <<'EOF'
import sys, zlib, struct
W = H = 3000
raw = bytearray()
for y in range(H):
    raw.append(0)                                   # PNG filter: None
    raw += bytes(v for x in range(W)
                 for v in ((x + y) % 256, (x * 2) % 256, (y * 3) % 256))
def chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
open(sys.argv[1], "wb").write(
    b"\x89PNG\r\n\x1a\n"
    + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0))
    + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
    + chunk(b"IEND", b""))
EOF
PNG_BYTES="$(stat -c %s "$PNG")"
[ "$PNG_BYTES" = "2329429" ] || die "generated PNG is $PNG_BYTES bytes, expected 2329429 (findings.md 11)"
info "fixture PNG: $PNG_BYTES bytes (3000x3000, deterministic)"

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
info "as shipped : $A_ART ($(stat -c %s "$A_ART") B, md5 $(md5sum "$A_ART" | cut -d' ' -f1))"
if [ -n "$B_ART" ]; then
  info "shimmed    : $B_ART ($(stat -c %s "$B_ART") B, md5 $(md5sum "$B_ART" | cut -d' ' -f1))"
  if cmp -s "$A_ART" "$B_ART"; then
    warn "the two artifacts are byte-identical: NRC_NO_IMAGE_SHIM changed nothing."
  fi
else
  warn "no shimmed side: tools/postprocess.py has no NRC_NO_IMAGE_SHIM support yet."
  warn "  running the as-shipped and globally-flipped sides only."
fi
info "global     : $G_ART ($(stat -c %s "$G_ART") B, md5 $(md5sum "$G_ART" | cut -d' ' -f1))"

# ------------------------------------------------------------------ runner

# stream-json is a JSONL transcript; this pulls out the one line that matters
# (the tool_result the CLI produced) plus the final result line.
SUMMARIZE="$SCRATCH/summarize.py"
cat > "$SUMMARIZE" <<'EOF'
import base64
import json, sys

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

print(f"tool={tool_name}")
print(f"tool_result={summary}")
print(f"result={final}")
EOF

# The loopback-only property, enforced per run instead of asserted in a comment.
# This is how the npm reach-out documented in the header was found, and a header
# is not a mechanism: the previous version of this script claimed "no traffic
# leaves the host" and was wrong about it for two different reasons at once.
# Poll-based, so it can only ever MISS a socket, never invent one - a finding
# here is real.
EGRESS="$SCRATCH/egress.py"
cat > "$EGRESS" <<'EOF'
import os, re, sys, time

artifact, outpath = sys.argv[1], sys.argv[2]
out = open(outpath, "a", buffering=1)
seen = set()
# backstop: the caller kills this, but if the caller is itself killed, an
# endless /proc scan on a shared box is a bad thing to leave behind. Longer
# than the longest case timeout above (180 s).
deadline = time.time() + 600


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


def addr(fam, hexaddr):
    h, port = hexaddr.split(":")
    port = int(port, 16)
    if fam == 4:
        return ".".join(str(b) for b in bytes.fromhex(h)[::-1]), port
    raw = b"".join(bytes.fromhex(h[i:i + 8])[::-1] for i in range(0, 32, 8))
    if raw[:12] == b"\0" * 10 + b"\xff\xff":       # v4-mapped
        return ".".join(str(b) for b in raw[12:]), port
    return raw.hex(), port


while time.time() < deadline:
    tbl = inode_table()
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            cmd = open(f"/proc/{entry}/cmdline", "rb").read().decode("utf8", "replace")
        except OSError:
            continue
        # the artifact path, so a sibling agent's Claude on the same host is not
        # attributed to this run
        if artifact not in cmd:
            continue
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
            if not (ip.startswith("127.") or ip == "::1" or ip == "0.0.0.0"):
                out.write(f"{ip}:{port}\n")
    time.sleep(0.03)
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
  : > "$egress_file"
  python3 "$EGRESS" "$artifact" "$egress_file" &
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
    ( cd "$WORK" && timeout 120 env -i "${base_env[@]}" \
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
    ( cd "$WORK" && timeout 180 env -i "${base_env[@]}" \
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
}

# -------------------------------------------------------------------- cases

# What each side is supposed to produce, as documented in docs/findings.md 11.
# Without these the script is a printer, not a check: two sides that both
# silently produced nothing would agree, and "SAME" would be reported as a
# result. Every string below was measured on this host before it was written.
expect_for() {  # expect_for <case> <side>
  case "$1:$2" in
    bash:*)        echo "HELLO-FROM-SUBPROCESS" ;;
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
