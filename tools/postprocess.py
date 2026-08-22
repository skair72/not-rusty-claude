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
  5. Report any leftover /$bunfs/ references (should be none after step 2).
  6. Write a one-line sibling cli.js next to it, because Claude's own code
     resolves join(__filename,'..','cli.js') for two MCP self-spawns.

check() then validates the transformed code is actually sound (starts with
`(function`, has exactly one trailing IIFE invocation). If it isn't, main()
prints the errors to stderr and exits non-zero WITHOUT writing cli.original.cjs
— a silently-broken output file reaching Bun surfaces only as the confusing
panic "Expected CommonJS module to have a function wrapper".

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
LEFTOVER_BUNFS = re.compile(r"/\$bunfs/root/[\w.\-]*")

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


def _asset_expr(match):
    name = match.group(2)
    return "require('path').join(__dirname,'assets'," + json.dumps(name) + ")"


def transform(code):
    """Pure text transform. Returns (new_code, counts)."""
    counts = {}
    code, counts["pragma"] = re.subn(r"^(?:\/\/[^\n]*\n)+", "", code, count=1)
    code, counts["assets"] = BUNFS_LITERAL.subn(_asset_expr, code)
    code, counts["file_urls"] = FILE_URL_LEAK.subn("__filename", code)
    code, counts["iife"] = re.subn(
        r"\}\)\s*$",
        "})(exports, require, module, __filename, __dirname)",
        code)
    counts["build_paths"] = sorted(set(BUILD_PATH_LEAK.findall(code)))
    counts["leftovers"] = sorted(set(LEFTOVER_BUNFS.findall(code)))
    return code, counts


def check(code, counts, assets_on_disk=None):
    """Return a list of fatal problems; empty means the output should load.

    assets_on_disk, when given, is the number of files extract_bun.py wrote to
    <extract-dir>/assets (None if that directory does not exist / was not
    checked - e.g. when transform() is exercised in isolation on a text
    snippet with no accompanying assets/ dir on disk).
    """
    errors = []
    if not code.startswith("(function"):
        errors.append("output does not start with '(function' - Bun's CJS loader "
                      "will panic with 'Expected CommonJS module to have a "
                      "function wrapper'")
    if counts["iife"] != 1:
        errors.append("expected exactly 1 trailing IIFE to invoke, found "
                      + str(counts["iife"])
                      + " - the file does not end in '})'")
    if counts["assets"] == 0 and assets_on_disk:
        errors.append(
            "0 /$bunfs/ paths were rewired but assets/ has "
            f"{assets_on_disk} file(s) on disk - BUNFS_LITERAL matched nothing "
            "(wrong VFS prefix for this platform? see docs/status.md's "
            "Windows/PE section) and the output would silently ship without "
            "its assets rather than fail loudly")
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
    assets_on_disk = None
    if os.path.isdir(assets_dir):
        assets_on_disk = len(os.listdir(assets_dir))
    else:
        sys.stderr.write("warning: no assets/ dir; native/file modules will be missing\n")

    with open(src, "r", encoding="utf-8", errors="replace") as fh:
        code = fh.read()
    orig_len = len(code)

    code, counts = transform(code)
    errors = check(code, counts, assets_on_disk)

    print(f"pragma block stripped  : {counts['pragma']}")
    print(f"/$bunfs/ paths rewired : {counts['assets']}")
    print(f"file:// leaks rewritten: {counts['file_urls']}")
    print(f"IIFE invocations added : {counts['iife']}  (expected 1)")
    print(f"size: {orig_len} -> {len(code)} bytes")

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

    for name in counts["leftovers"]:
        sys.stderr.write(f"warning: leftover bunfs reference: {name}\n")
    for path in counts["build_paths"]:
        sys.stderr.write(f"note: build-machine path still present: {path}\n")

    if assets_on_disk is not None:
        for entry in sorted(os.listdir(assets_dir)):
            if entry not in code:
                sys.stderr.write(f"note: extracted asset never referenced: {entry}\n")


if __name__ == "__main__":
    main()
