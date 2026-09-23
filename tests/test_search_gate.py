"""The embedded-search gate rewrite in tools/postprocess.py.

Design of record:
docs/superpowers/specs/2026-09-23-embedded-search-gate-design.md.

Claude's shell snapshot shadows `find` and `grep` with functions that re-exec
$CLAUDE_CODE_EXECPATH as bfs / ugrep, which the native binary embeds. Here that
path is bun, so every Bash-tool grep printed Bun's help text and was reported
as success (docs/findings.md §10). One gate decides it, and its first clause is
the build-time constant EMBEDDED_SEARCH_TOOLS, inlined as `isEnvTruthy("true")`.
postprocess.py rewrites that constant to `false` in the gate's declaration, so
the artifact runs the configuration native runs under its own Glob/Grep
opt-in (any --allowedTools or --tools entry naming Glob or Grep).

Three properties are under test.

*One edit, in the declaration.* Not asserted by spot checks alone: the output
is reconstructed with the single known edit undone and must equal the input.
The gate's call sites and the other `isEnvTruthy("true")` sites (2.1.280 has
seven) must not move.

*Tied to the bug.* The rewrite is licensed because this gate guards the
find/grep shadowing, so the generator is located too and its guard must call
the declared gate. Every state in which that cannot be shown while shadowing
code is present is fatal and writes nothing: shipping it is shipping the bug.

*It does what it says, in JavaScript.* The rewritten gate, run under Bun,
returns false, and the generator then emits nothing.

Fixtures are copied verbatim from the two real extracts and cut down:
chunk-1xqpf2j8.js of 2.1.280 and cli.original.cjs of 2.1.231.
"""

import json
import pathlib
import re
import subprocess
import sys

import pytest

import fixtures

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE_ENV = {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"}

# --- verbatim shapes ---------------------------------------------------------

# 2.1.280, chunk-1xqpf2j8.js @95971 and @833172. `Me` is isEnvTruthy, `$Rr`
# the searchToolsOptIn getter, `a` the typed env registry.
GATE_280 = ('function zb(){if(!Me("true"))return!1;if($Rr())return!1;'
            'return a.CLAUDE_CODE_ENTRYPOINT!=="local-agent"}')
GEN_280 = ('function gAn(){if(!zb())return null;return["unalias find 2>/dev/null || true",'
           '"unalias grep 2>/dev/null || true",S_e("find","bfs",["-S","dfs","-regextype",'
           '"findutils-default"]),S_e("grep","ugrep",["-G","--ignore-files","--hidden","-I"])]'
           '.join(`\\n`)}')
# the snapshot builder's use of it, cut down to the lines that name it
CONSUMER_280 = ('function vAn(){let g="";let h=gAn();if(h!==null)g+=`\n'
                '      # Shadow find/grep with embedded bfs/ugrep (ant-native only)\n'
                '      echo "# Shadow find/grep with embedded bfs/ugrep" >> "$SNAPSHOT_FILE"\n'
                '      cat >> "$SNAPSHOT_FILE" << \'FIND_GREP_FUNC_END\'\n${h}\n'
                'FIND_GREP_FUNC_END\n    `;return g}')
# one of the 16 call sites: the Glob/Grep tool disallow set
CALLS_280 = ('var GJt=new Set,qJt=new Set(["Glob","Grep"]);'
             'function $ae(){if(!zb()||!ea())return GJt;return qJt}')
# another isEnvTruthy("true") site in the same call shape: the pure-JS .md
# walker @345904 (which flag it inlined is lost to the build; only the gate's
# own identity is certain)
DECOY_280 = 'async function dR(e,n){let r=Me("true");return r}'

# 2.1.231, cli.original.cjs @5476325 and @6864232: the same shapes, other names
GATE_231 = ('function HP(){if(!fn("true"))return!1;if(CJi())return!1;'
            'return Q.CLAUDE_CODE_ENTRYPOINT!=="local-agent"}')
GEN_231 = ('function ytb(){if(!HP())return null;return["unalias find 2>/dev/null || true",'
           '"unalias grep 2>/dev/null || true",AJs("find","bfs",["-S","dfs","-regextype",'
           '"findutils-default"]),AJs("grep","ugrep",["-G","--ignore-files","--hidden","-I"])]'
           '.join(`\\n`)}')
CONSUMER_231 = CONSUMER_280.replace("gAn", "ytb").replace("vAn", "Ftb")
CALLS_231 = ('var UB_=new Set,jB_=new Set(["Glob","Grep"]);'
             'function vAt(){if(!HP()||!Hf())return UB_;return jB_}')
DECOY_231 = 'function cSw(){return fn("true")}'

SHAPES = {
    "2.1.280": dict(gate=GATE_280, gen=GEN_280, consumer=CONSUMER_280, calls=CALLS_280,
                    decoy=DECOY_280, name="zb", truthy='Me("true")'),
    "2.1.231": dict(gate=GATE_231, gen=GEN_231, consumer=CONSUMER_231, calls=CALLS_231,
                    decoy=DECOY_231, name="HP", truthy='fn("true")'),
}

REWRITTEN = "if(!false)return!1;"

HEAD = "// @bun @bytecode @bun-cjs\n(function(exports, require, module, __filename, __dirname) {\n"
TAIL = 'r("cli_after_main_complete")}PSE();})\n'


def _body(shape, **replace):
    """The gate, its generator and consumer, a call site and a decoy, in file
    order as they appear in the real builds."""
    parts = dict(SHAPES[shape], **replace)
    return ";".join(parts[k] for k in ("gate", "calls", "decoy", "gen", "consumer") if parts[k])


def _legacy(shape="2.1.231", **replace):
    return HEAD + _body(shape, **replace) + ";" + TAIL


def _single_edit(before, after):
    """(offset, removed, inserted) of the one contiguous difference."""
    p = 0
    while p < min(len(before), len(after)) and before[p] == after[p]:
        p += 1
    s = 0
    while (s < min(len(before), len(after)) - p
           and before[len(before) - 1 - s] == after[len(after) - 1 - s]):
        s += 1
    return p, before[p:len(before) - s], after[p:len(after) - s]


# --- the legacy (single-file) path -------------------------------------------

@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_the_gate_declaration_is_rewritten_to_false(postprocess, shape):
    code = _legacy(shape)
    out, counts = postprocess.transform(code)

    assert postprocess.check(out, counts) == []
    assert counts["search_gate"] == 1
    assert counts["search_gate_name"] == SHAPES[shape]["name"]
    gate = SHAPES[shape]["gate"].replace("if(!%s)" % SHAPES[shape]["truthy"], "if(!false)")
    assert gate in out and out.count(REWRITTEN) == 1


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_nothing_but_the_constant_in_the_declaration_moves(postprocess, shape):
    """The call sites, the generator and the other inlined flag are untouched:
    undoing the one edit gives back exactly the transformed input."""
    code = _legacy(shape)
    out, _ = postprocess.transform(code)

    # up to the tail, where transform() appends the IIFE invocation
    before = code[code.index("(function"):code.index(TAIL)]
    after = out[:out.index(TAIL[:-2])]
    gate = SHAPES[shape]["gate"]
    rewritten = gate.replace(SHAPES[shape]["truthy"], "false")
    assert after.count(rewritten) == 1
    assert after.replace(rewritten, gate) == before, _single_edit(before, after)
    assert out.count(SHAPES[shape]["truthy"]) == 1, "the decoy flag must survive"
    assert SHAPES[shape]["calls"] in out and SHAPES[shape]["gen"] in out


def test_both_image_shim_modes_apply_it(postprocess):
    """NRC_NO_IMAGE_SHIM rebuilds the image A/B's unshimmed half; it must not
    also bring back the bun-as-ugrep bug. With no image gate in the module the
    two modes must then produce the same bytes (with one, test_image_shim's
    real-binary single-edit tests show the image gate is all that differs)."""
    on, on_counts = postprocess.transform(_legacy(), image_shim=True)
    off, off_counts = postprocess.transform(_legacy(), image_shim=False)
    assert on_counts["search_gate"] == off_counts["search_gate"] == 1
    assert on.count(REWRITTEN) == 1 and on == off


def test_a_build_with_no_gate_and_no_shadowing_is_not_applicable(postprocess):
    """A future Claude that drops embedded search entirely: nothing to do, and
    nothing wrong with that."""
    out, counts = postprocess.transform(HEAD + "var x=1;" + TAIL)

    assert postprocess.check(out, counts) == []
    assert counts["search_gate"] == 0
    assert counts["search_gate_name"] is None
    assert "not applicable" in counts["search_gate_reason"]


def test_a_gate_with_no_shadowing_left_is_still_rewritten(postprocess):
    """The gate also removes Glob/Grep and steers the prompts; flipping it is
    still native's opt-in configuration when the snapshot no longer shadows."""
    out, counts = postprocess.transform(_legacy(gen="", consumer=""))

    assert postprocess.check(out, counts) == []
    assert counts["search_gate"] == 1 and REWRITTEN in out


# Each of these leaves shadowing code in the build without a provable rewrite
# of the gate that guards it. Shipping one is shipping the bug.
FATAL = {
    "two declarations": dict(decoy=GATE_231.replace("HP(", "HQ(")),
    "declaration drifted": dict(gate=GATE_231.replace('fn("true")', "fn(!0)")),
    "guard calls another gate": dict(gen=GEN_231.replace("if(!HP())", "if(!HQ())")),
    "generator drifted": dict(gen=GEN_231.replace("return null;", "return null;let q=1;")),
    "two generators": dict(decoy=GEN_231.replace("ytb", "ztb")),
    # the gate AND both original markers drift at once; the generator's calls
    # of the shadow-function builder still name ugrep and bfs
    "gate and markers drifted": dict(
        gate=GATE_231.replace('fn("true")', "fn(!0)"), consumer="",
        gen=GEN_231.replace("unalias grep 2>/dev/null || true", "unalias grep || :")),
}


@pytest.mark.parametrize("case", sorted(FATAL))
def test_unprovable_states_are_fatal(postprocess, case):
    out, counts = postprocess.transform(_legacy(**FATAL[case]))

    errors = postprocess.check(out, counts)
    assert any("embedded-search gate" in e for e in errors), (case, errors)
    assert counts["search_gate"] == 0
    assert REWRITTEN not in out, "a refused rewrite must leave the code untouched"


def test_the_bookkeeping_catches_a_rewrite_that_did_not_land(postprocess, monkeypatch):
    """Mutation: a replacement that leaves the declaration in its original
    shape. The post-condition, not the selection, has to catch it."""
    monkeypatch.setattr(postprocess, "SEARCH_GATE_REPLACEMENT", 'fn("true")')
    out, counts = postprocess.transform(_legacy())

    errors = postprocess.check(out, counts)
    assert any("embedded-search gate" in e and "bookkeeping" in e for e in errors), errors


def _run_postprocess(d, source):
    (d / "cli.original.js").write_text(source)
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "postprocess.py"), str(d)],
        capture_output=True, text=True, env=BASE_ENV)


def test_main_reports_the_rewrite_on_stdout(tmp_path):
    result = _run_postprocess(tmp_path, _legacy())

    assert result.returncode == 0, result.stderr
    assert re.search(r"^embedded search off    : 1  \(gate HP\(\)", result.stdout, re.M)


def test_main_reports_not_applicable_with_its_reason(tmp_path):
    result = _run_postprocess(tmp_path, HEAD + "var x=1;" + TAIL)

    assert result.returncode == 0, result.stderr
    assert "embedded search off    : 0  (not applicable:" in result.stdout


def test_main_writes_nothing_when_the_rewrite_cannot_be_proven(tmp_path):
    result = _run_postprocess(tmp_path, _legacy(**FATAL["declaration drifted"]))

    assert result.returncode != 0
    assert "error:" in result.stderr and "embedded-search gate" in result.stderr
    assert not (tmp_path / "cli.original.cjs").exists()


# --- the code-split path (2.1.280+) ------------------------------------------

JS, ESM, LATIN1 = 1, fixtures.FORMAT_ESM, fixtures.ENC_LATIN1
VFS = "/$bunfs/root/"


def _js(name, code):
    return (VFS + name, code.encode("ascii"), JS, ESM, LATIN1)


# 2.1.280 keeps the gate and its generator in one chunk and imports the gate
# into four others; this is that, at its smallest.
SEARCH_CHUNK = _body("2.1.280") + ";export{zb,gAn};\n"
IMPORTER = 'import{zb}from"/$bunfs/root/chunk-search.js";export function Ku(){return zb()}\n'
ENTRY = ('// @bun @bytecode\nimport"/$bunfs/root/chunk-search.js";'
         'import"/$bunfs/root/chunk-use.js";\n')


def _tree(extract_bun, tmp_path, search=SEARCH_CHUNK, extra=()):
    mods = [_js("chunk-search.js", search), _js("chunk-use.js", IMPORTER),
            _js("cli", ENTRY)] + list(extra)
    tmp_path.mkdir(parents=True, exist_ok=True)
    binary = tmp_path / "claude.elf"
    binary.write_bytes(fixtures.build_elf(fixtures.build_payload(mods, entry=2)))
    out = tmp_path / "x"
    extract_bun.extract(str(binary), str(out))
    return out


def test_the_tree_gets_one_rewrite_in_the_gates_own_chunk(extract_bun, postprocess, tmp_path):
    out = _tree(extract_bun, tmp_path)
    totals, errors = postprocess.transform_tree(str(out))

    assert errors == []
    assert totals["search_gate"] == 1
    assert totals["search_gate_name"] == "zb"
    assert totals["search_gate_module"] == "chunk-search.js"
    chunk = (out / "root" / "chunk-search.js").read_text()
    assert chunk.count(REWRITTEN) == 1 and 'Me("true")' in chunk, "the decoy survives"
    assert "return zb()" in (out / "root" / "chunk-use.js").read_text()


def test_a_generator_in_another_module_than_the_gate_is_fatal(extract_bun, postprocess, tmp_path):
    """The guard's callee could then be an import alias of anything; the rule
    is measured true on both builds and is not guessed past."""
    split = _body("2.1.280", gen="", consumer="") + ";export{zb};\n"
    elsewhere = ('import{zb}from"/$bunfs/root/chunk-search.js";'
                 + GEN_280 + ";" + CONSUMER_280 + ";export{gAn};\n")
    out = _tree(extract_bun, tmp_path, search=split, extra=[_js("chunk-gen.js", elsewhere)])
    totals, errors = postprocess.transform_tree(str(out))

    assert any("embedded-search gate" in e for e in errors), errors
    assert not (out / "root").exists(), "a failed tree build writes nothing"


def test_an_unread_module_is_not_reported_as_a_build_without_the_gate(extract_bun, tmp_path):
    """The graph is incomplete, so "no gate here" would describe modules
    nobody looked at. The build fails on the unread module either way."""
    out = _tree(extract_bun, tmp_path)
    with open(out / "original" / "chunk-search.js", "ab") as fh:
        fh.write(b" ")
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "postprocess.py"), str(out)],
                       capture_output=True, text=True, env=BASE_ENV)

    assert r.returncode != 0 and "sha256" in r.stderr
    line = next(l for l in r.stdout.splitlines() if l.startswith("embedded search off"))
    assert line.startswith("embedded search off    : 0  (not checked: 1 module(s) could not be read")
    assert "not applicable" not in line


def test_a_drifted_declaration_in_a_tree_is_fatal(extract_bun, postprocess, tmp_path):
    drifted = SEARCH_CHUNK.replace('if(!Me("true"))', "if(!Me(!0))")
    out = _tree(extract_bun, tmp_path, search=drifted)
    _, errors = postprocess.transform_tree(str(out))

    assert any("embedded-search gate" in e for e in errors), errors
    assert not (out / "root").exists()


def test_main_tree_reports_the_rewrite(extract_bun, tmp_path):
    out = _tree(extract_bun, tmp_path)
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "postprocess.py"), str(out)],
                       capture_output=True, text=True, env=BASE_ENV)

    assert r.returncode == 0, r.stderr
    assert re.search(r"^embedded search off    : 1  \(gate zb\(\) in chunk-search\.js",
                     r.stdout, re.M), r.stdout


# --- it does what it says, in JavaScript --------------------------------------

# Just enough of the surroundings for the three functions to run: isEnvTruthy
# verbatim (chunk-cd4d2mc0.js), no opt-in, the default entrypoint, a Bash-capable
# host, and S_e reduced to naming what it would shadow.
STUBS = (
    'function Me(e){if(!e)return!1;if(typeof e==="boolean")return e;'
    'let n=String(e).toLowerCase().trim();return["1","true","yes","on"].includes(n)}'
    "function $Rr(){return!1}var a={CLAUDE_CODE_ENTRYPOINT:void 0};function ea(){return!0}"
    'function S_e(n,a0){return "function "+n+" -> "+a0}\n')
PROBE = ('\nconsole.log(JSON.stringify({gate:zb(),shadow:gAn(),'
         'disallowed:[...$ae()]}));\n')


def _run_js(bun_bin, tmp_path, code):
    path = tmp_path / "probe.js"
    path.write_text(STUBS + code + PROBE)
    r = subprocess.run([bun_bin, str(path)], capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_under_bun_the_rewritten_gate_is_false_and_nothing_is_shadowed(
        extract_bun, postprocess, bun_bin, tmp_path):
    before = _run_js(bun_bin, tmp_path, _body("2.1.280"))
    assert before["gate"] is True and "ugrep" in before["shadow"]
    assert before["disallowed"] == ["Glob", "Grep"], "the fixture must show the bug first"

    out = _tree(extract_bun, tmp_path / "t")
    assert postprocess.transform_tree(str(out))[1] == []
    chunk = (out / "root" / "chunk-search.js").read_text().replace("export{zb,gAn};", "")
    after = _run_js(bun_bin, tmp_path, chunk)
    assert after == {"gate": False, "shadow": None, "disallowed": []}


# --- the real binaries ------------------------------------------------------

@pytest.mark.integration
def test_real_legacy_entry_module_changes_only_in_the_gate(postprocess, extract_bun,
                                                          real_elf_binary):
    """23 MB of real minified JavaScript, not the shapes this file chose."""
    from test_integration import _entry_source
    code = _entry_source(extract_bun, real_elf_binary)
    assert len(postprocess.SEARCH_GATE_DEF.findall(code)) == 1

    out, counts = postprocess.transform(code, image_shim=False)
    assert postprocess.check(out, counts) == [] and counts["search_gate"] == 1
    assert out.count(REWRITTEN) == 1
    assert not postprocess.SEARCH_GATE_DEF.search(out)
    # every other isEnvTruthy("true") in the file is still there
    truthy = '%s("true")' % counts["search_gate_truthy"]
    assert out.count(truthy) == code.count(truthy) - 1
