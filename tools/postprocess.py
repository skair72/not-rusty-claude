#!/usr/bin/env python3
"""
postprocess.py - turn an extracted Bun standalone entry module (cli.original.js)
into a CommonJS file a stock external Bun can require and run.

What it does (see docs/findings.md §6):
  1. Strip the leading `// @bun ...` pragma comment lines so the file starts
     with `(function` — Bun's CJS loader requires that.
  2. Rewrite every `/$bunfs/root/<name>` string literal — whether it appears
     inside a `require(...)` call (native .node addons) or as a bare string
     constant later read via `fs/promises.readFile` (file-loader assets like
     chart.umd.min.js) — to a `require('path').join(__dirname,'assets',...)`
     expression pointing at the extracted assets/ directory on real disk.
  3. Rewrite build-time fileURLToPath()/import.meta.url leaks to __filename.
  4. Append the CJS IIFE invocation so require()-ing the file actually runs it.
  5. Write a one-line sibling cli.js next to it, because Claude's own code
     resolves join(__filename,'..','cli.js') for two MCP self-spawns.
  6. Rewrite the ONE Bun.isStandaloneExecutable gate that guards native image
     processing to `true`, making that branch reachable at all - it is what
     the Read tool needs to resize a large image instead of erroring. Scoped
     to that single call site on purpose: setting the flag globally is
     MEASURED to break Grep - "embedded ripgrep" then means
     "re-exec process.execPath with argv0 rg", process.execPath is bun, and a
     search for a string that exists answers "No matches found". See
     docs/findings.md §11 and
     docs/superpowers/specs/2026-08-23-scoped-image-shim-design.md.
     The site is chosen by SHAPE - the branch's own `if(<gate>())try{` - and
     not by proximity to the anchor, because the nearest gate call to the
     anchor is not necessarily the image branch's own guard. The gate NAME is
     chosen the same way: a file that declares two differently named
     isStandaloneExecutable gates is refused rather than bound to whichever
     one comes first, because "first in file order" picks the gate for the
     same bad reason "nearest to the anchor" picked the site.
     NRC_NO_IMAGE_SHIM set to ANY non-empty value skips this step, so the "as
     shipped" half of that A/B can be regenerated from the same tree. (Any
     non-empty value, matching scripts/build.sh's `[ -n ... ]`; the two must
     agree or a shim FAILURE gets announced as a deliberate opt-out.)

check() then validates the transformed code is actually sound. It has SIX
fatal conditions:
  a. the output starts with `(function`;
  b. a trailing IIFE invocation was appended;
  c. no /$bunfs/ (or Windows B:/~BUN/) reference survived the rewrite;
  d. every assets/<name> the rewritten code will reach for at runtime is a
     file extract_bun.py actually wrote — the referenced-but-never-extracted
     direction, which is what a whole loader kind dropping out of the
     extractor's accept-set looks like from here;
  e. it is not the case that zero /$bunfs/ literals were rewritten while
     assets/ holds files on disk (the "silently asset-less" outcome a wrong
     VFS prefix produces — see docs/status.md's Windows/PE section);
  f. the image-shim bookkeeping adds up: rewriting one gate call site leaves
     exactly one fewer `<gate>()` call in the file, and rewriting none leaves
     the count untouched. It catches a rewrite that SPREAD - two sites moved,
     or none while one was claimed - so it is fatal, not a note. When no gate
     could be NAMED there is nothing to count: the counts are then reported as
     unknown rather than as zero, and a rewrite claimed against a gate nobody
     named is the same bookkeeping failure and is fatal here too. What it
     cannot catch is a rewrite aimed at the WRONG site: it counts how many
     moved, never which, and one wrong site scores exactly like the right one.
     That is why the site is picked by shape (step 6) and not by distance.
If any of them fails, main() prints the errors to stderr and exits non-zero
WITHOUT writing cli.original.cjs — a silently-broken output file reaching Bun
surfaces only as the confusing panic "Expected CommonJS module to have a
function wrapper", or worse, as a silent degradation, because Claude's addon
loaders swallow their own failures.

Usage:
  ./postprocess.py <extract-dir>
      <extract-dir> is the output of extract_bun.py: it must contain
      cli.original.js and an assets/ directory. Writes cli.original.cjs and a
      cli.js shim beside it.
"""

import json
import os
import re
import sys

# A /$bunfs/root/<name> string literal. This single pattern covers BOTH shapes
# observed in the real minified cli.js (see docs/findings.md §6):
#   require("/$bunfs/root/image-processor.node")   -> native addon
#   var _qo="/$bunfs/root/chart.umd.min.js"        -> file asset read via
#                                                     fs/promises.readFile
# The .node case simply becomes a dynamic require of an absolute path.
BUNFS_LITERAL = re.compile(r"""(['"])/\$bunfs/root/([\w.\-]+)\1""")

# Deliberately WIDER than BUNFS_LITERAL: this is the net that catches what the
# rewriter could not. It used to require the same `root/` segment, which made
# it blind in exactly the cases that matter - a nested path, a different VFS
# root, or the Windows prefix all rewrote to nothing AND flagged nothing. It
# was provably vacuous: neutering it so it could never match left the whole
# suite green. Both real binaries produce zero matches for this wider pattern
# after transform (measured on linux-x64 2.1.222 and darwin-arm64 2.1.239), so
# a hit here means something genuinely new, and check() treats it as fatal.
# `B:/~BUN/` is Bun's Windows VFS prefix - see docs/status.md's Windows/PE
# section for why PE is not shipped.
LEFTOVER_BUNFS = re.compile(r"(?:/\$bunfs/|B:/~BUN/)[^\s'\"`]*")

# Bun's bundler resolves import.meta.url at build time into a literal file://
# URL of the build machine, e.g.
#   nwu.fileURLToPath("file:///home/runner/work/.../setup.ts")
# The optional `ns.` / `(0, ns.fn)` callee prefix must be consumed as well,
# otherwise the replacement yields the syntax error `nwu.__filename`.
FILE_URL_LEAK = re.compile(
    r"(?:\(0,\s*[\w$]+\.fileURLToPath\)|(?:[\w$]+\.)?fileURLToPath)"
    r"\((['\"])file://[^'\"]*\1\)"
)
BUILD_PATH_LEAK = re.compile(r"""['"](/home/runner/[^'"]*)['"]""")

# Two sites in Claude's own code resolve a SIBLING cli.js of the running entry
# module and spawn it as an MCP server (docs/findings.md; reviewer C1/A4):
#   let e=__filename, t=join(e,".."), r=join(t,"cli.js")        --claude-in-chrome-mcp
#   [join(__filename,"..","cli.js"), "--computer-use-mcp"]      --computer-use-mcp
# Our entry module is cli.original.cjs, so both resolve a file that does not
# exist - and the first one PERSISTS that broken path into a Chrome
# native-messaging-host manifest that outlives the session. Renaming the
# artifact would invalidate the whole evidence record, so emit a one-line
# sibling instead. Bun loads this CJS-shaped .js with no package.json, with
# {"type":"commonjs"} and with {"type":"module"} alike.
SHIM_NAME = "cli.js"
SHIM_SOURCE = (
    "// not-rusty-claude: Claude's own code resolves a sibling cli.js for its MCP\n"
    "// self-spawns (--claude-in-chrome-mcp, --computer-use-mcp). Provide it.\n"
    'require("./cli.original.cjs");\n'
)


# --- the scoped image-processing shim ----------------------------------------
#
# Claude asks one question to decide whether it is a Bun standalone. Outside a
# standalone the answer is false everywhere, which is right everywhere except
# one place: native image processing is gated behind it, so the Read tool
# cannot resize a large image and errors instead. Setting the flag globally is
# NOT the fix - measured, docs/findings.md §11: it also makes "embedded
# ripgrep" mean "re-exec process.execPath (= bun) with argv0 rg", and Grep then
# answers "No matches found" for a string that exists. A wrong answer is worse
# than a missing feature, so the rewrite is scoped to a single call site.

# The gate's own declaration, in the two shapes seen in the wild:
#   function CE(){return Bun.isStandaloneExecutable===!0}                linux-x64 2.1.222
#   function AE(){return typeof Bun<"u"&&Bun.isStandaloneExecutable===!0}  darwin-arm64 2.1.239
# The minified name changes between builds, so it is CAPTURED, never
# hard-coded. `[^{}]*?` is what absorbs the `typeof Bun<"u"&&` guard 2.1.239
# added while `[^{}]` keeps the match inside one function body. Measured: one
# match in each of the two extracted entry modules, capturing CE and AE.
STANDALONE_DEF = re.compile(
    r"function\s+([\w$]+)\s*\(\s*\)\s*\{\s*return\s+[^{}]*?"
    r"Bun\.isStandaloneExecutable\s*===\s*!0\s*\}")

# The property that declaration tests, as plain text. Used for one thing only:
# telling the two "no declaration matched" drifts apart in the refusal message,
# so the build stops naming a single cause for every refusal. Measured on this
# host 2026-08-24: it occurs exactly ONCE in the 22,960,130-byte linux-x64
# 2.1.222 entry module - inside the gate declaration itself - so "still
# mentioned, no declaration matched" means STANDALONE_DEF's shape drifted (an
# arrow form, or the gate inlined into its callers), while "not mentioned at
# all" means the flag is gone from the build.
STANDALONE_PROPERTY = "Bun.isStandaloneExecutable"

# The image branch has no name of its own (`if(CE())`), but it ends in a
# distinctive literal. Measured: exactly ONE occurrence in each of the two
# entry modules, and in BOTH the branch's own guard `if(<gate>())try{` starts
# exactly 132 bytes before it (the call itself ends 125 bytes before it) - the
# shape `if(<gate>())try{let r=await Promise.resolve().then(...` is stable
# across two versions and two platforms.
IMAGE_ANCHOR = "Native image processor not available"

# 132 measured, 400 allowed. Wide enough that a minifier renaming locals or
# reordering the try body cannot push the guard out of reach; nowhere near wide
# enough to wander into a neighbouring gate. Measured distance from the image
# gate call to the nearest OTHER gate call site: 506,792 bytes (linux-x64
# 2.1.222) and 1,732,905 bytes (darwin-arm64 2.1.239).
IMAGE_SHIM_WINDOW = 400

# What the gate call is rewritten to. Deliberately the boring `true` and not
# `!0`: this is the one place in a 23 MB minified file that a human will grep
# for when the image path misbehaves.
IMAGE_SHIM_REPLACEMENT = "true"


def _gate_call_re(name):
    r"""`name()` where `name` is not the tail of a longer identifier.

    `(?<![\w$])` rather than `\b`, because `$` is a legal JavaScript
    identifier character but not a regex word character, so `\bCE\(\)` would
    match inside `x$CE()`. Measured on linux-x64 2.1.222: this boundary
    excludes the 4 `isGCE()` / `_checkIsGCE()` lookalikes a bare `CE\(\)`
    search finds. A preceding `.` is deliberately NOT excluded - three real
    call sites per binary are spread-prefixed, `[...CE()?[e]:[...]]`, and
    dropping them from the count would leave the safety invariant blind to a
    rewrite that spread into exactly those.
    """
    return re.compile(r"(?<![\w$])" + re.escape(name) + r"\(\)")


def _image_site_re(name):
    r"""The image branch's OWN guard: `if(<gate>())try{`, call captured.

    Picking the nearest gate call before the anchor is not the same thing as
    picking the image branch's guard, and the difference is the whole safety
    argument. An entry module whose image function has lost its `if(<gate>())`
    but kept the anchor still has a gate call within the window - the next one
    up the file is embedded ripgrep's `if(<gate>()){let r={mode:"embedded"...`
    - and rewriting THAT satisfies the before/after count invariant exactly as
    a correct rewrite does, because the invariant counts how many sites moved
    and never which. check() then returns clean and the build ships a Grep
    that answers "No matches found" for a string that exists
    (docs/findings.md §11). So the site is chosen by SHAPE, not by distance.

    Measured on both real entry modules (linux-x64 2.1.222, darwin-arm64
    2.1.239): the image branch opens a try block - `if(CE())try{` /
    `if(AE())try{` - and that shape occurs exactly ONCE in each of the 23 MB
    and 28 MB files, while the ripgrep site opens a plain `{`. The literal
    `if(` prefix also puts a non-identifier character in front of the name, so
    this cannot match inside `x$CE()`, and it cannot overlap the declaration
    `function CE(){...}` at all.
    """
    return re.compile(r"if\s*\(\s*(" + re.escape(name) + r"\(\))\s*\)\s*try\s*\{")


def _gate_name(code):
    r"""The gate's one minified identifier, or (None, why-not).

    Everything downstream binds to a single NAME: `_image_site_re` looks for
    `if(<name>())try{` and `_count_gate_calls` counts `<name>()`. Choosing that
    name is a selection exactly like choosing the site, and taking the first
    declaration in file order is the same "nearest wins" rule that handed the
    first draft of this shim the embedded-ripgrep gate. Two DIFFERENTLY named
    isStandaloneExecutable gates is enough: declare `ZZ` before `CE`, leave the
    image branch on `if(CE())try{`, and put an `if(ZZ())try{` in the window,
    and the shim rewrites ZZ's branch, reports `applied = 1` with ZZ's call
    count going 1 -> 0, and check() returns clean - a gate nobody inspected
    flipped in a signed binary while image processing is still off. So two
    names is a refusal, symmetric with the duplicated-anchor refusal below and
    for the same reason.

    Two declarations of the SAME name are NOT an ambiguity and must keep
    shimming: every call site in the file still means one gate, and the shape
    rule then picks among sites, which is what it is for.

    Measured on this host 2026-08-23: exactly one match in each real entry
    module - `CE` in the 22,960,130-byte linux-x64 2.1.222 module and `AE` in
    the 28,244,743-byte darwin-arm64 2.1.239 one - so this refusal cannot fire
    on either binary that exists today. Cost of scanning the whole file rather
    than stopping at the first match, measured on those two: 19 ms and 24 ms,
    against a 4.1 s and 5.4 s transform().
    """
    names = sorted({m.group(1) for m in STANDALONE_DEF.finditer(code)})
    if not names:
        # Say WHICH drift this is. The gate is resolved before the anchor is
        # ever looked for, so an unmatched declaration cannot be an anchor
        # problem - and blaming the anchor here is not hypothetical: a build of
        # a linux-x64 2.1.222 binary whose declaration was replaced in place
        # with an equal-length arrow form (`var CE=()=>Bun.isStandalone...`)
        # closed with "Most likely a new Claude release renamed the anchor
        # string" while that same artifact still held the anchor exactly once
        # and 21 live CE() calls (measured here 2026-08-24).
        mentions = code.count(STANDALONE_PROPERTY)
        if mentions:
            return None, (
                "no Bun.isStandaloneExecutable gate DECLARATION matched, but "
                "the property itself is still mentioned %d time(s) - so the "
                "declaration's minified SHAPE drifted (emitted as an arrow, or "
                "inlined into its callers?), and the anchor string is not "
                "implicated. Re-measure STANDALONE_DEF against the new shape"
                % mentions)
        return None, (
            "no Bun.isStandaloneExecutable gate declaration found, and the "
            "property is not mentioned anywhere in this entry module - the "
            "flag is gone from this Claude build, so there is no gate to scope "
            "a rewrite to")
    if len(names) > 1:
        return None, (
            "%d differently-named Bun.isStandaloneExecutable gate declarations "
            "(%s) - refusing to guess which one gates image processing"
            % (len(names), ", ".join(names)))
    return names[0], None


def _count_gate_calls(code, name):
    """How many `name()` CALLS the file contains - the declaration excluded.

    The declaration `function CE(){...}` contains the token `CE()` too, and
    counting it would make the before/after invariant compare two different
    things once the declaration itself moves.
    """
    decl = STANDALONE_DEF.search(code)
    span = decl.span() if decl else (-1, -1)
    return sum(1 for m in _gate_call_re(name).finditer(code)
               if not (span[0] <= m.start() < span[1]))


def _apply_image_shim(code):
    """Rewrite the single image-processing gate call to `true`.

    Returns (code, name, before, after, applied, reason). `name` is the gate's
    minified identifier, or None when the file does not name exactly one gate;
    `reason` is None on success and a short explanation of the refusal
    otherwise. Every refusal path returns the code UNCHANGED: a Claude release
    that renames the anchor should degrade to today's artifact, not fail the
    build - see the design doc's "When the anchor is not found".

    With no gate named, `before` and `after` are None and not 0. Nothing was
    counted in that case, and 0 is not the same statement: it says this entry
    module holds no gate calls at all. Measured false on the drift that
    produces it - the arrow-declaration build described in _gate_name left 21
    live CE() calls in the artifact while the build log said `0 -> 0`.
    """
    name, why = _gate_name(code)
    if name is None:
        return code, None, None, None, 0, why
    before = _count_gate_calls(code, name)

    hits = code.count(IMAGE_ANCHOR)
    # Absent and duplicated are different drifts and get different sentences:
    # build.sh now quotes this reason as its closing headline, and "occurs 0
    # times, not once - refusing to guess which one" told a reader whose anchor
    # had simply been renamed that the tool was declining to choose between
    # occurrences it never found.
    if hits == 0:
        return code, name, before, before, 0, (
            "the anchor %r is not in this entry module at all - it was renamed "
            "or dropped in this Claude release, so there is nothing left to "
            "locate the image branch by. The gate itself is fine: %s() is "
            "declared and has %d call site(s), so this is not declaration "
            "drift" % (IMAGE_ANCHOR, name, before))
    if hits > 1:
        return code, name, before, before, 0, (
            "the anchor %r occurs %d times, not once - refusing to guess which "
            "one guards image processing" % (IMAGE_ANCHOR, hits))

    anchor = code.index(IMAGE_ANCHOR)
    window_start = max(0, anchor - IMAGE_SHIM_WINDOW)
    sites = list(_image_site_re(name).finditer(code, window_start, anchor))
    if not sites:
        return code, name, before, before, 0, (
            "no if(%s())try{ image-gate shape in the %d bytes before the "
            "anchor (measured on linux-x64 2.1.222 and darwin-arm64 2.1.239: "
            "it starts 132 bytes before it). Some OTHER %s() call may well be "
            "in there - embedded ripgrep's is one `if(` away in shape - and "
            "rewriting that one would pass every count this file checks"
            % (name, IMAGE_SHIM_WINDOW, name))
    if len(sites) > 1:
        return code, name, before, before, 0, (
            "%d if(%s())try{ shapes in the %d bytes before the anchor - "
            "refusing to guess which one guards image processing"
            % (len(sites), name, IMAGE_SHIM_WINDOW))

    site = sites[0]
    code = (code[:site.start(1)] + IMAGE_SHIM_REPLACEMENT + code[site.end(1):])
    return code, name, before, _count_gate_calls(code, name), 1, None


def _asset_expr(match):
    name = match.group(2)
    return "require('path').join(__dirname,'assets'," + json.dumps(name) + ")"


def transform(code, image_shim=True):
    """Pure text transform. Returns (new_code, counts).

    image_shim=False skips the scoped isStandaloneExecutable rewrite but still
    measures the gate, so the reporting is identical either way. main() is what
    reads NRC_NO_IMAGE_SHIM; keeping the env var out of here is what lets the
    tests drive both sides of the A/B without mutating os.environ.
    """
    counts = {}
    code, counts["pragma"] = re.subn(r"^(?:\/\/[^\n]*\n)+", "", code, count=1)
    if image_shim:
        code, name, before, after, applied, reason = _apply_image_shim(code)
    else:
        # Measure the gate anyway. The opt-out build has to be comparable to
        # the shimmed one line for line, or the A/B's two build logs differ in
        # ways that have nothing to do with the shim.
        # Same gate-naming rule as the shimmed half, not a second copy of it:
        # an A/B whose two halves disagree about WHICH gate they counted is
        # comparing two different numbers and calling the difference evidence.
        name, _ = _gate_name(code)
        before = after = _count_gate_calls(code, name) if name else None
        applied, reason = 0, "disabled by caller (NRC_NO_IMAGE_SHIM)"
    counts.update(gate_name=name, gate_calls_before=before,
                  gate_calls_after=after, image_shim=applied,
                  image_shim_reason=reason)
    # the distinct asset basenames the rewritten code will reach for at
    # runtime; check() matches them against what the extractor actually wrote
    counts["asset_names"] = sorted({m.group(2) for m in BUNFS_LITERAL.finditer(code)})
    code, counts["assets"] = BUNFS_LITERAL.subn(_asset_expr, code)
    code, counts["file_urls"] = FILE_URL_LEAK.subn("__filename", code)
    code, counts["iife"] = re.subn(
        r"\}\)\s*$",
        "})(exports, require, module, __filename, __dirname)",
        code)
    counts["build_paths"] = sorted(set(BUILD_PATH_LEAK.findall(code)))
    counts["leftovers"] = sorted(set(LEFTOVER_BUNFS.findall(code)))
    return code, counts


def check(code, counts, assets_on_disk=None, asset_names_on_disk=None):
    """Return a list of fatal problems; empty means the output should load.

    assets_on_disk, when given, is the number of files extract_bun.py wrote to
    <extract-dir>/assets (None if that directory does not exist / was not
    checked - e.g. when transform() is exercised in isolation on a text
    snippet with no accompanying assets/ dir on disk).

    asset_names_on_disk, when given, is the set of their names; it lets the
    referenced-but-never-extracted check run. None skips that check for the
    same reason.
    """
    errors = []
    if not code.startswith("(function"):
        errors.append("output does not start with '(function' - Bun's CJS loader "
                      "will panic with 'Expected CommonJS module to have a "
                      "function wrapper'")
    if counts["iife"] == 0:
        # `\})\s*$` is $-anchored, so subn() can never make more than one
        # substitution; 0 is the only failure this can report. That anchor is
        # what makes the sentence above true, so it is pinned by a test of its
        # own (tests/test_postprocess.py::
        # test_only_the_final_wrapper_is_invoked_not_every_closure): without
        # it, an entry with two interior `})` transforms to iife == 3 - the
        # invocation spliced in after every closure - and this branch still
        # reports 0 errors, because it only asks about 0.
        errors.append("no trailing IIFE to invoke - the file does not end in "
                      "'})', so require()-ing it would define the wrapper and "
                      "never run it")
    if counts["leftovers"]:
        errors.append(
            "%d /$bunfs/ reference(s) survived the rewrite: %s - they point "
            "into the standalone's virtual filesystem, which does not exist "
            "outside the native binary, so whatever reads them fails at "
            "runtime (or degrades silently, since both addon loaders swallow "
            "their errors). Widen BUNFS_LITERAL to cover the new shape."
            % (len(counts["leftovers"]), ", ".join(counts["leftovers"])))
    if asset_names_on_disk is not None:
        missing = sorted(set(counts["asset_names"]) - set(asset_names_on_disk))
        if missing:
            errors.append(
                "the rewritten code will require()/readFile() %d asset(s) "
                "that were never extracted: %s. Either extract_bun.py dropped "
                "a loader kind (check its LOADERS table and WRITTEN_LOADERS "
                "against Bun's src/bundler/options.zig) or the two tools were "
                "run against different binaries."
                % (len(missing), ", ".join("assets/" + m for m in missing)))
    if counts["assets"] == 0 and assets_on_disk:
        errors.append(
            "0 /$bunfs/ paths were rewired but assets/ has "
            f"{assets_on_disk} file(s) on disk - BUNFS_LITERAL matched nothing "
            "(wrong VFS prefix for this platform? see docs/status.md's "
            "Windows/PE section) and the output would silently ship without "
            "its assets rather than fail loudly")

    # The scoped image shim's whole safety argument is arithmetic: it claims to
    # have rewritten N gate call sites, so exactly N must have disappeared. If
    # more went missing the text rewrite spread past the site it was aimed at -
    # and the site it would reach first is embedded ripgrep, whose failure mode
    # is not an error but the wrong answer ("No matches found" for a string
    # that exists, docs/findings.md §11). That must never be a warning.
    applied = counts["image_shim"]
    before = counts["gate_calls_before"]
    after = counts["gate_calls_after"]
    if before is None or after is None:
        # No gate was named, so nothing was counted and there is no arithmetic
        # to check - the counts are unknown, deliberately, rather than the 0
        # they used to be (see _apply_image_shim). A claimed rewrite here is
        # not a miscount but a fabrication: every path that fails to name a
        # gate returns the code untouched, so `applied` must be 0. Still part
        # of condition (f) - the bookkeeping has to describe the artifact.
        if applied:
            errors.append(
                "image shim accounting is wrong: it reports %d site(s) "
                "rewritten against a gate that was never identified, so "
                "nothing counted what the rewrite did to the file and nobody "
                "can say which gates this build leaves false - embedded "
                "ripgrep above all. Nothing was written." % applied)
    elif applied not in (0, 1):
        # Reported separately from the arithmetic below because the arithmetic
        # does not catch it: (before, after, applied) = (21, 19, 2) balances
        # perfectly and is still two rewrites in a shim licensed to make one.
        # Saying "went 21 -> 19 (expected 19)" while failing the build reads as
        # a bug in check(), so this branch never quotes an expectation the
        # numbers already meet.
        errors.append(
            "image shim accounting is wrong: it reports %d site(s) rewritten, "
            "and this shim is licensed to rewrite at most ONE (%s() call sites "
            "went %d -> %d). Whatever the extra rewrites hit, nobody can say "
            "which gates this build still leaves false - embedded ripgrep "
            "above all. Nothing was written."
            % (applied, counts["gate_name"], before, after))
    elif after != before - applied:
        errors.append(
            "image shim accounting is wrong: it reports %d site(s) rewritten "
            "but %s() call sites went %d -> %d (expected %d). The rewrite "
            "spread beyond the one call site it was aimed at, so gates this "
            "build deliberately leaves false - embedded ripgrep above all - "
            "may have been flipped too. Nothing was written."
            % (applied, counts["gate_name"], before, after, before - applied))
    return errors


def die(msg):
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(1)


def main():
    if len(sys.argv) != 2:
        die("usage: postprocess.py <extract-dir>")
    d = sys.argv[1]
    src = os.path.join(d, "cli.original.js")
    if not os.path.isfile(src):
        die(f"{src} not found - run extract_bun.py first")

    assets_dir = os.path.join(d, "assets")
    asset_names = set()
    assets_on_disk = None
    if os.path.isdir(assets_dir):
        asset_names = set(os.listdir(assets_dir))
        assets_on_disk = len(asset_names)
    else:
        sys.stderr.write("warning: no assets/ dir; native/file modules will be missing\n")

    with open(src, "r", encoding="utf-8", errors="replace") as fh:
        code = fh.read()
    orig_len = len(code)

    # Read here and not in transform(): transform() stays a pure function of
    # its input, which is what lets the tests drive both halves of the
    # docs/findings.md §11 A/B in one process.
    # ANY non-empty value opts out, not just "1", because build.sh decides
    # which of its two "NOT APPLIED" messages to print with `[ -n ... ]`. Under
    # the old `!= "1"` rule, NRC_NO_IMAGE_SHIM=true meant "shim it" here and
    # "opt-out" there: a shim that genuinely FAILED to find its gate would be
    # announced as deliberate, and nobody would look. One rule, both files.
    image_shim = not os.environ.get("NRC_NO_IMAGE_SHIM")
    code, counts = transform(code, image_shim=image_shim)
    errors = check(code, counts, assets_on_disk, asset_names)

    print(f"pragma block stripped  : {counts['pragma']}")
    print(f"/$bunfs/ paths rewired : {counts['assets']}")
    print(f"file:// leaks rewritten: {counts['file_urls']}")
    print(f"IIFE invocations added : {counts['iife']}  (expected 1)")
    # Loud either way, on stdout, because "the shim quietly did nothing" and
    # "the shim worked" produce artifacts that differ only when you happen to
    # Read a large image. build.sh greps the third line.
    print(f"image shim gate        : {counts['gate_name']}")
    if counts["gate_calls_before"] is None:
        # Not "0 -> 0". No gate was named, so no call site was ever counted,
        # and zeros here are a claim about this artifact that is measured
        # false: building a linux-x64 2.1.222 binary whose gate declaration had
        # been replaced in place by an equal-length arrow form printed
        # `0 -> 0` for an entry module that still held 21 live CE() calls
        # (measured on this host 2026-08-24).
        print("image shim call sites  : not counted (no gate identified)")
    else:
        print(f"image shim call sites  : {counts['gate_calls_before']} -> "
              f"{counts['gate_calls_after']}")
    # "(expected 1)" would be a lie in the opt-out build, where 0 is the whole
    # point; build.sh's sed only reads the number, so the tail is free text.
    print(f"image shim applied     : {counts['image_shim']}  "
          + ("(expected 1)" if image_shim else "(opt-out: NRC_NO_IMAGE_SHIM set)"))
    if counts["image_shim_reason"] is not None:
        # The CAUSE, on stdout, only when there is one - build.sh tees stdout
        # and reads this line for its closing headline. Before it existed that
        # headline named one cause ("a new Claude release renamed the anchor
        # string") for every refusal, including the drifted-declaration one
        # where the anchor was measured present exactly once. Omitted entirely
        # in a shimmed build so the log of the default build is unchanged.
        print(f"image shim not applied : {counts['image_shim_reason']}")
    print(f"size: {orig_len} -> {len(code)} bytes")

    # Diagnostics belong ABOVE the write: printed after "wrote:" they read as
    # commentary on a finished artifact rather than as reasons to look at it.
    for path in counts["build_paths"]:
        sys.stderr.write(f"note: build-machine path still present: {path}\n")
    for entry in sorted(asset_names):
        if entry not in code:
            sys.stderr.write(f"note: extracted asset never referenced: {entry}\n")
    if counts["image_shim"] == 0:
        # Not fatal: an artifact without the shim is exactly as good as every
        # artifact this repo shipped before it existed. Failing here would turn
        # a renamed string upstream into an outage.
        sys.stderr.write(
            "warning: image shim NOT applied (%s); the Read tool will error on "
            "images that need resizing, as it does in the unshimmed build\n"
            % counts["image_shim_reason"])

    if errors:
        for e in errors:
            sys.stderr.write(f"error: {e}\n")
        sys.exit(1)

    out = os.path.join(d, "cli.original.cjs")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(code)
    print(f"wrote: {out}")

    shim = os.path.join(d, SHIM_NAME)
    with open(shim, "w", encoding="utf-8") as fh:
        fh.write(SHIM_SOURCE)
    print(f"wrote: {shim}  (sibling for Claude's MCP self-spawns)")


if __name__ == "__main__":
    main()
