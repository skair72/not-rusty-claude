"""Generative differential: the shim against Bun, on inputs nobody chose.

The curated corpora next door are the reason thirteen defects survived a
"2,800 cases, byte-identical" claim. Somebody had to think of each case, and
nobody thought of multi-parameter SGR - so nothing in 2,800 cases could catch a
carry model that fabricated a nonexistent escape code out of every 256-colour
sequence.

tests/fuzz_shim.cjs generates inputs from a grammar instead. This file runs it
under both runtimes and compares. It is the only thing in the suite that can
fail on an input no human selected, which makes it the enforcement layer for
the "match exactly or refuse" contract on unforeseen input.

HOW THE PIN WORKS. wrapAnsi has known divergences that are not yet fixed. They
are pinned as a COUNT that may only decrease: the suite stays green while the
number is at or under the pin, and fails the moment it climbs. That is the same
shape as tests/test_node_runtime.py's known-divergence pin, and it exists so
this file can be wired in NOW rather than after the last fix - an enforcement
layer that is not running enforces nothing.

Determinism matters more than volume here. fuzz_shim.cjs uses a seeded
xorshift32 and no clock, so both runtimes generate byte-identical inputs and a
failure is reproducible from the seed printed in the message. A flaky
differential would be worse than none, because nobody chases a failure they
cannot reproduce.
"""

import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIM = ROOT / "scripts" / "bun-shim.cjs"
FUZZER = ROOT / "tests" / "fuzz_shim.cjs"

TIMEOUT = 300

# Cases generated per mode, per seed. Measured 2026-08-26: 8,000 cases through
# both runtimes takes 0.93s, so breadth here is close to free and the earlier
# 2,000 was timidity rather than a budget.
COUNT = 8000

# Seeds are listed, not generated, so the suite runs the same inputs on every
# machine and every day - a corpus that changes per run cannot be pinned, and a
# failure nobody can reproduce is one nobody fixes. Adding a seed is how
# coverage grows; each one is 8,000 more inputs nobody chose.
SEEDS = [1, 7, 13, 99, 1337, 20260826, 24301, 424242]

# Measured 2026-08-27 after two more fixes on top of the glue-toggle rewrite:
# (1) trailing space/tab trimming keyed on each token's immediate predecessor
# could not see PAST the first tab it could not remove, so a run like
# "<tab><space><tab><tab>" stopped at the first tab instead of finding the
# space two tokens back - the real rule cuts at the FIRST real space in the
# trailing run, keeping only what comes before it; (2) the glue-lookahead
# used by the zero-width-code-point break exemption treated a BEL-terminated
# OSC 8 event as "needs room" even when glue was never on to begin with,
# which is a no-op, not a reason to force a break - only an ST event (which
# actually grants the exemption) should short-circuit the scan. Both fixes
# dropped every seed again. May only go DOWN. Raising it to make a run pass
# would be reintroducing the exact defect class this file exists to catch -
# fix the divergence instead, or pin the input as a refusal if it cannot be
# matched.
#
# Measured 2026-08-27, later the same day: a line that starts with a non-SGR
# CSI collapses every LATER whitespace run that directly follows an SGR down
# to just its rightmost character - tabs and extra spaces before that space
# vanish. A regression during this fix (seed 20260826 briefly rose to 8)
# showed a third factor: an OSC 8 link ANYWHERE earlier in the line, even one
# already closed, turns the whole mechanism off for the rest of the line -
# found by diffing the new failures against the old shim on that seed
# specifically, not by trusting the aggregate count. Separately, the same
# leading-run scan that decides whether a CSI shelters a tab was OR-ing every
# escape in the run instead of keying on the LAST one, so a CSI immediately
# followed by an SGR reset kept sheltering a tab the SGR should have
# unsheltered. Both fixes dropped every seed or held it even; none rose.
#
# Measured 2026-08-27, a third fix the same day: a row that is refilled to
# exactly full by the separator space right after a forced pre-push folds a
# GLUED word onto that row instead of opening another - but only when
# wordWrap is false. Two broader attempts at this each regressed before
# landing here: skipping the push for every glued word broke an ordinary
# full-then-glued-word case that genuinely needs its own fresh row (110+
# new failures, caught by the fresh 8-seed sweep, not the aggregate count);
# skipping it whenever the pre-push had just fired broke plain non-glued
# wrapping ("the"/"quick" each needing a row at width 1) and a glued word
# reached with wordWrap left at its true default. Only the three-way
# combination - pre-pushed, glued throughout, AND wordWrap:false - matches
# Bun; the curated corpus (4,900 cases) caught the second attempt within
# one pytest run, well before either fuzz sweep was re-measured.
#
# Measured 2026-08-27, a fourth fix: inside wrap_breakWord, an ST-terminated
# OSC 8 event (open or close) does not just mean "glued" - while active it
# suppresses the column-budget check entirely, for every token, escape or
# character, until a BEL-terminated OSC 8 event is reached. Two prior
# attempts at a narrower version of this (see git history - a "glue just
# started, row exactly full" heuristic, tried twice) each passed every
# hand-picked probe and still regressed 100+ new failures on the full 8-seed
# sweep, because they gated on the wrong condition (whether GLUE was
# starting) instead of the real one (whether ST-DISABLED mode was starting).
# The rule that finally held, nailed down with ~50 direct probes against the
# real Bun binary before touching this file:
#   - Entering ST-disabled mode from normal IS checked against the column
#     budget like any other token, but ONLY if the rest of the word contains
#     a BEL event with something still after it - a span that never turns
#     disabled back off (or turns off with nothing left to place) behaves as
#     if it were never checked at all, merging onto however-full the row
#     already is. Once already disabled, no further ST event re-triggers
#     this - only the transition out of normal mode does.
# - Leaving ST-disabled mode via a BEL event touches nothing: the counter
#     resumes being checked from whatever value it was frozen at on entry.
#     An early version of this fix zeroed the counter unconditionally on
#     every such exit and got a width-2 case wrong by exactly one character
#     (see git history) - the entry-side check above is what actually zeroes
#     it, via an ordinary push, whenever entry happened to land on a row
#     that was already exactly full.
# This dropped every one of the 8 seeds again (49 -> 30 total), none rose.
MAX_WRAP_DIVERGENCES = {
    1: 4, 7: 4, 13: 5, 99: 5,
    1337: 1, 20260826: 4, 24301: 3, 424242: 4,
}

# YAML must be exact: it has a refusal channel, so anything it cannot match is
# required to throw rather than answer differently.
MAX_YAML_DIVERGENCES = 0


def _run(argv, env_extra):
    import os
    env = dict(os.environ)
    env.update(env_extra)
    proc = subprocess.run(argv, capture_output=True, timeout=TIMEOUT,
                          cwd=str(ROOT / "tests"), env=env)
    assert proc.returncode == 0, (
        f"{argv[0]} exited {proc.returncode}:\n"
        f"{proc.stderr.decode('utf-8', 'replace')[-2000:]}")
    return json.loads(proc.stdout.decode("utf-8"))


# Four tests ask for the same (mode, seed) answers; generating them once keeps
# a wider sweep cheap.
_CACHE = {}


def _both(bun_bin, node_bin, mode, seed):
    key = (mode, seed)
    if key not in _CACHE:
        env = {"NRC_FUZZ_MODE": mode, "NRC_FUZZ_SEED": str(seed),
               "NRC_FUZZ_COUNT": str(COUNT)}
        _CACHE[key] = (_run([bun_bin, str(FUZZER)], env),
                       _run([node_bin, "--require", str(SHIM), str(FUZZER)], env))
    return _CACHE[key]


def _describe(entry, bun, shim):
    parts = [f"    input   {json.dumps(entry['input'])[:160]}"]
    if "width" in entry:
        parts.append(f"    width   {entry['width']}  options {json.dumps(entry['options'])}")
    parts.append(f"    Bun     {json.dumps(bun)[:200]}")
    parts.append(f"    shim    {json.dumps(shim)[:200]}")
    return "\n".join(parts)


@pytest.mark.parametrize("seed", SEEDS)
def test_both_runtimes_generate_the_same_inputs(bun_bin, node_bin, seed):
    """Without this the comparison is meaningless and would silently pass.

    The generator runs inside each runtime, so a difference in Math, string
    iteration or property order would make the two sides compare unrelated
    inputs - and two unrelated lists of answers can agree by accident.
    """
    for mode in ("wrap", "yaml"):
        bun, node = _both(bun_bin, node_bin, mode, seed)
        assert len(bun) == len(node) == COUNT, (
            f"{mode}/seed {seed}: {len(bun)} cases under Bun, {len(node)} under Node")
        mismatched = [i for i, (b, n) in enumerate(zip(bun, node))
                      if b["input"] != n["input"]]
        assert not mismatched, (
            f"{mode}/seed {seed}: the generators diverged at case "
            f"{mismatched[0]} - the differential below is comparing different "
            "inputs and proves nothing")


@pytest.mark.parametrize("seed", SEEDS)
def test_yaml_never_answers_differently_from_bun(bun_bin, node_bin, seed):
    """Zero tolerance: YAML can refuse, so a wrong answer is never necessary."""
    bun, node = _both(bun_bin, node_bin, "yaml", seed)

    wrong = []
    for b, n in zip(bun, node):
        if "err" in n["answer"]:
            continue  # refusing is the contract's other half
        if b["answer"] != n["answer"]:
            wrong.append(_describe(b, b["answer"], n["answer"]))

    assert len(wrong) <= MAX_YAML_DIVERGENCES, (
        f"{len(wrong)} generated inputs parse differently from Bun "
        f"(seed {seed}, {COUNT} cases). Each one is a config silently read "
        "wrong. Reproduce with:\n"
        f"  NRC_FUZZ_MODE=yaml NRC_FUZZ_SEED={seed} NRC_FUZZ_COUNT={COUNT} "
        "bun tests/fuzz_shim.cjs\n" + "\n\n".join(wrong[:3]))


@pytest.mark.parametrize("seed", SEEDS)
def test_wrap_ansi_divergences_do_not_grow(bun_bin, node_bin, seed):
    """A ratchet, not a green light.

    wrapAnsi cannot refuse - a throw inside a render is swallowed by an error
    boundary and the TUI dies silently, which is the failure this whole branch
    exists to fix. So its contract is enforceable only by comparison, and the
    known gap is pinned as a number that may only fall.
    """
    bun, node = _both(bun_bin, node_bin, "wrap", seed)
    diverged = [(b, n) for b, n in zip(bun, node) if b["answer"] != n["answer"]]
    limit = MAX_WRAP_DIVERGENCES[seed]

    assert len(diverged) <= limit, (
        f"wrapAnsi divergences rose to {len(diverged)} (pinned at {limit}) for "
        f"seed {seed}. Reproduce with:\n"
        f"  NRC_FUZZ_MODE=wrap NRC_FUZZ_SEED={seed} NRC_FUZZ_COUNT={COUNT} "
        "bun tests/fuzz_shim.cjs\n"
        + "\n\n".join(_describe(b, b["answer"], n["answer"])
                      for b, n in diverged[:3]))


@pytest.mark.parametrize("seed", SEEDS)
def test_the_wrap_pin_is_not_slack(bun_bin, node_bin, seed):
    """The ratchet has to be tight, or it stops ratcheting.

    A pin far above the real count would let a regression hide underneath it.
    This fails when the gap is comfortable enough to be worth tightening.
    """
    bun, node = _both(bun_bin, node_bin, "wrap", seed)
    actual = sum(1 for b, n in zip(bun, node) if b["answer"] != n["answer"])
    limit = MAX_WRAP_DIVERGENCES[seed]

    assert actual >= limit - 5, (
        f"seed {seed}: only {actual} divergences remain but the pin still says "
        f"{limit}. Lower MAX_WRAP_DIVERGENCES to {actual} so the ratchet keeps "
        "catching regressions.")


def test_the_fuzzer_uses_no_clock_and_no_unseeded_randomness():
    """Reproducibility is the property that makes a failure actionable.

    Comments are stripped before scanning: the fuzzer's own header explains
    that it avoids these, and a naive substring search flags that sentence.
    """
    import re
    source = FUZZER.read_text()
    code = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    code = "\n".join(re.sub(r"//.*$", "", line) for line in code.splitlines())

    for forbidden in ("Math.random", "Date.now", "new Date"):
        assert forbidden not in code, (
            f"{forbidden} in the fuzzer makes failures unreproducible")
    assert "NRC_FUZZ_SEED" in code, "the fuzzer takes no seed"
