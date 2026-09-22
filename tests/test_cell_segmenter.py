"""Bun.ant.CellSegmenter: the JS port against the native class's recorded answers.

Claude Code 2.1.280's Ink renderer cannot draw a cell without
Bun.ant.CellSegmenter, a native class of Anthropic's private Bun
(docs/findings.md 14). scripts/bun-ant-cell-segmenter.mjs is a JavaScript port
of it, characterised and fuzzed against the real class: the native binary
honours BUN_OPTIONS=--preload, so tests/cell_segmenter_oracle.cjs can run inside
it.

tests/data/cell_segmenter_golden.json.gz records what the native class
answered on 2026-09-22 for 5,904 cases, as one sha256 prefix per case:
3,904 hand-built by the characterisation (grapheme and width rules, SGR and
OSC 8 parsing, bidi reordering, paint and setCell against pre-filled screens,
capacity overflow), plus the generated window 900000000..900001999 of
tests/cell_segmenter_fuzz.cjs, which is deterministic per index. Here the
port answers the same cases under stock Bun and every line must hash the same.
A mismatch names its case; `scripts/harness.py --only segmenter` shows the
diff live against the binary.
"""

import gzip
import hashlib
import json
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")
GOLDEN = os.path.join(TESTS, "data", "cell_segmenter_golden.json.gz")


def test_the_port_answers_every_recorded_case_as_the_native_class_did(bun_bin, tmp_path):
    golden = json.loads(gzip.decompress(open(GOLDEN, "rb").read()))
    window = golden["random_window"]
    rand = tmp_path / "random.json"
    r = subprocess.run([bun_bin, os.path.join(TESTS, "cell_segmenter_fuzz.cjs"),
                        str(window["start"]), str(window["count"]), str(rand)],
                       capture_output=True, text=True, timeout=300, env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    cases = golden["hand_cases"] + json.load(open(rand))
    assert len(cases) == len(golden["sha256_24"]) == 5904

    case_file, out_file = tmp_path / "cases.json", tmp_path / "port.jsonl"
    case_file.write_text(json.dumps(cases))
    r = subprocess.run([bun_bin, "--preload", os.path.join(ROOT, "scripts", "bun-ant.mjs"),
                        os.path.join(TESTS, "cell_segmenter_oracle.cjs")],
                       capture_output=True, text=True, timeout=900,
                       env={"PATH": "/usr/bin:/bin", "NRC_CASES": str(case_file),
                            "NRC_OUT": str(out_file)})
    assert r.returncode == 0, r.stderr
    # "\n" only: JSON.stringify leaves U+2028/2029 raw, splitlines() splits there
    lines = [l for l in out_file.read_text(encoding="utf-8").split("\n") if l]
    assert len(lines) == len(cases)
    bad = [cases[i]["id"] for i, line in enumerate(lines)
           if hashlib.sha256(line.encode("utf-8")).hexdigest()[:24] != golden["sha256_24"][i]]
    assert not bad, ("%d of %d cases answer differently from the native class, first: %s"
                     % (len(bad), len(cases), bad[:10]))


def test_the_port_is_not_a_native_passthrough(bun_bin, tmp_path):
    """The golden test must be exercising the JS, not a Bun that has Bun.ant."""
    probe = tmp_path / "p.mjs"
    probe.write_text("console.log(typeof Bun.ant)")
    r = subprocess.run([bun_bin, str(probe)], capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin"})
    assert r.stdout.strip() == "undefined", "this Bun already has Bun.ant - the test would prove nothing"
