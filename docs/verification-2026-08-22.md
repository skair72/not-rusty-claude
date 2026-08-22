# End-to-end verification run — 2026-08-22

This document records the first real execution of the extraction + post-process
pipeline against Bun's last Zig-era release, answering the open question in
`docs/findings.md` §10: does a current Claude Code `cli.js`, built by Anthropic
against Bun's canary channel, actually run on Bun 1.3.14 (the newest Bun that
still predates the Zig→Rust rewrite)?

**Result: yes, for Claude Code 2.1.222 on Linux. The extracted, post-processed
`cli.original.cjs` runs under vanilla external Bun 1.3.14 and prints the
correct version string.** Every rung below was executed on this host; nothing
was skipped, patched, or worked around.

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
| Repo / branch | `not-rusty-claude`, branch `claude/implement`, at commit `77a6584` (HEAD before this doc's commit) |

Safety constraints observed throughout: `/usr/bin/claude` was never executed
and never written to. No file named `claude` was created. Nothing was added to
`PATH`. `~/.bashrc`, `~/.bash_profile`, `~/.zshrc`, and `~/.profile` were
diffed before and after the run and are byte-identical (`~/.bashrc` and
`~/.bash_profile` do not exist on this host, before or after; `~/.zshrc` and
`~/.profile` keep the same md5 `d50dec2a334463a79eac95753a5e67a2` they had at
the start of the session). The L4 real run used a scratch
`CLAUDE_CONFIG_DIR` (`mktemp -d`), which stayed empty after the run — the
live session's `~/.claude` was never touched.

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

Output:

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

---

## Step 3 — L3: syntactic validity of the CJS wrapper (Linux/x64 output)

`scripts/syntax-check.js` was created exactly as specified in the brief
(compile-only check via `new Function()`, run under Bun because Node 22
rejects the `using`/`await using` declarations in the real source).

Command:

```bash
"$HOME/.bun-1.3.14/bun" scripts/syntax-check.js build/extract/cli.original.cjs
```

Output:

```
SYNTAX OK
```

Exit code: `0`.

**PASSED.**

---

## Step 3b — L3 on the darwin output (the macOS path's real check on this host)

Command:

```bash
OUT_DIR=/tmp/macbuild scripts/build.sh /tmp/ccmac/package/claude
"$HOME/.bun-1.3.14/bun" scripts/syntax-check.js /tmp/macbuild/extract/cli.original.cjs
```

Output:

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
SYNTAX OK
```

**PASSED** (extraction/post-process and syntax check). `/$bunfs/ paths
rewired : 9` and `file:// leaks rewritten: 8` match the expected values
exactly, and `SYNTAX OK` confirms the darwin `cli.original.cjs` parses cleanly
under Bun's parser.

`bun not found` in this step's output is expected: the brief's Step 3b command
intentionally omits `BUN_BIN`, so `build.sh` falls back to `command -v bun`,
which is empty because Bun 1.3.14 was deliberately not put on `PATH` (Step 1's
constraint). This is advisory only — extraction and post-processing do not
require Bun; only the later syntax check does, and it was invoked with the
full path to `~/.bun-1.3.14/bun` explicitly.

**Not verifiable on this host / left as an explicit gap:** actually *running*
(L4) the darwin build requires macOS on Apple Silicon (Mach-O binaries are not
executable on Linux, and there is no realistic emulation path for a Bun-hosted
GUI-less CLI of this size). Only the syntax rung (L3) could be exercised for
the macOS artifact here. That is the strongest check available on this host,
per the brief's Step 3b framing, but it does not prove the darwin build boots.

---

## Step 4 — L5: rewritten asset paths resolve (Linux/x64 output)

Commands:

```bash
ls -la build/extract/assets/
grep -o "require('path').join(__dirname,'assets'" build/extract/cli.original.cjs | wc -l
```

Output:

```
total 6328
drwxr-xr-x 2 claude claude    4096 Aug 22 12:44 .
drwxr-xr-x 3 claude claude    4096 Aug 22 12:44 ..
-rw-r--r-- 1 claude claude  492184 Aug 22 12:44 audio-capture.node
-rw-r--r-- 1 claude claude  208522 Aug 22 12:44 chart.umd.min.js
-rw-r--r-- 1 claude claude  985483 Aug 22 12:44 hljsBundle.generated.min.js
-rw-r--r-- 1 claude claude 1464760 Aug 22 12:44 image-processor.node
-rw-r--r-- 1 claude claude 3312967 Aug 22 12:44 mermaid.min.js
5
```

**PASSED.** 5 assets listed; `grep -o | wc -l` count is `5`, matching
expectation exactly (as the brief warns, `grep -c` would under-count at `4`
here because minified code places several rewrites on one line — not used).

---

## Step 5 — L4: the actual run under Zig-era Bun

This is the rung that answers `findings.md` §10.

Command:

```bash
CLAUDE_CONFIG_DIR="$(mktemp -d)" \
  "$HOME/.bun-1.3.14/bun" build/extract/cli.original.cjs --version
```

Output (stdout):

```
2.1.222 (Claude Code)
```

stderr: empty. Exit code: `0`.

**PASSED.** Claude Code 2.1.222's extracted, post-processed `cli.original.cjs`
runs correctly under vanilla, unmodified Bun 1.3.14 — the last Zig-era
release — and prints exactly the expected version string. This was re-run a
second time with a fresh scratch `CLAUDE_CONFIG_DIR` to confirm determinism,
with identical output (`2.1.222 (Claude Code)`, exit `0`, empty stderr), and
the scratch config directory was confirmed empty afterward (no state was
written to it, so a fortiori nothing was written to the real `~/.claude`).

**Conclusion for `findings.md` §10, as of Claude Code 2.1.222 / Bun 1.3.14
(2026-08-22): the risk has NOT materialized.** The APIs this build of Claude
Code's `cli.js` needs are all present in Bun 1.3.14. This is an empirical,
version-specific answer, not a permanent guarantee — a future Claude Code
build compiled against a newer canary Bun could still regress this, per the
same findings.md §10 reasoning. No workaround, patch, or edit to the extracted
JavaScript was made or would have been acceptable; had this rung failed, the
failure itself would have been the recorded finding.

---

## Regression check — full test suite

Command:

```bash
python3 -m pytest -q
```

Output:

```
......................                                                   [100%]
22 passed in 7.96s
```

**PASSED.** 22/22, unchanged from before this task (includes the 4
`integration` tests that require the real ELF/Mach-O binaries present on this
host: `test_real_elf_binary_extracts`, `test_real_elf_transforms_leave_no_bunfs_references`,
`test_real_macho_binary_extracts`, `test_real_macho_transforms_leave_no_bunfs_references`).

---

## Summary table

| Rung | Description | Result |
|---|---|---|
| Step 1 | Install Bun 1.3.14 (direct zip, no PATH/rc mutation) | PASSED — `1.3.14` |
| Step 2 (L1+L2) | Build from real Linux ELF (`/usr/bin/claude`, 2.1.222) | PASSED — 8 modules, 5 rewired, 7 file:// rewrites, 1 IIFE |
| Step 3 (L3) | Syntax check, Linux output | PASSED — `SYNTAX OK` |
| Step 3b (L3, darwin) | Build from macOS Mach-O (2.1.239) + syntax check | PASSED (extraction: 9 rewired / 8 file:// rewrites; syntax: `SYNTAX OK`). **Actually running the darwin build (L4) is unverifiable on this host** — requires Apple Silicon hardware. |
| Step 4 (L5) | Rewritten asset paths resolve, Linux output | PASSED — 5 assets, grep count 5 |
| Step 5 (L4) | Real run under Bun 1.3.14, Linux output | **PASSED** — `2.1.222 (Claude Code)`, exit 0 |
| Regression | Full test suite | PASSED — 22/22 |

Safety constraints (`/usr/bin/claude` never executed/written, nothing on
`PATH`, no rc file touched, scratch `CLAUDE_CONFIG_DIR` for the real run):
all held for the duration of this task, confirmed by before/after diffs.
