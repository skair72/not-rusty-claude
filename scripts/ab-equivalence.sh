#!/usr/bin/env bash
#
# ab-equivalence.sh - run the extracted Claude Code artifact BOTH ways against
# the committed loopback mock and print the difference.
#
# The two sides are the same extraction of the same native binary, differing
# only in whether tools/postprocess.py applied the image shim:
#
#   as shipped : NRC_NO_IMAGE_SHIM=1 scripts/build.sh   (no shim: the CLI takes
#                its non-standalone branch and never reaches for
#                image-processor.node - docs/findings.md 11)
#   shimmed    : scripts/build.sh                       (shim applied)
#
# WHY: docs/findings.md section 11 claims a behaviour flip between those two
# builds - a Read of an oversized PNG errors on one side and returns a JPEG on
# the other - and claims that a *global* isStandaloneExecutable flip silently
# breaks Grep instead. Those measurements were taken through a mock that lived
# in /tmp and no longer exists, so they stopped being reproducible and one
# number had to be retracted. This script is the reproduction.
#
# Everything is loopback and throwaway: 127.0.0.1 mock on an ephemeral port, a
# fake API key, a scratch HOME and CLAUDE_CONFIG_DIR. The real ~/.claude is
# never read or written. /usr/bin/claude is only ever READ (as build input); it
# is never executed - the artifact runs under Bun.
#
# CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 is load-bearing, not decoration.
# Pointing ANTHROPIC_BASE_URL at the mock redirects only the Messages API.
# MEASURED on this host by polling /proc/<pid>/fd against /proc/net/tcp while a
# case ran: without that variable the artifact opened 6 non-loopback sockets -
# 5 to Anthropic on :443 and 1 to a Statsig/feature-gate host - alongside the
# one loopback socket to the mock. With it: 1 socket total, 0 non-loopback.
# Control: bun alone running a busy loop opened 0, so it is the CLI, not bun.
# The header used to claim "no traffic leaves the host" while that was false.
#
# Usage:
#   scripts/ab-equivalence.sh                       # build both sides, run all cases
#   scripts/ab-equivalence.sh --case read
#   scripts/ab-equivalence.sh --as-shipped some/extract      # reuse a prebuilt tree
#
# --as-shipped / --shimmed skip a build (5.8 s per side, measured here) -
# but nothing checks that the tree you name was built the way you say. Once
# scripts/build.sh applies the shim by default, build/extract is the SHIMMED
# side, and handing it to --as-shipped compares a tree against itself. The
# byte-identical warning below is the only thing that will tell you.
#
# Options:
#   --case bash|grep|read|all   which turn(s) to drive          (default: all)
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
  all)  CASES="bash grep read" ;;
  bash|grep|read) CASES="$CASE" ;;
  *) die "unknown --case '$CASE' (want: bash, grep, read, all)" ;;
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
  for pidfile in "$SCRATCH"/*.mockpid; do
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
# NRC_NO_IMAGE_SHIM there is only one side to run, and saying so beats building
# two identical trees and calling their sameness a result.
SHIM_SUPPORTED=0
if grep -q 'NRC_NO_IMAGE_SHIM' "$REPO/tools/postprocess.py"; then SHIM_SUPPORTED=1; fi

# Sets BUILT_DIR rather than echoing it: info() prints to stdout, so a
# $(build_side ...) would capture the progress lines into the path.
BUILT_DIR=""
build_side() {  # build_side <label> <outdir> <NRC_NO_IMAGE_SHIM value>
  local label="$1" out="$2" noshim="$3"
  [ -f "$NATIVE" ] || die "native binary not found: $NATIVE (pass --native)"
  info "building '$label' from $NATIVE (NRC_NO_IMAGE_SHIM=$noshim)"
  # OUT_DIR keeps this out of the repo's build/: a comparison run must not
  # clobber the tree the user is otherwise working with.
  OUT_DIR="$out" NRC_NO_IMAGE_SHIM="$noshim" "$REPO/scripts/build.sh" "$NATIVE" >"$out.log" 2>&1 \
    || { sed -n '1,40p' "$out.log" >&2; die "build of '$label' failed; see $out.log"; }
  BUILT_DIR="$out/extract"
}

if [ -z "$AS_SHIPPED_DIR" ]; then
  build_side as-shipped "$SCRATCH/asshipped" 1
  AS_SHIPPED_DIR="$BUILT_DIR"
fi
if [ -z "$SHIMMED_DIR" ] && [ "$SHIM_SUPPORTED" = "1" ]; then
  build_side shimmed "$SCRATCH/shimmed" 0
  SHIMMED_DIR="$BUILT_DIR"
fi

A_ART="$AS_SHIPPED_DIR/cli.original.cjs"
[ -f "$A_ART" ] || die "as-shipped artifact missing: $A_ART"
B_ART=""
if [ -n "$SHIMMED_DIR" ]; then
  B_ART="$SHIMMED_DIR/cli.original.cjs"
  [ -f "$B_ART" ] || die "shimmed artifact missing: $B_ART"
fi

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
  warn "  running the as-shipped side only."
fi

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

# Which tool each case drives, and what the invocation needs for it to be
# offered at all. Grep is special: measured on 2.1.222, the CLI hides Grep and
# Glob from the tool list unless the invocation opts in, and without the opt-in
# the turn "succeeds" with the tool_result "No such tool available: Grep".
case_tool()  { case "$1" in bash) echo bash ;; grep) echo grep ;; read) echo read ;; esac; }
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
  # shellcheck disable=SC2086
  ( cd "$WORK" && timeout 180 env -i \
      PATH="/usr/bin:/bin" HOME="$home" CLAUDE_CONFIG_DIR="$home/config" \
      DISABLE_AUTOUPDATER=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
      ANTHROPIC_BASE_URL="http://127.0.0.1:$port" ANTHROPIC_API_KEY=sk-ant-fake \
      "$BUN_BIN" "$artifact" \
      -p "$(case_prompt "$kase")" --output-format stream-json --verbose \
      --dangerously-skip-permissions $(case_extra "$kase") \
      </dev/null >"$out" 2>"$SCRATCH/$tag.err" ) || rc=$?

  kill "$mock_pid" 2>/dev/null || true
  wait "$mock_pid" 2>/dev/null || true
  rm -f "$SCRATCH/$tag.mockpid"

  python3 "$SUMMARIZE" "$out"
  echo "cli_rc=$rc"
  # A turn that produced no tool_result is the failure this harness is most
  # likely to hit (wrong artifact path, a Bun that cannot load it, a tool the
  # CLI does not offer). Print what the process actually said instead of
  # leaving the operator with an empty summary.
  if ! grep -q '"type":"user"' "$out" 2>/dev/null; then
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
    # Same expectation on BOTH sides, deliberately. findings.md 11 measured a
    # *global* Bun.isStandaloneExecutable=true turning this hit into the silent
    # "No matches found"; a shim that is genuinely scoped to the image call site
    # has to keep the hit. This line is where that regression would show up.
    grep:*)        echo "hay/a.txt:1:NEEDLE-12345" ;;
    # The documented failure, not merely "an error": the whole claim in
    # findings.md 11 is that this specific message is what an unshimmed build
    # gives back for an oversized image.
    read:asshipped) echo "Unable to resize image" ;;
    read:shimmed)   echo "IMAGE media=image/jpeg" ;;
  esac
}

FAILED=0
check_side() {  # check_side <case> <side> <summary>
  local kase="$1" side="$2" out="$3"
  local want; want="$(expect_for "$kase" "$side")"
  local got;  got="$(printf '%s\n' "$out" | sed -n 's/^tool_result=//p')"
  [ -n "$want" ] || return 0
  case "$got" in
    *"$want"*) echo "    check: OK (contains '$want')" ;;
    *) echo "    check: FAIL (expected '$want')"; FAILED=$((FAILED + 1)) ;;
  esac
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
  if [ -n "$B_ART" ]; then
    b_out="$(run_case "$kase" "$B_ART" shimmed)"
    echo "--- shimmed"
    echo "$b_out" | sed 's/^/    /'
    check_side "$kase" shimmed "$b_out"
    a_res="$(echo "$a_out" | sed -n 's/^tool_result=//p')"
    b_res="$(echo "$b_out" | sed -n 's/^tool_result=//p')"
    if [ "$a_res" = "$b_res" ]; then
      echo "--- verdict: SAME"
    else
      echo "--- verdict: DIFFERS"
    fi
  else
    echo "--- shimmed: SKIPPED (no NRC_NO_IMAGE_SHIM support in tools/postprocess.py)"
  fi
  echo
done

if [ "$FAILED" -gt 0 ]; then
  die "$FAILED expected result(s) did not reproduce"
fi
info "all expected results reproduced"
