# Claude Code 2.1.280: a code-split ESM build on a private Bun

**Date:** 2026-09-22
**Status:** design of record for branch `claude/claude-2-1-280`. Implemented,
and reconciled with a four-dimension review of the branch: what the review
changed is marked **Changed after review**. The verification harness below is
the definition of done.

The host's `/usr/bin/claude` moved from 2.1.222 to **2.1.280**, and the
pipeline stopped working on it: `build.sh` failed with four fatal errors.
Two changes in how Anthropic builds the binary explain all of them:

1. **The graph is code-split ESM.** The legacy shape was one CommonJS entry
   module with everything inlined (2.1.231: 11 modules, entry `fmt 2`). 2.1.280
   carries **2196 modules**: 1975 ES modules (`fmt 1`, the entry is a 21 KB
   `/$bunfs/root/cli`), 84 `text`-loader modules (skills, prompts), 135 `file`
   assets and 2 napi addons. The chunks import each other by `/$bunfs/root/`
   path: 119,359 side-effect `import"…"`, 17,195 `from"…"`, 1,196 `import("…")`,
   445 `import.meta.require("…")`.
2. **It targets a private Bun.** `Bun.ant` exists only in
   `@anthropic-ai/bun-internal`. Stock Bun 1.3.14 has every other `Bun.*` API
   the bundle calls (`Image`, `sliceAnsi`, `JSONL`, `WebView`, … — measured),
   but not `Bun.ant`, and Ink's renderer cannot draw a single cell without
   `Bun.ant.CellSegmenter`.

## Extraction: branch on the entry's format byte

Record offset 50 is Bun's module-format byte (0 none, 1 esm, 2 cjs). The
entry module's own value selects the shape — never module counts or names:

- `cjs`/`none` → the legacy path, byte-for-byte unchanged (every synthetic
  fixture leaves the byte 0).
- `esm` → every module written **verbatim** under `extract/original/<VFS path>`
  plus `extract/manifest.json` (path, loader, format, encoding, size, sha256).
  Subdirectories are kept (`src/plugins/functionHooks/hooks-worker/…`), so path
  validation refuses `..`, absolute, empty and NUL-bearing segments.

Record offset 48 is the content encoding, **measured** rather than transcribed:
0 = raw bytes (`file`, `napi`), 1 = Latin-1 (all JS and 64 text modules, all
ASCII), 2 = **UTF-16LE** (20 text modules; UTF-8 decoding rejects 16 of them).

## Post-processing: rewire by context

`postprocess.py` sees `manifest.json` and builds `extract/root/`:

| reference shape | rewritten to | why |
|---|---|---|
| `from"…"`, `import"…"`, `import("…")`, `require("…")`, `import.meta.require("…")` | a **relative** specifier | static specifiers must stay literals; these resolve against the module they are written in |
| `Re("…")` (`Re = import.meta.require` of the shared helper chunk), path constants, `HOOKS_WORKER_URL` | `(import.meta.dirname+"/…")` | these resolve against *another* module, or reach `fs` |
| a `text` module | a CommonJS wrapper `<name>.cjs` exporting the exact string | native `require()` returns the raw string; stock Bun returns `{default}` and renders `.md` **to HTML** |

The extensionless entry becomes `root/cli.js`. The Windows VFS prefix constant
(`"B:/~BUN/root/"`) is left alone and counted. Fatal: an unknown target, a
text module reached other than by a call to `import.meta.require` or a name
bound to it, any surviving `/$bunfs/` path (a bare POSIX prefix included), a
module that cannot be decoded with its recorded encoding, a sha256 mismatch
between `original/` and the manifest, two modules bound for one output file.

**Changed after review:**
- `root/` is built in a staging directory, so a failed re-run leaves the
  previous artifact whole.
- The regex-driven rewrite is checked by a parser: `scripts/verify-tree.js`
  makes Bun accept every module and keep exactly the import records it had.
  `build.sh` runs it before swapping a build in.
- Two self-spawn concerns were added: the Claude-in-Chrome MCP config gains the
  entry as its first argument, since `process.execPath` is bun here. The one
  module reached by path (the hooks Worker) gets the polyfill as its first
  import.

The artifact's entry is **`extract/cli.js`**, ours:

```js
import "./bun-ant.mjs";     // Bun.ant polyfill, installed only if absent
import "./root/cli.js";     // Claude's own entry
```

plus `package.json` `{"type":"module"}` for Node. `bun extract/cli.js …` is
therefore the run command in both shapes (legacy builds already ship a
`cli.js` sibling that requires `cli.original.cjs`).

## `Bun.ant`: a polyfill validated against the real thing

The native binary honours `BUN_OPTIONS=--preload <file>`, so a probe runs
**inside** Anthropic's runtime, where `Bun.ant` is real. That is the oracle.
It is a test-time instrument only; the pipeline still never executes the
binary.

- `CellSegmenter` — ported to pure JS and differential-fuzzed against the
  native class until byte-equal (cells, runs, tables, painted screens, return
  values).
- `getPeerPid/Uid` — `SO_PEERCRED` via `bun:ffi` on Linux; `null` where the
  native returns `null` (not a socket), measured. **Changed after review:** the
  argument is coerced like ToNumber, as native does, measured with a real peer
  process on the socket.
- `setDumpable` — `prctl(PR_SET_DUMPABLE)` via `bun:ffi`; throws where FFI is
  unavailable, which the call site reports as "prctl unavailable".
- `memoryPressureLevel` — throws the native Linux message verbatim; macOS is
  not guessed.

## The image shim is not applicable

2.1.280 has no `image-processor.node`; the Read tool resizes through
`new Bun.Image(…)`, ungated, and stock 1.3.14 has `Bun.Image`. The legacy shim
stays for legacy binaries; the esm path reports it as not applicable, and the
harness checks a real oversized-PNG Read instead.

## The verification harness (definition of done)

`scripts/harness.py`, `make harness`: every check runs the artifact **and** the
native binary where comparison is meaningful, and prints PASS/FAIL with
evidence plus a JSON report. The development loop is: run it, fix the first
failure, run it again, until it is green.

1. extract + post-process succeed, with counts that add up to the manifest;
2. every emitted module parses under Bun 1.3.14 and keeps its import records;
3. all 84 text modules `require()` to the same string as native;
4. `--version`, `--help`, `mcp list`, an `mcp add`/`get`/`remove`/`list`
   round trip, `plugin list`, `auth status`: exit code, stdout and stderr equal
   to native at every step; `doctor` equal bar the documented lines; the
   Claude-in-Chrome MCP server, started directly and through Claude's own
   config for it;
5. mock-API agentic turns (Bash, Read of a text file, Read of a 3000×3000 PNG,
   Grep, Write, Glob, function hooks) produce equal tool results and request
   bodies;
6. the interactive TUI under a pty: onboarding, a REPL turn and a unicode-heavy
   REPL turn draw identical screens, styles and links included, and exit
   cleanly;
7. `Bun.ant.CellSegmenter`: the fuzz corpus is byte-equal to native;
8. the pytest suite passes.
