#!/usr/bin/env /usr/bin/python3
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

Usage:
  ./postprocess.py <extract-dir>
      <extract-dir> is the output of extract_bun.py: it must contain
      cli.original.js and an assets/ directory. Writes cli.original.cjs beside it.
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


def _asset_expr(match):
    name = match.group(2)
    return "require('path').join(__dirname,'assets'," + json.dumps(name) + ")"


def transform(code):
    """Pure text transform. Returns (new_code, counts)."""
    counts = {}
    code, counts["pragma"] = re.subn(r"^(?:\/\/[^\n]*\n)+", "", code, count=1)
    code, counts["assets"] = BUNFS_LITERAL.subn(_asset_expr, code)
    counts["file_urls"] = 0
    counts["iife"] = 0
    counts["leftovers"] = sorted(set(LEFTOVER_BUNFS.findall(code)))
    return code, counts


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
    if not os.path.isdir(os.path.join(d, "assets")):
        sys.stderr.write("warning: no assets/ dir; native/file modules will be missing\n")

    with open(src, "r", encoding="utf-8", errors="replace") as fh:
        code = fh.read()
    orig_len = len(code)

    # (1) strip leading `//` pragma comment lines (e.g. "// @bun @bytecode @bun-cjs")
    code, n_pragma = re.subn(r"^(?:\/\/[^\n]*\n)+", "", code, count=1)

    # (2) bunfs native .node requires → extracted assets/ on disk
    #     require('/$bunfs/root/NAME.node')  →
    #     require(require('path').join(__dirname,'assets','NAME.node'))
    def node_repl(m):
        name = m.group(1)
        return (f"require(require('path').join(__dirname,'assets',"
                f"{name!r}+'.node'))")
    code, n_node = re.subn(
        r"require\(['\"]\/\$bunfs\/root\/([\w-]+)\.node['\"]\)",
        node_repl, code)

    # (3) build-time fileURLToPath(import.meta.url) leaks → __filename
    code, n_url = re.subn(
        r"\(0,\s*[\w$]+\.fileURLToPath\)\([\w$.]*import\.meta\.url\)",
        "__filename", code)

    # (4) invoke the trailing CJS IIFE:  ...})  →  ...})(exports, require, module, __filename, __dirname)
    code, n_iife = re.subn(
        r"\}\)\s*$",
        "})(exports, require, module, __filename, __dirname)",
        code)

    out = os.path.join(d, "cli.original.cjs")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(code)

    # (5) report leftover bunfs references (file-loader assets, etc.)
    leftovers = sorted(set(re.findall(r"\/\$bunfs\/root\/[\w.\-]+", code)))

    print(f"pragma lines stripped : {n_pragma}")
    print(f".node requires rewired: {n_node}")
    print(f"fileURLToPath leaks   : {n_url}")
    print(f"IIFE invocations added: {n_iife}  (expected 1)")
    print(f"size: {orig_len} -> {len(code)} bytes")
    print(f"wrote: {out}")
    if n_iife != 1:
        sys.stderr.write("warning: expected exactly 1 trailing IIFE to invoke; "
                         "check the tail of cli.original.js\n")
    if leftovers:
        sys.stderr.write("\nleftover /$bunfs/ references (file-loader assets that "
                         "may still need rewriting for that feature):\n")
        for l in leftovers:
            sys.stderr.write(f"  {l}\n")
        sys.stderr.write("Basic use (--version, chat) does not touch these.\n")


if __name__ == "__main__":
    main()
