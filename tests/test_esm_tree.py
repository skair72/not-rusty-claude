"""The code-split ESM shape (Claude Code 2.1.280+), hermetically.

Synthetic graphs whose entry record carries format byte 1 (esm), built with
tests/fixtures.py like every other container test, so the whole path -
extract_bun.py's tree + manifest, postprocess.py's context-sensitive rewrite,
and the artifact actually running under stock Bun - is exercised without the
233 MB binary. The shapes mirror what docs/findings.md 14 measured on 2.1.280:
side-effect `import"…"`, `from"…"`, `import("…")`, `import.meta.require("…")`,
`Re("…")` of text modules and addons through a helper chunk, path constants
read with fs, and a module in a subdirectory.
"""

import json
import os
import shutil
import subprocess

import pytest

import fixtures

JS, FILE, TEXT, NAPI = 1, 5, 13, 10
ESM = fixtures.FORMAT_ESM
LATIN1, UTF16 = fixtures.ENC_LATIN1, fixtures.ENC_UTF16LE
ROOT = "/$bunfs/root/"


def js(name, code):
    return (ROOT + name, code.encode("ascii"), JS, ESM, LATIN1)


# A miniature 2.1.280: the entry imports a chunk for its side effects and
# a helper by name; the helper exports `Re = import.meta.require` exactly as
# 2.1.280's shared runtime chunk does; the chunk loads a text module and reads
# a file asset through Re and a path constant, dynamically imports a module
# in a subdirectory, and prints what it got.
HELPER = "var Re=import.meta.require;export{Re};\n"
CHUNK = (
    'import{Re}from"/$bunfs/root/chunk-help.js";'
    'import{readFileSync as r}from"fs";'
    'var t=Re("/$bunfs/root/guide-1.md");'
    'var u=Re("/$bunfs/root/wide-2.md");'
    'var a="/$bunfs/root/asset.txt";'
    'var w={URL:"/$bunfs/root/sub/dir/worker.js"}.URL;'
    'var{deep}=await import("/$bunfs/root/sub/dir/worker.js");'
    'console.log(JSON.stringify({t,u,a:r(a,"utf8"),deep:deep(),w:w.endsWith("/sub/dir/worker.js")}));\n')
WORKER = ('import{Re}from"/$bunfs/root/chunk-help.js";'
          'export function deep(){return Re("/$bunfs/root/guide-1.md").length}\n')
ENTRY = ('// @bun @bytecode\n'
         'import"/$bunfs/root/chunk-main.js";import{Re}from"/$bunfs/root/chunk-help.js";\n')
GUIDE = "# Guide\n\nplain ascii text\n"
WIDE = "# Wide \u2014 caf\u00e9 \u4e2d\u6587 \U0001F600\n"


def graph(entry_code=ENTRY, extra=()):
    mods = [
        js("chunk-help.js", HELPER),
        js("chunk-main.js", CHUNK),
        js("cli", entry_code),
        js("sub/dir/worker.js", WORKER),
        (ROOT + "guide-1.md", GUIDE.encode("ascii"), TEXT, 0, LATIN1),
        (ROOT + "wide-2.md", WIDE.encode("utf-16-le"), TEXT, 0, UTF16),
        (ROOT + "asset.txt", b"ASSET-BYTES\n", FILE, 0, 0),
    ] + list(extra)
    return fixtures.build_payload(mods, entry=2)


def extracted(extract_bun, tmp_path, payload=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    binary = tmp_path / "claude.elf"
    binary.write_bytes(fixtures.build_elf(payload or graph()))
    out = tmp_path / "x"
    extract_bun.extract(str(binary), str(out))
    return out


# ------------------------------------------------------------------ extract

def test_esm_entry_takes_the_tree_path_and_writes_every_module(extract_bun, tmp_path):
    out = extracted(extract_bun, tmp_path)

    assert not (out / "cli.original.js").exists(), "the legacy output leaked into a tree build"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["kind"] == "esm-tree"
    assert manifest["entry"] == "cli"
    paths = sorted(m["path"] for m in manifest["modules"])
    assert paths == sorted(["chunk-help.js", "chunk-main.js", "cli", "sub/dir/worker.js",
                            "guide-1.md", "wide-2.md", "asset.txt"])
    # verbatim bytes, subdirectories kept
    assert (out / "original" / "sub" / "dir" / "worker.js").read_text() == WORKER
    assert (out / "original" / "wide-2.md").read_bytes() == WIDE.encode("utf-16-le")
    by = {m["path"]: m for m in manifest["modules"]}
    assert by["wide-2.md"]["encoding"] == "utf16le"
    assert by["guide-1.md"]["encoding"] == "latin1"
    assert by["cli"]["format"] == "esm" and by["asset.txt"]["loader"] == "file"


def test_a_cjs_or_unset_entry_format_keeps_the_legacy_path(extract_bun, tmp_path):
    for fmt in (0, 2):
        payload = fixtures.build_payload([(ROOT + "cli", b"(function(){})", JS, fmt)])
        out = extracted(extract_bun, tmp_path / str(fmt), payload)
        assert (out / "cli.original.js").read_bytes() == b"(function(){})"
        assert not (out / "manifest.json").exists()


def test_only_the_entrys_format_decides(extract_bun, tmp_path):
    """esm chunks behind a cjs entry are still the legacy shape."""
    payload = fixtures.build_payload([
        (ROOT + "cli", b"(function(){})", JS, 2),
        (ROOT + "chunk-a.js", b"export{}", JS, ESM),
    ])
    out = extracted(extract_bun, tmp_path, payload)
    assert (out / "cli.original.js").exists()
    assert not (out / "manifest.json").exists()


@pytest.mark.parametrize("bad", [
    "/$bunfs/root/../escape.js",
    "/$bunfs/root/a/../../escape.js",
    "/$bunfs/root//double.js",
    "/$bunfs/root/",
    "/$bunfs/root/back\\slash.js",
    "/$bunfs/root/./dot.js",
    "/elsewhere/x.js",
    "B:/~BUN/root/win.js",
])
def test_unsafe_or_foreign_module_paths_are_refused(extract_bun, tmp_path, capsys, bad):
    payload = fixtures.build_payload([js("cli", "export{}"), (bad, b"export{}", JS, ESM)])
    binary = tmp_path / "c"
    binary.write_bytes(fixtures.build_elf(payload))
    with pytest.raises(SystemExit):
        extract_bun.extract(str(binary), str(tmp_path / "x"))
    assert "error:" in capsys.readouterr().err
    assert not any(p.name == "escape.js" for p in tmp_path.rglob("*"))


def test_two_modules_with_one_path_are_refused(extract_bun, tmp_path, capsys):
    payload = fixtures.build_payload([js("cli", "export{}"), js("a.js", "1"), js("a.js", "2")])
    binary = tmp_path / "c"
    binary.write_bytes(fixtures.build_elf(payload))
    with pytest.raises(SystemExit):
        extract_bun.extract(str(binary), str(tmp_path / "x"))
    assert "same path" in capsys.readouterr().err


def test_a_file_where_a_directory_must_go_is_refused(extract_bun, tmp_path, capsys):
    payload = fixtures.build_payload([js("cli", "export{}"), js("a", "1"), js("a/b.js", "2")])
    binary = tmp_path / "c"
    binary.write_bytes(fixtures.build_elf(payload))
    with pytest.raises(SystemExit):
        extract_bun.extract(str(binary), str(tmp_path / "x"))
    assert "error:" in capsys.readouterr().err


# ------------------------------------------------------------- postprocess

def run_post(postprocess, out):
    return postprocess.transform_tree(str(out))


def test_references_are_rewired_by_context(extract_bun, postprocess, tmp_path):
    out = extracted(extract_bun, tmp_path)
    totals, errors = run_post(postprocess, out)
    assert errors == []

    root = out / "root"
    entry = (root / "cli.js").read_text()
    assert 'import"./chunk-main.js"' in entry
    assert 'from"./chunk-help.js"' in entry
    main = (root / "chunk-main.js").read_text()
    # Re is the HELPER's import.meta.require: a relative path would resolve
    # against chunk-help.js, so every Re() argument is runtime-absolute
    assert 'Re((import.meta.dirname+"/guide-1.md.cjs"))' in main
    assert 'var a=(import.meta.dirname+"/asset.txt")' in main
    assert '{URL:(import.meta.dirname+"/sub/dir/worker.js")}' in main
    assert 'import("./sub/dir/worker.js")' in main
    worker = (root / "sub" / "dir" / "worker.js").read_text()
    assert 'from"../../chunk-help.js"' in worker
    assert 'Re((import.meta.dirname+"/../../guide-1.md.cjs"))' in worker
    assert "/$bunfs/" not in "".join(p.read_text() for p in root.rglob("*.js"))
    assert totals["text_refs"] == 3


def test_text_modules_become_commonjs_strings_decoded_by_encoding(extract_bun, postprocess, tmp_path):
    out = extracted(extract_bun, tmp_path)
    _, errors = run_post(postprocess, out)
    assert errors == []
    for name, text in (("guide-1.md", GUIDE), ("wide-2.md", WIDE)):
        src = (out / "root" / (name + ".cjs")).read_text()
        assert src.isascii(), "non-ASCII must be escaped so no engine misreads it"
        body = src.split("module.exports = ", 1)[1].rstrip().rstrip(";")
        assert json.loads(body) == text
    assert not (out / "root" / "guide-1.md").exists(), \
        "a raw .md left beside the wrapper is what stock Bun would render to HTML"


def test_the_entry_wrapper_and_package_json(extract_bun, postprocess, tmp_path):
    out = extracted(extract_bun, tmp_path)
    _, errors = run_post(postprocess, out)
    assert errors == []
    lines = [l for l in (out / "cli.js").read_text().splitlines() if l.startswith("import")]
    assert lines == ['import "./bun-ant.mjs";', 'import "./root/cli.js";'], \
        "the polyfill must be imported FIRST: imports evaluate in order"
    assert (out / "bun-ant.mjs").exists() and (out / "bun-ant-cell-segmenter.mjs").exists()
    assert json.loads((out / "package.json").read_text())["type"] == "module"


def test_postprocess_is_rerunnable(extract_bun, postprocess, tmp_path):
    out = extracted(extract_bun, tmp_path)
    assert run_post(postprocess, out)[1] == []
    first = (out / "root" / "chunk-main.js").read_text()
    (out / "root" / "stale.js").write_text("left over")
    assert run_post(postprocess, out)[1] == []
    assert (out / "root" / "chunk-main.js").read_text() == first
    assert not (out / "root" / "stale.js").exists()


@pytest.mark.parametrize("code,fragment", [
    ('import"/$bunfs/root/chunk-gone.js";', "does not contain: chunk-gone.js"),
    ('var p="/$bunfs/root/guide-1.md";', "other than through a call to import.meta.require"),
    ('var s=`/$bunfs/root/tmpl-${1}`;', "survived the rewrite"),
])
def test_what_cannot_be_rewired_fails_the_build(extract_bun, postprocess, tmp_path, code, fragment):
    out = extracted(extract_bun, tmp_path, graph(entry_code=ENTRY + code))
    _, errors = run_post(postprocess, out)
    assert any(fragment in e for e in errors), errors


def test_the_windows_prefix_constant_is_left_alone(extract_bun, postprocess, tmp_path):
    """2.1.280's hooks-worker resolver compares paths against "B:/~BUN/root/"."""
    code = ENTRY + 'var x="B:/~BUN/root/";\n'
    out = extracted(extract_bun, tmp_path, graph(entry_code=code))
    totals, errors = run_post(postprocess, out)
    assert errors == []
    assert totals["prefix_constants"] == 1
    assert 'var x="B:/~BUN/root/"' in (out / "root" / "cli.js").read_text()


def test_a_bare_posix_prefix_is_not_exempt(extract_bun, postprocess, tmp_path):
    """On Linux/macOS it would be the stem of a path built by concatenation."""
    code = ENTRY + 'var y="/$bunfs/root/";var z=y+"asset.txt";\n'
    out = extracted(extract_bun, tmp_path, graph(entry_code=code))
    _, errors = run_post(postprocess, out)
    assert any("survived the rewrite" in e for e in errors), errors


def test_a_tampered_original_is_refused(extract_bun, postprocess, tmp_path):
    out = extracted(extract_bun, tmp_path)
    (out / "original" / "chunk-main.js").write_text("tampered")
    _, errors = run_post(postprocess, out)
    assert any("sha256" in e for e in errors)


def test_an_undecodable_module_is_refused(extract_bun, postprocess, tmp_path):
    odd = (ROOT + "odd.md", b"abc", TEXT, 0, UTF16)   # 3 bytes cannot be UTF-16LE
    out = extracted(extract_bun, tmp_path, graph(extra=[odd]))
    _, errors = run_post(postprocess, out)
    assert any("odd.md" in e for e in errors)


def test_a_missing_polyfill_is_fatal(extract_bun, postprocess, tmp_path, monkeypatch):
    out = extracted(extract_bun, tmp_path)
    monkeypatch.setenv("NRC_BUN_ANT_DIR", str(tmp_path / "nowhere"))
    _, errors = run_post(postprocess, out)
    assert any("polyfill" in e for e in errors)
    assert not (out / "cli.js").exists(), "no entry may be written for a failed build"


def test_main_dispatches_on_the_manifest(extract_bun, tmp_path):
    out = extracted(extract_bun, tmp_path)
    tool = os.path.join(os.path.dirname(__file__), "..", "tools", "postprocess.py")
    r = subprocess.run(["python3", tool, str(out)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "graph shape            : esm" in r.stdout
    assert "image shim applied     : 0" in r.stdout   # build.sh greps this line


# ------------------------------------------------------------- it runs

def test_the_rewired_tree_runs_under_stock_bun(extract_bun, postprocess, bun_bin, tmp_path):
    """End to end: every reference shape resolves, from a directory the
    graph was never built in, under a Bun that has no Bun.ant."""
    out = extracted(extract_bun, tmp_path)
    assert run_post(postprocess, out)[1] == []
    moved = tmp_path / "somewhere else"
    out.rename(moved)
    r = subprocess.run([bun_bin, str(moved / "cli.js")], capture_output=True, text=True,
                       timeout=60, cwd=str(tmp_path), env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    got = json.loads(r.stdout.strip().splitlines()[-1])
    assert got == {"t": GUIDE, "u": WIDE, "a": "ASSET-BYTES\n", "deep": len(GUIDE), "w": True}


def test_two_modules_bound_for_one_output_file_are_refused(extract_bun, postprocess, tmp_path):
    """`cli` gains .js; a real `cli.js` beside it would be overwritten in silence."""
    out = extracted(extract_bun, tmp_path, graph(extra=[js("cli.js", "console.log('CHUNK')")]))
    _, errors = run_post(postprocess, out)
    assert any("would both be written to root/cli.js" in e for e in errors), errors
    assert not (out / "root").exists()


def test_a_text_module_handed_to_anything_but_import_meta_require_is_refused(
        extract_bun, postprocess, tmp_path):
    code = ENTRY + 'import{readFileSync as r}from"fs";var p=r("/$bunfs/root/guide-1.md","utf8");\n'
    out = extracted(extract_bun, tmp_path, graph(entry_code=code))
    _, errors = run_post(postprocess, out)
    assert any("not bound to import.meta.require: r" in e for e in errors), errors


def test_a_file_further_up_the_path_is_refused_not_a_traceback(extract_bun, tmp_path, capsys):
    payload = fixtures.build_payload([js("cli", "export{}"), js("a", "1"), js("a/b/c.js", "2")])
    binary = tmp_path / "c"
    binary.write_bytes(fixtures.build_elf(payload))
    with pytest.raises(SystemExit):
        extract_bun.extract(str(binary), str(tmp_path / "x"))
    assert "needs" in capsys.readouterr().err


def test_post_processing_without_original_says_why_once(extract_bun, postprocess, tmp_path):
    """build.sh deletes original/ after its parser check; a re-run of
    postprocess.py over that build gets one line saying so, not one
    "cannot be read" per module."""
    out = extracted(extract_bun, tmp_path)
    shutil.rmtree(out / "original")
    totals, errors = run_post(postprocess, out)
    assert len(errors) == 1, errors
    assert "NRC_KEEP_ORIGINAL=1" in errors[0]


def test_a_failed_rerun_leaves_the_previous_artifact_whole(extract_bun, postprocess, tmp_path):
    out = extracted(extract_bun, tmp_path)
    assert run_post(postprocess, out)[1] == []
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*")
              if p.is_file() and "original" not in p.parts}
    (out / "original" / "chunk-main.js").write_text("tampered")
    assert run_post(postprocess, out)[1] != []
    after = {p.relative_to(out): p.read_bytes() for p in out.rglob("*")
             if p.is_file() and "original" not in p.parts}
    assert after == before, "a failed run changed the artifact it was run over"


def test_a_comment_between_import_and_its_specifier_keeps_it_static(postprocess):
    by = {"a.js": {"path": "a.js", "loader": "js"}}
    code, c = postprocess.transform_module('import/*c*/"/$bunfs/root/a.js";', "sub/m.js", by)
    assert code == 'import/*c*/"../a.js";' and c["specifier"] == 1 and c["expression"] == 0


def test_import_meta_require_of_a_chunk_becomes_relative(postprocess):
    """445 sites on 2.1.280: `import.meta.require("/$bunfs/root/chunk-X.js")`."""
    by = {"chunk-x.js": {"path": "chunk-x.js", "loader": "js"}}
    code, c = postprocess.transform_module(
        'var m=import.meta.require("/$bunfs/root/chunk-x.js");', "sub/dir/m.js", by)
    assert code == 'var m=import.meta.require("../../chunk-x.js");' and c["specifier"] == 1


def test_a_module_reached_by_path_gets_bun_ant_as_its_first_import(
        extract_bun, postprocess, tmp_path):
    """sub/dir/worker.js is referenced by path ({URL:...}), i.e. it starts a realm."""
    out = extracted(extract_bun, tmp_path)
    totals, errors = run_post(postprocess, out)
    assert errors == []
    assert totals["realm_entries"] == ["sub/dir/worker.js"]
    worker = (out / "root" / "sub" / "dir" / "worker.js").read_text()
    assert worker.startswith('import "../../../bun-ant.mjs";\n')
    assert 'import "' not in (out / "root" / "chunk-main.js").read_text()


def test_the_chrome_mcp_self_spawn_gains_the_entry(extract_bun, postprocess, tmp_path):
    spawn = 'function $7e(){return{type:"stdio",command:process.execPath,args:["--claude-in-chrome-mcp"],scope:"dynamic"}}'
    out = extracted(extract_bun, tmp_path, graph(entry_code=ENTRY + spawn + "\n"))
    totals, errors = run_post(postprocess, out)
    assert errors == [] and totals["self_spawns"] == 1
    assert 'args:[process.argv[1],"--claude-in-chrome-mcp"]' in (out / "root" / "cli.js").read_text()


def test_verify_tree_accepts_a_clean_tree_and_catches_a_demoted_import(
        extract_bun, postprocess, bun_bin, tmp_path):
    out = extracted(extract_bun, tmp_path)
    assert run_post(postprocess, out)[1] == []
    tool = os.path.join(os.path.dirname(__file__), "..", "scripts", "verify-tree.js")
    ok = subprocess.run([bun_bin, tool, str(out)], capture_output=True, text=True, timeout=60)
    assert ok.returncode == 0, ok.stdout + ok.stderr
    # a static side-effect import turned into an un-awaited dynamic one still
    # parses - only the import records give it away
    entry = out / "root" / "cli.js"
    entry.write_text(entry.read_text().replace('import"./chunk-main.js"',
                                               'import(import.meta.dirname+"/chunk-main.js")'))
    bad = subprocess.run([bun_bin, tool, str(out)], capture_output=True, text=True, timeout=60)
    assert bad.returncode == 1
    assert json.loads(bad.stdout)["problemCount"] >= 1
