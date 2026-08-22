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
