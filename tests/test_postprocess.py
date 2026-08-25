import pytest


NODE_REQUIRE = 'var l5l=re(function(A,q){q.exports=require("/$bunfs/root/image-processor.node")});'
ASSET_CONST = 'var _qo="/$bunfs/root/chart.umd.min.js";'


def test_node_require_is_rewritten_to_the_assets_dir(postprocess):
    out, counts = postprocess.transform(NODE_REQUIRE)

    assert "/$bunfs/" not in out
    assert "require('path').join(__dirname,'assets',\"image-processor.node\")" in out
    assert counts["assets"] == 1


def test_file_asset_string_constant_is_rewritten(postprocess):
    """The mechanism is a plain string read by fs/promises.readFile, not a
    require - the scaffold's .node-only regex missed all of these."""
    out, counts = postprocess.transform(ASSET_CONST)

    assert "/$bunfs/" not in out
    assert out.startswith("var _qo=require('path').join(__dirname,'assets',")
    assert counts["assets"] == 1


def test_both_shapes_are_counted_together(postprocess):
    out, counts = postprocess.transform(NODE_REQUIRE + ASSET_CONST)

    assert counts["assets"] == 2
    assert counts["leftovers"] == []


def test_single_quoted_bunfs_literals_are_handled(postprocess):
    out, counts = postprocess.transform("var x='/$bunfs/root/mermaid.min.js';")

    assert counts["assets"] == 1
    assert "/$bunfs/" not in out


REAL_HEAD = "// @bun @bytecode @bun-cjs\n(function(exports, require, module, __filename, __dirname) {"
REAL_TAIL = 'r("cli_after_main_complete")}PSE();})\n'
LEAK = ('function qpy(e){let r=ole.dirname(nwu.fileURLToPath('
        '"file:///home/runner/work/claude-cli-internal/claude-cli-internal/'
        'src/utils/computerUse/setup.ts"));return r}')


def test_pragma_is_stripped_so_the_file_starts_with_the_wrapper(postprocess):
    out, counts = postprocess.transform(REAL_HEAD + REAL_TAIL)

    assert counts["pragma"] == 1
    assert out.startswith("(function(exports, require, module, __filename, __dirname)")


def test_trailing_iife_is_invoked(postprocess):
    out, counts = postprocess.transform(REAL_HEAD + REAL_TAIL)

    assert counts["iife"] == 1
    assert out.rstrip().endswith("})(exports, require, module, __filename, __dirname)")


# Interior closures: two `})` before the wrapper's own, in the two shapes the
# real entry module is full of - a factory call and an event handler. Every
# other fixture in this file ends at its first and only `})`, which is what
# left the `$` anchor untested.
INTERIOR_CLOSURES = ('var a=re(function(A,q){q.exports=1});'
                     'process.on("exit",function(){})\n')

INVOCATION = "})(exports, require, module, __filename, __dirname)"


def test_only_the_final_wrapper_is_invoked_not_every_closure(postprocess):
    """The `$` on the IIFE regex is load-bearing, and only this pins it.

    Dropping the anchor (`\\}\\)\\s*$` -> `\\}\\)\\s*`) left the whole hermetic
    suite green before this test existed, and with it in place this is the
    only test that fails - measured both ways on a copy of the tree. Every
    other fixture here contains exactly one `})`, at the very end, so subn()
    makes one substitution either way. On this input the un-anchored form makes
    three: counts["iife"] == 3, and the output becomes

        ...function(A,q){q.exports=1})(exports, require, module, __filename,
        __dirname);...function(){})(exports, require, module, __filename,
        __dirname)r("cli_after_main_complete")...

    - two function expressions called with five arguments they do not take,
    and, because `\\s*` also eats the newline that separated them, `)r(...)`,
    which is a syntax error. check() reports 0 errors on all of it, since it
    only asks whether iife == 0. So the corruption is what has to be asserted
    here, not the reasoning in check()'s comment: that reasoning is the thing
    the anchor makes true.
    """
    out, counts = postprocess.transform(REAL_HEAD + INTERIOR_CLOSURES + REAL_TAIL)

    assert counts["iife"] == 1
    assert out.count(INVOCATION) == 1
    assert out.endswith(INVOCATION)
    # the interior closures survive untouched, newline and all
    assert 'var a=re(function(A,q){q.exports=1});' in out
    assert 'process.on("exit",function(){})\n' in out
    assert postprocess.check(out, counts) == []


def test_baked_in_build_machine_file_url_is_rewritten(postprocess):
    """Bun's bundler resolved import.meta.url into a literal file:// URL of the
    build machine. The namespace prefix must be consumed too, or the result is
    the syntax error `nwu.__filename`."""
    out, counts = postprocess.transform(LEAK)

    assert counts["file_urls"] == 1
    assert "/home/runner/" not in out
    assert "nwu.__filename" not in out
    assert "ole.dirname(__filename)" in out


def test_iife_free_input_is_reported_as_fatal(postprocess):
    out, counts = postprocess.transform("(function(){return 1}")

    errors = postprocess.check(out, counts)

    assert errors
    assert any("IIFE" in e for e in errors)


def test_sound_output_reports_no_errors(postprocess):
    out, counts = postprocess.transform(REAL_HEAD + REAL_TAIL)

    assert postprocess.check(out, counts) == []


def test_zero_rewrites_with_populated_assets_dir_is_fatal(postprocess):
    """Reproduces the win32 VFS-prefix case: BUNFS_LITERAL matches nothing
    (Windows uses 'B:/~BUN/root/', not '/$bunfs/root/'), so counts['assets']
    is 0 while extract_bun.py has already written real files to assets/.
    That combination must be fatal, not a silent asset-less success - the
    exact anti-pattern docs/status.md cites as a reason not to ship PE
    support."""
    out, counts = postprocess.transform(REAL_HEAD + REAL_TAIL)
    assert counts["assets"] == 0

    errors = postprocess.check(out, counts, assets_on_disk=3)

    assert errors
    assert any("assets" in e.lower() for e in errors)


def test_zero_rewrites_with_empty_or_absent_assets_dir_is_not_fatal(postprocess):
    """0 rewrites is fine when there was nothing to rewrite in the first
    place: no assets/ dir at all (None), or an assets/ dir extract_bun.py
    made but left empty (0)."""
    out, counts = postprocess.transform(REAL_HEAD + REAL_TAIL)
    assert counts["assets"] == 0

    assert postprocess.check(out, counts, assets_on_disk=None) == []
    assert postprocess.check(out, counts, assets_on_disk=0) == []


def test_nonzero_rewrites_with_populated_assets_dir_is_fine(postprocess):
    out, counts = postprocess.transform(REAL_HEAD + ASSET_CONST + REAL_TAIL)
    assert counts["assets"] == 1

    assert postprocess.check(out, counts, assets_on_disk=1) == []


# --- the cli.js sibling shim -------------------------------------------------
#
# Claude's own code resolves a sibling cli.js for two MCP self-spawns
# (join(__filename,"..","cli.js") at the --claude-in-chrome-mcp and
# --computer-use-mcp sites). Our artifact is cli.original.cjs, so without a
# sibling those spawns point at a file that does not exist - and one of them
# PERSISTS the broken path into a Chrome native-messaging-host manifest.

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

# A minimal stand-in for the real 28 MB entry module: same shape (pragma line,
# `(function(...)` wrapper, trailing `})`) so postprocess.py accepts it.
TINY_ENTRY = (
    "// @bun @bytecode @bun-cjs\n"
    "(function(exports, require, module, __filename, __dirname) {\n"
    "console.log('2.1.222 (Claude Code)');\n"
    "console.log('argv1=' + process.argv[1]);\n"
    "console.log('filename=' + __filename);\n"
    "})\n"
)


def _postprocess(d):
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "postprocess.py"), str(d)],
        capture_output=True, text=True)


def test_postprocess_emits_a_cli_js_sibling_shim(tmp_path):
    (tmp_path / "cli.original.js").write_text(TINY_ENTRY)

    result = _postprocess(tmp_path)

    assert result.returncode == 0, result.stderr
    shim = tmp_path / "cli.js"
    assert shim.is_file(), "no sibling cli.js: both MCP self-spawns stay broken"
    assert 'require("./cli.original.cjs")' in shim.read_text()


def test_no_shim_is_written_when_the_transform_is_rejected(tmp_path):
    """check() failing must leave the directory without a cli.js promising an
    entry point that was never written."""
    (tmp_path / "cli.original.js").write_text("(function(){return 1}")

    result = _postprocess(tmp_path)

    assert result.returncode != 0
    assert not (tmp_path / "cli.js").exists()
    assert not (tmp_path / "cli.original.cjs").exists()


@pytest.mark.parametrize("pkg", [None, '{"type":"commonjs"}', '{"type":"module"}'])
def test_cli_js_shim_boots_the_entry_under_bun(tmp_path, bun_bin, pkg):
    """The shim is CJS-shaped `.js`; Bun must load it regardless of any
    package.json "type" that happens to sit next to the artifacts."""
    (tmp_path / "cli.original.js").write_text(TINY_ENTRY)
    if pkg is not None:
        (tmp_path / "package.json").write_text(pkg)
    assert _postprocess(tmp_path).returncode == 0

    result = subprocess.run([bun_bin, str(tmp_path / "cli.js"), "--version"],
                            capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert "2.1.222 (Claude Code)" in result.stdout
    # the self-spawn helper re-invokes process.argv[1]; the hardcoded MCP sites
    # resolve join(__filename,"..","cli.js") - both must land on the shim's dir
    assert f"argv1={tmp_path / 'cli.js'}" in result.stdout
    assert f"filename={tmp_path / 'cli.original.cjs'}" in result.stdout


# --- check()'s remaining fatal conditions ------------------------------------

def test_output_not_starting_with_the_wrapper_is_reported_as_fatal(postprocess):
    """The guard whose message names the exact Bun panic. Every other test's
    input happens to satisfy it, so deleting the guard changed nothing: this
    input trips it and only it."""
    out, counts = postprocess.transform("var x=1;\n(function(){})")
    assert counts["iife"] == 1, "this input must fail the (function check alone"

    errors = postprocess.check(out, counts)

    assert errors
    assert any("(function" in e for e in errors)
    assert not any("IIFE" in e for e in errors)


# --- leftover /$bunfs/ references --------------------------------------------
#
# LEFTOVER_BUNFS used to require the same `root/<basename>` shape BUNFS_LITERAL
# rewrites, which made it blind in exactly the cases that matter: anything the
# rewriter cannot handle also failed to be reported. It was vacuous - neutering
# it so it could never match left the whole suite green - and what it did find
# was a warning printed after "wrote:", not a reason to stop.

@pytest.mark.parametrize("reference", [
    'require("/$bunfs/root/vendor/nested.node")',   # nested path
    'require("/$bunfs/dist/thing.node")',           # different VFS root
    'require("B:/~BUN/root/thing.node")',           # Bun's Windows prefix
])
def test_a_reference_the_rewriter_cannot_handle_is_flagged_and_fatal(
        postprocess, reference):
    out, counts = postprocess.transform(
        REAL_HEAD + "var x=" + reference + ";" + REAL_TAIL)

    assert counts["assets"] == 0, "this shape must be one BUNFS_LITERAL misses"
    assert counts["leftovers"], "an unrewritable VFS reference was not reported"

    errors = postprocess.check(out, counts)

    assert any("bunfs" in e.lower() for e in errors), errors


def test_a_normally_rewritten_reference_leaves_no_leftovers(postprocess):
    """The widened pattern must not fire on its own replacement text."""
    out, counts = postprocess.transform(REAL_HEAD + NODE_REQUIRE + REAL_TAIL)

    assert counts["assets"] == 1
    assert counts["leftovers"] == []
    assert postprocess.check(out, counts) == []


def test_referenced_assets_are_matched_against_what_was_extracted(postprocess):
    """The dangerous direction: the rewritten code reaches for an asset that
    is not on disk. postprocess.py only ever warned about the harmless
    inverse ("extracted asset never referenced")."""
    out, counts = postprocess.transform(REAL_HEAD + NODE_REQUIRE + REAL_TAIL)
    assert counts["asset_names"] == ["image-processor.node"]

    assert postprocess.check(out, counts,
                             asset_names_on_disk={"image-processor.node"}) == []

    errors = postprocess.check(out, counts, asset_names_on_disk={"something-else"})
    assert any("image-processor.node" in e for e in errors), errors


def test_diagnostics_are_printed_before_the_artifact_is_written(tmp_path):
    """Order matters for whether anyone acts on them. Printed after "wrote:"
    these notes read as commentary on a finished artifact; printed before, they
    read as reasons to look at it.

    PYTHONUNBUFFERED makes the interleaving of the two streams reflect the
    order of the writes rather than Python's buffering.
    """
    (tmp_path / "cli.original.js").write_text(
        TINY_ENTRY.replace("})\n", 'var p="/home/runner/work/leaked/path";\n})\n'))

    result = subprocess.run(
        [sys.executable, str(ROOT / "tools" / "postprocess.py"), str(tmp_path)],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        env={"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"})

    assert result.returncode == 0, result.stdout
    note = result.stdout.index("note: build-machine path still present")
    assert note < result.stdout.index("wrote:"), result.stdout
