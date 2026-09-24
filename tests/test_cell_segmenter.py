"""Bun.ant.CellSegmenter: the JS port against the native class's recorded answers.

Claude Code 2.1.280's Ink renderer cannot draw a cell without
Bun.ant.CellSegmenter, a native class of Anthropic's private Bun
(docs/findings.md 14). scripts/bun-ant-cell-segmenter.mjs is a JavaScript port
of it, characterised and fuzzed against the real class: the native binary
honours BUN_OPTIONS=--preload, so tests/cell_segmenter_oracle.cjs can run inside
it.

tests/data/cell_segmenter_golden.json.gz records what the native class
answered on 2026-09-22 for 4,204 cases - each case verbatim, each answer as a
sha256 prefix: 3,904 hand-built by the characterisation (grapheme and width
rules, SGR and OSC 8 parsing, bidi reordering, paint and setCell against
pre-filled screens, capacity overflow) and 300 generated ones. The generated
ones are stored rather than regenerated: tests/cell_segmenter_fuzz.cjs keeps
growing new families, and a record that depended on it would drift with it
(it did, once - 586 "mismatches" that were the generator's). Here the port
answers the same cases under stock Bun and every line must hash the same.
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
    cases = golden["cases"]
    assert len(cases) == len(golden["sha256_24"]) == 4204

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


TRIE_PROBE = r"""
import { CellSegmenter } from "%s";
import { heapStats } from "bun:jsc";
// One segmenter, as Ink keeps one per Output. The working set is W two-entry
// SGR lists (a truecolour foreground over one fixed background), so every
// first-level node of the key trie owns a Map. Between replays of that set,
// 9000 fresh truecolours wrap the module's 8192-entry cache of canonical
// entries - after which the same strings parse to NEW entry objects.
const seg = new CellSegmenter();
const cells = new Int32Array(4096), runs = new Int32Array(4096);
const W = 1500;
const work = (i) => `\x1b[38;2;${i & 255};${i >> 8};7m\x1b[48;5;17mx`;
const fresh = (round, i) => `\x1b[38;2;${i & 255};${(i >> 8) & 255};${100 + round}mx`;
const maps = () => { Bun.gc(true); return heapStats().objectTypeCounts.Map ?? 0; };
for (let i = 0; i < W; i++) seg.segment(work(i), cells, runs);
const before = maps();
for (let round = 0; round < 6; round++) {
  for (let i = 0; i < 9000; i++) seg.segment(fresh(round, i), cells, runs);
  for (let i = 0; i < W; i++) seg.segment(work(i), cells, runs);
}
console.log(JSON.stringify({ W, before, after: maps(), sgrKeys: seg.sgrKeys.length }));
"""


def test_the_key_trie_does_not_grow_when_the_entry_cache_wraps(bun_bin, tmp_path):
    """The port interns SGR lists through a per-instance trie keyed by the
    IDENTITY of canonical entry objects, which come from a module-wide cache
    cleared at 8192 entries. Once it wraps, the same strings parse to new
    objects: the trie grew a second branch for every list it knew, on every
    wrap, while sgrKeys (deduplicated by string) stayed flat - so Ink's
    size-based resets never saw it. Native has no such trie. Measured before
    the fix: +W Maps per wrap, without bound."""
    probe = tmp_path / "trie.mjs"
    probe.write_text(TRIE_PROBE % os.path.join(ROOT, "scripts", "bun-ant-cell-segmenter.mjs"))
    r = subprocess.run([bun_bin, str(probe)], capture_output=True, text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    m = json.loads(r.stdout)
    # the lists themselves are all new, so sgrKeys grows exactly as native's does
    assert m["sgrKeys"] == 1 + m["W"] + 6 * 9000, m
    assert m["after"] - m["before"] < m["W"] // 4, m
