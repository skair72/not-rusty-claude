"""Running the extracted artifact under stock Node instead of Bun.

Bun is the oracle everywhere here: every assertion compares two runs of the
same input rather than a hardcoded expectation, so a Claude release that
changes its help text moves both sides at once and nothing has to be edited.

What this pins:
  - `--version`, `mcp list` and `--help` produce byte-identical stdout and the
    same exit code under Node + scripts/bun-shim.cjs as under Bun.
  - the shim's Bun.* stand-ins answer exactly what Bun answers, api by api,
    through tests/bun_shim_probe.cjs - one file run by both runtimes. That
    includes the width table on every one of the 1,114,112 code points.
  - where the shim cannot match Bun it THROWS rather than answers, and the
    count of those refusals is pinned so one cannot quietly become an answer.
  - the two places where it answers differently instead - both representation-
    dependent, both written up in the shim's header - are pinned case by case,
    Bun's answer beside the shim's, so neither side can move quietly.
  - the APIs the shim does NOT implement throw an error naming themselves, and
    the names it leaves undefined stay undefined - because the bundle
    feature-detects them and a stub would take the wrong branch, or, for
    Bun.ant, because stock Bun does not define it either.

Needs a Node >= 24 plus `ws` and `undici`; each is a separate skip naming the
env var that fixes it. See tests/conftest.py.
"""

import json
import os
import pathlib
import re
import shlex
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIM = ROOT / "scripts" / "bun-shim.cjs"
PROBE = ROOT / "tests" / "bun_shim_probe.cjs"

TIMEOUT = 300

# stringWidth is the one entry that approximates. Measured 2026-08-25 against
# Bun 1.3.14 with the probe's own corpora, identically on Node 24.0.0, 24.19.0,
# 25.0.0, 25.9.0 and 26.7.0: 0 mismatches on the realistic corpus, 0 on all
# 1,114,112 single code points, 236 of 4,235 on the adversarial one. The bound
# is an upper bound - getting closer to Bun is fine, drifting away is the
# regression this catches. It moves when the corpus does: adding the C1 ST atom
# (U+009C) grew the corpus and took this from 268 to 236, re-measured on all
# five Node versions above.
ADVERSARIAL_MISMATCH_BOUND = 236

# Where the shim REFUSES an input Bun answers. Each of these is the safe
# direction of the rule the shim obeys - match Bun or throw - but a refusal is
# still a difference, so the count is pinned and the direction is asserted: the
# test below fails if the shim ever ANSWERS one of these instead, which is the
# lie the rule exists to prevent. Every count below was measured on 2026-08-26
# and is identical on all five Node versions above.
#
#   semver.order-invalid  26 of the probe's 50 malformed versions. Bun.semver
#                         .order is a RANGE parser: it answers "^1.2.3" with a
#                         value ABOVE "2.0.0", sorts "x", "*" and a bare "   "
#                         above every real version, reads "1.2.3junk" as a
#                         prerelease but "1.2.3xx" as two wildcards, makes
#                         "1.2.30x" mean 1.2.30, accepts "1.2-rc" while
#                         throwing on "1-rc", and orders numeric components
#                         past 2^64 incoherently. The shim names each one.
#   semver.order-nonascii 36 of 36. One code point above U+007F anywhere in the
#                         string and Bun stops comparing: the same string is 0
#                         against "0.0.0" AND against "2.0.0", which are not
#                         equal to each other. Every code point in
#                         U+0080..U+20FF was tried and all 8320 behave that way,
#                         so this is not "Bun swallows exotic whitespace" - the
#                         reading it would be easy to implement by mistake.
#   stringWidth-options   33 of 80. The two options do not coerce the same way
#                         and neither is plain `=== true`: countAnsiEscapeCodes
#                         is ToBoolean, so 1, "no", {} and `new Boolean(false)`
#                         all turn it on, while ambiguousIsNarrow goes wide only
#                         for `false` and a zero-or-NaN number or bigint - it
#                         keeps the default for null and for "". The guards used
#                         to compare against the literal boolean, so 27 of these
#                         were answered from the wrong table instead of refused
#                         - measured by re-running the probe against the old
#                         guards. The 47 that MATCH are the other half of the
#                         fix: a guard spelled `!options.ambiguousIsNarrow` would
#                         refuse null and "" where Bun quietly stays narrow, and
#                         the old ones refused an empty string, which Bun
#                         answers 0 without reading the options at all.
#   hash-refused           9 of 9: Bun stringifies a non-string input and
#                         quietly seeds with 0 for a Number seed it cannot
#                         represent (see the shim's note).
#   deepEquals-symbols    24 of 25 pairs: a symbol-keyed object is not a JSON
#                         value, and Bun compares symbol keys.
#   deepEquals-nonenum    20 of 20. A non-enumerable property is read through
#                         [[Get]] when it sits on the RIGHT and is invisible on
#                         the left, so deepEquals({a:1}, hidden_a_1) is true and
#                         the swap is false - and no single rule covers both
#                         that and the rest of the corpus (see the shim's note).
#   deepEquals-representation
#                         10 of 16, and the other 6 are controls that must
#                         MATCH. {x:1} and {y:undefined,x:1} are equal; give
#                         both an identical "0" key and they are not. The
#                         equality depends on JSC's internal representation, so
#                         the rows carrying an index key or a getter are refused
#                         while the plain ones are still answered.
REFUSAL_DIVERGENCES = {
    "semver.order-invalid": 26,
    "semver.order-nonascii": 36,
    "stringWidth-options": 33,
    "hash-refused": 9,
    "deepEquals-symbols": 24,
    "deepEquals-nonenum": 20,
    "deepEquals-representation": 10,
}

# The two places where the shim ANSWERS something other than Bun rather than
# throwing. The first cannot be reproduced from the arguments at all - it turns
# on how JSC is storing the string, and two strings that are `===` get two
# answers. The second could be imitated, but only by statting a different string
# from the one handed back, so it is written down instead. The shim's header
# carries the reasoning for both. Pinned here case by case - Bun's answer beside
# the shim's - and every OTHER case in these two groups must match, so a Bun or
# Node release that fixes, widens or shifts one of them fails this test instead
# of drifting past unnoticed.
#
#   stringWidth-csi-representation
#       Bun ends a CSI at 0x40..0x7E in a Latin-1 string and at any code point
#       >= 0x40 except 0x7F in a 16-bit one, so `"A" ESC "[" CJK "B"` is 2 to
#       Bun and 1 to the shim while the same shape in Latin-1 agrees. Needs a
#       malformed CSI in a string JSC holds as 16-bit - which is NOT the same as
#       "contains a code point >= U+0100": a Latin-1-only string decoded from
#       bytes (TextDecoder, Buffer.toString, readFileSync) also qualifies, and
#       is `===` to a literal that does not. The well-formed SGR rows in the
#       group are the controls that must match.
#   which-nul
#       Bun stats the name as a C string, so a NUL truncates it at the syscall
#       and Bun answers with a path that still carries the tail. Node's fs
#       rejects a NUL in a path outright, so the shim answers null. The other
#       NUL placements in the group already agree.
KNOWN_DIVERGENCES = {
    "stringWidth-csi-representation": {
        "16-bit, CJK inside the CSI": (2, 1),
        "16-bit, U+00FF inside and CJK after": (4, 3),
        "16-bit, U+0100 after (the boundary)": (3, 2),
    },
    "which-nul": {
        "absolute name, NUL and a tail": ("<FIXTURE>/bin/exe1\x00zzz", None),
        "absolute name, trailing NUL": ("<FIXTURE>/bin/exe1\x00", None),
        "PATH lookup, NUL and a tail": ("<FIXTURE>/bin/exe1\x00zzz", None),
    },
}

# Names the shim leaves undefined that stock Bun 1.3.14 defines - measured by
# the probe's `surface` group rather than assumed. Bun.ant is NOT among them:
# stock Bun does not have it either, so the shim must not invent it.
SURFACE_DIVERGENCES = 5

EXACT_GROUPS = ("stringWidth-realistic", "stringWidth-coercion", "stripANSI",
                "stripANSI-coercion", "hash", "hash-seeded", "hash-seeded-number",
                "semver.order", "semver.order-arity", "semver.order-coercion",
                "semver.order-grammar", "which", "deepEquals", "deepEquals-arrays",
                "deepEquals-arity")


def _env(home, config_dir, node_path=None):
    """A run environment that touches no real config and opens no sockets.

    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC is not decoration: without it the
    CLI opens non-loopback sockets on startup, which scripts/ab-equivalence.sh
    exists to prove it does not do here.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    if node_path:
        env["NODE_PATH"] = node_path
    return env


def _run(argv, env, cwd=None):
    return subprocess.run(argv, env=env, cwd=cwd, capture_output=True,
                          timeout=TIMEOUT)


@pytest.fixture(scope="session")
def node_env(node_bin, ws_module, undici_module, built_artifact):
    """Everything the Node side needs, or a skip naming what is missing."""
    assert ws_module == undici_module, "ws and undici must share one node_modules"
    return {"node": node_bin, "modules": ws_module, "artifact": built_artifact}


def _both(node_env, bun_bin, tmp_path, args):
    """Run the same CLI command under Bun and under Node + shim.

    Separate HOME and CLAUDE_CONFIG_DIR per side so neither run can see state
    the other left behind - otherwise the second run could match the first for
    the wrong reason.
    """
    results = {}
    for name, argv in (
        ("bun", [bun_bin, node_env["artifact"], *args]),
        ("node", [node_env["node"], "--require", str(SHIM),
                  node_env["artifact"], *args]),
    ):
        home = tmp_path / name / "home"
        cfg = tmp_path / name / "config"
        home.mkdir(parents=True, exist_ok=True)
        cfg.mkdir(parents=True, exist_ok=True)
        node_path = node_env["modules"] if name == "node" else None
        results[name] = _run(argv, _env(home, cfg, node_path))
    return results["bun"], results["node"]


def _assert_same(bun, node, args):
    assert node.returncode == bun.returncode, (
        f"`{' '.join(args)}` exited {node.returncode} under Node and "
        f"{bun.returncode} under Bun; node stderr:\n"
        f"{node.stderr.decode('utf-8', 'replace')[-2000:]}")
    assert node.stdout == bun.stdout, (
        f"`{' '.join(args)}` stdout differs: {len(bun.stdout)} bytes under Bun, "
        f"{len(node.stdout)} under Node")
    # stderr too, although the contract this PR claims is stdout-only: measured
    # 2026-08-25, all four commands emit byte-identical stderr under both
    # runtimes (`config ls` writes 157 bytes of stdin-timeout warning, the rest
    # write nothing), so the assertion is free today and catches the thing it
    # is worth catching - Node deprecation warnings, or a chatty shim, leaking
    # into a stream users pipe.
    assert node.stderr == bun.stderr, (
        f"`{' '.join(args)}` stderr differs: {bun.stderr!r} under Bun, "
        f"{node.stderr!r} under Node")


@pytest.mark.parametrize("args", [["--version"], ["mcp", "list"], ["--help"]])
def test_stdout_is_byte_identical_to_bun(node_env, bun_bin, tmp_path, args):
    """The whole point: Node produces exactly what Bun produces.

    All three, because they need different amounts of the shim. Measured on
    2026-08-25 by running each with `globalThis.Bun = {}`: `--version` prints
    its 22 bytes and exits 0 with no Bun at all, `mcp list` exits 0 printing a
    single newline (1 byte, where Bun prints 65), and `--help` exits 1. So the
    exit code alone would wave `mcp list` through with a completely broken shim
    - the stdout comparison is what catches it.

    What these do NOT pin is the width table. All 16,890 bytes of --help are
    ASCII, where character count and column count are the same number, so
    replacing stringWidth with `s.length` still renders it byte-identically -
    confirmed by mutation. test_shim_api_matches_bun_exactly is what catches
    that; these three catch the shim being absent, broken or throwing.
    """
    bun, node = _both(node_env, bun_bin, tmp_path, args)
    assert bun.returncode == 0, bun.stderr.decode("utf-8", "replace")[-2000:]
    assert bun.stdout, "the Bun oracle printed nothing; nothing to compare"
    _assert_same(bun, node, args)


def test_config_ls_fails_the_same_way(node_env, bun_bin, tmp_path):
    """A NON-zero exit has to match too.

    Comparing only successful commands would let a shim that crashes early look
    fine on anything that was going to fail anyway. `config ls` without
    credentials exits 1 under both, and the message must be the same message.
    """
    args = ["config", "ls"]
    bun, node = _both(node_env, bun_bin, tmp_path, args)
    assert bun.returncode != 0, "expected `config ls` to fail without credentials"
    _assert_same(bun, node, args)


@pytest.fixture(scope="session")
def which_fixture(tmp_path_factory):
    """One directory tree, shared by both probe runs.

    Bun.which hands back the path it was given - absolute if it was asked with
    an absolute one, relative if it was not - so the two runtimes have to be
    asked about the same directory, and the probe chdirs into cwd/ before the
    relative cases so "./myexe" means the same file on both sides.
    """
    root = tmp_path_factory.mktemp("which")
    (root / "bin" / "adir").mkdir(parents=True)
    (root / "bin2").mkdir()
    (root / "cwd").mkdir()
    for path in (root / "bin" / "exe1", root / "bin2" / "exe1",
                 root / "bin2" / "exe2", root / "rel", root / "cwd" / "myexe"):
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    noexec = root / "bin" / "noexec"
    noexec.write_text("x")
    noexec.chmod(0o644)
    return root


@pytest.fixture(scope="session")
def probe_results(node_bin, bun_bin, which_fixture, tmp_path_factory):
    """tests/bun_shim_probe.cjs run under both runtimes, parsed and zipped.

    Deliberately does not need the artifact: this measures the shim itself, so
    it still runs on a host that has never built anything.
    """
    home = tmp_path_factory.mktemp("probe")
    env = _env(home, home)
    out = {}
    for name, argv in (
        ("bun", [bun_bin, str(PROBE), str(which_fixture)]),
        ("node", [node_bin, "--require", str(SHIM), str(PROBE), str(which_fixture)]),
    ):
        proc = _run(argv, env)
        assert proc.returncode == 0, (
            f"the probe failed under {name}: "
            f"{proc.stderr.decode('utf-8', 'replace')[-2000:]}")
        out[name] = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines()]
    assert out["bun"], "the probe emitted nothing under Bun"
    assert len(out["bun"]) == len(out["node"])
    pairs = []
    for expected, got in zip(out["bun"], out["node"]):
        assert expected[:2] == got[:2], "the two runs disagree about the corpus"
        pairs.append((expected[0], expected[1], expected[2], got[2]))
    return pairs


def _group(pairs, group):
    rows = [p for p in pairs if p[0] == group]
    assert rows, f"the probe emitted no {group} cases"
    return rows


@pytest.mark.parametrize("group", EXACT_GROUPS)
def test_shim_api_matches_bun_exactly(probe_results, group):
    """The entries the shim claims to implement faithfully, api by api.

    Split per group rather than asserted as one number so a failure says which
    stand-in drifted instead of just "something differs".
    """
    rows = _group(probe_results, group)
    wrong = [(key, want, got) for _, key, want, got in rows if want != got]
    assert not wrong, (
        f"{len(wrong)} of {len(rows)} {group} cases differ from Bun; "
        f"first three: {wrong[:3]}")


def test_string_width_residual_is_no_worse_than_measured(probe_results):
    """stringWidth approximates on pathological input; hold the line at what was measured.

    Bun resolves some (base, combining mark) pairs from a two-dimensional table
    the shim does not have. On text the CLI actually renders this never shows
    up - the realistic corpus and --help are exact - so the residual is pinned
    rather than required to be zero, and a widening one fails here.
    """
    rows = _group(probe_results, "stringWidth-adversarial")
    wrong = [r for r in rows if r[2] != r[3]]
    assert len(wrong) <= ADVERSARIAL_MISMATCH_BOUND, (
        f"{len(wrong)} of {len(rows)} adversarial stringWidth cases differ from "
        f"Bun, was {ADVERSARIAL_MISMATCH_BOUND} when measured; "
        f"first three: {[(r[1], r[2], r[3]) for r in wrong[:3]]}")


@pytest.mark.parametrize("group", sorted(REFUSAL_DIVERGENCES))
def test_the_shim_only_ever_refuses_what_it_cannot_match(probe_results, group):
    """Where the shim and Bun disagree, the shim must be the one that THROWS.

    Bun sorts "x", "*" and a bare "   " above every real version and puts
    "^1.2.3" above "2.0.0"; a single code point above U+007F makes it answer 0
    against every version at once; it stringifies whatever you hand Bun.hash;
    it compares symbol keys; and its object comparison reads a non-enumerable
    property on the right-hand side but not on the left. The shim refuses each
    of those rather than guess. That is the safe direction,
    but it IS a difference, so the count is pinned here - and the direction is
    asserted, which is the part that matters: the day one of these starts
    answering instead of throwing, this test says so, and that is exactly the
    silent-wrong-answer failure the shim exists to avoid.

    A widening the other way - the shim ACCEPTING something Bun rejects - shows
    up here too. `Bun.semver.order("1.2.3\n", "1.2.3")` throws under Bun; a
    .trim() in the shim used to answer 0, and that row lands in `wrong` with
    got=0, failing the assertion below.
    """
    rows = _group(probe_results, group)
    wrong = [r for r in rows if r[2] != r[3]]
    for _, key, want, got in wrong:
        assert str(got).startswith("throw"), (
            f"{group}: {key!r} - Bun answers {want!r} and the shim answers "
            f"{got!r}. A different answer is the one thing it may not do; "
            f"refusing is allowed, guessing is not.")
    assert len(wrong) == REFUSAL_DIVERGENCES[group], (
        f"{len(wrong)} {group} divergences, expected "
        f"{REFUSAL_DIVERGENCES[group]}: {[(r[1], r[2], r[3]) for r in wrong][:6]}")


@pytest.mark.parametrize("group", sorted(KNOWN_DIVERGENCES))
def test_known_divergences_are_exactly_what_was_measured(probe_results, group):
    """The two answers the shim gives that are not Bun's, pinned as such.

    Not a bound and not a count: the exact pair of answers, per case. One comes
    from how JSC is storing the string, which nothing in JS can see; the other
    from the C-string boundary under Bun's stat, which could only be imitated by
    statting a different string from the one handed back. So the honest thing is
    to write down what each side says today and fail when either moves. Every
    other case in the same group, including the well-formed sequences and the
    Latin-1 controls, must match.
    """
    expected = KNOWN_DIVERGENCES[group]
    rows = _group(probe_results, group)
    for _, key, want, got in rows:
        if key in expected:
            assert (want, got) == expected[key], (
                f"{group}: {key!r} was measured as Bun {expected[key][0]!r} / "
                f"shim {expected[key][1]!r} and is now Bun {want!r} / shim "
                f"{got!r}. A documented divergence moved - re-measure it and "
                f"update the shim's header, do not just re-pin the number.")
        else:
            assert want == got, (
                f"{group}: {key!r} is a NEW divergence - Bun {want!r}, shim "
                f"{got!r}. This group is meant to hold exactly the documented "
                f"ones; a new one needs writing up, not adding to the table.")
    seen = {key for _, key, _, _ in rows}
    assert not sorted(set(expected) - seen), (
        f"{sorted(set(expected) - seen)} are pinned as {group} divergences but "
        f"the probe no longer asks about them")


# Every spelling of the two stringWidth options, and whether the guard has to
# fire. Bun 1.3.14 is what these were measured against - countAnsiEscapeCodes is
# plain ToBoolean, ambiguousIsNarrow is ToBoolean with undefined, null and ""
# pulled back to the default - and the probe above compares them to the oracle
# directly. This list repeats the same question with NO Bun involved, so the
# guards stay covered on a host that only has Node.
_AMB = "'\\u03b1'"                       # U+03B1, ambiguous: 1 narrow, 2 wide
_SGR = "'\\u001b[31mfoo\\u001b[0m'"       # 3 with the escapes skipped
_NARROW = "stringWidth({ambiguousIsNarrow:false})"
_COUNT = "stringWidth({countAnsiEscapeCodes:true})"
OPTION_GUARDS = [
    # Spellings Bun reads as the unsupported setting: each must name the API.
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: false}})", ["throw", _NARROW]),
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: 0}})", ["throw", _NARROW]),
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: -0}})", ["throw", _NARROW]),
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: NaN}})", ["throw", _NARROW]),
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: 0n}})", ["throw", _NARROW]),
    (f"Bun.stringWidth({_SGR}, {{countAnsiEscapeCodes: true}})", ["throw", _COUNT]),
    (f"Bun.stringWidth({_SGR}, {{countAnsiEscapeCodes: 1}})", ["throw", _COUNT]),
    (f"Bun.stringWidth({_SGR}, {{countAnsiEscapeCodes: 'no'}})", ["throw", _COUNT]),
    (f"Bun.stringWidth({_SGR}, {{countAnsiEscapeCodes: {{}}}})", ["throw", _COUNT]),
    (f"Bun.stringWidth({_SGR}, {{countAnsiEscapeCodes: new Boolean(false)}})", ["throw", _COUNT]),
    # Both unsupported at once. Bun reads countAnsiEscapeCodes FIRST (measured
    # with two counting getters), so that is the one the shim must name - a
    # swapped pair of guards passes every count in the probe and fails here.
    (f"Bun.stringWidth({_SGR}, {{ambiguousIsNarrow: false, countAnsiEscapeCodes: true}})",
     ["throw", _COUNT]),
    # ...and the spellings Bun reads as the DEFAULT, which must still answer.
    # A guard written as `!options.ambiguousIsNarrow` would refuse the first two
    # of these, where Bun quietly stays narrow.
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: null}})", ["ok", 1]),
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: ''}})", ["ok", 1]),
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: 'no'}})", ["ok", 1]),
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: new Boolean(false)}})", ["ok", 1]),
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: true}})", ["ok", 1]),
    (f"Bun.stringWidth({_AMB}, {{ambiguousIsNarrow: 1}})", ["ok", 1]),
    (f"Bun.stringWidth({_SGR}, {{countAnsiEscapeCodes: false}})", ["ok", 3]),
    (f"Bun.stringWidth({_SGR}, {{countAnsiEscapeCodes: 0}})", ["ok", 3]),
    (f"Bun.stringWidth({_SGR}, {{countAnsiEscapeCodes: ''}})", ["ok", 3]),
    (f"Bun.stringWidth({_SGR}, {{countAnsiEscapeCodes: null}})", ["ok", 3]),
    (f"Bun.stringWidth({_SGR}, {{countAnsiEscapeCodes: NaN}})", ["ok", 3]),
    # An empty string is 0 before the options are looked at at all, so a guard
    # hoisted above that short-circuit throws where Bun answers.
    (f"Bun.stringWidth('', {{ambiguousIsNarrow: false}})", ["ok", 0]),
    (f"Bun.stringWidth('', {{countAnsiEscapeCodes: true}})", ["ok", 0]),
]


def test_the_option_guards_fire_on_every_spelling_bun_reads_that_way(node_bin):
    """A guard that only knows the literal boolean is a silent wrong answer.

    Both refusals were once spelled `=== false` and `=== true`. Bun does not:
    it coerces, and not even the same way for the two options. So
    `{ambiguousIsNarrow: 0}` meant Bun switched to the wide table while the shim
    answered from the narrow one and said nothing - measured, 27 of the 80
    spellings the probe now asks about did exactly that. The refusals here do
    NOT make the shim match Bun on those inputs; the options are still
    unsupported. They turn a wrong number into a named error, which is the whole
    rule this file exists to enforce. The last two rows go the other way: the
    old guards THREW on an empty string, where Bun answers 0 without ever
    reading the object.
    """
    script = (
        "require(process.argv[1]);"
        "console.log(JSON.stringify(JSON.parse(process.argv[2]).map((expr) => {"
        "  try { return ['ok', eval(expr)]; }"
        "  catch (e) { return ['throw', e && e.bunApi ? e.bunApi : 'OTHER: ' + e.message]; }"
        "})));"
    )
    proc = _run([node_bin, "-e", script, str(SHIM),
                 json.dumps([expr for expr, _ in OPTION_GUARDS])],
                {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    got = json.loads(proc.stdout)
    wrong = [(expr, want, is_) for (expr, want), is_ in zip(OPTION_GUARDS, got)
             if want != is_]
    assert not wrong, (
        f"{len(wrong)} of {len(OPTION_GUARDS)} option spellings behave wrongly; "
        f"first three: {wrong[:3]}")


def test_the_width_table_matches_bun_on_every_code_point(probe_results):
    """All 1,114,112 of them, in blocks of 1024.

    WIDTHS in the shim is a run-length encoding of Bun's own answer to
    stringWidth(String.fromCodePoint(cp)), and this is that question asked
    again. The realistic and adversarial corpora between them reach about
    25,000 code points, so a regeneration error outside that - a private-use
    plane, say, where Nerd Font glyphs live - used to pass everything;
    mutating the last RLE run's width was invisible to the whole suite.

    It also holds the ICU line. Asking \\p{RGI_Emoji} before the table put 7
    code points (Node 24.0.0) and 14 (Node 26.7.0) one column wider than Bun -
    a table that is supposed to be ICU-independent, made ICU-dependent again by
    the code that reads it. Both are zero now, and this is what keeps them zero.
    """
    rows = _group(probe_results, "stringWidth-codepoints")
    for _, key, want, got in rows:
        if want == got:
            continue
        base = int(key, 16)
        first = next(i for i, (a, b) in enumerate(zip(want, got)) if a != b)
        pytest.fail(
            f"the width table differs from Bun starting at U+{base + first:04X}: "
            f"Bun says {want[first]}, the shim says {got[first]} "
            f"(block U+{base:04X}, {sum(1 for a, b in zip(want, got) if a != b)} "
            f"code points differ in it)")


def test_the_shim_defines_no_name_stock_bun_lacks(probe_results):
    """The absent list, asked of the oracle instead of hardcoded.

    The shim leaves five names Bun defines undefined on purpose, because the
    bundle feature-detects them and "not here" is the true answer under Node.
    Every OTHER name has to agree with stock Bun - in particular Bun.ant, which
    the shipped binary's patched Bun has and stock Bun does not: defining it
    here would make the shim the only runtime in the comparison that claims it.
    """
    rows = _group(probe_results, "surface")
    wrong = [(key, want, got) for _, key, want, got in rows if want != got]
    for key, want, got in wrong:
        assert got == "undefined", (
            f"Bun.{key} is {want!r} in stock Bun and {got!r} in the shim; the "
            f"shim may leave a name out, but it may not invent one")
    assert len(wrong) == SURFACE_DIVERGENCES, (
        f"{len(wrong)} names differ from stock Bun, expected "
        f"{SURFACE_DIVERGENCES}: {wrong}")


def test_which_reads_the_path_the_process_started_with(node_bin, bun_bin, which_fixture, tmp_path):
    """Bun.which ignores later edits to process.env.PATH; so must the shim.

    Measured on Bun 1.3.14: a process launched with PATH=<dir> still answers
    <dir>/exe1 after `process.env.PATH = "/nonexistent"` and after `delete
    process.env.PATH`. Reading process.env.PATH live - which the shim did -
    turns a cleared environment into null, and null from Bun.which means "that
    binary is not installed". The bundle calls Bun.which bare to find git, node
    and the user's editor, and hooks and daemons routinely run with a stripped
    environment.

    Not part of the probe because the probe cannot choose its own launch
    environment, which is the whole point of the test.
    """
    script = ("const before = Bun.which('exe1');"
              "process.env.PATH = '/nonexistent';"
              "const replaced = Bun.which('exe1');"
              "delete process.env.PATH;"
              "const deleted = Bun.which('exe1');"
              "console.log(JSON.stringify([before, replaced, deleted]));")
    env = {"PATH": str(which_fixture / "bin"), "HOME": str(tmp_path)}
    bun = _run([bun_bin, "-e", script], env)
    node = _run([node_bin, "--require", str(SHIM), "-e", script], env)
    assert bun.returncode == 0, bun.stderr.decode("utf-8", "replace")
    assert node.returncode == 0, node.stderr.decode("utf-8", "replace")
    expected = json.loads(bun.stdout)
    assert expected[0] == str(which_fixture / "bin" / "exe1"), (
        f"the oracle did not find the fixture binary at all: {expected}")
    assert expected[2] is not None, (
        "Bun stopped keeping the launch PATH; re-measure before trusting this")
    assert json.loads(node.stdout) == expected, (
        f"Bun answered {expected} and the shim answered "
        f"{json.loads(node.stdout)} for the same three questions")


# Every Bun API the artifact can reach, and what the shim must do about it.
# A stub that returned a plausible value instead of throwing is the failure
# this repo exists to prevent, so the two lists are asserted, not documented.
THROWING = [
    ("YAML.parse", "Bun.YAML.parse('a: 1')"),
    ("YAML.stringify", "Bun.YAML.stringify({})"),
    ("TOML.parse", "Bun.TOML.parse('a = 1')"),
    ("semver.satisfies", "Bun.semver.satisfies('1.2.3', '^1.0.0')"),
    ("wrapAnsi", "Bun.wrapAnsi('a b c', 3)"),
    ("spawn", "Bun.spawn(['true'])"),
    ("file", "Bun.file('/etc/hostname')"),
    ("serve", "Bun.serve({})"),
    ("listen", "Bun.listen({})"),
    ("connect", "Bun.connect({})"),
    ("generateHeapSnapshot", "Bun.generateHeapSnapshot('v8')"),
    ("SQL", "new Bun.SQL('x')"),
    ("Transpiler", "new Bun.Transpiler({})"),
    ("stringWidth({ambiguousIsNarrow:false})",
     "Bun.stringWidth('a', {ambiguousIsNarrow: false})"),
    ("stringWidth({countAnsiEscapeCodes:true})",
     "Bun.stringWidth('a', {countAnsiEscapeCodes: true})"),
    ("deepEquals", "Bun.deepEquals(new Date(), new Date())"),
    # The third argument selects Bun's STRICT mode, which answers differently
    # from the loose mode this shim measured: Bun.deepEquals({a: undefined},
    # {}, true) is false where the default is true. Ignoring the argument and
    # answering loosely is a plausible wrong boolean, so it has to throw.
    ("deepEquals(_, _, strict)", "Bun.deepEquals({}, {}, true)"),
]

# Left undefined on purpose: the bundle feature-detects each one and takes a
# fallback when it is missing. Defining a throwing stub here would be WORSE
# than nothing - `"WebView" in Bun` and `Bun.JSONL?.parseChunk` would both
# start saying yes and then explode down a path that had a working answer.
# "ant" is here for a different reason, and it is the reason the probe asks the
# oracle instead of trusting this list: stock Bun 1.3.14 has no Bun.ant at all
# (it is patched into the Bun inside the shipped binary). Defining it was the
# one place the shim claimed a surface the oracle lacks; the three call sites
# are bare `Bun.ant.x(...)` inside try/catch, so undefined throws the same
# TypeError Bun throws, in the same place.
ABSENT = ["Terminal", "WebView", "JSONL", "version", "isStandaloneExecutable",
          "stdin", "ant"]


@pytest.mark.parametrize("api,expression", THROWING, ids=[t[0] for t in THROWING])
def test_unimplemented_apis_throw_naming_themselves(node_bin, api, expression):
    """No silent placeholder: the error has to say which Bun API was missing.

    Needs Node only - no artifact, no Bun - so this half of the contract is
    checked even on a host that cannot run the CLI at all.
    """
    script = (
        "require(process.argv[1]);"
        "try { " + expression + "; console.log('NO-THROW'); }"
        "catch (e) { console.log(e && e.bunApi ? 'API:' + e.bunApi : 'OTHER:' + e.message); }"
    )
    proc = _run([node_bin, "-e", script, str(SHIM)],
                {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    got = proc.stdout.decode("utf-8").strip()
    assert got == "API:" + api, f"expected Bun.{api} to name itself, got {got!r}"


def test_gc_really_collects_without_expose_gc(node_bin):
    """Bun.gc is the one entry that has to DO something rather than answer.

    It is called on a 1-second timer, so a version that threw would take the
    process down a second after startup, and one that silently did nothing
    would be a lie. Plain `node`, no --expose-gc: the shim reaches V8's hook
    itself, and heapUsed has to actually move after allocating garbage.
    """
    script = (
        "require(process.argv[1]);"
        "let junk = []; for (let i = 0; i < 200000; i++) junk.push({ i, s: 'x'.repeat(50) });"
        "const before = process.memoryUsage().heapUsed;"
        "junk = null;"
        "const returned = globalThis.Bun.gc(true);"
        "console.log(JSON.stringify([before > process.memoryUsage().heapUsed, returned === undefined]));"
    )
    proc = _run([node_bin, "-e", script, str(SHIM)],
                {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    collected, returns_undefined = json.loads(proc.stdout)
    assert collected, "Bun.gc did not actually collect; it is a no-op, not a stand-in"
    assert returns_undefined, ("Bun.gc must return undefined here - Bun returns a heap "
                               "size, and inventing one is the failure this repo is about")


def test_absent_apis_stay_undefined(node_bin):
    """The bundle's own fallbacks depend on these being missing."""
    script = (
        "require(process.argv[1]);"
        "console.log(JSON.stringify(" + json.dumps(ABSENT) +
        ".map((k) => [k, k in globalThis.Bun])));"
    )
    proc = _run([node_bin, "-e", script, str(SHIM)],
                {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    present = [k for k, defined in json.loads(proc.stdout) if defined]
    assert not present, (
        f"{present} are defined on the shim's Bun; the artifact feature-detects "
        f"them and a stub sends it down a path that has no working answer")


def test_the_shim_path_make_node_run_prints_is_the_one_it_runs():
    """`make node-run` used to ECHO a different --require argument than it ran.

    The echo said `scripts/bun-shim.cjs`; the recipe executed
    `$(ROOT)/scripts/bun-shim.cjs`. Those are not two spellings of one path: to
    `node --require`, a bare `scripts/bun-shim.cjs` is a PACKAGE specifier
    resolved in node_modules, so the line the target printed died with "Cannot
    find module" while the target itself worked. Reported from a real macOS host
    that copied the line it was shown.

    Read statically rather than by running `make`: a subprocess version of this
    test failed once, unreproducibly, for a reason I could not explain, and a
    test whose red I cannot account for is worse than none. This asserts the
    property directly - the printed argument and the executed one are the same
    string, and it is one Node can resolve (absolute, or explicitly relative).
    """
    recipe = (ROOT / "Makefile").read_text()
    echoed = re.findall(r'echo "==> \$\$node --require (\S+)', recipe)
    executed = re.findall(r'"\$\$node" --require (\S+)', recipe)
    assert len(echoed) == 1, f"expected one echoed --require, got {echoed}"
    assert len(executed) == 1, f"expected one executed --require, got {executed}"

    assert echoed[0] == executed[0], (
        f"make node-run prints --require {echoed[0]} but runs "
        f"--require {executed[0]}")

    path = echoed[0].strip("'\"")
    assert path.startswith("$(ROOT)/") or path.startswith("./"), (
        f"--require {path!r} is a bare specifier: node resolves it in "
        f"node_modules, not against the cwd, so the printed command cannot run")
