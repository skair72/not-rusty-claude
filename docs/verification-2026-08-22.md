# End-to-end verification run — 2026-08-22

> ## 📌 This body is PINNED to commit `56e8877`. Read the addendum first.
>
> Everything from "## Host" down to "## Summary table" is the original
> evidence record as it was produced, and is **deliberately not being
> rewritten**: an evidence record whose history is edited is not evidence.
> Two of its numbers are stale as a result — it pastes the label
> `pragma lines stripped` (the code now prints `pragma block stripped`, renamed
> in `59d9a98`) and a `22 passed` test run (HEAD is 43). Neither reflects a
> behaviour change; both are the record stopping one commit short.
>
> An **8-reviewer audit on 2026-08-22 falsified several claims this document
> and the rest of the docs made.** The corrections, and a fresh re-run at
> current HEAD with pasted output, are in the
> [**2026-08-22 addendum**](#addendum-2026-08-22-fleet-audit-wave-1-fixes-and-a-re-run-at-head)
> at the end of this file. Where the body and the addendum disagree, **the
> addendum is current.**

*(Revised after code review, fix round 1/5: the original version overstated
what the evidence proved in three places. Those claims are corrected below;
the underlying commands were re-run against freshly rebuilt artifacts and
every number reproduced identically. The itemized changes were recorded in a
task report that is not part of this repository.)*

This document records the first real execution of the extraction + post-process
pipeline against Bun's last Zig-era release, answering the open question in
`docs/findings.md` §10: does a current Claude Code `cli.js`, built by Anthropic
against Bun's canary channel, actually run on Bun 1.3.14 (the newest Bun that
still predates the Zig→Rust rewrite)?

**Result: yes, for Claude Code 2.1.222 on Linux, on every code path actually
exercised below.** The extracted, post-processed `cli.original.cjs` runs
under vanilla external Bun 1.3.14 for `--version`, `--help`, and `mcp list`
(the last of which reads and writes real config-file state — the deepest
code path exercised here). Every rung below was executed on this host;
nothing was skipped, patched, or worked around. What was *not* exercised is
stated explicitly wherever it matters, most importantly in Steps 4 and 5.

## Host

| | |
|---|---|
| `uname -a` | `Linux cf8a06c63e8d 6.12.95+deb13-amd64 #1 SMP PREEMPT_DYNAMIC Debian 6.12.95-1 (2026-07-04) x86_64 GNU/Linux` |
| OS | Debian GNU/Linux 12 (bookworm), `x86_64` |
| glibc | 2.36 (`Debian GLIBC 2.36-9+deb12u14`) |
| CPU | AVX2 present → standard (non-baseline) Bun build is correct |
| Claude Code under test (native binary) | `/usr/bin/claude`, 289,467,400 bytes — **2.1.222** |
| Claude Code under test (macOS, syntax-only) | `/tmp/ccmac/package/claude`, 324,973,552 bytes — **2.1.239** (`darwin-arm64`, from `package.json`) |
| Bun installed for this run | **1.3.14** (`bun-linux-x64.zip`, standard/non-baseline), installed to `~/.bun-1.3.14/bun`, not on `PATH`, no rc file touched |
| Repo / branch | `not-rusty-claude`, branch `claude/implement` |

---

## Safety checks

These constraints were asserted in the original document without pasted
evidence; here is the evidence.

```
$ md5sum /usr/bin/claude
94e673a283dd91d0456080cc05a09083  /usr/bin/claude

$ ls -la /usr/bin/claude
-rwxr-xr-x 1 root root 289467400 Aug  4 02:01 /usr/bin/claude
```

(`/usr/bin/claude`'s mtime, `Aug 4 02:01`, predates this entire task and is
unchanged by it — the binary was only ever read, by `extract_bun.py`, never
executed or written.)

```
$ command -v bun
(nothing — not found; exit 1)

$ command -v claude
/usr/bin/claude
```

(Bun 1.3.14 was never put on `PATH`; `command -v claude` still resolves only
to the pre-existing system binary, i.e. no `claude` launcher shadowing it was
created.)

```
$ ls -la ~/.bashrc ~/.bash_profile
ls: cannot access '/home/claude/.bashrc': No such file or directory
ls: cannot access '/home/claude/.bash_profile': No such file or directory

$ cat ~/.zshrc

. "$HOME/.local/bin/env"

$ cat ~/.profile

. "$HOME/.local/bin/env"

$ md5sum ~/.zshrc ~/.profile
d50dec2a334463a79eac95753a5e67a2  /home/claude/.zshrc
d50dec2a334463a79eac95753a5e67a2  /home/claude/.profile
```

`~/.bashrc` and `~/.bash_profile` do not exist on this host, before or after
this task. `~/.zshrc` and `~/.profile` were md5-checksummed immediately
before Step 1 ran; the checksums above, taken after every other step in this
document (including this fix round), are identical
(`d50dec2a334463a79eac95753a5e67a2` for both, both times). Neither file's
content (a pre-existing, unrelated `. "$HOME/.local/bin/env"` line from
before this task started) was touched.

The L4 real run (Step 5) used `CLAUDE_CONFIG_DIR="$(mktemp -d)"` for every
invocation; each scratch directory's contents were inspected immediately
afterward and are reported per-command below.

---

## Step 1 — Install Bun 1.3.14 without mutating the shell profile

Command:

```bash
mkdir -p "$HOME/.bun-1.3.14"
curl -fsSL -o /tmp/bun-1.3.14.zip \
  https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-linux-x64.zip
unzip -o -j /tmp/bun-1.3.14.zip 'bun-linux-x64/bun' -d "$HOME/.bun-1.3.14"
chmod +x "$HOME/.bun-1.3.14/bun"
"$HOME/.bun-1.3.14/bun" --version
```

Output:

```
Archive:  /tmp/bun-1.3.14.zip
  inflating: /home/claude/.bun-1.3.14/bun
1.3.14
```

**PASSED.** Exactly `1.3.14`, matching expectation. No `curl | bash` installer
was used; nothing was added to `PATH`; no rc file was written.

---

## Step 2 — L1+L2: build from the real ELF binary

Command:

```bash
BUN_BIN="$HOME/.bun-1.3.14/bun" scripts/build.sh /usr/bin/claude
```

Output (ANSI colour codes stripped via `sed -E 's/\x1b\[[0-9;]*m//g'` for
readability — `scripts/build.sh` emits `\033[36m==>\033[0m`-style codes
unconditionally, with no isatty check, so the raw terminal output is
otherwise identical to this but interleaved with escape bytes; confirmed by
re-running with output redirected to a file and inspecting it with `od -c`,
which shows the `\033` (octal) / `0x1b` ESC byte preceding each `==>` and
`warning:` line):

```
==> native binary: /usr/bin/claude
==> bun: 1.3.14 (/home/claude/.bun-1.3.14/bun)
==> extracting cli.js + assets -> /projects/skair72-not-rusty-claude/worktrees/implement/build/extract
Size:    276.1 MB
Section: offset=86904832 size=202513494 (193.1 MB)
Payload: 202513486 bytes, trailer OK
Modules: 8 (entry id=0)
  entry   js       21.90 MB -> /projects/skair72-not-rusty-claude/worktrees/implement/build/extract/cli.original.js
  native  base64     1430 KB -> /projects/skair72-not-rusty-claude/worktrees/implement/build/extract/assets/image-processor.node
  native  base64      481 KB -> /projects/skair72-not-rusty-claude/worktrees/implement/build/extract/assets/audio-capture.node
  asset   file        204 KB -> /projects/skair72-not-rusty-claude/worktrees/implement/build/extract/assets/chart.umd.min.js
  asset   file        962 KB -> /projects/skair72-not-rusty-claude/worktrees/implement/build/extract/assets/hljsBundle.generated.min.js
  asset   file       3235 KB -> /projects/skair72-not-rusty-claude/worktrees/implement/build/extract/assets/mermaid.min.js
Extracted: 1 cli.js + 5 assets (2 loader shims left inlined in cli.js)
==> post-processing cli.js for external Bun
note: build-machine path still present: /home/runner/work/claude-cli-internal/claude-cli-internal/node_modules/@ant/computer-use-swift/js
note: build-machine path still present: /home/runner/work/claude-cli-internal/claude-cli-internal/node_modules/@grpc/grpc-js/build/src
note: build-machine path still present: /home/runner/work/claude-cli-internal/claude-cli-internal/src/frame
pragma lines stripped  : 1
/$bunfs/ paths rewired : 5
file:// leaks rewritten: 7
IIFE invocations added : 1  (expected 1)
size: 22960130 -> 22959448 bytes
wrote: /projects/skair72-not-rusty-claude/worktrees/implement/build/extract/cli.original.cjs
==> artifacts ready:
      /projects/skair72-not-rusty-claude/worktrees/implement/build/extract/cli.original.cjs
      /projects/skair72-not-rusty-claude/worktrees/implement/build/extract/assets/

==> run it with:
      /home/claude/.bun-1.3.14/bun /projects/skair72-not-rusty-claude/worktrees/implement/build/extract/cli.original.cjs --version

warning: Nothing was installed on PATH. Creating a 'claude' launcher could shadow
warning: your real installation - run the command above by full path instead.
```

**PASSED.** 8 modules, `/$bunfs/ paths rewired : 5`, `file:// leaks rewritten: 7`,
`IIFE invocations added : 1` — all match the expected values exactly. The three
`note:` lines are the tool's own informational category for build-machine paths
left in string literals that are not `/$bunfs/` references (see
`tools/postprocess.py`, "build-machine path still present"); they are distinct
from, and did not include, any `warning: leftover bunfs reference` line, so
there were no leftover-`/$bunfs/` warnings.

This step (and Step 3b's extraction) was re-run from a clean `build/`
directory during the fix round below, purely to capture output with ANSI
codes intact for inspection; every number reproduced identically to the
original run.

---

## Step 3 — L3: syntactic validity of the CJS wrapper (Linux/x64 output)

**Correction:** the original version of this document treated
`scripts/syntax-check.js` (`new Function(source)`) as proof the file "parses
cleanly under Bun's parser." That is wrong: `new Function()` invokes
**JavaScriptCore's Function-constructor parser** — a general JS engine
facility, unrelated to Bun's own module loader, which uses its own
transpiler. The two demonstrably disagree in both directions on this host:

- **False OK (JSC accepts, Bun's loader rejects):** a file whose first line
  is a legacy HTML comment (`<!-- ...`) is valid, ignorable syntax to
  `new Function()` but is explicitly unsupported by Bun's parser.
  Reproduced here:

  ```
  $ "$HOME/.bun-1.3.14/bun" scripts/syntax-check.js /tmp/.../html-comment-test.cjs
  SYNTAX OK
  (exit 0)

  $ "$HOME/.bun-1.3.14/bun" build --no-bundle --target=bun /tmp/.../html-comment-test.cjs --outfile=/dev/null
  1 | <!-- legacy html comment
       ^
  error: Unsupported syntax: Legacy HTML comments not implemented yet!
      at /tmp/.../html-comment-test.cjs:1:2
  (exit 1)
  ```

- **False OK, a second way (a real defect `new Function()` cannot catch):** a
  function expression that was supposed to be an invoked IIFE but is missing
  its trailing `()` — `(function(){ ... })` with no call — is perfectly
  valid JavaScript on its own (it just evaluates to an unused function value)
  and both checkers accept it:

  ```
  $ "$HOME/.bun-1.3.14/bun" scripts/syntax-check.js /tmp/.../missing-iife-test.cjs
  SYNTAX OK
  (exit 0)
  ```

  **`scripts/syntax-check.js` does NOT catch a broken/missing IIFE append.**
  (`tools/postprocess.py`'s own `check()` step is what actually guards this
  in the real pipeline — it counts and verifies IIFE invocations structurally
  before `new Function()` ever runs — so the pipeline as a whole is sound,
  but this specific script must not be credited with catching that case.)

Given this, the **primary** L3 evidence is now Bun's own parser/transpiler,
invoked the way the pipeline's actual consumer (`bun cli.original.cjs`)
would parse the file:

```bash
"$HOME/.bun-1.3.14/bun" build --no-bundle --target=bun build/extract/cli.original.cjs --outfile=/dev/null
```

Output:

```
Transpiled file in 1699ms

  null  29.60 MB  (chunk)

(exit 0, real 1.735s)
```

`scripts/syntax-check.js` is retained only as a **secondary**, much faster
sanity check, with its description corrected in the script's own header
comment to name JavaScriptCore's Function parser explicitly and document the
divergence above.

Command and output for the secondary check:

```bash
"$HOME/.bun-1.3.14/bun" scripts/syntax-check.js build/extract/cli.original.cjs
```

```
SYNTAX OK
(exit 0)
```

**PASSED** on both checks.

---

## Step 3b — L3 on the darwin output (the macOS path's real check on this host)

Command:

```bash
OUT_DIR=/tmp/macbuild scripts/build.sh /tmp/ccmac/package/claude
```

Output (ANSI stripped, same method as Step 2):

```
==> native binary: /tmp/ccmac/package/claude
warning: bun not found; artifacts will still be built. Install the last Zig release:
warning:   curl -fsSL https://bun.sh/install | bash -s "bun-v1.3.14"
==> extracting cli.js + assets -> /tmp/macbuild/extract
Size:    309.9 MB
Section: offset=69107712 size=255007133 (243.2 MB)
Payload: 255007125 bytes, trailer OK
Modules: 15 (entry id=0)
  entry   js       26.94 MB -> /tmp/macbuild/extract/cli.original.js
  native  base64     1220 KB -> /tmp/macbuild/extract/assets/image-processor.node
  native  base64      859 KB -> /tmp/macbuild/extract/assets/computer-use-swift.node
  native  base64     1652 KB -> /tmp/macbuild/extract/assets/computer-use-input.node
  asset   file        204 KB -> /tmp/macbuild/extract/assets/chart.umd.min.js
  asset   file        962 KB -> /tmp/macbuild/extract/assets/hljsBundle.generated.min.js
  asset   file       3235 KB -> /tmp/macbuild/extract/assets/mermaid.min.js
  native  base64      428 KB -> /tmp/macbuild/extract/assets/audio-capture.node
  asset   file       2177 KB -> /tmp/macbuild/extract/assets/payload.template.html.asset
  native  base64      329 KB -> /tmp/macbuild/extract/assets/url-handler.node
Extracted: 1 cli.js + 9 assets (5 loader shims left inlined in cli.js)
==> post-processing cli.js for external Bun
note: build-machine path still present: /home/runner/work/claude-cli-internal/claude-cli-internal/node_modules/@grpc/grpc-js/build/src
note: build-machine path still present: /home/runner/work/claude-cli-internal/claude-cli-internal/src/frame
note: build-machine path still present: /home/runner/work/claude-cli-internal/claude-cli-internal/src/skills/bundled
pragma lines stripped  : 1
/$bunfs/ paths rewired : 9
file:// leaks rewritten: 8
IIFE invocations added : 1  (expected 1)
size: 28244743 -> 28244063 bytes
wrote: /tmp/macbuild/extract/cli.original.cjs
==> artifacts ready:
      /tmp/macbuild/extract/cli.original.cjs
      /tmp/macbuild/extract/assets/

==> run it with:
      bun /tmp/macbuild/extract/cli.original.cjs --version

warning: Nothing was installed on PATH. Creating a 'claude' launcher could shadow
warning: your real installation - run the command above by full path instead.
```

`/$bunfs/ paths rewired : 9` and `file:// leaks rewritten: 8` match the
expected values exactly. `bun not found` is expected: the brief's Step 3b
command intentionally omits `BUN_BIN`, so `build.sh` falls back to
`command -v bun`, which is empty because Bun 1.3.14 was deliberately not put
on `PATH`. This is advisory only — extraction and post-processing do not
require Bun.

L3, primary check (Bun's own parser/transpiler):

```bash
"$HOME/.bun-1.3.14/bun" build --no-bundle --target=bun /tmp/macbuild/extract/cli.original.cjs --outfile=/dev/null
```

```
Transpiled file in 2192ms

  null  36.33 MB  (chunk)

(exit 0, real 2.240s)
```

L3, secondary check:

```bash
"$HOME/.bun-1.3.14/bun" scripts/syntax-check.js /tmp/macbuild/extract/cli.original.cjs
```

```
SYNTAX OK
(exit 0)
```

**PASSED** on both checks. Given the JSC-vs-Bun divergence documented in
Step 3, the primary (`bun build --no-bundle`) result is the one that
actually matters here, and it too passed.

**Not verifiable on this host / left as an explicit gap:** actually *running*
(L4) the darwin build requires macOS on Apple Silicon (Mach-O binaries are not
executable on Linux, and there is no realistic emulation path for a Bun-hosted
GUI-less CLI of this size). Only the syntax rungs (L3) could be exercised for
the macOS artifact here. `bun build --no-bundle --target=bun` is the
strongest check available on this host for the darwin artifact, but it does
not prove the darwin build boots, loads its native `.node` assets, or
resolves any Bun API at runtime.

---

## Step 4 — L5: asset paths rewritten (static check; runtime resolution unverified)

**Correction:** the original title, "rewritten asset paths resolve," claimed
more than the commands below can show. `ls` proves 5 asset files exist on
disk; `grep -o | wc -l` proves 5 rewrite expressions exist as text in
`cli.original.cjs`. Neither command executes anything, so neither proves any
asset is ever actually loaded at runtime.

Commands:

```bash
ls -la build/extract/assets/
grep -o "require('path').join(__dirname,'assets'" build/extract/cli.original.cjs | wc -l
```

Output:

```
total 6328
drwxr-xr-x 2 claude claude    4096 Aug 22 13:02 .
drwxr-xr-x 3 claude claude    4096 Aug 22 13:02 ..
-rw-r--r-- 1 claude claude  492184 Aug 22 13:02 audio-capture.node
-rw-r--r-- 1 claude claude  208522 Aug 22 13:02 chart.umd.min.js
-rw-r--r-- 1 claude claude  985483 Aug 22 13:02 hljsBundle.generated.min.js
-rw-r--r-- 1 claude claude 1464760 Aug 22 13:02 image-processor.node
-rw-r--r-- 1 claude claude 3312967 Aug 22 13:02 mermaid.min.js
5
```

**PASSED** as a static check: 5 assets listed; `grep -o | wc -l` count is `5`,
matching expectation exactly (as the brief warns, `grep -c` would under-count
at `4` here because minified code places several rewrites on one line — not
used).

**No executed command loaded an asset; runtime resolution of `assets/*`
remains unverified on this host.** Confirmed directly: `build/extract/assets`
was renamed away (`mv assets assets.hidden`) and both `--version` and
`--help` were re-run — both still exited `0` with unchanged output:

```
$ mv build/extract/assets build/extract/assets.hidden

$ CLAUDE_CONFIG_DIR="$(mktemp -d)" "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs --version
2.1.222 (Claude Code)
(exit 0)

$ CLAUDE_CONFIG_DIR="$(mktemp -d)" "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs --help
(234 lines of output, exit 0 — identical to the Step 5b --help output below)

$ mv build/extract/assets.hidden build/extract/assets
```

Neither `--version` nor `--help` (nor, by extension, `mcp list` in Step 5b,
which never mentions an asset) touches the native `.node` modules
(`image-processor.node`, `audio-capture.node`) or the bundled JS assets
(`chart.umd.min.js`, `hljsBundle.generated.min.js`, `mermaid.min.js`) — those
are used by features (image processing, audio capture, syntax highlighting,
diagram rendering) that none of the commands exercised in this document
reach. Whether the rewritten `require('path').join(__dirname,'assets',...)`
expressions actually resolve correctly at runtime is unverified here.

---

## Step 5 — L4: the actual run under Zig-era Bun

This is the rung that answers `findings.md` §10 for the simplest possible
invocation.

Command (run twice, each with an independent scratch `CLAUDE_CONFIG_DIR`, to
confirm determinism):

```bash
CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs --version
```

Run 1:

```
$ CLAUDE_CONFIG_DIR=/tmp/tmp.17ojEI6vbI
2.1.222 (Claude Code)
(exit 0, stderr empty, scratch dir entries after run: 0)
```

Run 2 (fresh, independent scratch dir):

```
$ CLAUDE_CONFIG_DIR=/tmp/tmp.xfEIwFzdot
2.1.222 (Claude Code)
(exit 0, stderr empty, scratch dir entries after run: 0)
```

**PASSED**, identically, both times. Both scratch config directories were
confirmed empty after the run (`ls -A | wc -l` → `0`), so nothing was written
to them, and a fortiori nothing was written to the real `~/.claude`.

**What this alone does NOT prove:** Claude's own `--version` handler is a
`commander` `.version()` option that resolves and exits during CLI argument
parsing, before the bulk of the application initializes. Confirmed directly:
with the entire `build/extract/assets/` directory renamed away (Step 4),
`--version` still returns `2.1.222 (Claude Code)`, exit `0` — i.e. this
single rung, by itself, is compatible with a `cli.original.cjs` that cannot
load any of its assets, has broken MCP handling, or is missing large parts of
its runtime, provided the small amount of code on the `--version` path
happens to run. Step 5b below exercises substantially more of the runtime
under the same Bun 1.3.14.

---

## Step 5b — L4, deeper: `--help` and `mcp list`

Added in this fix round in response to review: `--version` alone under-states
what has actually been verified. These two additional commands were run
under the same Bun 1.3.14, with the same scratch-`CLAUDE_CONFIG_DIR`
discipline.

### `--help`

```bash
CLAUDE_CONFIG_DIR="$(mktemp -d)" "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs --help
```

Output (234 lines, exit `0`):

```
Usage: claude [options] [command] [prompt]

Claude Code - starts an interactive session by default, use -p/--print for
non-interactive output

Arguments:
  prompt                                Your prompt

Options:
  --add-dir <directories...>            Additional directories to allow tool
                                        access to
  --agent <agent>                       Agent for the current session. Overrides
                                        the 'agent' setting.
  --agents <json>                       JSON object defining custom agents (e.g.
                                        '{"reviewer": {"description": "Reviews
                                        code", "prompt": "You are a code
                                        reviewer"}}')
  --allow-dangerously-skip-permissions  Enable bypassing all permission checks
                                        as an option, without it being enabled
                                        by default. Recommended only for
                                        sandboxes with no internet access.
  --allowedTools, --allowed-tools <tools...>
      Comma or space-separated list of tool names to allow (e.g. "Bash(git *)
      Edit")
  --append-system-prompt <prompt>       Append a system prompt to the default
                                        system prompt
  --autocompact <auto|tokens>           Auto-compact window size (auto, or
                                        100k–1M tokens)
  --ax-screen-reader                    Render screen-reader friendly output
                                        (flat text, no decorative borders or
                                        animations).
  --bg, --background                    Start the session as a background agent
                                        and return immediately (manage with
                                        `claude agents`)
  --bare                                Minimal mode: skip hooks, LSP, plugin
                                        sync, attribution, auto-memory,
                                        background prefetches, keychain reads,
                                        and CLAUDE.md auto-discovery. Sets
                                        CLAUDE_CODE_SIMPLE=1. Anthropic auth is
                                        strictly ANTHROPIC_API_KEY or
                                        apiKeyHelper via --settings (OAuth and
                                        keychain are never read). 3P providers
                                        (Bedrock/Vertex/Foundry) use their own
                                        credentials. Skills still resolve via
                                        /skill-name. Explicitly provide context
                                        via: --system-prompt[-file],
                                        --append-system-prompt[-file], --add-dir
                                        (CLAUDE.md dirs), --mcp-config,
                                        --settings, --agents, --plugin-dir.
  --betas <betas...>                    Beta headers to include in API requests
                                        (API key users only)
  --brief                               Enable SendUserMessage tool for
                                        agent-to-user communication
  --chrome                              Enable Claude in Chrome integration
  -c, --continue                        Continue the most recent conversation in
                                        the current directory
  --dangerously-skip-permissions        Bypass all permission checks.
                                        Recommended only for sandboxes with no
                                        internet access.
  -d, --debug [filter]                  Enable debug mode with optional category
                                        filtering (e.g., "api,hooks" or
                                        "!1p,!file")
  --debug-file <path>                   Write debug logs to a specific file path
                                        (implicitly enables debug mode)
  --disable-slash-commands              Disable all skills
  --disallowedTools, --disallowed-tools <tools...>
      Comma or space-separated list of tool names to deny (e.g. "Bash(git *)
      Edit")
  --effort <level>                      Effort level for the current session
                                        (low, medium, high, xhigh, max)
  --exclude-dynamic-system-prompt-sections
      Move per-machine sections (cwd, env info, memory paths, git status) from
      the system prompt into the first user message. Improves cross-user
      prompt-cache reuse. Only applies with the default system prompt (ignored
      with --system-prompt). (default: false)
  --fallback-model <model>              Enable automatic fallback to specified
                                        model(s) when the default model is
                                        overloaded or not available. Accepts a
                                        comma-separated list to try each in
                                        order. Re-tries the primary at the start
                                        of each user turn. (only works with
                                        --print)
  --file <specs...>                     File resources to download at startup.
                                        Format: file_id:relative_path (e.g.,
                                        --file file_abc:doc.txt
                                        file_def:img.png)
  --fork-session                        When resuming, create a new session ID
                                        instead of reusing the original (use
                                        with --resume or --continue)
  --forward-subagent-text               Forward subagent text and thinking
                                        blocks as assistant/user messages with
                                        parent_tool_use_id set (only works with
                                        --print and --output-format=stream-json)
  --from-pr [value]                     Resume a session linked to a PR by PR
                                        number/URL, or open interactive picker
                                        with optional search term
  -h, --help                            Display help for command
  --ide                                 Automatically connect to IDE on startup
                                        if exactly one valid IDE is available
  --include-hook-events                 Include all hook lifecycle events in the
                                        output stream (only works with
                                        --output-format=stream-json)
  --include-partial-messages            Include partial message chunks as they
                                        arrive (only works with --print and
                                        --output-format=stream-json)
  --input-format <format>               Input format (only works with --print):
                                        "text" (default), or "stream-json"
                                        (realtime streaming input) (choices:
                                        "text", "stream-json")
  --json-schema <schema>                JSON Schema for structured output
                                        validation. Example:
                                        {"type":"object","properties":{"name":{"type":"string"}},"required":["name"]}
  --max-budget-usd <amount>             Maximum dollar amount to spend on API
                                        calls (only works with --print)
  --mcp-config <configs...>             Load MCP servers from JSON files or
                                        strings (space-separated)
  --model <model>                       Model for the current session. Provide
                                        an alias for the latest model (e.g.
                                        'fable', 'opus', or 'sonnet') or a
                                        model's full name (e.g.
                                        'claude-fable-5').
  -n, --name <name>                     Set a display name for this session
                                        (shown in the prompt box, /resume
                                        picker, and terminal title)
  --no-chrome                           Disable Claude in Chrome integration
  --no-session-persistence              Disable session persistence - sessions
                                        will not be saved to disk and cannot be
                                        resumed (only works with --print)
  --output-format <format>              Output format (only works with --print):
                                        "text" (default), "json" (single
                                        result), or "stream-json" (realtime
                                        streaming) (choices: "text", "json",
                                        "stream-json")
  --permission-mode <mode>              Permission mode to use for the session
                                        (choices: "acceptEdits", "auto",
                                        "bypassPermissions", "manual",
                                        "dontAsk", "plan")
  --plugin-dir <path>                   Load a plugin from a directory or .zip
                                        for this session only (repeatable:
                                        --plugin-dir A --plugin-dir B.zip)
                                        (default: [])
  --plugin-url <url>                    Fetch a plugin .zip from a URL for this
                                        session only (repeatable: --plugin-url A
                                        --plugin-url B) (default: [])
  -p, --print                           Print response and exit (useful for
                                        pipes). Note: The workspace trust dialog
                                        is skipped when Claude is run in
                                        non-interactive mode (via -p, or when
                                        stdout is not a TTY, e.g. piped or
                                        redirected output). Only use this in
                                        directories you trust. Settings files
                                        that fail validation are silently
                                        ignored in this mode (no error dialog is
                                        shown).
  --prompt-suggestions [value]          Enable prompt suggestions. In print/SDK
                                        mode, emits a prompt_suggestion message
                                        after each turn with a predicted next
                                        user prompt (choices: "true", "false",
                                        "1", "0", "yes", "no", "on", "off",
                                        preset: "true")
  --remote-control [name]               Start an interactive session with Remote
                                        Control enabled (optionally named)
  --remote-control-session-name-prefix <prefix>
      Prefix for auto-generated Remote Control session names (default: hostname)
  --replay-user-messages                Re-emit user messages from stdin back on
                                        stdout for acknowledgment (only works
                                        with --input-format=stream-json and
                                        --output-format=stream-json)
  -r, --resume [value]                  Resume a conversation by session ID, or
                                        open interactive picker with optional
                                        search term
  --safe-mode                           Start with all customizations
                                        (CLAUDE.md, skills, plugins, hooks, MCP
                                        servers, custom commands and agents,
                                        output styles, workflows, custom themes,
                                        keybindings, and more) disabled — useful
                                        for troubleshooting a broken
                                        configuration. Admin-managed (policy)
                                        settings still apply. Auth, model
                                        selection, built-in tools, and
                                        permissions work normally. Sets
                                        CLAUDE_CODE_SAFE_MODE=1.
  --session-id <uuid>                   Use a specific session ID for the
                                        conversation (must be a valid UUID)
  --setting-sources <sources>           Comma-separated list of setting sources
                                        to load (user, project, local).
  --settings <file-or-json>             Path to a settings JSON file or a JSON
                                        string to load additional settings from
  --strict-mcp-config                   Only use MCP servers from --mcp-config,
                                        ignoring all other MCP configurations
  --system-prompt <prompt>              System prompt to use for the session
  --tmux                                Create a tmux session for the worktree
                                        (requires --worktree). Uses iTerm2
                                        native panes when available; use
                                        --tmux=classic for traditional tmux.
  --tools <tools...>                    Specify the list of available tools from
                                        the built-in set. Use "" to disable all
                                        tools, "default" to use all tools, or
                                        specify tool names (e.g.
                                        "Bash,Edit,Read").
  --verbose                             Override verbose mode setting from
                                        config
  -v, --version                         Output the version number
  -w, --worktree [name]                 Create a new git worktree for this
                                        session (optionally specify a name)

Commands:
  agents [options]                      Manage background agents
  auth                                  Manage authentication
  auto-mode                             Inspect or reset auto mode classifier
                                        configuration
  doctor                                Check the health of your Claude Code
                                        installation. Reads settings files in
                                        the current directory without a trust
                                        prompt. For a full checkup that can also
                                        fix issues, run /doctor in a session.
  gateway [options]                     Run the enterprise auth/telemetry
                                        gateway
  import [options] [source]             Import config from another AI coding
                                        agent into Claude Code
  install [options] [target]            Install Claude Code native build. Use
                                        [target] to specify version (stable,
                                        latest, or specific version)
  mcp                                   Configure and manage MCP servers
  plugin|plugins                        Manage Claude Code plugins
  project                               Manage Claude Code project state
  setup-token                           Set up a long-lived authentication token
                                        (requires Claude subscription)
  ultrareview [options] [target]        Run a cloud-hosted multi-agent code
                                        review of the current branch (or a PR
                                        number / base branch) and print the
                                        findings
  update|upgrade                        Check for updates and install if
                                        available
```

**PASSED.** This renders the CLI's complete registered command/option table —
substantially more of the argument-parsing and command-registration machinery
than `--version` touches — with exit `0`. The scratch config dir stayed empty
after this run too (`--help` also exits before touching config state).

### `mcp list`

```bash
CLAUDE_CONFIG_DIR="$(mktemp -d)" "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs mcp list
```

Output:

```
No MCP servers configured. Use `claude mcp add` to add a server.
(exit 0)
```

**This is the strongest evidence in this document.** Unlike `--version` and
`--help`, `mcp list` does not just parse-and-exit: it genuinely touches disk.
Immediately after the run, the scratch `CLAUDE_CONFIG_DIR` contained:

```
$ find "$CLAUDE_CONFIG_DIR" | sort
/tmp/tmp.b8UVTry4IP
/tmp/tmp.b8UVTry4IP/.claude.json
/tmp/tmp.b8UVTry4IP/backups
/tmp/tmp.b8UVTry4IP/backups/.claude.json.backup.1787403804503

$ cat "$CLAUDE_CONFIG_DIR/.claude.json"      # machineID/userID redacted below; not secrets, just unique per-run identifiers with no bearing on this check
{
  "firstStartTime": "2026-08-22T13:03:24.398Z",
  "machineID": "<redacted>",
  "opusProMigrationComplete": true,
  "sonnet1m45MigrationComplete": true,
  "seenNotifications": {},
  "hasResetAutoModeOptInForDefaultOffer": true,
  "migrationVersion": 13,
  "userID": "<redacted>"
}
```

**PASSED**, and this is genuinely deeper evidence than parse-and-exit: it
exercises config-file read (none existed, so it initialized one), JSON
serialization, a timestamped backup-file write, and MCP-subsystem dispatch
down to "no servers configured," all under Bun 1.3.14, with no error.

**What remains unexercised even after Step 5b:** no network call, no model
API request, no interactive TUI rendering, no tool execution, no asset
(`.node` native module or bundled JS) load. This document does not claim
those work — only that everything explicitly run above did.

---

## Conclusion for `findings.md` §10

**Corrected framing:** the original conclusion claimed "the APIs this build
of Claude Code's `cli.js` needs are all present in Bun 1.3.14." That
overstates the evidence — only `--version`, `--help`, and `mcp list` were
ever executed, not the full application. The evidence-supported claim is:

**Every Bun API reached on the exercised code paths (`--version`, `--help`,
and `mcp list`, including `mcp list`'s config-file read/write and MCP
subsystem dispatch) is present and working in Bun 1.3.14, for Claude Code
2.1.222.** As of this run, `findings.md` §10's risk has not materialized on
these paths. This is an empirical, version- and code-path-specific answer,
not a permanent guarantee and not a claim that the full interactive
application (model API calls, TUI rendering, tool execution, native asset
loading) works — those remain unverified here. A future Claude Code build
compiled against a newer canary Bun could still regress this, per the same
findings.md §10 reasoning, and even this version could still hit a missing
API on a code path this document didn't reach. No workaround, patch, or edit
to the extracted JavaScript was made or would have been acceptable at any
point; had any rung failed, the failure itself would have been the recorded
finding.

---

## Regression check — full test suite

Command:

```bash
python3 -m pytest tests/ -q
```

Output:

```
......................                                                   [100%]
22 passed in 8.46s
```

**PASSED.** 22/22 (re-run at the end of the fix round below; includes the 4
`integration` tests that require the real ELF/Mach-O binaries present on this
host: `test_real_elf_binary_extracts`, `test_real_elf_transforms_leave_no_bunfs_references`,
`test_real_macho_binary_extracts`, `test_real_macho_transforms_leave_no_bunfs_references`).

---

## Summary table

| Rung | Description | Result |
|---|---|---|
| Step 1 | Install Bun 1.3.14 (direct zip, no PATH/rc mutation) | PASSED — `1.3.14` |
| Step 2 (L1+L2) | Build from real Linux ELF (`/usr/bin/claude`, 2.1.222) | PASSED — 8 modules, 5 rewired, 7 file:// rewrites, 1 IIFE |
| Step 3 (L3) | Bun's own parser (`bun build --no-bundle`, primary) + JSC `Function()` check (secondary), Linux output | PASSED on both — `bun build`: exit 0 in 1.7s; `syntax-check.js`: `SYNTAX OK` |
| Step 3b (L3, darwin) | Build from macOS Mach-O (2.1.239) + both L3 checks | PASSED (extraction: 9 rewired / 8 file:// rewrites; `bun build`: exit 0 in 2.2s; `syntax-check.js`: `SYNTAX OK`). **Actually running the darwin build (L4) is unverifiable on this host** — requires Apple Silicon hardware. |
| Step 4 (L5) | Asset paths rewritten — **static check only** | PASSED as a static check (5 assets, grep count 5). **Runtime asset resolution is unverified** — confirmed no executed command in this document loads an asset. |
| Step 5 (L4) | Real run under Bun 1.3.14, `--version`, Linux output | **PASSED** — `2.1.222 (Claude Code)`, exit 0, reproduced twice. Exercises only the CLI-parsing/early-exit path. |
| Step 5b (L4, deeper) | `--help` and `mcp list` under Bun 1.3.14 | **PASSED** — full option/command registry rendered; `mcp list` reads/writes real config-file state (`.claude.json`, `backups/`) under Bun 1.3.14 with no error. |
| Regression | Full test suite | PASSED — 22/22 |

Safety constraints (`/usr/bin/claude` never executed/written, nothing on
`PATH`, no rc file touched, scratch `CLAUDE_CONFIG_DIR` for every real run):
all held for the duration of this task, confirmed by before/after diffs
pasted in the "Safety checks" section above.

---

# Addendum: 2026-08-22 fleet audit, wave-1 fixes, and a re-run at HEAD

*Appended 2026-08-22, after the body above was already written and pinned to
commit `56e8877`. Nothing above this line was edited except the pin notice at
the top of the file. Where the two disagree, **this addendum is current**.*

## What happened

An eight-reviewer audit fleet was run against the branch — cold-start
reproduction, adversarial input, claim falsification, transform completeness,
code quality, mutation testing, spec conformance, and a devil's advocate whose
brief was to break the headline. Two remediation waves followed:

- **Wave 1 (correctness)** — commits `081b200`, `61957a6`, `c8467aa`,
  `f7c05b9`, `1c510e7`. Tests 31 → 42.
- **Wave 2 (documentation truth)** — this addendum, plus `306a72a` and the doc
  corrections committed alongside it. Tests 42 → 43.

## What the audit falsified

Each of these was a claim this repository made in prose. Each is now corrected
in place, with the correction marked in the file that carried it.

| Claim, as it stood | Measured truth |
|---|---|
| The `.node` addons use the **`base64`** loader | Their raw loader byte is **10 = `napi`**, on both shipped binaries. The repo's loader enum omitted `jsonc = 7`, shifting every id ≥ 7 |
| ClawGod handles only `napi`, so it extracts **zero** native modules | ClawGod's enum *is* Bun's; it labels them `napi` and **extracts them correctly**. What it drops is the `file`-loader assets |
| ClawGod's `fileURLToPath` transform matches nothing on current binaries | It matches **7 sites** — the same 7 this project rewrites. The 0-match regex was *this repo's own* scaffolded port |
| Approach A "needs an external **Zig** Bun" | It needs an external **Bun**. The same artifact runs on **1.4.0**, the Rust build |
| Bun 1.3.14 is "pre-Rust" | True of the *rewrite*; false literally — its `.comment` reads `rustc 1.94.0-nightly` and it links vendored Rust crates |
| The pragma line alone would make Bun panic | Pragma-kept + **not** invoking runs fine on 1.3.14 **and 1.3.13**. The panic needs pragma **plus** manual invocation |
| Claude Code needs Bun ≥ 1.3.14 | 2.1.222 runs on **1.3.13** in the pragma-preserving shape. The floor is this project's transform, not Claude |
| The darwin artifact "has never been executed / needs Apple hardware" | It runs **here, on Linux**, and prints `2.1.239 (Claude Code)`. What needs a Mac is macOS-*specific* behaviour |
| Export `CLAUDE_CODE_EXECPATH` yourself for shell integrations | The CLI **never reads** it (0 occurrences of `process.env.CLAUDE_CODE_EXECPATH`). It *writes* it as `process.execPath` — now bun |
| "The Rust rewrite is experimental and Linux-x64-only" | Stale. `bun-v1.4.0` shipped **2026-08-20**, before this work, targeting all platforms |
| PR oven-sh/bun#30412 merged 2026-05-11 | Merged **2026-05-14T08:09:34Z** |
| 12 `fileURLToPath` **calls** survive | 12 textual hits; **9** are calls, 3 are import lines in embedded script text |
| Windows/PE "would be silently asset-less rather than loudly broken" | `check()` makes exactly that case **fatal** since `59d9a98` |

Plus one whole subject the repository had never mentioned: the **equivalence
gap** (`findings.md` §11). The word `isStandaloneExecutable` appeared in **zero**
files here before this addendum.

## Re-run at HEAD `306a72a`

Same host as the body (Linux x86_64, Debian 12, glibc 2.36).
`/usr/bin/claude` was read and never executed or written.

### Build

```
$ OUT_DIR=<out> BUN_BIN=~/.bun-1.3.14/bun scripts/build.sh /usr/bin/claude
==> native binary: /usr/bin/claude
==> bun: 1.3.14 (/home/claude/.bun-1.3.14/bun)
==> extracting cli.js + assets -> <out>/extract
Size:    276.1 MB
Section: offset=86904832 size=202513494 (193.1 MB)
Payload: 202513486 bytes, trailer OK
Modules: 8 (entry id=0)
  entry   js       21.90 MB -> <out>/.extract.stage.NNNNN/cli.original.js
  native  napi       1430 KB -> <out>/.extract.stage.NNNNN/assets/image-processor.node
  native  napi        481 KB -> <out>/.extract.stage.NNNNN/assets/audio-capture.node
  asset   file        204 KB -> <out>/.extract.stage.NNNNN/assets/chart.umd.min.js
  asset   file        962 KB -> <out>/.extract.stage.NNNNN/assets/hljsBundle.generated.min.js
  asset   file       3235 KB -> <out>/.extract.stage.NNNNN/assets/mermaid.min.js
Extracted: 1 cli.js + 5 assets (2 loader shims left inlined in cli.js)
==> post-processing cli.js for external Bun
note: build-machine path still present: /home/runner/work/claude-cli-internal/claude-cli-internal/node_modules/@ant/computer-use-swift/js
note: build-machine path still present: /home/runner/work/claude-cli-internal/claude-cli-internal/node_modules/@grpc/grpc-js/build/src
note: build-machine path still present: /home/runner/work/claude-cli-internal/claude-cli-internal/src/frame
pragma block stripped  : 1
/$bunfs/ paths rewired : 5
file:// leaks rewritten: 7
IIFE invocations added : 1  (expected 1)
size: 22960130 -> 22959448 bytes
wrote: <out>/.extract.stage.NNNNN/cli.original.cjs
wrote: <out>/.extract.stage.NNNNN/cli.js  (sibling for Claude's MCP self-spawns)
==> staged build swapped into place -> <out>/extract
```

Two differences from the body: the label is now `pragma block stripped` (was
`pragma lines stripped`), and there is a second `wrote:` line for the `cli.js`
sibling that wave 1 added. Byte-identical output to the body's run:

```
$ md5sum <out>/extract/cli.original.cjs
5e3662ee9e2cfd8143c7a6a1bb0662bb  <out>/extract/cli.original.cjs
```

### Run

```
$ DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=$(mktemp -d) <bun-1.3.14> <out>/extract/cli.original.cjs doctor
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

`Running: unknown` and `Search: OK (/usr/bin/rg)` are not cosmetic — they are
the equivalence gap showing itself. See below, and `findings.md` §11.

```
$ … <bun-1.3.14> <out>/extract/cli.original.cjs mcp list
No MCP servers configured. Use `claude mcp add` to add a server.        (exit 0)
$ … <bun-1.4.0>  <out>/extract/cli.original.cjs mcp list
No MCP servers configured. Use `claude mcp add` to add a server.        (exit 0)
$ … <bun-1.3.14> <out>/extract/cli.js --version          # the wave-1 sibling shim
2.1.222 (Claude Code)                                                  (exit 0)
$ <bun-1.3.14> build --no-bundle --target=bun <out>/extract/cli.original.cjs --outfile=/dev/null
  null  29.60 MB  (chunk)                                              (exit 0)
```

### Regression

```
$ python3 -m pytest tests/ -q
...........................................                              [100%]
43 passed in 8.81s

$ python3 -m pytest tests/ -q -m integration
....                                                                     [100%]
4 passed, 39 deselected in 8.24s
```

43, not the body's 22 and not the 31 the docs claimed until wave 1: `+11` from
wave 1 (loader-enum pinning, the genuine-`base64` latent bug, the `cli.js`
shim's Bun-loadability matrix, and the `build.sh` staging suite) and `+1` from
wave 2's split of the staging-leak test.

### Safety

```
$ md5sum /usr/bin/claude            # before and after every command above
94e673a283dd91d0456080cc05a09083  /usr/bin/claude
```

Unchanged. Never executed. Nothing installed on `PATH`; no file named `claude`
created; no shell profile touched.

## New measurements taken for wave 2

Each of these backs a specific ✅ elsewhere in the docs.

### Loader bytes, read with a standalone parser (not this repo's tools)

```
/usr/bin/claude              modules=8   entry=0
  idx=0  byte=1   /$bunfs/root/src/entrypoints/cli.js        first4=b'// @'
  idx=3  byte=10  /$bunfs/root/image-processor.node          first4=b'\x7fELF'
  idx=4  byte=10  /$bunfs/root/audio-capture.node            first4=b'\x7fELF'
  idx=5..7 byte=5 chart.umd.min.js / hljsBundle… / mermaid.min.js
/tmp/ccmac/package/claude    modules=15  entry=0
  idx=6,7,8,12,14  byte=10   *.node   first4=b'\xcf\xfa\xed\xfe' or b'\xca\xfe\xba\xbe'
```

Bun 1.3.14's `src/bundler/options.zig` (fetched at tag `bun-v1.3.14`):
`jsx=0 js=1 ts=2 tsx=3 css=4 file=5 json=6 jsonc=7 toml=8 wasm=9 napi=10
base64=11 dataurl=12 text=13 bunsh=14 sqlite=15 sqlite_embedded=16 html=17
yaml=18 json5=19 md=20`. **Byte 10 is `napi`.**

ClawGod at commit `4401fdb`, `install.sh`: `const LOADERS = { …, 7:'jsonc',
…, 10:'napi', 11:'base64', … }` — Bun's table. Its `napi` branch writes the
addons out. Its `.node`-only rewrite matches 2 of the 5 `/$bunfs/` literals
here, leaving `chart.umd.min.js`, `hljsBundle.generated.min.js` and
`mermaid.min.js` unrewritten and unextracted. Its `fileURLToPath` regex matches
**7**.

### Bun 1.3.14 vs 1.4.0

```
$ readelf -p .comment ~/.bun-1.3.14/bun
  [    47]  rustc version 1.94.0-nightly (c61a3a44d 2025-12-09)
$ strings ~/.bun-1.3.14/bun | grep -c '\.zig'      → 7
$ strings ~/.bun-1.4.0/bun  | grep -c '\.zig'      → 0
$ strings ~/.bun-1.3.14/bun | grep -i lolhtml | head -1
  /var/lib/buildkite-agent/build/vendor/lolhtml/src/memory/arena.rs
$ ~/.bun-1.3.14/bun -e 'console.log(Bun.isStandaloneExecutable)'   → undefined
$ ~/.bun-1.4.0/bun  -e 'console.log(Bun.isStandaloneExecutable)'   → false
```

### The pragma/IIFE 2×2, run to completion

Bun 1.3.13 was unpacked into a scratch directory for this (not on `PATH`).

```
                                              1.3.13   1.3.14
pragma stripped + IIFE invoked (as shipped)   panic    2.1.222 (Claude Code)
pragma kept     + IIFE not invoked            2.1.222  2.1.222 (Claude Code)
pragma kept     + IIFE invoked                panic    panic
pragma stripped + IIFE not invoked            panic    exit 0, no output
```

`panic` = `TypeError: Expected CommonJS module to have a function wrapper.`
On 1.3.13 the pragma-preserving build also ran `--help`, `mcp list` and
`doctor`, all exit 0.

### Lazy-module initialisation per command

The bundle's two lazy-module helpers were instrumented (`re` = CJS wrapper,
1644 instances; `E` = ESM lazy init, 5104; 6748 total) and the count of
*initialised* modules printed at exit:

```
--version  ->  LAZY re=0/1644    E=0/5104     TOTAL=0/6748
--help     ->  LAZY re=394/1644  E=2331/5104  TOTAL=2725/6748
doctor     ->  LAZY re=407/1644  E=2350/5104  TOTAL=2757/6748
mcp list   ->  LAZY re=407/1644  E=2354/5104  TOTAL=2761/6748
```

### The darwin artifact under Linux Bun

```
$ OUT_DIR=<macout> scripts/build.sh /tmp/ccmac/package/claude
Modules: 15 (entry id=0)
Extracted: 1 cli.js + 9 assets (5 loader shims left inlined in cli.js)
/$bunfs/ paths rewired : 9    file:// leaks rewritten: 8    IIFE: 1
size: 28244743 -> 28244063 bytes

$ DISABLE_AUTOUPDATER=1 CLAUDE_CONFIG_DIR=$(mktemp -d) \
    ~/.bun-1.3.14/bun <macout>/extract/cli.original.cjs --version
2.1.239 (Claude Code)                                                  (exit 0)

$ ~/.bun-1.3.14/bun -e 'require("<macout>/extract/assets/image-processor.node")'
Error [ERR_DLOPEN_FAILED]: … invalid ELF header

$ od -N4 -tx1 <macout>/extract/assets/*.node
audio-capture.node        cf fa ed fe      computer-use-input.node   ca fe ba be
computer-use-swift.node   ca fe ba be      image-processor.node      cf fa ed fe
url-handler.node          cf fa ed fe
```

### The equivalence gap, measured end to end

Driven by a **loopback-only** mock of the Messages API — `127.0.0.1`, a
throwaway `HOME` and `CLAUDE_CONFIG_DIR`, a fake key. No traffic left the host;
no real account was touched.

The agentic loop itself works. `Bash`, through the full SSE + multi-turn path:

```
assistant tool_use  {"name":"Bash","input":{"command":"echo HELLO-FROM-SUBPROCESS; uname -s; echo $$"}}
TOOL_RESULT is_error=False  "HELLO-FROM-SUBPROCESS\nLinux\n111033"
assistant text      "MOCK-DONE"
result num_turns=2 is_error=False
```

`Read`, on a 3000×3000 PNG — the same artifact, twice:

```
as shipped:
  TOOL_RESULT is_error=True
    "Unable to resize image — dimensions exceed the 2000x2000px limit and
     image processing failed. Please resize the image to reduce its pixel
     dimensions."

with Object.defineProperty(Bun,'isStandaloneExecutable',{value:true}):
  TOOL_RESULT IMAGE  media=image/jpeg  decoded_bytes=469774  magic=ff d8 ff e0
```

The addon is not the problem — it works when called directly:

```
$ ~/.bun-1.3.14/bun -e '…require("<out>/extract/assets/image-processor.node")…'
exports: [ "processImage", "hasClipboardImage", "readClipboardImage", "ImageProcessor" ]
metadata: {"width":3000,"height":3000,"format":"png"}
resized JPEG bytes: 2534466  magic: ff d8 ff e0
```

`doctor`, same A/B:

```
as shipped                        forced isStandaloneExecutable=true
  Running: unknown (2.1.222)        Running: native (2.1.222)
  Search: OK (/usr/bin/rg)          Search: OK (bundled)
```

And why the global flip is **not** the fix — `Grep` for a string that exists:

```
as shipped        TOOL_RESULT  "hay/a.txt:1:NEEDLE-12345"
flag forced true  TOOL_RESULT  "No matches found"
```

A silently wrong answer, not an error: with the flag set, "embedded ripgrep"
means re-exec `process.execPath` — which is bun — with argv0 `rg`.

The Ink TUI was also rendered under a pty on 1.3.14 (welcome screen, theme
picker, syntax-highlighted diff preview).

### Generalisation to a newer Claude

`@anthropic-ai/claude-code-linux-x64@2.1.240` (18 releases after 2.1.222),
downloaded from npm, run through an **unmodified** `scripts/build.sh`:

```
Modules: 11 (entry id=0)
Extracted: 1 cli.js + 7 assets (3 loader shims left inlined in cli.js)
  native  napi   1430 KB  image-processor.node
  native  napi   1048 KB  clipboard-napi.node          <- new in 2.1.240
  native  napi    481 KB  audio-capture.node
  asset   file    204 KB  chart.umd.min.js
  asset   file    962 KB  hljsBundle.generated.min.js
  asset   file   3235 KB  mermaid.min.js
  asset   file   2177 KB  payload.template.html.asset  <- new on linux
/$bunfs/ paths rewired : 7    file:// leaks rewritten: 7    IIFE: 1

$ … <bun-1.3.14> <out240>/extract/cli.original.cjs --version   → 2.1.240 (Claude Code)  rc=0
$ … <bun-1.3.14> <out240>/extract/cli.original.cjs mcp list    → No MCP servers configured…  rc=0
$ … <bun-1.4.0>  <out240>/extract/cli.original.cjs --version   → 2.1.240 (Claude Code)  rc=0
$ … <bun-1.4.0>  <out240>/extract/cli.original.cjs mcp list    → No MCP servers configured…  rc=0
```

## What is still not verified

- **No real model traffic.** Everything agentic above went to a loopback mock.
- **macOS-specific behaviour.** The darwin JS boots here, but its addons are
  Mach-O and `process.platform` is `linux`.
- **Windows.** Unimplemented by choice (`status.md` § Windows/PE).
- **20 of the 21 `CE()` branches** were read from source, not exercised. Only
  the image path has an A/B measurement behind it.
- **The `find`/`grep` shell-function shadowing** was reconstructed from the
  shipped source, not observed live.
