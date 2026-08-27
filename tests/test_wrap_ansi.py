"""Bun.wrapAnsi under Node, against Bun as the oracle.

The renderer cannot draw a frame without this function. When the shim refused
it, the throw landed inside a React render, an error boundary swallowed it, and
the TUI sat idle painting nothing and never crashing - a failure that took a
day and two machines to name. So the implementation is held to the same
standard as the rest of the shim: not "looks right", but byte-identical to Bun
on every case in the corpus.

Nothing here hardcodes an expected string. Both runtimes run one file,
tests/wrap_ansi_probe.cjs, over one corpus, and the answers are compared. A
Bun release that changes the wrapping moves both sides at once and this test
keeps meaning what it says.
"""

import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIM = ROOT / "scripts" / "bun-shim.cjs"
PROBE = ROOT / "tests" / "wrap_ansi_probe.cjs"
CORPUS = ROOT / "tests" / "wrap_ansi_corpus.cjs"

TIMEOUT = 120


def _run(argv):
    proc = subprocess.run(argv, capture_output=True, timeout=TIMEOUT, cwd=str(ROOT / "tests"))
    assert proc.returncode == 0, (
        f"{argv[0]} exited {proc.returncode}:\n"
        f"{proc.stderr.decode('utf-8', 'replace')[-2000:]}")
    return json.loads(proc.stdout.decode("utf-8"))


@pytest.fixture(scope="module")
def answers(bun_bin, node_bin):
    """The same corpus, once under Bun and once under Node + the shim."""
    return {
        "bun": _run([bun_bin, str(PROBE)]),
        "node": _run([node_bin, "--require", str(SHIM), str(PROBE)]),
    }


def test_the_corpus_is_not_quietly_empty(node_bin):
    """A differential over zero cases passes and proves nothing.

    This asks the corpus how many cases it actually builds, rather than
    grepping its source for the shape of a loop - review showed the grep
    version stayed green with `strings = []`, which is exactly the state it
    claimed to rule out.
    """
    proc = subprocess.run(
        [node_bin, "-e",
         "const c = require('./wrap_ansi_corpus.cjs');"
         "process.stdout.write(JSON.stringify("
         "[c.strings.length, c.widths.length, c.optionSets.length, c.cases.length]))"],
        capture_output=True, timeout=60, cwd=str(ROOT / "tests"))
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    strings, widths, options, cases = json.loads(proc.stdout.decode())

    assert strings >= 28 and widths >= 10 and options >= 10, (
        f"a corpus dimension shrank: {strings} strings x {widths} widths x "
        f"{options} option sets")
    assert cases == strings * widths * options, (
        f"{cases} cases from {strings}x{widths}x{options} - the enumeration "
        "no longer covers every combination")


def test_wrap_ansi_matches_bun_on_every_case(answers):
    bun, node = answers["bun"], answers["node"]
    assert len(bun) == len(node) == 5100, (
        f"corpus size changed: {len(bun)} under Bun, {len(node)} under Node")

    mismatches = [i for i, (b, n) in enumerate(zip(bun, node)) if b != n]
    if mismatches:
        first = mismatches[0]
        raise AssertionError(
            f"{len(mismatches)} of {len(bun)} cases differ from Bun; first is "
            f"case {first}:\n  Bun  {bun[first]!r}\n  Node {node[first]!r}")


def test_neither_runtime_threw_anywhere_in_the_corpus(answers):
    """A refusal that survives here is the exact bug this file exists for."""
    for name, results in answers.items():
        threw = [(i, r["err"]) for i, r in enumerate(results) if "err" in r]
        assert not threw, (
            f"{name} threw on {len(threw)} cases; first: case {threw[0][0]} "
            f"-> {threw[0][1]}")


def test_the_shim_answers_rather_than_refusing(node_bin):
    """Named separately from the corpus so a regression to `unsupported` is
    unmistakable in the report, rather than showing up as 2,800 failures."""
    proc = subprocess.run(
        [node_bin, "--require", str(SHIM), "-e",
         "process.stdout.write(JSON.stringify(Bun.wrapAnsi('a b c', 3)))"],
        capture_output=True, timeout=60)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert json.loads(proc.stdout.decode()) == "a b\nc"
