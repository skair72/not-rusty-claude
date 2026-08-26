"""Bun.YAML.parse under Node, against Bun as the oracle.

Node ships no YAML parser and this repo ships no dependencies, so the shim
carries one. That makes the failure mode worse than usual: a subtly wrong parse
is somebody's skill or agent silently misconfigured, with no error anywhere.

So the contract is narrower than YAML and the test enforces both halves of it:

  - every input the shim ACCEPTS must produce exactly what Bun produces. Not
    "close" - equal. Zero tolerance, because there is no safe wrong answer.
  - every input it cannot match must THROW, naming what is unsupported. The
    set of those is pinned, so support cannot quietly shrink and a refusal
    cannot quietly become a guess.

Bun is the oracle; no expected value is written down here.
"""

import json
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIM = ROOT / "scripts" / "bun-shim.cjs"
PROBE = ROOT / "tests" / "yaml_parse_probe.cjs"
CORPUS = ROOT / "tests" / "yaml_parse_corpus.cjs"

TIMEOUT = 120

# Measured 2026-08-26 against Bun 1.3.14, and regenerated from a live run
# rather than edited by hand. Each entry is something Bun parses and the shim
# declines, because matching it exactly could not be verified.
#
# The list grew when review found the first version of this parser ACCEPTING
# six of these and answering differently from Bun - the one failure mode the
# contract forbids. Shrink it by implementing an entry, never by loosening a
# check.
PINNED_REFUSALS = {
    "---\na: 1\n---\nb: 2",
    "a: &anchor 1\nb: *anchor",
    "a: !!str 1",
    "? complex\n: key",
    "a: 1\n\tb: 2",
    "a:\n- 1\n  - 2",
    "s: |2\n   explicit indent",
    "a: !custom 1",
    "a: !!int '3'",
    "&a x",
    "a: &x {b: 1}\nc: *x",
    "? [a, b]\n: c",
    "--- |\n  text",
    "[a: 1]",
    "{a: 1,\nb: 2}",
    "{[1]: x}",
    "{x}: 1",
    "[]: 1",
}


def _run(argv):
    proc = subprocess.run(argv, capture_output=True, timeout=TIMEOUT,
                          cwd=str(ROOT / "tests"))
    assert proc.returncode == 0, (
        f"{argv[0]} exited {proc.returncode}:\n"
        f"{proc.stderr.decode('utf-8', 'replace')[-2000:]}")
    return json.loads(proc.stdout.decode("utf-8"))


@pytest.fixture(scope="module")
def cases():
    import re
    text = CORPUS.read_text()
    body = text[text.index("module.exports =") + len("module.exports ="):].rstrip().rstrip(";")
    parsed = json.loads(body)
    assert len(parsed) >= 178, f"corpus shrank to {len(parsed)} cases"
    return parsed


@pytest.fixture(scope="module")
def answers(bun_bin, node_bin):
    return {
        "bun": _run([bun_bin, str(PROBE)]),
        "node": _run([node_bin, "--require", str(SHIM), str(PROBE)]),
    }


def test_nothing_is_parsed_differently_from_bun(answers, cases):
    """The half with no safe failure mode: a wrong parse, silently accepted."""
    wrong = []
    for src, bun, node in zip(cases, answers["bun"], answers["node"]):
        if "err" in node:
            continue  # refusing is the other half of the contract
        if "err" in bun:
            wrong.append((src, "Bun threw", node.get("ok")))
        elif bun["ok"] != node["ok"]:
            wrong.append((src, bun["ok"], node["ok"]))

    assert not wrong, (
        f"{len(wrong)} inputs parse differently from Bun. Each one is a config "
        "silently read wrong:\n" + "\n".join(
            f"  {src!r}\n    Bun  {want!r}\n    shim {got!r}" for src, want, got in wrong[:6]))


def test_the_refusals_are_exactly_the_pinned_set(answers, cases):
    """Support cannot shrink quietly, and a refusal cannot become a guess."""
    refused = {
        src for src, bun, node in zip(cases, answers["bun"], answers["node"])
        if "err" in node and "err" not in bun
    }

    newly_refused = refused - PINNED_REFUSALS
    newly_accepted = PINNED_REFUSALS - refused
    assert not newly_refused, (
        "the shim now refuses inputs Bun parses and that were previously "
        f"supported: {sorted(newly_refused)}")
    assert not newly_accepted, (
        "these were pinned as refused but are now answered - if that is real "
        f"support, verify it against Bun and drop the pin: {sorted(newly_accepted)}")


def test_a_refusal_names_the_api_and_the_reason(node_bin):
    """A silent throw would put us back where this started."""
    proc = subprocess.run(
        [node_bin, "--require", str(SHIM), "-e",
         "try { Bun.YAML.parse('a: &x 1'); } "
         "catch (e) { process.stdout.write(e.message); }"],
        capture_output=True, timeout=60)
    message = proc.stdout.decode("utf-8")
    assert "YAML.parse" in message, f"the error does not name the api: {message!r}"
    assert "anchors" in message, f"the error does not name the reason: {message!r}"


def test_the_frontmatter_shapes_the_bundle_uses_all_parse(answers, cases):
    """Guard the centre, not just the edges.

    These are the shapes skill and agent frontmatter actually takes. A
    regression here would be invisible in the counts above but would break
    every skill on the machine.
    """
    required = [
        "name: foo",
        "name: foo\ndescription: does a thing",
        "allowed-tools: Read, Write, Bash",
        "tools:\n  - Read\n  - Write",
        "tools:\n- Read\n- Write",
        "nested:\n  a: 1\n  b: 2",
        "s: |\n  line one\n  line two",
        "a: 1 # trailing comment",
    ]
    index = {src: i for i, src in enumerate(cases)}
    for src in required:
        assert src in index, f"corpus no longer covers {src!r}"
        node = answers["node"][index[src]]
        assert "err" not in node, (
            f"a core frontmatter shape is refused: {src!r} -> {node['err']}")
        assert node["ok"] == answers["bun"][index[src]]["ok"]


def test_a_long_sequence_of_mappings_parses_in_linear_time(node_bin, tmp_path):
    """Complexity, not speed.

    An earlier version copied the whole line array once per sequence entry,
    which made a block sequence of mappings quadratic: 40k entries took seven
    seconds where Bun takes twenty milliseconds. Frontmatter is tens of lines,
    so nothing user-facing was at risk - but a parser that degrades as the
    square of its input is a defect waiting for a bigger document.

    Timing is noisy on a shared host, so this asserts the SHAPE of the growth
    with a generous ceiling rather than any absolute duration: doubling the
    input must not triple the time. Quadratic growth is 4x per doubling and
    fails this comfortably; the linear implementation measured ~2x.
    """
    script = tmp_path / "perf.cjs"
    script.write_text(
        "const parse = globalThis.Bun.YAML.parse;\n"
        "const timed = (n) => {\n"
        "  const doc = Array.from({length: n}, (_, i) => `- a: ${i}\\n  b: x`).join('\\n');\n"
        "  const t0 = process.hrtime.bigint();\n"
        "  const out = parse(doc);\n"
        "  if (out.length !== n) throw new Error('parsed ' + out.length + ' of ' + n);\n"
        "  return Number(process.hrtime.bigint() - t0) / 1e6;\n"
        "};\n"
        "timed(2000);\n"  # warm up, so JIT does not skew the first measurement
        "process.stdout.write(JSON.stringify([timed(10000), timed(20000)]));\n")

    proc = subprocess.run([node_bin, "--require", str(SHIM), str(script)],
                          capture_output=True, timeout=TIMEOUT)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    small, large = json.loads(proc.stdout.decode())

    # Guard against a degenerate measurement making the ratio meaningless.
    assert small > 1.0, f"the 10k-entry parse took {small:.1f}ms - too fast to compare"
    assert large / small < 3.0, (
        f"doubling the input multiplied the time by {large / small:.1f} "
        f"({small:.0f}ms -> {large:.0f}ms). Linear growth is ~2x; quadratic is "
        "~4x, which is what a per-entry copy of the line array produces.")
