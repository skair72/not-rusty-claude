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
