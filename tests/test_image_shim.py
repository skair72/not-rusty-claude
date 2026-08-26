"""The scoped `Bun.isStandaloneExecutable` image shim in tools/postprocess.py.

Design of record:
docs/superpowers/specs/2026-08-23-scoped-image-shim-design.md (Part 1).

The shim exists because one consequence of "we are not a standalone" is wrong:
native image processing is gated behind that flag, so the Read tool cannot
resize a large image. The tempting fix - set the flag - is MEASURED to break
Grep (docs/findings.md §10), so the rewrite is scoped to a single call site.

Everything therefore hangs on two properties.

*Nothing else moved.* Not asserted here by spot-checking the sites that matter;
asserted by reconstructing the shimmed output with the single known rewrite
undone and demanding the result be byte-identical to the unshimmed output. Spot
checks (the ripgrep site) are kept as well, because when this fails the spot
check is what tells a reader which gate got flipped and why that is bad.

*The site that moved was the right one.* That is a separate property, and the
count-based one cannot stand in for it: counting how many sites moved says
nothing about which. An entry module whose image branch has lost its own guard
puts the RIPGREP gate nearest the anchor, and rewriting that satisfies every
count and every arithmetic check - see
test_a_lost_image_guard_does_not_hand_the_rewrite_to_ripgrep, which is a
reviewer's working exploit of the first version of this shim. The site is
therefore chosen by SHAPE, `if(<gate>())try{`, and the tests below hold that
shape to its measurements on both real binaries.

The hermetic tests below use synthetic strings only. The tests that need a
real Claude binary take the conftest fixture and skip cleanly without one, and
both platforms are covered: the darwin gate declaration has a shape of its own
and is the reason STANDALONE_DEF is as tolerant as it is.
"""

import re

import pytest


# --- synthetic entry modules -------------------------------------------------
#
# Real shapes, copied verbatim from the extracted entry modules and then cut
# down. The gate declarations are the two that exist in the wild:
#   linux-x64 2.1.222   function CE(){return Bun.isStandaloneExecutable===!0}
#   darwin-arm64 2.1.239 function AE(){return typeof Bun<"u"&&...===!0}

GATE_DEF_PLAIN = "function CE(){return Bun.isStandaloneExecutable===!0}"
GATE_DEF_TYPEOF = 'function AE(){return typeof Bun<"u"&&Bun.isStandaloneExecutable===!0}'

# A gate declaration STANDALONE_DEF does not match, in the shape a minifier
# most plausibly produces: the same gate as an arrow. Not invented for this
# file - the drift was reproduced end to end by replacing this exact
# declaration in place in a copy of the linux-x64 2.1.222 binary with the
# equal-length `var CE=()=>Bun.isStandaloneExecutable===!0;var _q0=0;` (53
# bytes either way, one occurrence, at offset 260565233) and running a full
# build against it on this host on 2026-08-24. Everything else about that
# artifact - the anchor, the `if(CE())try{` branch, all 21 gate calls - was
# untouched, and the build still announced a renamed anchor string.
GATE_DEF_ARROW = "var CE=()=>Bun.isStandaloneExecutable===!0"

HEAD = "// @bun @bytecode @bun-cjs\n(function(exports, require, module, __filename, __dirname) {\n"
TAIL = 'r("cli_after_main_complete")}PSE();})\n'

# The image site, verbatim from build/extract/cli.original.cjs (linux-x64
# 2.1.222) with only the gate name parameterised. The gate call ends 125 bytes
# before the anchor here, exactly as it does in both real binaries.
IMAGE_SITE = (
    'async function uYe(){if(Fbo)return Fbo.default;if(%s())try{let r=await '
    'Promise.resolve().then(() => (uys(),cys)),n=r.sharp||r.default;return '
    'Fbo={default:n},n}catch{console.warn("Native image processor not '
    'available, falling back to sharp");return null}}'
)

# The ripgrep site, verbatim from the same file. This is the gate that must
# NEVER be flipped: with it true, "embedded ripgrep" means "re-exec
# process.execPath with argv0 rg", process.execPath is bun, and Grep answers
# "No matches found" for a string that exists (docs/findings.md §10).
RIPGREP_SITE = (
    'let{cmd:r}=Syo("rg",[]);if(r!=="rg")return{mode:"system",command:r,'
    'args:[]}}if(%s()){let r={mode:"embedded",command:process.execPath,'
    'args:["--no-config"],argv0:"rg"};if(Lfe(process.execPath))return r;'
)

# A third site, in the shape the boundary rule has to get right: the gate call
# is preceded by the spread `...`, which is why _gate_call_re deliberately does
# not exclude a preceding dot. Three sites per real binary look like this.
IMAGE_ANCHOR_TEXT = "Native image processor not available"

SPREAD_SITE = 'let e=process.execPath,r=[...%s()?[e]:[e,process.argv[1]]];'

# The adversarial entry module a reviewer used to break the pre-fix shim: the
# image function has LOST its own `if(<gate>())` guard but still carries the
# anchor, so the nearest gate call before the anchor is the RIPGREP one. It
# takes no gate parameter because that is the whole point - there is no gate
# call in here at all.
LOST_GUARD_IMAGE_SITE = (
    'async function uYe(){try{let r=await Promise.resolve().then(() => '
    '(uys(),cys)),n=r.sharp||r.default;return Fbo={default:n},n}catch{'
    'console.warn("Native image processor not available, falling back to '
    'sharp");return null}}'
)

# A `$`-containing identifier that ENDS in the gate's name. `$` is a legal
# JavaScript identifier character and not a regex word character, so a `\b`
# boundary matches inside this and counts it as a gate call.
DOLLAR_LOOKALIKE = 'var x$CE=()=>1;if(x$CE())return;'


def _module(gate_def=GATE_DEF_PLAIN, name="CE", sites=(RIPGREP_SITE, IMAGE_SITE, SPREAD_SITE)):
    """A postprocess-able entry module: pragma, wrapper, gate, sites, `})`."""
    return HEAD + gate_def + ";" + "".join(s % name for s in sites) + TAIL


def _live_gate_calls(text, name="CE"):
    """`<name>()` call sites in `text`, counted WITHOUT postprocess's counter.

    Deliberately a second implementation and not a call to
    `_count_gate_calls`: this is what checks that the numbers postprocess
    reports describe the text it actually produced, and a recount that goes
    through the code under test cannot see that code being bypassed.

    The declaration `function CE(){` carries the token `CE()` too and is not a
    call site, so it is subtracted here exactly as _count_gate_calls excludes
    its span. GATE_DEF_ARROW declares no such token, and 0 is then the right
    thing to subtract.
    """
    calls = len(re.findall(r"(?<![\w$])" + re.escape(name) + r"\(\)", text))
    return calls - text.count("function %s(){" % name)


def _single_edit(before, after):
    """The one contiguous (offset, removed, inserted) difference, or fail.

    Comparing common prefix and common suffix rather than diffing: any second
    edit anywhere in the file inflates `removed`/`inserted` to span everything
    between the two, which is exactly the failure this is looking for.
    """
    head = 0
    while head < min(len(before), len(after)) and before[head] == after[head]:
        head += 1
    tail = 0
    while (tail < min(len(before), len(after)) - head
           and before[len(before) - 1 - tail] == after[len(after) - 1 - tail]):
        tail += 1
    return head, before[head:len(before) - tail], after[head:len(after) - tail]


# --- capturing the gate ------------------------------------------------------

@pytest.mark.parametrize("gate_def,name", [
    (GATE_DEF_PLAIN, "CE"),          # linux-x64 2.1.222
    (GATE_DEF_TYPEOF, "AE"),         # darwin-arm64 2.1.239
])
def test_both_real_gate_declaration_shapes_are_recognised(postprocess, gate_def, name):
    """2.1.239 added a `typeof Bun<"u"&&` guard in front of the property test.
    A pattern that only knew the 2.1.222 shape would silently find no gate on
    macOS and produce an unshimmed artifact with no error."""
    out, counts = postprocess.transform(_module(gate_def, name))

    assert counts["gate_name"] == name
    assert counts["image_shim"] == 1
    assert counts["image_shim_reason"] is None
    assert postprocess.check(out, counts) == []


def test_the_declaration_itself_is_not_counted_as_a_call_site(postprocess):
    """`function CE()` contains the token `CE()`. Counting it would not break
    the invariant's arithmetic, but it would make the number printed at build
    time - and quoted in the docs - wrong by one."""
    _, counts = postprocess.transform(_module())

    assert counts["gate_calls_before"] == 3, "ripgrep + image + spread"


def test_a_spread_prefixed_call_site_is_counted(postprocess):
    """`[...CE()?[e]:[...]]`. A boundary rule that excluded a preceding `.`
    would drop three real call sites per binary from the count, and the safety
    invariant cannot notice a rewrite spreading into a site it never counted."""
    _, counts = postprocess.transform(_module(sites=(IMAGE_SITE, SPREAD_SITE)))

    assert counts["gate_calls_before"] == 2


def test_lookalike_identifiers_are_not_counted_as_call_sites(postprocess):
    """The real linux entry module contains `isGCE()` and `_checkIsGCE()`.
    Counting those as gate calls would make the before/after arithmetic
    meaningless - and a plain `CE()` search finds 4 of them."""
    noise = "class X{get isGCE(){return 1}}async function _checkIsGCE(){return this._checkIsGCE()}"
    _, counts = postprocess.transform(_module(sites=(IMAGE_SITE,)) + noise)

    assert counts["gate_calls_before"] == 1


def test_a_dollar_prefixed_lookalike_is_not_counted_as_a_call_site(postprocess):
    r"""`$` is a legal JavaScript identifier character but not a regex word
    character, so `\bCE\(\)` matches inside `x$CE()` and would count a call
    that is not the gate's. Neither real binary contains such an identifier
    today - measured: 0 matches for `[\w$]*\$CE\(\)` in linux-x64 2.1.222 and
    for `[\w$]*\$AE\(\)` in darwin-arm64 2.1.239 - which is precisely why the
    `(?<![\w$])` boundary needs a fixture of its own rather than a comment."""
    src = _module(sites=(IMAGE_SITE,)).replace(TAIL, DOLLAR_LOOKALIKE + TAIL)

    _, counts = postprocess.transform(src)

    assert counts["gate_calls_before"] == 1, "x$CE() was counted as a gate call"


# --- exactly one site, and it is the right one -------------------------------

def test_exactly_one_site_is_rewritten_and_it_is_the_one_before_the_anchor(postprocess):
    src = _module()

    out, counts = postprocess.transform(src)

    assert counts["image_shim"] == 1
    assert counts["gate_calls_after"] == counts["gate_calls_before"] - 1
    # the rewritten call is the one inside the image function, not either of
    # the other two
    assert 'if(true)try{let r=await Promise.resolve()' in out
    assert out.count("Native image processor not available") == 1


def test_every_other_gate_call_site_is_byte_identical(postprocess):
    """The strong form of "the rewrite did not spread": take the shimmed
    output, put `CE()` back where `true` went, and demand the result equal the
    unshimmed output byte for byte. Any second rewrite anywhere in the file
    makes the single-edit reconstruction fail."""
    src = _module()

    shimmed, counts = postprocess.transform(src)
    plain, plain_counts = postprocess.transform(src, image_shim=False)

    offset, removed, inserted = _single_edit(plain, shimmed)
    assert removed == "CE()"
    assert inserted == "true"
    assert shimmed[:offset] + removed + shimmed[offset + len(inserted):] == plain
    assert plain_counts["image_shim"] == 0
    assert plain_counts["gate_calls_after"] == plain_counts["gate_calls_before"]


def test_the_ripgrep_gate_site_survives_untouched(postprocess):
    """The named, measured harm of flipping the flag globally: with the
    ripgrep gate true, "embedded ripgrep" becomes "re-exec process.execPath
    with argv0 rg", process.execPath is bun, and a Grep for a string that
    exists answers `No matches found` (docs/findings.md §10). That is a wrong
    answer, not an error, so it is the one site this test names explicitly."""
    out, _ = postprocess.transform(_module())

    assert RIPGREP_SITE % "CE" in out
    assert 'if(true){let r={mode:"embedded"' not in out


def test_a_lost_image_guard_does_not_hand_the_rewrite_to_ripgrep(postprocess):
    """The demonstration that killed "the nearest call before the anchor".

    This entry module has the gate declaration, the verbatim ripgrep site, and
    an image function that has LOST its own `if(CE())` while keeping the
    anchor. Measured on this fixture: the ripgrep gate call then ends 262
    bytes before the anchor, comfortably inside the 400-byte window. The
    pre-fix shim took it, reported image_shim=1 and 1 -> 0 call sites, and
    check() returned [] - the before/after invariant counts how many sites
    moved, never which - so the build shipped `if(true){let r={mode:"embedded"`
    and a Grep that answers "No matches found" for a string that exists
    (docs/findings.md §10). Selecting by SHAPE is what makes this a no-op.
    """
    src = _module(sites=(RIPGREP_SITE,)).replace(TAIL, LOST_GUARD_IMAGE_SITE + TAIL)
    call_end = src.index("if(CE()){") + len("if(CE()")
    assert src.index(IMAGE_ANCHOR_TEXT) - call_end == 262, "fixture drifted"

    out, counts = postprocess.transform(src)

    assert counts["image_shim"] == 0, "a non-image gate was rewritten"
    assert counts["gate_calls_after"] == counts["gate_calls_before"] == 1
    assert RIPGREP_SITE % "CE" in out, "the embedded-ripgrep gate was flipped"
    assert 'if(true){let r={mode:"embedded"' not in out
    # a refusal, not a failure: this must still build (see the refusals below)
    assert postprocess.check(out, counts) == []
    assert "if(CE())try{" in counts["image_shim_reason"]


def test_the_gate_declaration_inside_the_window_is_never_the_site(postprocess):
    """A declaration sitting between the guard and the anchor is a `CE()`
    token nearer the anchor than the guard is. Rewriting it would produce
    `function true(){return Bun.isStandaloneExecutable===!0}` - a syntax error
    that Bun reports from the top of a 23 MB file. Positional selection needs
    an explicit exclusion to survive this; shape selection cannot match a
    declaration at all, because `if(` is a literal part of the pattern."""
    site = (IMAGE_SITE % "CE").replace(
        'console.warn("Native', GATE_DEF_PLAIN + ';console.warn("Native')
    src = HEAD + GATE_DEF_PLAIN + ";" + site + TAIL
    decl_at = src.index(GATE_DEF_PLAIN, len(HEAD) + 1)
    assert 0 < src.index(IMAGE_ANCHOR_TEXT) - decl_at < 400, "fixture drifted"

    out, counts = postprocess.transform(src)

    assert counts["image_shim"] == 1
    assert 'if(true)try{' in out
    assert out.count(GATE_DEF_PLAIN) == 2, "a declaration was rewritten"
    assert postprocess.check(out, counts) == []


# --- picking the gate NAME is a selection too --------------------------------

# A second isStandaloneExecutable gate under a different minified name. The
# shape rule picks among call SITES once the name is fixed; it has nothing to
# say about which name to fix, and that is the hole this pair of tests covers.
SECOND_GATE_DEF = "function ZZ(){return Bun.isStandaloneExecutable===!0}"


def test_two_differently_named_gate_declarations_refuse_to_guess(postprocess):
    """The lost-guard exploit again, with the gate NAME as the free variable.

    This entry module declares `ZZ` first and the real `CE` second, leaves the
    image branch correctly on `if(CE())try{`, and puts an `if(ZZ())try{` inside
    the image function just before the anchor. Bind to the first declaration
    and `ZZ` becomes "the gate": its branch is then the only `if(<gate>())try{`
    in the window, so it is rewritten, `image_shim` reports 1, ZZ's call sites
    go 1 -> 0, the arithmetic balances and `check()` returns clean - while the
    branch the shim was sent to fix is untouched and image processing is still
    off. Every count in this tool agrees with a flipped gate nobody inspected,
    exactly as it did for embedded ripgrep.

    Measured on this host 2026-08-23: STANDALONE_DEF matches exactly once in
    each real entry module (`CE` in linux-x64 2.1.222, `AE` in darwin-arm64
    2.1.239), so refusing here cannot cost today's builds their shim.
    """
    src = (HEAD + SECOND_GATE_DEF + ";" + GATE_DEF_PLAIN + ";"
           + RIPGREP_SITE % "CE"
           + (IMAGE_SITE % "CE").replace(
               'console.warn("Native',
               'if(ZZ())try{}catch{}console.warn("Native')
           + TAIL)

    out, counts = postprocess.transform(src)

    assert counts["image_shim"] == 0, "a gate nobody identified was rewritten"
    assert counts["gate_name"] is None, "one of two gates was named as THE gate"
    assert "CE, ZZ" in counts["image_shim_reason"]
    assert "if(ZZ())try{" in out, "the second gate's branch was flipped"
    assert "if(CE())try{" in out, "the image gate was flipped"
    assert "if(true)" not in out
    # a refusal, not a failure - the artifact is exactly the unshimmed one
    assert postprocess.check(out, counts) == []
    plain, plain_counts = postprocess.transform(src, image_shim=False)
    assert out == plain
    # and both halves of the A/B must refuse to name a gate for the same
    # reason. A shimmed half reporting `None` against an opt-out half reporting
    # `ZZ` is two different measurements presented as one comparison, and the
    # opt-out half is the one nobody re-reads.
    assert plain_counts["gate_name"] is None
    assert plain_counts["gate_calls_before"] is None
    assert plain_counts["gate_calls_after"] is None


def test_two_declarations_of_the_same_gate_are_not_an_ambiguity(postprocess):
    """The refusal above keys on the set of NAMES, not on the match count.

    `test_the_gate_declaration_inside_the_window_is_never_the_site` builds an
    entry module that carries `function CE(){...}` twice on purpose. Two
    declarations of one name leave nothing to guess - every call site in the
    file still means the same gate - so it must keep shimming. A refusal
    written as `len(matches) > 1` would silently stop shimming there, and
    "silently stops shimming" is precisely the failure this whole file exists
    to make loud.
    """
    src = (HEAD + GATE_DEF_PLAIN + ";" + GATE_DEF_PLAIN + ";"
           + (IMAGE_SITE % "CE") + TAIL)

    out, counts = postprocess.transform(src)

    assert counts["gate_name"] == "CE"
    assert counts["image_shim"] == 1
    assert 'if(true)try{let r=await' in out
    assert postprocess.check(out, counts) == []


def test_the_declaration_pattern_stays_inside_one_function_body(postprocess):
    """`[^{}]*?` between `return` and the property test, never `.*?`.

    With `.*?` the pattern starts at an earlier, unrelated
    `function q(){return 1}` and runs straight through its `}` into the real
    gate, so `group(1)` captures `q`. The shim would then hunt for `if(q())`,
    find nothing, and ship an unshimmed artifact whose warning names the wrong
    identifier. Both real entry modules catch this, but only when a real 300 MB
    binary is present; on a clean host nothing else in the hermetic suite does.
    """
    src = _module(sites=(RIPGREP_SITE, IMAGE_SITE)).replace(
        GATE_DEF_PLAIN, "function q(){return 1};" + GATE_DEF_PLAIN)

    out, counts = postprocess.transform(src)

    assert counts["gate_name"] == "CE", "the match ran past a } into another function"
    assert counts["image_shim"] == 1
    assert postprocess.check(out, counts) == []


# --- the shape is the WHOLE condition, not a part of it ----------------------

@pytest.mark.parametrize("guard,why", [
    ("if(!CE())try{",
     "a negated guard runs its branch when the gate is FALSE, so rewriting the "
     "call to true inverts the branch rather than enabling it"),
    ("if(CE(),1)try{",
     "the comma operator throws the gate's value away; this branch already "
     "always runs and the call is not what gates it"),
    ("if(CE()&&x)try{",
     "a compound condition has a second term the shim never looked at"),
    ("if(CE ())try{",
     "whitespace inside the call - a shape neither real entry module produces, "
     "so accepting it only widens what can be selected"),
])
def test_only_an_exact_if_gate_try_is_a_site(postprocess, guard, why):
    """Everything the shape rule buys is in the word EXACT.

    Each guard here puts a genuine `CE()` call in the window before the anchor
    of an image function that has lost its own guard - the lost-guard exploit's
    exact setup, with the decoy one character away from the shape instead of
    one `if(` away. Any widening of the matcher that lets one of these through
    hands the rewrite to a branch nobody inspected, and reports `image_shim: 1`
    with balanced counts and a clean check() while doing it.
    """
    src = _module(sites=(RIPGREP_SITE,)).replace(
        TAIL,
        LOST_GUARD_IMAGE_SITE.replace(
            'console.warn("Native', guard + '}catch{}console.warn("Native')
        + TAIL)
    assert 0 < src.index(IMAGE_ANCHOR_TEXT) - src.index(guard) < 400, "fixture drifted"

    out, counts = postprocess.transform(src)

    assert counts["image_shim"] == 0, why
    assert "if(true)" not in out, why
    assert guard in out
    assert postprocess.check(out, counts) == []


def test_a_gate_shape_after_the_anchor_is_out_of_reach(postprocess):
    """The search is bounded on the RIGHT as well as on the left.

    The window is `[anchor - 400, anchor)`, and every other fixture in this
    file puts nothing after the anchor - so dropping the right-hand bound
    changes none of their results. It is not harmless: a second
    `if(<gate>())try{` anywhere later in a 23 MB module becomes a second
    candidate, the uniqueness rule fires, and a build that should shim cleanly
    degrades to an unshimmed artifact. Anything past the anchor is a different
    branch by construction - the anchor is the END of the branch being picked.
    """
    src = _module(sites=(RIPGREP_SITE, IMAGE_SITE)).replace(
        TAIL, "if(CE())try{later()}catch{}" + TAIL)
    assert src.index("if(CE())try{later()") > src.index(IMAGE_ANCHOR_TEXT)

    out, counts = postprocess.transform(src)

    assert counts["image_shim"] == 1, "a shape past the anchor was let into the window"
    assert 'if(true)try{let r=await' in out
    assert "if(CE())try{later()" in out, "the shape past the anchor was rewritten"
    assert postprocess.check(out, counts) == []


def test_the_anchor_is_the_whole_literal_and_not_a_prefix(postprocess):
    """IMAGE_ANCHOR is long because a prefix of it is not unique.

    Measured on this host 2026-08-23: the full literal `Native image processor
    not available` occurs once in each real entry module, while the prefix
    `Native image processor` occurs 3 times in each. Shortening the constant
    does not merely lose precision - it trips the not-exactly-once refusal and
    every build silently stops shimming. Both real-binary tests catch that, and
    both skip on a host with no 300 MB binary, which is why one line of
    synthetic prefix stands behind the constant here too.
    """
    src = _module().replace(TAIL, 'X("Native image processor busy");' + TAIL)
    assert src.count("Native image processor") == 2
    assert src.count(IMAGE_ANCHOR_TEXT) == 1

    out, counts = postprocess.transform(src)

    assert counts["image_shim"] == 1
    assert 'if(true)try{let r=await' in out
    assert postprocess.check(out, counts) == []


# --- the refusals: warn, never fail ------------------------------------------

def test_no_anchor_means_no_rewrite_and_no_error(postprocess):
    """A future Claude that renames the string must degrade to exactly today's
    artifact, not fail the build. An outage is a much worse outcome than the
    image gap the shim closes."""
    src = _module(sites=(RIPGREP_SITE, SPREAD_SITE))

    out, counts = postprocess.transform(src)

    assert counts["gate_name"] == "CE"
    assert counts["image_shim"] == 0
    assert counts["gate_calls_after"] == counts["gate_calls_before"] == 2
    # and it says the gate is FINE, so nobody re-measures STANDALONE_DEF: this
    # reason is what build.sh prints as its closing headline
    assert "not in this entry module at all" in counts["image_shim_reason"]
    assert "not declaration drift" in counts["image_shim_reason"]
    assert postprocess.check(out, counts) == []


def test_no_gate_declaration_at_all_means_no_rewrite_and_no_error(postprocess):
    """Nothing to capture, nothing to count, nothing to rewrite - and the
    output is still a perfectly good CommonJS module.

    The counts are None and not 0. Zero is a measurement ("this file has no
    gate calls") and nothing measured it; the file that produced it in the
    field held 21 of them - see
    test_a_drifted_gate_declaration_is_not_reported_as_zero_call_sites. Here
    the flag genuinely is absent, and the refusal says which of the two this
    is, because the build now quotes it as its headline."""
    out, counts = postprocess.transform(HEAD + "var x=1;" + TAIL)

    assert counts["gate_name"] is None
    assert counts["image_shim"] == 0
    assert counts["gate_calls_before"] is None
    assert counts["gate_calls_after"] is None
    assert "not mentioned anywhere" in counts["image_shim_reason"]
    assert postprocess.check(out, counts) == []


def test_a_drifted_gate_declaration_is_not_reported_as_zero_call_sites(postprocess):
    """The declaration shape drifts; the anchor, the branch and every gate
    call are exactly where they were.

    Reproduced as a full build on this host on 2026-08-24 against a copy of
    the linux-x64 2.1.222 binary with GATE_DEF_ARROW spliced in place of the
    real declaration: postprocess printed `image shim call sites  : 0 -> 0`
    for an artifact that still contained 21 live `CE()` calls, the anchor
    exactly once and `if(CE())try{` exactly once, and build.sh closed with
    "Most likely a new Claude release renamed the anchor string". Both
    statements were false, and the second sends whoever re-measures to the one
    thing that had not moved.

    So: unknown counts stay unknown, and the reason names the declaration and
    says explicitly that the anchor is not implicated - the gate is resolved
    before the anchor is ever looked for, so an unmatched declaration cannot be
    an anchor problem."""
    src = _module(gate_def=GATE_DEF_ARROW)

    out, counts = postprocess.transform(src)

    assert counts["gate_name"] is None
    assert counts["gate_calls_before"] is None
    assert counts["gate_calls_after"] is None
    assert counts["image_shim"] == 0
    why = counts["image_shim_reason"]
    assert "DECLARATION" in why
    assert "anchor string is not implicated" in why
    assert "mentioned 1 time(s)" in why
    # the evidence for that sentence, in the very file the shim refused
    assert _live_gate_calls(out) == 3, "the gate calls are still all there"
    assert out.count("Native image processor not available") == 1
    assert out.count("if(CE())try{") == 1
    # still a refusal, not a failure: the artifact is the unshimmed one
    assert postprocess.check(out, counts) == []
    plain, _ = postprocess.transform(src, image_shim=False)
    assert out == plain


def test_two_anchors_refuse_to_guess(postprocess):
    """With the anchor duplicated there is no way to tell which occurrence
    guards image processing, and picking the wrong one flips an unrelated
    gate. Refusing is the only safe answer, and it is still not fatal."""
    src = _module(sites=(RIPGREP_SITE, IMAGE_SITE, IMAGE_SITE.replace("uYe", "uYf")))

    out, counts = postprocess.transform(src)

    assert out.count("Native image processor not available") == 2
    assert counts["image_shim"] == 0
    assert counts["gate_calls_after"] == counts["gate_calls_before"]
    assert "2 times" in counts["image_shim_reason"]
    assert postprocess.check(out, counts) == []


def test_two_image_gate_shapes_in_the_window_refuse_to_guess(postprocess):
    """Same reasoning as the duplicated anchor: with two `if(CE())try{` in
    reach there is nothing left to tell them apart, and a coin flip here
    rewrites a gate nobody inspected. Measured on both real entry modules the
    shape occurs exactly ONCE in the whole file (23 MB linux-x64 2.1.222,
    28 MB darwin-arm64 2.1.239), so this is a future shape change, not today's
    binaries - and it degrades to an unshimmed artifact, not to a failed
    build."""
    site = (IMAGE_SITE % "CE").replace(
        'console.warn("Native', 'if(CE())try{}catch{}console.warn("Native')
    src = _module(sites=(RIPGREP_SITE,)).replace(TAIL, site + TAIL)

    out, counts = postprocess.transform(src)

    assert counts["image_shim"] == 0
    assert counts["gate_calls_after"] == counts["gate_calls_before"] == 3
    assert "2 if(CE())try{" in counts["image_shim_reason"]
    assert "if(true)" not in out, "one of the two was rewritten anyway"
    assert postprocess.check(out, counts) == []


def _image_site_with_gap(pad):
    """The image site with `pad` bytes of filler wedged between the gate call
    and the anchor. Unpadded, `CE()` starts 140 bytes before the anchor - the
    125 bytes measured between the call's END and the anchor in both real
    binaries, plus the 4-byte call itself and 11 bytes of `var pad='';`."""
    filler = "var pad=%r;" % ("x" * pad)
    return (IMAGE_SITE % "CE").replace("if(CE())try{", "if(CE())try{" + filler)


@pytest.mark.parametrize("pad,gap,rewritten", [
    (0, 140, 1),      # the real-world shape, unpadded
    (257, 397, 1),    # `if(` starts exactly on the window boundary
    (258, 398, 0),    # one byte past it: the whole shape must fit in the window
    (900, 1040, 0),   # comfortably outside it
])
def test_the_backwards_search_is_bounded(postprocess, pad, gap, rewritten):
    """The window is what stops the search wandering into a neighbouring
    function. Deliberately written against fixed byte distances rather than
    against IMAGE_SHIM_WINDOW itself: a test that derives its own padding from
    the constant passes for every value of the constant, including one wide
    enough to reach the ripgrep gate.

    `gap` is measured from the CALL, so the boundary sits at 397 and not 400:
    the search matches the whole `if(CE())try{` shape, whose first three bytes
    precede the call.

    Measured for scale: in the real linux entry module the next gate call up
    from the image one is 506,792 bytes away, and in the darwin one 1,732,905.
    """
    site = _image_site_with_gap(pad)
    call_start = site.index("if(CE())try{") + len("if(")
    assert site.index(IMAGE_ANCHOR_TEXT) - call_start == gap, "fixture drifted"

    out, counts = postprocess.transform(
        _module(sites=(RIPGREP_SITE,)).replace(TAIL, site + TAIL))

    assert counts["gate_name"] == "CE"
    assert counts["image_shim"] == rewritten
    if not rewritten:
        assert "no if(CE())try{" in counts["image_shim_reason"]
        assert RIPGREP_SITE % "CE" in out, "the ripgrep gate was taken instead"
    assert postprocess.check(out, counts) == []


# --- the invariant is fatal --------------------------------------------------

@pytest.mark.parametrize("before,after,applied,why", [
    (21, 19, 1, "the rewrite hit two sites while claiming one"),
    (21, 21, 1, "it claims a rewrite that did not happen"),
    (21, 20, 0, "a call site vanished with no rewrite claimed"),
    (21, 0, 1, "the global flip this shim exists to avoid"),
    # The two below are the `applied not in (0, 1)` guard's OWN inputs: their
    # arithmetic BALANCES (19 == 21 - 2, 18 == 21 - 3), so the count comparison
    # waves them through and only the licensed-to-rewrite-one guard objects.
    # The previous fixture here was (21, 20, 2), which the arithmetic already
    # rejected - it never reached the guard, and deleting the guard left the
    # whole suite green.
    (21, 19, 2, "two rewrites, balanced - more than the one site licensed"),
    (21, 18, 3, "three rewrites, balanced - the same, further out"),
])
def test_a_broken_count_invariant_is_fatal(postprocess, before, after, applied, why):
    """Half the safety argument for a text rewrite of a 23 MB minified file -
    the other half is picking the right site to begin with, which no count can
    check. In every one of these outcomes the bookkeeping and the file
    disagree, so nobody can say which gates are still false, and the first one
    a spreading substitution would reach is embedded ripgrep. Fatal: the build
    stops and cli.original.cjs is never written."""
    out, counts = postprocess.transform(_module())
    counts.update(gate_calls_before=before, gate_calls_after=after,
                  image_shim=applied)

    errors = postprocess.check(out, counts)

    assert any("image shim accounting" in e for e in errors), (why, errors)
    assert any("ripgrep" in e for e in errors), "the message must name the harm"


def test_the_over_licensed_rewrite_message_does_not_contradict_itself(postprocess):
    """(21, 19, 2) balances, so the arithmetic branch's phrasing - "went
    21 -> 19 (expected 19)" - would report the expected value as the cause of
    a failing build. A maintainer reading that looks for a bug in check()
    rather than at their own rewrite, so the two conditions get two messages."""
    out, counts = postprocess.transform(_module())
    counts.update(gate_calls_before=21, gate_calls_after=19, image_shim=2)

    errors = postprocess.check(out, counts)

    assert len(errors) == 1
    assert "expected 19" not in errors[0], "reported and expected values agree"
    assert "at most ONE" in errors[0]


def test_the_honest_counts_are_not_reported_as_a_violation(postprocess):
    """The companion to the parametrised failures above: without this, a
    check() that flagged every input would pass all of them."""
    out, counts = postprocess.transform(_module())
    assert (counts["gate_calls_before"], counts["gate_calls_after"],
            counts["image_shim"]) == (3, 2, 1)

    assert postprocess.check(out, counts) == []


@pytest.mark.parametrize("replacement,live_after,fatal", [
    ("true", 2, False),                 # today's constant: one call site went
    ("CE()", 3, True),                  # a rewrite that puts the call back
    ("true||CE()||CE()", 4, True),      # a rewrite that multiplies call sites
])
def test_the_after_count_is_measured_on_the_rewritten_code(
        postprocess, monkeypatch, replacement, live_after, fatal):
    """`gate_calls_after` must be COUNTED on the output, never derived from it.

    Every other test of check() hands it its counts, so the validator is
    thoroughly covered while the thing that PRODUCES the numbers was not
    covered at all - and that is enough to disarm check()'s condition (f)
    permanently. Reproduced on this host 2026-08-24 before this test existed:
    `_apply_image_shim` returning `before - 1` in place of the recount left
    `200 passed`, and a rewrite that then flipped EVERY gate call in the real
    22,960,130-byte linux-x64 2.1.222 entry module reported
    `gate=CE before=21 after=20 image_shim=1` with `check() errors: []` over an
    artifact with 0 CE() call sites left - the global flip that breaks Grep,
    shipped as one tidy scoped rewrite. With the real recount in place the same
    spreading rewrite reports `after=0` and check() fires.

    IMAGE_SHIM_REPLACEMENT is the seam the perturbation goes through: it stands
    in for any future rewrite that removes more or fewer gate calls than the
    one site it was aimed at. Row one is today's constant and must stay clean;
    the other two can only be caught by a number measured on the text that came
    out, and `_live_gate_calls` recounts it here independently.
    """
    monkeypatch.setattr(postprocess, "IMAGE_SHIM_REPLACEMENT", replacement)

    out, counts = postprocess.transform(_module())

    assert counts["image_shim"] == 1
    assert _live_gate_calls(out) == live_after, "the fixture drifted"
    assert counts["gate_calls_after"] == live_after, (
        "the reported after-count is not the number of CE() calls left in the "
        "output - it was assumed, not measured")
    errors = postprocess.check(out, counts)
    assert bool(errors) is fatal, errors
    if fatal:
        assert any("image shim accounting" in e for e in errors)
        assert any("ripgrep" in e for e in errors), "the message must name the harm"


def test_a_rewrite_claimed_against_an_unidentified_gate_is_fatal(postprocess):
    """No gate named means no count, and "no arithmetic to do" must not become
    "nothing to check": `applied` > 0 with unknown counts is a rewrite nothing
    measured, over a file whose gate calls nobody enumerated. Unreachable from
    _apply_image_shim, which returns the code untouched on every path that
    fails to name a gate - which is exactly why check() has to say so rather
    than trust it, and why the counts are None here instead of the 0 they used
    to be."""
    out, counts = postprocess.transform(_module(gate_def=GATE_DEF_ARROW))
    assert (counts["gate_name"], counts["gate_calls_before"],
            counts["gate_calls_after"]) == (None, None, None)
    assert postprocess.check(out, counts) == []

    counts["image_shim"] = 1
    errors = postprocess.check(out, counts)

    assert any("image shim accounting" in e for e in errors), errors
    assert any("ripgrep" in e for e in errors), "the message must name the harm"


# --- the opt-out -------------------------------------------------------------

def test_the_opt_out_is_read_in_main_not_in_transform(postprocess, monkeypatch):
    """transform() must stay a pure function of its arguments: the A/B in
    docs/findings.md §10 is driven from one process, and an env var read down
    here would make the two halves depend on interpreter state."""
    monkeypatch.setenv("NRC_NO_IMAGE_SHIM", "1")

    _, counts = postprocess.transform(_module())

    assert counts["image_shim"] == 1, "transform() honoured the environment"


def test_the_opt_out_still_measures_the_gate(postprocess):
    """The unshimmed build must report the same numbers as the shimmed one,
    minus the rewrite. Reporting `None` for the gate would make the two halves
    of the A/B distinguishable by their build logs for the wrong reason."""
    _, counts = postprocess.transform(_module(), image_shim=False)

    assert counts["gate_name"] == "CE"
    assert counts["gate_calls_before"] == counts["gate_calls_after"] == 3
    assert counts["image_shim"] == 0
    assert "NRC_NO_IMAGE_SHIM" in counts["image_shim_reason"]


# --- against the real thing --------------------------------------------------
#
# Skipped, not failed, without a real binary - see tests/conftest.py's
# real_elf_binary fixture and pytest.ini's `integration` marker.

def _real_entry_source(extract_bun, path):
    import struct
    with open(path, "rb") as fh:
        buf = fh.read()
    off, size = extract_bun.find_bun_section(buf)
    payload, mod_off, _, entry = extract_bun.parse_payload(buf[off:off + size])
    size_of = extract_bun.MODULE_RECORD_SIZE
    rec = payload[mod_off + entry * size_of:mod_off + (entry + 1) * size_of]
    _, _, content_off, content_size = struct.unpack_from("<IIII", rec, 0)
    return payload[content_off:content_off + content_size].decode("utf-8", "replace")


def _assert_one_real_rewrite(postprocess, src):
    """The shim's contract against a real entry module, asserted as properties
    rather than as numbers: the exact call-site counts belong to the Claude
    build, and live in tests/test_integration.py's MEASURED drift tripwire."""
    out, counts = postprocess.transform(src)

    assert counts["gate_name"] is not None, "no isStandaloneExecutable gate found"
    assert counts["image_shim"] == 1
    assert counts["gate_calls_after"] == counts["gate_calls_before"] - 1
    assert counts["gate_calls_before"] > 1, "a lone call site proves nothing"
    # ...and that after-count is the number of calls the 23 MB of output really
    # holds, recounted here without postprocess's own counter. before - 1 is
    # what the reported number is SUPPOSED to equal; this is the check that it
    # was measured rather than computed from that expectation.
    assert _live_gate_calls(out, counts["gate_name"]) == counts["gate_calls_after"]
    assert _live_gate_calls(src, counts["gate_name"]) == counts["gate_calls_before"]
    assert postprocess.check(out, counts) == []
    return out, counts


def _assert_only_the_image_gate_moved(postprocess, src):
    """Reconstruct the shimmed output by undoing the single rewrite and demand
    it equal the unshimmed output. Every other gate call site in the binary -
    ripgrep, the seccomp sandbox, the installer identity, the two MCP
    self-spawns, the telemetry flag - is covered by this one comparison."""
    shimmed, counts = postprocess.transform(src)
    plain, _ = postprocess.transform(src, image_shim=False)

    offset, removed, inserted = _single_edit(plain, shimmed)
    assert removed == counts["gate_name"] + "()"
    assert inserted == "true"
    assert shimmed[:offset] + removed + shimmed[offset + len(inserted):] == plain
    assert len(shimmed) == len(plain), "`CE()`/`AE()` and `true` are four bytes"
    assert sum(a != b for a, b in zip(shimmed, plain)) == 4

    # and the rewrite really is the image gate, not some other call. Measured
    # on BOTH real entry modules (linux-x64 2.1.222, darwin-arm64 2.1.239): the
    # anchor sits 129 bytes past the start of the rewritten call. The bound is
    # loose so a minifier reshuffling the try body does not turn this into a
    # version tripwire.
    assert plain.index("Native image processor not available") - offset < 200
    assert shimmed[offset - 3:offset + 8] == "if(true)try", (
        "the rewritten site is not the image branch's own `if(<gate>())try{`")

    # the specific measured harm, named again on the real artifact. This exact
    # shape is present in both binaries, with CE and AE respectively.
    rg = re.compile(r'if\(r!=="rg"\)return\{mode:"system",command:r,args:\[\]\}\}'
                    r'if\(%s\(\)\)' % re.escape(counts["gate_name"]))
    assert rg.search(shimmed), "the embedded-ripgrep gate site was disturbed"


@pytest.mark.integration
def test_real_elf_entry_module_gets_exactly_one_rewrite(postprocess, extract_bun,
                                                        real_elf_binary):
    """The synthetic modules above are shapes this file chose. This is the
    23 MB of real minified JavaScript the shim actually ships against."""
    _assert_one_real_rewrite(postprocess, _real_entry_source(extract_bun,
                                                             real_elf_binary))


@pytest.mark.integration
def test_real_macho_entry_module_gets_exactly_one_rewrite(postprocess, extract_bun,
                                                          real_macho_binary):
    """The darwin build is the reason STANDALONE_DEF carries `[^{}]*?`: its
    gate reads `function AE(){return typeof Bun<"u"&&...}`. Without a test
    against the real Mach-O entry module, dropping that tolerance costs one
    synthetic assertion and silently produces an unshimmed macOS artifact."""
    _assert_one_real_rewrite(postprocess, _real_entry_source(extract_bun,
                                                             real_macho_binary))


@pytest.mark.integration
def test_real_elf_entry_module_changes_in_exactly_one_place(postprocess, extract_bun,
                                                            real_elf_binary):
    _assert_only_the_image_gate_moved(
        postprocess, _real_entry_source(extract_bun, real_elf_binary))


@pytest.mark.integration
def test_real_macho_entry_module_changes_in_exactly_one_place(postprocess, extract_bun,
                                                              real_macho_binary):
    _assert_only_the_image_gate_moved(
        postprocess, _real_entry_source(extract_bun, real_macho_binary))


# --- main() and build.sh must say which artifact this is ---------------------
#
# The two builds differ in four bytes and behave identically until someone
# Reads a large image. Silence here is the failure mode that matters most:
# nobody would notice for weeks.

import pathlib
import subprocess
import sys

import fixtures

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE_ENV = {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"}


def _run_postprocess(d, env=None, source=None):
    (d / "cli.original.js").write_text(source if source is not None else _module())
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "postprocess.py"), str(d)],
        capture_output=True, text=True, env=dict(BASE_ENV, **(env or {})))


def test_main_prints_the_gate_name_and_shim_count_on_stdout(tmp_path):
    result = _run_postprocess(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "image shim gate        : CE" in result.stdout
    assert "image shim applied     : 1" in result.stdout
    assert "3 -> 2" in result.stdout
    # and NO cause line: build.sh reads that line for its "NOT APPLIED"
    # headline, so a shimmed build has to leave it empty. Its absence also
    # keeps the build log of the default build the one docs/runbook.md quotes.
    assert "image shim not applied" not in result.stdout


def test_the_env_opt_out_skips_the_rewrite_and_warns(tmp_path):
    """NRC_NO_IMAGE_SHIM=1 regenerates the "as shipped" half of the A/B from
    this same tree. It must be visibly, noisily different in the build log,
    because the artifacts themselves are not."""
    shimmed = tmp_path / "on"
    plain = tmp_path / "off"
    shimmed.mkdir()
    plain.mkdir()

    on = _run_postprocess(shimmed)
    off = _run_postprocess(plain, env={"NRC_NO_IMAGE_SHIM": "1"})

    assert (on.returncode, off.returncode) == (0, 0), off.stderr
    assert "image shim applied     : 0" in off.stdout
    assert "image shim NOT applied" in off.stderr
    a = (shimmed / "cli.original.cjs").read_bytes()
    b = (plain / "cli.original.cjs").read_bytes()
    assert a != b
    assert len(a) == len(b), "CE() and true are both four bytes"
    assert sum(x != y for x, y in zip(a, b)) == 4


def test_a_refusal_prints_its_cause_and_invents_no_counts(tmp_path):
    """The two things a build printed about the drifted-declaration artifact
    that were not true: `image shim call sites  : 0 -> 0` for a file with
    every one of its gate calls still in it, and no statement of the cause at
    all - leaving build.sh to guess one, which it did, wrongly.

    Reproduced with a full build on this host on 2026-08-24; the module here
    is the hermetic version of the same drift."""
    result = _run_postprocess(tmp_path, source=_module(gate_def=GATE_DEF_ARROW))

    assert result.returncode == 0, result.stderr
    assert "image shim gate        : None" in result.stdout
    assert "image shim call sites  : not counted (no gate identified)" in result.stdout
    assert "0 -> 0" not in result.stdout, "a count nothing measured"
    assert "image shim applied     : 0" in result.stdout
    assert ("image shim not applied : no Bun.isStandaloneExecutable gate "
            "DECLARATION matched") in result.stdout
    # the artifact is still written - a refusal, not a failure
    assert (tmp_path / "cli.original.cjs").exists()


@pytest.mark.parametrize("value", ["1", "0", "false", "yes"])
def test_any_non_empty_opt_out_value_skips_the_rewrite(tmp_path, value):
    """The contract is shared with scripts/build.sh, which picks between its
    two "NOT APPLIED" messages with `[ -n "${NRC_NO_IMAGE_SHIM:-}" ]`. While
    postprocess.py honoured only "1" the two disagreed, and the direction of
    the disagreement is the bad one: NRC_NO_IMAGE_SHIM=true shimmed the
    artifact and build.sh announced a deliberate opt-out, so a shim that
    genuinely could not find its gate would be reported as intentional and
    nobody would look. "0" and "false" are in here because they are the
    values a reader most expects to mean "shim it anyway"."""
    result = _run_postprocess(tmp_path, env={"NRC_NO_IMAGE_SHIM": value})

    assert result.returncode == 0, result.stderr
    assert "image shim applied     : 0" in result.stdout
    assert "(opt-out: NRC_NO_IMAGE_SHIM set)" in result.stdout
    assert "NRC_NO_IMAGE_SHIM" in result.stderr


def test_an_empty_opt_out_value_is_not_an_opt_out(tmp_path):
    """`NRC_NO_IMAGE_SHIM=` is how a shell unsets a variable in place, and
    build.sh's `-n` reads it as "not set". Both must shim."""
    result = _run_postprocess(tmp_path, env={"NRC_NO_IMAGE_SHIM": ""})

    assert result.returncode == 0, result.stderr
    assert "image shim applied     : 1" in result.stdout


def _synthetic_binary(path, entry):
    payload = fixtures.build_payload([("/$bunfs/root/cli", entry, 1)])
    path.write_bytes(fixtures.build_elf(payload))
    return path


def _build_env(out_dir, env=None):
    """Exposed so a test can assert on the HOME a build really uses; see
    test_a_home_littered_by_the_platform_stays_out_of_the_output_dir."""
    return dict(BASE_ENV, HOME=str(fixtures.scratch_home(out_dir)),
                OUT_DIR=str(out_dir), BUN_BIN="/nonexistent/bun", **(env or {}))


def _build(out_dir, native, env=None):
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "build.sh"), str(native)],
        capture_output=True, text=True,
        env=_build_env(out_dir, env))


def test_build_sh_reports_the_shim_as_applied(tmp_path):
    native = _synthetic_binary(tmp_path / "native", _module().encode())

    result = _build(tmp_path / "out", native)

    assert result.returncode == 0, result.stderr
    assert "image shim APPLIED" in result.stdout
    assert "NOT APPLIED" not in result.stdout + result.stderr


def test_build_sh_reports_the_shim_as_not_applied(tmp_path):
    """No anchor in this entry module, so the shim finds nothing. The build
    still succeeds - that is the point - which is exactly why it has to say so
    out loud."""
    native = _synthetic_binary(
        tmp_path / "native", _module(sites=(RIPGREP_SITE,)).encode())

    result = _build(tmp_path / "out", native)

    assert result.returncode == 0, result.stderr
    assert "image shim NOT APPLIED" in result.stderr
    assert "image shim APPLIED" not in result.stdout
    # and the headline names THIS refusal's cause - the anchor - rather than a
    # single cause for every refusal alike
    assert "the anchor" in result.stderr
    assert "not declaration drift" in result.stderr


def test_build_sh_names_the_declaration_when_the_declaration_drifted(tmp_path):
    """build.sh's closing headline must not name a cause of its own.

    It used to end every refusal with "Most likely a new Claude release
    renamed the anchor string". Measured false on 2026-08-24 by building a
    copy of the linux-x64 2.1.222 binary whose 53-byte gate declaration had
    been replaced in place with the equal-length GATE_DEF_ARROW form: that
    build printed exactly that line while its own artifact still held the
    anchor exactly once and 21 live `CE()` calls. The anchor cannot even be
    the cause here - the gate is resolved first, so the search never reached
    it - and the reader it sends to re-measure IMAGE_ANCHOR is the reader who
    then has to find STANDALONE_DEF on their own.

    The headline now quotes postprocess.py's reason, which is the only place
    the cause is worked out."""
    native = _synthetic_binary(
        tmp_path / "native", _module(gate_def=GATE_DEF_ARROW).encode())
    out = tmp_path / "out"

    result = _build(out, native)

    assert result.returncode == 0, result.stderr
    assert ("image shim NOT APPLIED: no Bun.isStandaloneExecutable gate "
            "DECLARATION matched") in result.stderr
    assert "renamed the anchor string" not in result.stderr
    assert "0 -> 0" not in result.stdout
    # the artifact this build just made, which is what the old headline was
    # wrong about: the anchor and the image branch are both still in it
    artifact = (out / "extract" / "cli.original.cjs").read_text()
    assert artifact.count("Native image processor not available") == 1
    assert artifact.count("if(CE())try{") == 1


def test_build_sh_leaves_no_postprocess_log_in_the_output_dir(tmp_path):
    """The tee'd log lives inside the staging directory so the swap disposes
    of it. A stray file in OUT_DIR would also break the build script's own
    "nothing left behind" test."""
    native = _synthetic_binary(tmp_path / "native", _module().encode())
    out = tmp_path / "out"

    assert _build(out, native).returncode == 0

    assert sorted(p.name for p in out.iterdir()) == ["extract"]
    assert list(out.rglob(".postprocess.log")) == []


def test_a_home_littered_by_the_platform_stays_out_of_the_output_dir(tmp_path):
    """The macOS half of the same defect test_build_script.py pins: this file's
    build helper conflated HOME with OUT_DIR too, so ~/Library landed in the
    directory the assertion above walks. Reported from a real Mac 2026-08-24."""
    native = _synthetic_binary(tmp_path / "native", _module().encode())
    out = tmp_path / "out"
    home = pathlib.Path(_build_env(out)["HOME"])
    home.mkdir(parents=True, exist_ok=True)
    (home / "Library").mkdir()

    assert _build(out, native).returncode == 0

    assert sorted(p.name for p in out.iterdir()) == ["extract"]
