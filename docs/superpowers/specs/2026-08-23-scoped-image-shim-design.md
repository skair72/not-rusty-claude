# Scoped `isStandaloneExecutable` shim + `patch_claude.py` test coverage

**Date:** 2026-08-23
**Status:** design of record for branch `claude/image-shim-and-patch-tests`.
Both parts are **implemented**, and this document has been reconciled with what
actually shipped: where the implementation departed from the plan, the plan's
wording is kept and the departure is marked **Changed during implementation**
(or **Changed during re-review**, for what a second adversarial pass falsified
after the first fix landed), because in every case the reason is a review that
falsified the original claim.
The user-facing record is [findings.md](../../findings.md) §11 (*What shipped:
the scoped shim*).

Two independent pieces of work, both carried forward from the fleet review that
closed PR #1:

1. Close the *image-processing* half of the equivalence gap
   ([findings.md](../../findings.md) §11) with a shim scoped to one call site.
2. Give `tools/patch_claude.py` — the one tool in this repo that writes to a
   signed binary — a real test suite. Checked out and counted 2026-08-23: at
   `7ea5562~1` the tool was **261** lines with **no** `tests/test_patch_claude.py`
   at all; as shipped it is **507** lines with **94** tests (re-counted
   2026-08-24, after the review fleet's fixes landed; the figures this
   paragraph carried before — 330 lines, 58 tests — were correct at `9c98027`
   and went stale in the very next commit).

---

## Part 1 — the scoped shim

### The problem, restated

Claude Code asks one question to decide whether it is a Bun standalone:

```js
function CE(){return Bun.isStandaloneExecutable===!0}      // linux-x64 2.1.222
function AE(){return typeof Bun<"u"&&Bun.isStandaloneExecutable===!0}  // darwin-arm64 2.1.239
```

Outside a standalone that property is `undefined`, so the answer is **false**
everywhere. Most consequences are correct; one is not. Native image processing
is gated behind it and is therefore *unreachable by construction*, so the
**Read** tool cannot resize a large image and returns an error instead.

### Why not just set the flag

Measured, and recorded in findings §11: defining
`Bun.isStandaloneExecutable = true` globally makes the image path work **and
silently breaks `Grep`**. With the flag set, "embedded ripgrep" means
"re-exec `process.execPath` with argv0 `rg`", and `process.execPath` is `bun`.
A search for a string that exists returns `No matches found` — not an error, a
wrong answer. Any acceptable shim must therefore be *scoped to the call site*,
not applied to the flag.

### The mechanism: one call site, rewritten in source

`postprocess.py` already rewrites the entry module as text. The shim is one
more rewrite in the same pass, and it is applied to exactly one occurrence.

**Step 1 — learn the gate's minified name.** It changes between builds, so it
is captured rather than hard-coded:

```
STANDALONE_DEF = function\s+([\w$]+)\s*\(\s*\)\s*\{\s*return\s+[^{}]*?
                 Bun\.isStandaloneExecutable\s*===\s*!0\s*\}
```

The `[^{}]*?` is what makes the same pattern match both shapes above: it
absorbs the `typeof Bun<"u"&&` guard that 2.1.239 added, while `[^{}]` keeps
the match inside a single function body. Measured: one match in each of the two
extracted binaries, capturing `CE` and `AE` respectively. (`[^{}]` is not
decoration. With `.*?` there instead, the pattern starts at any earlier
`function q(){return 1}`, runs through its `}` into the real gate, and captures
`q` — pinned hermetically by
`test_the_declaration_pattern_stays_inside_one_function_body`.)

**Changed during re-review.** This step originally took whatever
`STANDALONE_DEF.search()` returned — the **first** declaration in file order.
That is the same "nearest wins" rule that broke step 2, moved from the site to
the *name*, and it fails the same way. Given an entry module declaring two
differently named isStandaloneExecutable gates — `ZZ` before the real `CE` —
with the image branch correctly on `if(CE())try{` and an `if(ZZ())try{` in the
window, the shim binds to `ZZ`, rewrites ZZ's branch, reports `applied = 1`
with ZZ's call sites going 1 → 0, balances every count and returns a clean
`check()` — while the branch it was sent to fix is untouched and image
processing is still off. What ships now refuses when the file declares more
than one *distinct* gate name (`tools/postprocess.py`'s `_gate_name`), warning
and leaving the artifact unshimmed, exactly like the duplicated-anchor refusal
in step 2. Two declarations of the *same* name stay a shimmable file: every
call site still means one gate, and the shape rule picks among sites. Measured
on this host 2026-08-23: exactly one match in each real entry module —
22,960,130 bytes / `CE` (linux-x64 2.1.222) and 28,244,743 bytes / `AE`
(darwin-arm64 2.1.239) — so the refusal cannot fire on today's binaries, and
scanning the whole module rather than stopping at the first match costs 19 ms
and 24 ms against a 4.1 s and 5.4 s `transform()`.

**Step 2 — locate the image gate by its error string.** The gate call itself is
anonymous-looking (`if(CE())`), but the branch it guards ends in a distinctive
literal:

```
IMAGE_ANCHOR = "Native image processor not available"
```

Measured 2026-08-23, on both extracted entry modules: **exactly one**
occurrence of the anchor in each, and in both the image branch's own guard
starts **132** bytes before it (the gate call itself ends 125 bytes before it).
The shape `if(<gate>())try{let r=await Promise.resolve().then(...` is stable
across two versions and two platforms.

**Changed during implementation.** This step originally said the shim
"searches backwards from the anchor within a bounded window (400 bytes) and
rewrites the **last** `<gate>()` in it". That rule shipped in the first draft
and was **broken by review**. The nearest preceding gate call is not the same
thing as the image branch's guard: given an entry module whose image function
has lost its `if(<gate>())` but kept the anchor, "the last gate call before the
anchor" is embedded **ripgrep's**, and rewriting that passes every count this
tool makes. What ships instead selects by **shape** —

```
_image_site_re(name) = if\s*\(\s*(<gate>\(\))\s*\)\s*try\s*\{
```

— still searched for in the 400 bytes before the anchor, but required to be
that shape, and required to be **unique** within the window (zero or two or
more matches is a refusal, not a guess). Measured on this host 2026-08-23: the
`if(<gate>())try{` shape occurs exactly once in each *whole* entry module —
22,960,130 bytes / `CE` and 28,244,743 bytes / `AE` — and starts 132 bytes
before the anchor in both (the call itself ends 125 bytes before it); the
ripgrep site opens a plain `{`. The window is a cheap bound, not the safety
argument: the nearest *other* gate call is 506,792 bytes away on linux-x64
2.1.222 and 1,732,905 bytes away on darwin-arm64 2.1.239.

The shape is the **whole** condition. `if(!<gate>())try{`, `if(<gate>(),1)try{`
and `if(<gate>()&&x)try{` each contain a real gate call in the window and are
each refused, because rewriting the call in any of them changes a branch nobody
inspected — the negated one *inverts*. The window is bounded on the **right**
as well: the anchor is the END of the branch being selected, so a
`if(<gate>())try{` occurring after it is a different branch and is out of
reach. Both properties are pinned hermetically
(`test_only_an_exact_if_gate_try_is_a_site`,
`test_a_gate_shape_after_the_anchor_is_out_of_reach`) because neither shows up
in any fixture that predates them: mutating either one left the whole suite
green before those tests existed.

**Step 3 — rewrite it to `true`.** One occurrence, nothing else. Deliberately
the boring `true` and not `!0`: this is the one place in a 23 MB minified file
a human will grep for, and `grep -o 'if(true)try' <artifact> | wc -l` answers
"is this a shimmed build?" — 1 shimmed, 0 as shipped, measured on both
binaries.

### The safety property, enforced

**Changed during implementation.** This section originally claimed that the
before/after count invariant "pins the ripgrep gate". That claim was
**demonstrated false** during review and is retracted; what follows is the rule
that actually ships. There are two properties, they are independent, and only
one of them is arithmetic.

**1. Nothing else moved — enforced by counting.** The transform counts gate
calls before and after, and `check()` treats a mismatch as **fatal**:

```
calls_after == calls_before - 1        (when the shim applied)
calls_after == calls_before            (when it did not)
```

plus a separate check that `applied` is 0 or 1 at all, since
`(before, after, applied) = (21, 19, 2)` balances perfectly and is still two
rewrites from a shim licensed to make one. A rewrite that *spread* therefore
fails the build and writes no `cli.original.cjs`. Measured on the real
artifacts: 21 → 20 (`CE`, linux-x64 2.1.222) and 23 → 22 (`AE`, darwin-arm64
2.1.239), a four-byte difference from the unshimmed output in both cases.

**2. The site that moved was the right one — enforced by the selection rule,
not by the count.** This is where the original claim was wrong. The invariant
counts how many sites moved and *never which*. Rewriting embedded ripgrep's
gate instead of the image gate produces exactly the same arithmetic, exactly
the same `applied = 1`, a clean `check()` and a build that ships a `Grep`
answering `No matches found` for a string that exists. A reviewer built that
entry module — image function with the anchor but without its own
`if(<gate>())` — and the first draft of this shim rewrote ripgrep and reported
success. It is kept as
`tests/test_image_shim.py::test_a_lost_image_guard_does_not_hand_the_rewrite_to_ripgrep`.

So the ripgrep gate is pinned by **shape**: the site must be a
`if(<gate>())try{`, unique in the 400 bytes before the anchor, and the ripgrep
site opens a plain `{`. That property is asserted directly rather than inferred
— the tests reconstruct the shimmed output by undoing its single known edit and
demand byte equality with the unshimmed output, which covers *every* other gate
site at once, and additionally assert that the rewritten site reads
`if(true)try` and that the ripgrep site's exact source text survives.

### What the selection rule still does not prove

Recorded because a rule whose limits are unwritten gets trusted past them. Two
constructed entry modules still get a rewrite this document cannot call
correct, and both were built and run against the shipped code:

1. **Image guard out of the window, decoy inside it.** Move the image branch's
   own `if(<gate>())try{` more than 400 bytes from its anchor *and* put another
   one inside the window, and the decoy is the only candidate, so it is
   rewritten with `applied = 1` and a clean `check()`. This is inherent to
   "a window plus a shape": uniqueness inside the window cannot distinguish
   "the one right site" from "the one remaining wrong site". Requiring the
   shape to be unique in the *whole* module would close it — and would also
   make any future Claude that grows a second, entirely unrelated
   `if(<gate>())try{` anywhere in 23 MB stop shimming, in a case where today's
   rule is *correct*. That trade was declined: the attack needs two independent
   drifts at once, the cost lands on a single benign one.
2. **The shape inside a string literal.** The matcher is textual, not a
   parser, so `var msg="if(<gate>())try{"` in the window is a candidate. It
   rewrites four bytes of a string constant rather than a gate, so no gate is
   flipped either way; it is noted so nobody re-derives it as a gate hazard.

Neither is reachable on the binaries this repo ships against: measured on this
host, the shape occurs exactly once per module and 132 bytes before its anchor.

### When the anchor is not found

**Warn, do not fail.** If a future Claude renames the string or restructures
the function, the artifact is still exactly as good as today's — it runs, with
image processing degraded. Failing the build there would turn a cosmetic
upstream change into an outage. But it must not be *silent*: `postprocess.py`
prints the gate name and the shim count on stdout, and `build.sh` prints an
explicit line either way.

**Changed during re-review — the refusal has to name which thing drifted, and
must not invent counts.** Three separate things can drift and they are
re-measured in different places: the gate **declaration**'s minified shape
(`STANDALONE_DEF`), the **anchor** string (`IMAGE_ANCHOR`), and the
`if(<gate>())try{` branch shape (`_image_site_re`). `build.sh` used to close
every refusal alike with *"Most likely a new Claude release renamed the anchor
string."* Reproduced on this host on 2026-08-24, both revisions against the
same input — a copy of `/usr/bin/claude` whose one 53-byte gate declaration at
offset 260,565,233 was replaced in place with an equal-length arrow form,
anchor untouched — the old build exits 0 and prints exactly that line for an
artifact that still holds `Native image processor not available` **once**, and
prints `image shim call sites  : 0 -> 0` for an entry module with **21** live
`CE()` call sites. `postprocess.py` now emits the cause on stdout as
`image shim not applied : …`, `build.sh` quotes it rather than guessing, and
where no gate could be named the counts print as
`not counted (no gate identified)`: `0` there is not "unknown", it is a claim
about the artifact, and it measured false.

### Opt-out

`NRC_NO_IMAGE_SHIM` set to **any non-empty value** skips the rewrite. This
exists so the "as shipped" side of the A/B in findings §11 can be regenerated
by anyone, from the same tree.

"Any non-empty value" is a contract between two files, not a convenience.
`postprocess.py` decides with `not os.environ.get(...)` and `build.sh` decides
which of its two "NOT APPLIED" messages to print with `[ -n ... ]`. Under a
`!= "1"` rule they disagreed for every other value: `NRC_NO_IMAGE_SHIM=false`
meant "shim it" in one file and "the user opted out" in the other, so a shim
that genuinely *failed to find its gate* would be announced as a deliberate
choice — the one wording that stops anyone investigating. Verified 2026-08-23
against both real binaries with `NRC_NO_IMAGE_SHIM=false` and `=yes`, and
pinned by
`tests/test_image_shim.py::test_any_non_empty_opt_out_value_skips_the_rewrite`
and
`tests/test_build_script.py::test_any_non_empty_opt_out_value_is_an_opt_out_here_too`.

### Explicitly out of scope

The other gate sites stay `false`. Each is a deliberate refusal, not an
oversight:

| Site | Why it stays false |
|---|---|
| embedded ripgrep | flipping it is the measured `No matches found` bug |
| seccomp sandbox (`kms`) | would arm a sandbox whose `/proc/self/exe` is `bun`; unverified, and a wrong sandbox is worse than a documented missing one |
| installer identity / updater | `DISABLE_AUTOUPDATER=1` already covers the hazard; flipping identity to `native` would make the updater's story *less* true, not more |
| chrome + computer-use MCP self-spawn | the generic non-standalone branch is correct, and the `cli.js` sibling shim already serves it |
| telemetry `is_native_binary` | reporting `native` would be a lie |

### Verification

**Static**, in `tests/test_image_shim.py` (**59** tests, collected 2026-08-24): the
gate name is captured from both real declaration shapes; exactly one site is
rewritten and it is the one before the anchor; every *other* gate call site is
byte-identical, asserted by reconstruction rather than by spot check; the
ripgrep site is additionally spot-checked, because when this fails the spot
check is what names the gate that got flipped; the count invariant is fatal
when violated; the reviewer's lost-guard exploit is refused; both real entry
modules get exactly one rewrite.

Added by re-review, each because deleting or widening the thing it covers left
the rest of the suite green: two differently named gate declarations are
refused and one name declared twice is not; the declaration pattern cannot run
past a `}` into another function; only an exact `if(<gate>())try{` is a site;
the search is bounded on the right of the anchor as well as on the left; and
`IMAGE_ANCHOR` is the whole literal, not a prefix — measured on this host, the
prefix `Native image processor` occurs 3 times in each real entry module while
the full string occurs once, so a shortened constant would trip the
not-exactly-once refusal and silently stop shimming every build.

Held to a **mutation test**: 37 mutations of the shim, its `check()` condition
and its env handling, run against the hermetic suite (and, for the two that
only the 300 MB binaries catch, against those). 34 died. Two of the three
survivors are equivalent mutants and are listed here so nobody re-derives
them: `sites[0]` → `sites[-1]` (the uniqueness refusal above guarantees
exactly one candidate) and `reason` → `None or reason` (the same expression).

**Changed during re-review — the third survivor was not equivalent.** It was
recorded as one: `after` recounted → `before - 1`, on the reasoning that a
single-slice rewrite removes exactly one call so the two agree by
construction. The reasoning is true of *today's* rewrite and says nothing
about the guard, which is the point of condition (f): every other test of
`check()` hands it its counts, so the validator was thoroughly covered and the
thing that *produces* the numbers was not covered at all. Reproduced on a
private copy on 2026-08-24: with `before - 1` in place of the recount, a
rewrite that spreads into every gate call in the real 22,960,130-byte
`linux-x64` 2.1.222 entry module reports
`gate=CE before=21 after=20 image_shim=1` and `check() errors: []` over an
output with **0** `CE()` call sites left — the global flip that breaks `Grep`,
shipped as one tidy scoped rewrite. Control, same spreading rewrite with the
real recount: `before=21 after=0`, and `check()` fires with the accounting
error. `test_the_after_count_is_measured_on_the_rewritten_code` (in
`tests/test_image_shim.py`) now recounts the output independently and kills
the mutant: with `before - 1` applied to a private copy, that file goes from
`59 passed` to `2 failed, 57 passed`. So the tally above should be read as of
its own date — that mutation is no longer a survivor.

**Dynamic**, on the real artifact: an A/B driven through a **loopback-only mock
of the Messages API**, committed to this repo as
`scripts/mock-messages-api.mjs` and driven by `scripts/ab-equivalence.sh`.
findings §11's evidence was previously unreproducible because that harness
lived in `/tmp`; committing it is part of this change.

All three measurements promised here are implemented, and the harness went
further in two ways worth recording:

1. **Read** on a deterministic 3000×3000 PNG → error as shipped, real JPEG with
   the shim. ✅
2. **Grep** for a string that exists → the same hit both ways. This is the
   regression the global flip caused; it must not reappear. ✅
3. `doctor` → `Running: unknown` **both** ways. The install identity is a
   different gate site, and it staying `unknown` is the positive evidence that
   the rewrite did not spread. ✅
4. *Added:* **Bash** running a subprocess → the same output on every side. A
   control: two sides that both silently produce nothing would otherwise
   "agree", and a case that is supposed to be identical everywhere is what
   tells you the harness is comparing artifacts rather than comparing
   failures.
5. *Added:* a **third side**. The globally-flipped artifact — the as-shipped
   build with `try{Bun.isStandaloneExecutable=true}catch(e){};` injected after
   the CJS wrapper — is built and run as a case, not described in prose,
   because the claim that the global flip breaks `Grep` is the entire
   justification for this design. Its expectation is the *breakage*: if a
   future Bun or Claude makes the global flip harmless, that assertion goes
   red and the premise has expired.

Every expectation is explicit per side, so the script fails rather than merely
printing. It also polls `/proc/<pid>/fd` against `/proc/net/tcp` for the whole
process tree and fails on any non-loopback socket — which is how it was found
that the as-shipped Read case had been quietly fetching sharp's libvips
packages from npm mid-run. Re-run in full on 2026-08-24 against `linux-x64`
2.1.222 under Bun 1.3.14: four cases × three sides, `all expected results
reproduced`, exit 0, every `egress=` line empty. The result table is in
findings §11.

**Changed during re-review — what "the whole process tree" and "empty" now
mean.** Both were claims before they were mechanisms:

- The poller filtered on a cmdline containing the artifact path, which sees
  only the bun process. The Bash tool, `rg` and any tool-driven network access
  run in *children*, and a child opening a socket to a non-loopback address
  was reported as nothing at all. It now walks `/proc/<pid>/stat` parent
  chains, so a child or grandchild of a marked process is attributed to the
  run — and an unrelated process is still not.
- It failed **open**. Being SIGTERMed is the normal end of a case, so "the
  poller is gone" could not be told from "the run was clean"; a poller that
  died on its first iteration left an empty egress file that read as a pass.
  It now writes a status file, and a case whose guard did not report `OK` is
  failed. So is a turn-driving case whose guard attributed **zero** sockets,
  which is what a guard watching the wrong processes looks like.
- Any IPv6 socket with no peer was reported as egress, because the all-zero v6
  remote was compared as a raw hex string that matched none of the loopback
  exemptions — so the guard could invent traffic, contradicting its own claim
  that a finding there is always real. Peers are now decoded with
  `inet_ntop`.

**Changed during re-review — the harness is Linux-only, and now says so.** The
egress guard has no portable substitute, so a preflight refuses to start where
`/proc/net/tcp` and `/proc/<pid>/fd` are unreadable, rather than run the
comparison with its safety net silently missing; the same preflight names
every other missing prerequisite in one message. Everything else in the script
was made portable on the way (`python3` for sizes and md5s instead of
`stat -c` / `md5sum`), because
the previous failure on a macOS-like `PATH` was BSD `stat`'s
`illegal option -- c` from inside fixture setup, before any of the script's
own checks could speak.

**Changed during re-review — the fixture is checked on its decoded content.**
The 3000×3000 PNG's file size was asserted exactly, which pinned the harness
to this host's deflate rather than to the image: measured 2026-08-24, the same
27,003,000 scanline bytes at level 6 give a 2,329,372-byte IDAT through this
`python3`'s zlib 1.2.13 and a 2,329,196-byte one through node v22's zlib
1.3.1-e00f703. Dimensions, colour type, chunk CRCs and the md5 of the decoded
scanlines are asserted instead; the on-disk size is printed as informational.

**Changed during re-review — a pass off the mock's non-streaming fallback is
not a pass.** The mock's `sse()` carried a *measured* claim that the SSE
`event:` line is not what the client dispatches on. It is; the four runs that
establish that, and the fallback that confounded the original measurement, are
recorded in `scripts/mock-messages-api.mjs`'s `sse()` comment. The mechanism
that matters here is the check, not the retraction: a mock defect can move
every turn onto a code path a real API run never takes while the A/B still
prints the expected string, so the harness now greps each case's mock log for
`stream=false` and fails the case.

---

## Part 2 — `patch_claude.py` tests

### What the tool is

A length-preserving byte patcher and ad-hoc re-signer for the *native* Mach-O
binary. It is pre-existing and macOS-only in its second half; it *was* untested,
which is what this part of the work is about. Its load-bearing invariant is that
the output is **exactly** as long as the input: Mach-O offsets are absolute, so
a file that grows or shrinks mid-patch is corrupt — and under `--in-place` it
corrupts the signed binary where it stands (the `darwin-arm64` 2.1.239 build
measured here is 324,973,552 bytes).

### What is testable on Linux, and how

`--no-sign` returns from `main()` before any `codesign` call, so the entire
byte-patching path runs end-to-end on this host against a synthetic binary. The
suite drives the real CLI through `subprocess`, in the style of
`tests/test_build_script.py`, and covers (all of the following shipped):

- the length invariant: `--new` longer than `--old` is refused *before* any
  write; padding lands at the end; the output size is unchanged
- `--pad`: one byte only; `\0` accepted for C strings
- `--occurrence`: `all`, a valid 1-based index, out of range, non-integer
- `--dry-run` writes nothing at all
- `--out` vs `--in-place`, and that `--in-place` leaves a `.bak` that is the
  *pristine* original even across repeated runs
- neither `--out` nor `--in-place` is an error, not a silent no-op
- a `--old` that is absent is refused
- multi-byte UTF-8 is measured in bytes, not characters

The macOS-only helpers (`original_identifier`, `dump_entitlements`, `resign`)
are unit-tested against a stubbed `run()`, which is also how the *ordering*
requirement is checked: entitlements and identifier must be read from the
binary **before** its bytes are overwritten.

The shipped suite went past this list, in the places where writing the tests
turned something up: overlapping `--old` matches that would write over each
other's padding, back-to-back hits that only *look* overlapping, `--out`
pointing at the input, the quarantine xattr having to be dropped *after* the
signature lands, a failed `codesign --verify` being fatal, and `--verify`
actually launching the patched binary. **94** tests in total, collected on
this host on 2026-08-24.

### What the tests changed

"No behaviour change beyond what a test proves wrong" was the rule, and the
tests did prove something wrong, so this is what moved:

- **`splice()` is now a function of its own**, and it raises rather than
  returning a resized buffer. The length invariant used to be checked by
  `os.path.getsize()` *after* the write — and under `--in-place` the
  destination *is* the source, so by the time that check could fire the
  original was already overwritten. It now runs on the in-memory buffer before
  the destination is opened, and before `--dry-run` returns, so a rehearsal
  reports it too.
- The post-write size check survives, with a narrower job: catching a write
  that lands **short** (quota, `ENOSPC`) and telling the user to recover from
  the `.bak`, instead of printing `Patched` over a truncated binary.
- The `--verify` help text says to attach dashed arguments with `=`
  (`--verify=--version`), because argparse otherwise reads them as options.
- The measured cost of an empty `--old` was re-stated as load-dependent
  ("tens of seconds per MiB") rather than as a single wall-clock figure, since
  this host is shared between agents.

**Changed during re-review.** The review fleet found four more. Three of them
are behaviour a user of this tool has to know about, and each was reproduced
on this Linux host on 2026-08-24:

- **A signing run on a host with no `codesign` used to leave an unpatched copy
  of the input at `--out`.** This was a regression introduced by the same
  reordering that made `splice()` raise: `shutil.copy2(src, dest)` ran before
  the `codesign` reads, and those raise `FileNotFoundError` rather than
  returning non-zero, so the destination was created and then never written.
  `printf 'HEAD NEEDLE TAIL' > fixture.bin` with
  `--old NEEDLE --new N --out out.bin` left `out.bin` holding
  `HEAD NEEDLE TAIL` — verbatim, unpatched, under the name the user asked for,
  behind an unhandled traceback that said nothing about it. There is now a
  pre-flight refusal before anything is created, and `main()` reads the
  signing metadata before it creates the destination so that a failure in
  `codesign` itself cannot leave a file behind either. The same fixture now
  prints one sentence and leaves no `out.bin` at all.
- **Hits inside `LC_CODE_SIGNATURE` are dropped, reported, and excluded from
  the patched count.** On the shipped 324,973,552-byte `darwin-arm64` Mach-O,
  `--old com.anthropic --dry-run` finds **137** hits and **2** of them
  (`0x1354BE54`, `0x135E69E5`) are inside the signature blob
  (324,320,704..324,973,552) — the signing Identifier
  `com.anthropic.claude-code`, stored as a literal C string in the
  CodeDirectory. It now prints `Found 137 occurrence(s); patching 135` and
  names the two it skipped; `--patch-signature` overrides.
  `code_signature_range()` reads the Mach-O header and load-command table only,
  so the check costs the same on a 325 MB binary as on a 3 KB one, and returns
  `None` — "I cannot tell", not a refusal — for anything that is not a thin
  Mach-O it recognises, which is what keeps the test suite's Mach-O-shaped
  fixtures patchable.
- **`--out <the input>` is refused**, by `os.path.samefile` rather than a
  string compare, so a symlink or hard link to the input is caught too.
  Answering it as written was an in-place patch with no `.bak`; it now dies
  with `--out names the input; use --in-place, which keeps a .bak`.
- The fourth, not user-visible: the third full in-RAM copy of the binary that
  this work had added was removed —
  `blob` is read as `bytes` and no longer re-copied at the find and at every
  preview line. `splice()` takes its own `bytearray()` and `preview()` only
  slices, so the change is behaviour-preserving.

Still out of scope: this tool is not part of the extract → postprocess → run
pipeline; it is a macOS utility that happens to live here.
