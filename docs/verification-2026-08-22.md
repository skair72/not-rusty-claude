# End-to-end verification run — 2026-08-22

The evidence record for the Linux end-to-end run: what was executed on this host,
and the output that came back. Later waves (2026-08-23, and the Apple Silicon run
of 2026-08-24) appended to it; where anything here disagrees with
[findings.md](./findings.md), **findings.md is current** — it holds every figure
this repo still claims, and this file holds the evidence that a command was run.

**Question answered:** does a current Claude Code `cli.js`, built by Anthropic
against Bun's canary channel, run on Bun 1.3.14 — the newest Bun that predates
the Zig→Rust rewrite? **Yes, for Claude Code 2.1.222 on Linux, on every code path
exercised below.** Nothing was skipped, patched, or worked around; what was *not*
exercised is stated where it matters.

## Host

| | |
|---|---|
| `uname -a` | `Linux cf8a06c63e8d 6.12.95+deb13-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.95-1 (2026-07-04) x86_64 GNU/Linux` |
| OS | Debian GNU/Linux 12 (bookworm), `x86_64` |
| glibc | 2.36 (`Debian GLIBC 2.36-9+deb12u14`) |
| CPU | AVX2 present → standard (non-baseline) Bun build is correct |
| Native binary under test | `/usr/bin/claude`, 289,467,400 bytes — **2.1.222** |
| macOS binary under test | `/tmp/ccmac/package/claude`, 324,973,552 bytes — **2.1.239** (`darwin-arm64`) |
| Bun for this run | **1.3.14** (`bun-linux-x64.zip`, standard build), unpacked to `~/.bun-1.3.14/bun`, **not** on `PATH`, no rc file touched |
| Repo / branch | `not-rusty-claude`, branch `claude/implement` |

---

## Safety checks

```
$ md5sum /usr/bin/claude                     # before and after every command
94e673a283dd91d0456080cc05a09083  /usr/bin/claude

$ ls -la /usr/bin/claude
-rwxr-xr-x 1 root root 289467400 Aug  4 02:01 /usr/bin/claude

$ command -v bun
(nothing — not found; exit 1)

$ command -v claude
/usr/bin/claude
```

The binary's mtime predates this work and is unchanged by it: it was only ever
read, by `extract_bun.py`, never executed or written. Bun 1.3.14 was never put on
`PATH`, and `command -v claude` still resolves only to the pre-existing system
binary — no `claude` launcher shadowing it was created.

`~/.bashrc` and `~/.bash_profile` do not exist on this host, before or after.
`~/.zshrc` and `~/.profile` were md5-checksummed immediately before Step 1 and
again after every step: `d50dec2a334463a79eac95753a5e67a2` for both, both times.
Neither file's content (a pre-existing, unrelated `. "$HOME/.local/bin/env"`
line) was touched. Every real run used `CLAUDE_CONFIG_DIR="$(mktemp -d)"`.

---

## Step 1 — install Bun 1.3.14 without mutating the shell profile

`unzip` of the release asset into `~/.bun-1.3.14`, then `bun --version` → exactly
`1.3.14`. **PASSED.** No `curl | bash` installer; nothing added to `PATH`; no rc
file written.

## Step 2 — build from the real ELF binary

`BUN_BIN=… scripts/build.sh /usr/bin/claude` — 8 modules, entry id 0,
`/$bunfs/ paths rewired : 5`, `file:// leaks rewritten: 7`,
`IIFE invocations added : 1`, all matching expectation, plus three informational
`note: build-machine path still present` lines and no leftover-`/$bunfs/`
warning. **PASSED.** The full output of a build is reproduced in
[runbook.md](./runbook.md) § 2; the figures are
[findings.md](./findings.md) §6's table.

Three labels in that run's output have since changed and are not behaviour
changes: `pragma lines stripped` is now `pragma block stripped` (renamed in
`59d9a98`); a second `wrote:` line for the `cli.js` sibling was added; and every
`.node` line was labelled `native base64`, which was the falsified loader-enum
bug itself — the code now correctly prints `native napi`. The *bytes* extracted
were and are correct; only the label was wrong
([findings.md](./findings.md) §5a).

Re-run at HEAD `306a72a`, the artifact came out byte-identical to the original
run's: `md5sum` → `5e3662ee9e2cfd8143c7a6a1bb0662bb`.

## Step 3 — syntactic validity

**Correction.** The original version of this document treated
`scripts/syntax-check.js` (`new Function(source)`) as proof the file "parses
cleanly under Bun's parser". It is not: `new Function()` invokes
**JavaScriptCore's** Function-constructor parser, unrelated to Bun's module
loader. The two demonstrably disagree in both directions on this host:

```
# false OK #1 — JSC accepts, Bun's loader rejects
$ bun scripts/syntax-check.js /tmp/.../html-comment-test.cjs      SYNTAX OK  (exit 0)
$ bun build --no-bundle --target=bun /tmp/.../html-comment-test.cjs --outfile=/dev/null
error: Unsupported syntax: Legacy HTML comments not implemented yet!  (exit 1)

# false OK #2 — a real defect it cannot catch: `(function(){…})` with the
# trailing `()` missing is valid JavaScript, so both checkers accept it
$ bun scripts/syntax-check.js /tmp/.../missing-iife-test.cjs       SYNTAX OK  (exit 0)
```

So `syntax-check.js` does **not** catch a broken or missing IIFE append;
`postprocess.py`'s own `check()` is what guards that. The **primary** L3 evidence
is Bun's own parser, invoked the way the real consumer parses the file:
`bun build --no-bundle --target=bun … --outfile=/dev/null` → exit 0 on the linux
artifact (29.60 MB chunk) and on the darwin one (36.33 MB chunk). **PASSED** on
both, both artifacts.

## Step 4 — asset paths rewritten (static check)

**Correction:** the original title, "rewritten asset paths resolve", claimed more
than the commands show. `ls` proves 5 asset files exist on disk;
`grep -o "require('path').join(__dirname,'assets'" … | wc -l` proves 5 rewrite
expressions exist as text. Neither executes anything. (`grep -c` would
under-count at 4 here, because minified code puts several rewrites on one line.)

**No executed command in this document loads an asset**, confirmed directly:
`build/extract/assets` was renamed away and both `--version` and `--help` still
exited 0 with unchanged output. Runtime asset resolution was unverified *here*;
it was settled later, in the equivalence-gap work
([findings.md](./findings.md) §10).

## Step 5 — the actual run under Zig-era Bun

`--version` printed `2.1.222 (Claude Code)`, exit 0, reproduced twice with
independent scratch config dirs, both left empty afterwards (`ls -A | wc -l` →
`0`), so nothing was written to them and a fortiori nothing to the real
`~/.claude`.

**What that alone does NOT prove:** `--version` resolves and exits during CLI
argument parsing. With the whole `assets/` directory renamed away it still
returns `2.1.222 (Claude Code)`, exit 0 — this rung by itself is compatible with
an artifact that cannot load any asset. (Measured later: it initialises **0**
lazy modules, [findings.md](./findings.md) §9.)

`--help` rendered the CLI's complete registered command/option table, 234 lines,
exit 0 — substantially more of the argument-parsing and command-registration
machinery — with the scratch config dir still empty afterwards.

`mcp list` is **the strongest evidence in this document**, because it touches
disk:

```
$ … bun build/extract/cli.original.cjs mcp list
No MCP servers configured. Use `claude mcp add` to add a server.        (exit 0)

$ find "$CLAUDE_CONFIG_DIR" | sort
/tmp/tmp.b8UVTry4IP
/tmp/tmp.b8UVTry4IP/.claude.json
/tmp/tmp.b8UVTry4IP/backups
/tmp/tmp.b8UVTry4IP/backups/.claude.json.backup.1787403804503
```

Config-file read (none existed, so it initialised one), JSON serialization, a
timestamped backup write and MCP-subsystem dispatch, all under Bun 1.3.14 with no
error.

`doctor`, at HEAD, on the same host:

```
Claude Code doctor

Running: unknown (2.1.222)
Commit: fbf49312c284
Platform: linux-x64
Path: <bun-1.3.14>
Invoked: <out>/extract/cli.original.cjs
Config install method: not set
Search: OK (/usr/bin/rg)
Auto-updates: disabled (set by env: DISABLE_AUTOUPDATER)
Auto-update channel: latest
Last update attempt: none recorded
(exit 0)
```

`Running: unknown` and `Search: OK (/usr/bin/rg)` are not cosmetic — they are the
equivalence gap showing itself ([findings.md](./findings.md) §10).

**The same artifact on Bun 1.4.0**, the Rust build: `--version`, `--help`,
`doctor` and `mcp list` all rc 0, first lines identical. This is what "1.3.14 is
sufficient, not necessary" rests on.

## Conclusion for findings.md §9

**Every Bun API reached on the exercised code paths** — `--version`, `--help`,
`mcp list` including its config read/write and MCP dispatch, and `doctor` — **is
present and working in Bun 1.3.14, for Claude Code 2.1.222.** An empirical,
version- and code-path-specific answer; not a permanent guarantee, and not a
claim that the full interactive application works. Had any rung failed, the
failure itself would have been the recorded finding.

---

## What the 2026-08-22 audit falsified

An eight-reviewer audit falsified several claims this repository made in prose.
Each is corrected in place in the file that carried it; this table exists so that
nobody re-derives a retracted value.

| Claim, as it stood | Measured truth |
|---|---|
| The `.node` addons use the **`base64`** loader | Their raw loader byte is **10 = `napi`**, on both shipped binaries. The repo's loader enum omitted `jsonc = 7`, shifting every id ≥ 7 |
| ClawGod handles only `napi`, so it extracts **zero** native modules | ClawGod's enum *is* Bun's; it labels them `napi` and **extracts them correctly**. What it drops is the `file`-loader assets |
| ClawGod's `fileURLToPath` transform matches nothing on current binaries | It matches **7 sites** — the same 7 this project rewrites. The 0-match regex was *this repo's own* scaffolded port |
| The extracted artifact needs an external **Zig** Bun | It needs an external **Bun**. The same artifact runs on **1.4.0**, the Rust build |
| Bun 1.3.14 is "pre-Rust" | True of the *rewrite*; false literally — its `.comment` reads `rustc 1.94.0-nightly` and it links vendored Rust crates |
| The pragma line alone would make Bun panic | Pragma-kept + **not** invoking runs fine on 1.3.14 **and 1.3.13**. The panic needs pragma **plus** manual invocation |
| Claude Code needs Bun ≥ 1.3.14 | 2.1.222 runs on **1.3.13** in the pragma-preserving shape. The floor is this project's transform, not Claude |
| The darwin artifact "has never been executed / needs Apple hardware" | It runs **here, on Linux**, and prints `2.1.239 (Claude Code)`. What needs a Mac is macOS-*specific* behaviour |
| Export `CLAUDE_CODE_EXECPATH` yourself for shell integrations | The CLI **never reads** it (0 occurrences of `process.env.CLAUDE_CODE_EXECPATH`). It *writes* it as `process.execPath` — now bun |
| "The Rust rewrite is experimental and Linux-x64-only" | Stale. `bun-v1.4.0` shipped **2026-08-20**, before this work, targeting all platforms |
| PR oven-sh/bun#30412 merged 2026-05-11 | Merged **2026-05-14T08:09:34Z** |
| 12 `fileURLToPath` **calls** survive | 12 textual hits; **9** are calls, 3 are import lines in embedded script text |
| Windows/PE "would be silently asset-less rather than loudly broken" | `check()` makes exactly that case **fatal** since `59d9a98` |
| Zig source paths in bun 1.3.14: "7" | **4** paths; 7 strings contain `.zig`, three being JS with an identifier `newResolver.zig` |
| ClawGod patches at `4401fdb`: 29 | **40** (`grep -cE '^    name: ' install.sh`) |

Plus one whole subject the repository had never mentioned: the **equivalence
gap**. The word `isStandaloneExecutable` appeared in **zero** files here before
that audit.

---

## Supporting measurements

Read with a standalone parser, **not** this repo's tools — the raw loader byte at
module-record offset 49:

```
/usr/bin/claude              modules=8   entry=0
  idx=0  byte=1   /$bunfs/root/src/entrypoints/cli.js        first4=b'// @'
  idx=3  byte=10  /$bunfs/root/image-processor.node          first4=b'\x7fELF'
  idx=4  byte=10  /$bunfs/root/audio-capture.node            first4=b'\x7fELF'
  idx=5..7 byte=5 chart.umd.min.js / hljsBundle… / mermaid.min.js
/tmp/ccmac/package/claude    modules=15  entry=0
  idx=6,7,8,12,14  byte=10   *.node   first4=b'\xcf\xfa\xed\xfe' or b'\xca\xfe\xba\xbe'
```

Bun 1.3.14 against 1.4.0:

```
$ readelf -p .comment ~/.bun-1.3.14/bun
  [    47]  rustc version 1.94.0-nightly (c61a3a44d 2025-12-09)
$ strings ~/.bun-1.3.14/bun | grep -c '\.zig'      → 7      (4 are source paths)
$ strings ~/.bun-1.4.0/bun  | grep -c '\.zig'      → 0
$ strings ~/.bun-1.3.14/bun | grep -i lolhtml | head -1
  /var/lib/buildkite-agent/build/vendor/lolhtml/src/memory/arena.rs
$ ~/.bun-1.3.14/bun -e 'console.log(Bun.isStandaloneExecutable)'   → undefined
$ ~/.bun-1.4.0/bun  -e 'console.log(Bun.isStandaloneExecutable)'   → false
```

**`bun -e` swallows a failing `require()` on 1.3.14** — found while checking that
this record's own pasted commands reproduce. Same addon, same host, same file:

```
$ ~/.bun-1.3.14/bun -e 'require("<macout>/…/image-processor.node")'
                                                          (no output, exit 0)
$ ~/.bun-1.3.14/bun /tmp/probe.cjs        # the same line, from a file
error: …/image-processor.node: invalid ELF header
 code: "ERR_DLOPEN_FAILED"                                            (exit 1)
$ ~/.bun-1.4.0/bun  -e 'require("<macout>/…/image-processor.node")'
error: …/image-processor.node: invalid ELF header                     (exit 1)
```

Exit 0 from `bun -e` there means "the expression was evaluated", not "the addon
loaded". Use a script file.

## What this record does not cover

- **No real model traffic.** Everything agentic here went to a loopback mock.
- **macOS-specific behaviour.** The darwin JS boots on this host, but its addons
  are Mach-O and `process.platform` is `linux`. The 2026-08-24 Apple Silicon run
  is reported in [README's macOS section](../README.md#macos), not here — it was
  not run on this host.
- **Windows.** Unimplemented by choice ([status.md](./status.md) § Windows/PE).
- **Most gate branches** were read from source, not exercised; the four that have
  an A/B behind them are [findings.md](./findings.md) §10's table.
- **The `find`/`grep` shell-function shadowing** was reconstructed from the
  shipped source, not observed live.
