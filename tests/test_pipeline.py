"""The seam between the two tools, exercised as programs.

Everything else in this suite tests `extract_bun.py` and `postprocess.py` as
importable functions, in isolation. That leaves the one property the whole
project rests on unverified: extract_bun.py writes `assets/<name>`, and
postprocess.py independently rewrites `/$bunfs/root/<name>` literals into
`join(__dirname,'assets',<name>)`. Nothing checked that the two halves agree.

The failure this catches is the one that hurts most: a whole loader kind going
missing from extract_bun.py's accept-set, producing a cli.js that looks
perfectly correct, transforms cleanly, exits 0 on `--version`, and then dies -
or silently degrades, since both addon loaders swallow their failures - the
first time it reaches for an addon that was never written to disk.

Both tools are invoked here through `subprocess`, the way `build.sh` invokes
them, so their argv handling, exit codes and write-vs-validate ordering are
covered too.
"""

import importlib.util
import json
import pathlib
import re
import subprocess
import sys

import pytest

import fixtures

ROOT = pathlib.Path(__file__).resolve().parent.parent
EXTRACT = ROOT / "tools" / "extract_bun.py"
POSTPROCESS = ROOT / "tools" / "postprocess.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# Loaded at import time, not through the session fixture, because the
# parametrisation below has to be resolved during collection.
EB = _load(EXTRACT, "extract_bun_for_collection")
LOADER_ID = {name: i for i, name in EB.LOADERS.items()}

# What postprocess._asset_expr() emits, e.g.
#   require('path').join(__dirname,'assets',"image-processor.node")
ASSET_REF = re.compile(
    r"require\('path'\)\.join\(__dirname,'assets',(\"[^\"]*\")\)")

# One asset per writable loader kind, with a name and content resembling the
# real thing for that kind.
ASSET_PER_KIND = {
    "napi": ("image-processor.node", b"\x7fELF\x02\x01native-addon-bytes"),
    "base64": ("payload.template.html.asset", b"\x00\x01\x02raw-bytes\xff"),
    "file": ("chart.umd.min.js", b"/* a file-loader asset */\n"),
}


def _entry_source(names):
    """A minimal entry module referencing each name the way the real one does.

    The two shapes are the ones measured in the real bundle (findings.md 6):
    a dynamic `require()` of the literal for native addons, and a bare string
    constant later handed to `fs/promises.readFile` for file-loader assets.
    Alternating between them keeps both rewrite paths in play.
    """
    lines = []
    for i, name in enumerate(names):
        literal = '"/$bunfs/root/%s"' % name
        if i % 2 == 0:
            lines.append("var m%d=require(%s);" % (i, literal))
        else:
            lines.append("var p%d=%s;readFile(p%d);" % (i, literal, i))
    return ("// @bun @bytecode @bun-cjs\n"
            "(function(exports, require, module, __filename, __dirname) {\n"
            + "\n".join(lines) + "\n"
            "})\n")


def _run(*argv):
    return subprocess.run([sys.executable] + [str(a) for a in argv],
                          capture_output=True, text=True)


def _build_pipeline(tmp_path, modules, entry_source):
    """Write a synthetic binary, then run BOTH tools as programs over it."""
    payload = fixtures.build_payload(
        [("/$bunfs/root/cli", entry_source.encode(), 1)] + modules)
    binary = tmp_path / "synthetic-claude"
    binary.write_bytes(fixtures.build_elf(payload))
    out = tmp_path / "out"

    extracted = _run(EXTRACT, binary, out)
    assert extracted.returncode == 0, extracted.stderr
    processed = _run(POSTPROCESS, out)
    assert processed.returncode == 0, processed.stderr
    return out, extracted, processed


def test_every_rewritten_asset_path_exists_on_disk(tmp_path):
    """The load-bearing direction: everything the transformed JS will reach for
    at runtime must be a file the extractor actually wrote.

    postprocess.py already warned about the harmless direction ("extracted
    asset never referenced"). This is the dangerous inverse - referenced, never
    extracted - and it is what a whole loader kind dropping out of
    extract_bun.py's accept-set looks like from the outside.
    """
    names = [name for name, _ in ASSET_PER_KIND.values()]
    modules = [("/$bunfs/root/" + name, content, LOADER_ID[kind])
               for kind, (name, content) in ASSET_PER_KIND.items()]

    out, _, _ = _build_pipeline(tmp_path, modules, _entry_source(names))

    code = (out / "cli.original.cjs").read_text()
    referenced = {json.loads(m) for m in ASSET_REF.findall(code)}
    assert referenced == set(names), (
        "the transform did not rewrite one literal per asset: %r" % (referenced,))
    on_disk = {p.name for p in (out / "assets").iterdir()}

    missing = sorted(referenced - on_disk)
    assert not missing, (
        "the transformed cli.js will require()/readFile() assets that "
        "extract_bun.py never wrote: " + ", ".join(missing))
    assert "/$bunfs/" not in code, "a VFS path survived into the shipped file"


def test_a_referenced_asset_that_was_never_extracted_is_fatal(tmp_path):
    """The same seam at build time, from the failing side.

    An entry that references an asset no module carries (the shape a loader
    kind falling out of the accept-set produces) must stop the build rather
    than write a cli.js that dies - or silently degrades - at runtime.
    """
    entry = _entry_source(["image-processor.node", "ghost.node"])
    name, content = ASSET_PER_KIND["napi"]
    payload = fixtures.build_payload([
        ("/$bunfs/root/cli", entry.encode(), 1),
        ("/$bunfs/root/" + name, content, LOADER_ID["napi"]),
    ])
    binary = tmp_path / "synthetic-claude"
    binary.write_bytes(fixtures.build_elf(payload))
    out = tmp_path / "out"
    assert _run(EXTRACT, binary, out).returncode == 0

    result = _run(POSTPROCESS, out)

    assert result.returncode != 0, result.stdout
    assert "ghost.node" in result.stderr
    assert not (out / "cli.original.cjs").exists(), \
        "a cli.js reaching for a missing addon was written anyway"


def test_pipeline_rejects_a_reference_the_rewriter_cannot_resolve(tmp_path):
    """A `/$bunfs/` literal in a shape BUNFS_LITERAL does not match (here a
    nested path) still points at a filesystem that no longer exists once the JS
    runs outside the standalone. That must stop the build too."""
    payload = fixtures.build_payload([
        ("/$bunfs/root/cli",
         ("// @bun\n(function(exports, require, module, __filename, __dirname) {\n"
          'var x=require("/$bunfs/root/vendor/nested.node");\n'
          "})\n").encode(), 1),
        ("/$bunfs/root/vendor/nested.node", b"\x7fELFnested", LOADER_ID["napi"]),
    ])
    binary = tmp_path / "synthetic-claude"
    binary.write_bytes(fixtures.build_elf(payload))
    out = tmp_path / "out"
    assert _run(EXTRACT, binary, out).returncode == 0

    result = _run(POSTPROCESS, out)

    assert result.returncode != 0, result.stdout
    assert "bunfs" in result.stderr.lower()
    assert not (out / "cli.original.cjs").exists()


@pytest.mark.parametrize("kind", sorted(EB.WRITTEN_LOADERS))
def test_every_asset_loader_kind_is_written_to_disk(tmp_path, kind):
    """Each loader in the accept-set must reach disk as raw bytes.

    Dropping any one of them is invisible to the rest of this suite: the real
    binaries only carry `js`, `file` and `napi`, so `base64` has no coverage at
    all from the integration tests - and `base64` is exactly what the
    off-by-one loader enum silently dropped (a genuine base64 module carries
    id 11, which the old table mislabelled `dataurl`).
    """
    loader_id = LOADER_ID[kind]
    name, content = ASSET_PER_KIND[kind]

    out, _, _ = _build_pipeline(
        tmp_path,
        [("/$bunfs/root/" + name, content, loader_id)],
        _entry_source([name]))

    dest = out / "assets" / name
    assert dest.is_file(), "loader %r (id %d) never reached disk" % (kind, loader_id)
    assert dest.read_bytes() == content, "content was transformed, not copied verbatim"


def test_the_accept_set_covers_exactly_the_kinds_this_file_exercises():
    """Guards the parametrisation above: adding a loader to the accept-set
    without adding a case here would silently leave the new kind untested."""
    assert set(EB.WRITTEN_LOADERS) == set(ASSET_PER_KIND)


# --- both tools as programs ---------------------------------------------------

def test_extract_bun_as_a_program_reports_usage_and_exits_non_zero():
    result = _run(EXTRACT)

    assert result.returncode != 0
    assert "usage" in result.stderr


def test_extract_bun_as_a_program_refuses_a_non_bun_binary(tmp_path):
    """`build.sh /bin/ls`: a real file that is a perfectly good ELF with no
    .bun section. It must exit non-zero with an `error:` line, not a
    traceback, and must not leave a half-written output directory behind."""
    not_bun = tmp_path / "not-a-bun-standalone"
    not_bun.write_bytes(pathlib.Path("/bin/ls").read_bytes())
    out = tmp_path / "out"

    result = _run(EXTRACT, not_bun, out)

    assert result.returncode != 0
    assert result.stderr.startswith("error:"), result.stderr
    assert "Traceback" not in result.stderr
    assert not (out / "cli.original.js").exists()


def test_postprocess_as_a_program_reports_usage_and_exits_non_zero():
    result = _run(POSTPROCESS)

    assert result.returncode != 0
    assert "usage" in result.stderr
