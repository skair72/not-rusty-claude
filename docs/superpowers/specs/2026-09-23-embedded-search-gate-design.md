# The embedded-search gate: Bash `grep`/`find` run bun

**Date:** 2026-09-23
**Status:** design of record for branch `claude/fix-grep`.
The user-facing record is [findings.md](../../findings.md) §10 (*Embedded
search: `grep` and `find` ran bun*).

Reported first-hand: started as `bun build/extract/cli.js`, Claude cannot run a
plain `grep`. Every Bash-tool `grep` prints Bun's help text and fails. Findings
§6 had predicted this from source on 2026-08-22 ("read out of the shipped
source, **not observed live**") and recorded no workaround.

---

### The problem, restated (measured 2026-09-23, this host)

Claude's Bash tool sources a per-session *shell snapshot*. In a native build
that snapshot shadows `find` and `grep` with functions that re-exec the Claude
binary under another name, because the native binary embeds **bfs** and
**ugrep** and dispatches on `argv[0]`:

```bash
function grep {
  ...
  local _cc_bin="${CLAUDE_CODE_EXECPATH:-}"
  [[ -x $_cc_bin ]] || _cc_bin=<~/.local/bin/claude>
  if [[ ! -x $_cc_bin ]]; then command grep ${1+"$@"}; return; fi
  ...
  (exec -a ugrep "$_cc_bin" -G --ignore-files --hidden -I --exclude-dir=.git ... ${1+"$@"})
}
```

`CLAUDE_CODE_EXECPATH` is `process.execPath`, written unconditionally into every
spawned shell (findings §6). Here that is **bun**, which is executable, has no
ugrep or bfs mode, and answers `bun -G ...` with `error: Invalid Argument '-G'`
and its help text.

Driven through the loopback mock, a Bash `grep -n needle somefile.txt` returns
Bun's 38-line help text as the tool result, with **`is_error=false`**. Exit 1
is how `grep` says "no matches", so Claude classes it as no error. The
interactive TUI draws `Searched for 1 pattern` as if it had worked. `find` fails
the same way on `-S`. Native `/usr/bin/claude` answers `2:needle here` in the
same setup. Both the code-split 2.1.280 build and the legacy 2.1.231 build
reproduce it.

**Why the harness never saw it.** Its `bash` case runs `echo …; uname -s`, and
its `grep` and `glob` cases pass `--allowedTools "Grep,Bash,Read"`. That flag
switches the shadowing off, for the reason given next.

### The root cause: a build-time constant, not a runtime check

The snapshot generator is guarded by one function, the **embedded-search gate**:

```js
function zb(){if(!Me("true"))return!1;if($Rr())return!1;return a.CLAUDE_CODE_ENTRYPOINT!=="local-agent"}   // 2.1.280, chunk-1xqpf2j8.js
function HP(){if(!fn("true"))return!1;if(CJi())return!1;return Q.CLAUDE_CODE_ENTRYPOINT!=="local-agent"}  // 2.1.231, cli.original.cjs
```

- `Me` / `fn` is `isEnvTruthy`, and `"true"` is `EMBEDDED_SEARCH_TOOLS`, inlined
  at build time. The runtime never reads the variable: setting
  `EMBEDDED_SEARCH_TOOLS=0` changes nothing (measured).
- `$Rr` / `CJi` is `searchToolsOptIn`, the opt-in described below.
- The ripgrep embed is gated at runtime on `Bun.isStandaloneExecutable`, so
  outside a standalone it falls back to the system `rg` (findings §10). The
  bfs/ugrep embed has no such check: it is `true` under any runtime.

The gate is not only the snapshot's. It has **16 call sites** in each build: 11
in its own chunk plus 5 in four importing chunks on 2.1.280, and 16 in the one
file on 2.1.231. While it is true:

- `$ae()` removes the **Glob** and **Grep** tools.
- The snapshot shadows `find`/`grep` with the embedded binaries.
- About a dozen prompt and tool-list texts steer the model to use `grep`/`find`
  through Bash: the Explore and Plan agents, the Bash tool's guidance, "Using
  your tools", the claude-code-guide agent's tools, the Agent tool's "When not
  to use", and plan mode.

So under the artifact the model is sent down the one search path that cannot
work.

### Claude's own switch, and what it proves

`searchToolsOptIn` is set in exactly one place,
`initializeToolPermissionContext`:

```js
J=(n)=>D.includes(n)||E.some((d)=>Jn(d).toolName===n);FRr([so,Jr].some(J))   // so="Glob", Jr="Grep"
```

It is true exactly when a command-line `--allowedTools` rule or a `--tools`
entry names Glob or Grep. The rule's content does not matter:
`Glob(/nonexistent/**)` counts. It is the gate's only reader. Measured on the
unmodified artifact, `--allowedTools=Grep` gives 22 tools with Glob and Grep, a
real `grep` (`grep is /usr/bin/grep`), and a Grep tool that answers through the
system `rg`.

That makes the opt-in the **native twin** of the fix below. Native, started with
an inert opt-in (`--allowedTools=Glob(/nonexistent-nrc/**)`), and the fixed
artifact, started with no flags, were measured equal on this host by the design
panel:

- request bodies, including the tool results, for a Bash `grep -rn`/`find` turn,
  in a plain directory and in a git repo;
- the Glob turn;
- all three bodies of an interactive REPL turn, and its screen apart from the
  random spinner verb the harness already masks.

The Grep-tool turn differed only in hit order (system rg 13.0.0 against the
embedded rg), which is the ripgrep gap findings §10 already records.

### The mechanism: the gate's declaration, rewritten in source

`postprocess.py` rewrites the one declaration and leaves its 16 call sites
alone. The `isEnvTruthy("true")` inside it becomes the literal `false`:

```js
function zb(){if(!false)return!1;if($Rr())return!1;return a.CLAUDE_CODE_ENTRYPOINT!=="local-agent"}
```

The meaning is "this build has no embedded search tools", and that is true of a
bun that carries neither ugrep nor bfs. `false` is chosen, like the image shim's
`true`, because it is boring and greppable: `grep -c 'if(!false)return!1'`
tells you whether an artifact is patched.

The declaration is found by its **whole shape**, never by its minified name and
never by one of its halves:

```
SEARCH_GATE_DEF = function\s+([\w$]+)\s*\(\s*\)\s*\{\s*if\s*\(\s*!\s*([\w$]+\s*\(\s*"true"\s*\))\s*\)\s*return\s*!1\s*;
                  \s*if\s*\(\s*[\w$]+\s*\(\s*\)\s*\)\s*return\s*!1\s*;
                  \s*return\s+[\w$.]+\.CLAUDE_CODE_ENTRYPOINT\s*!==\s*"local-agent"\s*\}
```

Measured: 1 match in the whole 2.1.280 tree, 1 in the 2.1.231 `cli.original.cjs`,
and 1 in each native binary. Neither half is unique on its own:

- `Me("true")` occurs **7** times in the 2.1.280 tree. The other six are other
  inlined flags, such as the pure-JS `.md` walker, ingress persistence, the
  installer and resume.
- `CLAUDE_CODE_ENTRYPOINT!=="local-agent"` occurs **4** times.

`[\w$.]+` before `.CLAUDE_CODE_ENTRYPOINT` accepts both the alias both builds
use (`a.` / `Q.`) and a literal `process.env.`.

**Tied to the bug it fixes.** The rewrite is licensed only because this gate is
the one that guards the shadowing. So the generator is located too:

```
SHADOW_GEN = function\s+([\w$]+)\s*\(\s*\)\s*\{\s*if\s*\(\s*!\s*([\w$]+)\s*\(\s*\)\s*\)\s*return\s+null\s*;
             \s*return\s*\[\s*"unalias find 2>/dev/null \|\| true"\s*,\s*"unalias grep 2>/dev/null \|\| true"
```

Its guard's callee must be the declared gate's name. Measured: `gAn` guarded by
`zb` in the same chunk on 2.1.280, and `ytb` guarded by `HP` on 2.1.231. The
rewrite applies identically in both of the legacy path's `image_shim` modes, so
the image shim's single-edit test still isolates the image gate.

### The safety property, enforced

"Shadowing present" is proven by either **marker**: the literal
`unalias grep 2>/dev/null || true`, or the snapshot comment
`Shadow find/grep with embedded bfs/ugrep`. They are measured once and twice
per build. The strings of S_e, the generic shadow-function builder (`exec -a`,
`_cc_bin`, `ARGV0=`), are **not** markers: the ripgrep emitter uses S_e too, so
they survive a correct build.

The rules below apply per file on the legacy path and across the whole tree on
the code-split path. D is the number of declarations matched, G the number of
generators matched, M the number of marker hits.

| state | outcome |
|---|---|
| D = 1, and G = 1 with its guard calling the declared gate in the same module | **rewrite**; the summary says `embedded search off    : 1` |
| D = 1, G = 0, M = 0 | **rewrite**: the gate still steers tools and prompts, and flipping it is still native's opt-in configuration |
| D = 0, M = 0 | not applicable, not fatal: `0  (not applicable: …)` |
| D = 0, M > 0 | **fatal**: the declaration drifted while the shadowing is still in the build, and shipping it runs bun as ugrep/bfs |
| D > 1, or G > 1 | **fatal**: ambiguous, refusing to guess |
| D = 1, M > 0, and G = 0, or the guard names another function, or it is in another module | **fatal**: cannot prove the flipped gate is the one guarding the shadowing |
| after the rewrite: D ≠ 0, or the rewritten form ≠ 1 | **fatal**: bookkeeping; the rewrite spread or missed |

Why fatal, when the image shim's refusals are warnings: a missing image shim
degrades to an artifact this repo shipped for weeks, and it errors visibly. A
missing gate rewrite ships the bug. The model's primary search path then returns
Bun's help text marked as success. The image-shim design's rule decides it: *a
wrong answer is worse than a missing feature*. A build that cannot prove the
rewrite writes nothing and says which rule failed.

On the legacy path this is `check()`'s **seventh** fatal condition. The docs that
say "six" say "seven".

### Why not the snapshot-only rewrite

The obvious narrower fix rewrites only the generator's guard
(`if(!zb())return null;` → `if(true)return null;`). It keeps every request body
equal to native's default, because the tool list and the prompts do not move,
and the existing harness stays green untouched. It was the first choice, and a
three-agent design panel (two judges, one refuter) measured it out:

- **It matches no native configuration.** It pairs native's default prompts,
  which say "use `grep` via Bash", with native's opt-in `grep`, which is the
  system one. No Anthropic build does that.
  - In a git fixture, native's `grep -rln` via embedded ugrep
    (`--ignore-files --hidden -I`) found `hay/a.txt` and `.hidden/y.txt`.
  - The snapshot-only build found those two plus `./ignored/x.txt` (gitignored)
    and `./bin.dat` (binary), all with `./` prefixes.
- **It degrades the model's primary search path.** On a repo with a gitignored
  `node_modules/` and `dist/`, `grep -rn readFileSync .`:
  - native, and this design's Grep tool: 4 lines, 247 characters;
  - the snapshot-only build: 151 KB, over the Bash tool's inline limit, saved to
    a file whose 2 KB preview held none of the 4 real hits.
- **The user's own aliases leak through.** Native `unalias`es `grep` and
  `find`. Here `alias grep='grep --color=always'` from `~/.bashrc` put ANSI
  escapes into the tool result the model reads.
- **Its difference is invisible to every existing check**, including the naive
  `hay/a.txt` regression case. The harness would stay green while the model's
  primary search behaved differently.

### Also rejected

| route | why not |
|---|---|
| `CLAUDE_CODE_ENTRYPOINT=local-agent` (the gate's third clause) | It turns on Desktop "Cowork" mode. Measured, it strips `ANTHROPIC_API_KEY` from the Bash environment and changes the Bash tool's description. It also skips remote managed settings, drops built-in plugins and skills, and more (41 sites). |
| leave `CLAUDE_CODE_EXECPATH` unset or non-executable | The functions then fall back to `~/.local/bin/claude`. On the reporting Mac that is the **native** binary, silently run as ugrep/bfs (measured with a symlink). |
| an entry module that prepends `--allowedTools=Glob(...)` to argv | This works; measured, `mcp list` and `doctor` still exit 0. But it is argv-dependent, appending it breaks subcommands, and the rule shows up in permission views. Setting the opt-in from the entry does not work: `initializeToolPermissionContext` overwrites it. |
| keep the shadowing, pointed at a `ugrep`/`bfs` on `PATH` | Neither tool is a prerequisite, and both are absent here, so the working branch cannot be measured. The fallback is the snapshot-only build. It also keeps the failure class that hid this bug: exit 1 is "no matches", so a rejected flag passes as success. |
| ClawGod's `restore-search-tools` patch (findings §7) | Same gate and same idea, but its regex needs a literal `process.env.`. Both native extracts use an alias, so it matches **0** times on 2.1.231 and on 2.1.280, and its optional sentinel reports success anyway. |

### Opt-out

None. The image shim's `NRC_NO_IMAGE_SHIM` exists to rebuild the "as shipped"
half of an A/B from the same tree. Here the as-shipped half is known to be
broken, and the harness pins the native side of the comparison directly (below).
There is no native-default configuration to return to: that configuration needs
the embedded ugrep and bfs, which are exactly what the artifact lacks. The
runtime direction is Claude's own: nothing reaches the gate once it is `false`.

### Verification (definition of done)

1. **Hermetic tests** (`tests/test_search_gate.py`, on cut-down verbatim
   fixtures of both builds' declaration and generator):
   - The rewrite lands once and yields exactly the text above.
   - Every call site, and every other `isEnvTruthy("true")`, is byte-for-byte
     untouched.
   - Each fatal row of the table refuses and writes nothing.
   - The not-applicable row passes.
   - Both `image_shim` modes apply it.
   - The rewritten gate, evaluated under Bun, returns `false`, and the generator
     then returns `null`.
2. **Integration tests** on the real 2.1.231 and 2.1.280 extracts: exactly one
   rewrite in the module holding the generator, and the summary line in the log.
3. **The harness**, restated rather than exempted. The native side of every
   agentic and TUI session runs under Claude's own inert opt-in
   `--allowedTools=Glob(/nonexistent-nrc-search-optin/**)`, so equality stays
   zero-tolerance: bodies, tool results and screens.
   - **`agentic:bash-search`**: a Bash `grep -rn`/`find` turn with no
     `--allowedTools`, in a fixture holding a gitignored file, a binary file and
     a hidden directory. The artifact equals native under the opt-in, and its
     tool result never contains `Invalid Argument` or Bun's banner.
   - **`agentic:search-optin`** pins what the opt-in changes on native itself.
     Native's default first body equals its opt-in first body once the Glob and
     Grep tool entries are removed, and those two are the only tools that
     differ. Native's default Bash `grep` still skips the gitignored file, which
     the opt-in does not. If Anthropic changes either configuration, this goes
     red and the claim above gets re-measured.
   - `build` requires `embedded search off    : 1`.
4. **Docs**, each reconciled with what the harness measured:
   - findings §6 changes from "not observed live" to observed and fixed;
   - findings §10 gains the consequence and what shipped;
   - findings §7 gets the ClawGod measurement;
   - the runbook's "Shell integrations" section and troubleshooting table;
   - status.md and the README's gap text;
   - build.sh's gap list, which gains `search tools` when the rewrite applied.
   `rg` stays a stated prerequisite. Glob and Grep now depend on it, and fail
   visibly without it ("ripgrep not found on PATH").

### Explicitly out of scope

| item | why |
|---|---|
| `CLAUDE_CODE_EXECPATH` itself | Nothing reads it once the shadowing is off. It still points at bun, which is true. |
| the seccomp sandbox's `apply-seccomp` argv0 | It is gated on `Bun.isStandaloneExecutable` at runtime (findings §10), so it is already off. |
| zsh and Windows shells | They are the same generator and the same gate, so the rewrite covers them by construction. No zsh or Windows measurement exists here. |
| BSD `grep`/`find` on macOS | Under this design the model reaches them only when it chooses Bash over Grep/Glob. That is native's behaviour under the same opt-in. It has not been measured on a Mac. |
