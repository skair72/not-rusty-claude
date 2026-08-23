"""The scoped `Bun.isStandaloneExecutable` image shim in tools/postprocess.py.

Design of record:
docs/superpowers/specs/2026-08-23-scoped-image-shim-design.md (Part 1).

The shim exists because one consequence of "we are not a standalone" is wrong:
native image processing is gated behind that flag, so the Read tool cannot
resize a large image. The tempting fix - set the flag - is MEASURED to break
Grep (docs/findings.md §11), so the rewrite is scoped to a single call site.

Everything therefore hangs on one property: *nothing else moved*. That is not
asserted here by spot-checking the sites that matter; it is asserted by
reconstructing the shimmed output with the single known rewrite undone and
demanding the result be byte-identical to the unshimmed output. Spot checks
(the ripgrep site) are kept as well, because when this fails the spot check is
what tells a reader which gate got flipped and why that is bad.

The hermetic tests below use synthetic strings only. The two tests that need a
real Claude binary take the conftest fixture and skip cleanly without one.
"""

import re

import pytest


# --- synthetic entry modules -------------------------------------------------
#
# Real shapes, copied verbatim from the extracted entry modules and then cut
# down. The gate declarations are the two that exist in the wild:
#   linux-x64 2.1.222   function CE(){return Bun.isStandaloneExecutable===!0}
#   darwin-arm64 2.1.239 function AE(){return typeof Bun<"u"&&...===!0}

GATE_DEF_PLAIN = "function CE(){return Bun.isStandaloneExecutable===!0}"
GATE_DEF_TYPEOF = 'function AE(){return typeof Bun<"u"&&Bun.isStandaloneExecutable===!0}'

HEAD = "// @bun @bytecode @bun-cjs\n(function(exports, require, module, __filename, __dirname) {\n"
TAIL = 'r("cli_after_main_complete")}PSE();})\n'

# The image site, verbatim from build/extract/cli.original.cjs (linux-x64
# 2.1.222) with only the gate name parameterised. The gate call ends 125 bytes
# before the anchor here, exactly as it does in both real binaries.
IMAGE_SITE = (
    'async function uYe(){if(Fbo)return Fbo.default;if(%s())try{let r=await '
    'Promise.resolve().then(() => (uys(),cys)),n=r.sharp||r.default;return '
    'Fbo={default:n},n}catch{console.warn("Native image processor not '
    'available, falling back to sharp");return null}}'
)

# The ripgrep site, verbatim from the same file. This is the gate that must
# NEVER be flipped: with it true, "embedded ripgrep" means "re-exec
# process.execPath with argv0 rg", process.execPath is bun, and Grep answers
# "No matches found" for a string that exists (docs/findings.md §11).
RIPGREP_SITE = (
    'let{cmd:r}=Syo("rg",[]);if(r!=="rg")return{mode:"system",command:r,'
    'args:[]}}if(%s()){let r={mode:"embedded",command:process.execPath,'
    'args:["--no-config"],argv0:"rg"};if(Lfe(process.execPath))return r;'
)

# A third site, in the shape the boundary rule has to get right: the gate call
# is preceded by the spread `...`, which is why _gate_call_re deliberately does
# not exclude a preceding dot. Three sites per real binary look like this.
IMAGE_ANCHOR_TEXT = "Native image processor not available"

SPREAD_SITE = 'let e=process.execPath,r=[...%s()?[e]:[e,process.argv[1]]];'


def _module(gate_def=GATE_DEF_PLAIN, name="CE", sites=(RIPGREP_SITE, IMAGE_SITE, SPREAD_SITE)):
    """A postprocess-able entry module: pragma, wrapper, gate, sites, `})`."""
    return HEAD + gate_def + ";" + "".join(s % name for s in sites) + TAIL


def _single_edit(before, after):
    """The one contiguous (offset, removed, inserted) difference, or fail.

    Comparing common prefix and common suffix rather than diffing: any second
    edit anywhere in the file inflates `removed`/`inserted` to span everything
    between the two, which is exactly the failure this is looking for.
    """
    head = 0
    while head < min(len(before), len(after)) and before[head] == after[head]:
        head += 1
    tail = 0
    while (tail < min(len(before), len(after)) - head
           and before[len(before) - 1 - tail] == after[len(after) - 1 - tail]):
        tail += 1
    return head, before[head:len(before) - tail], after[head:len(after) - tail]


# --- capturing the gate ------------------------------------------------------

@pytest.mark.parametrize("gate_def,name", [
    (GATE_DEF_PLAIN, "CE"),          # linux-x64 2.1.222
    (GATE_DEF_TYPEOF, "AE"),         # darwin-arm64 2.1.239
])
def test_both_real_gate_declaration_shapes_are_recognised(postprocess, gate_def, name):
    """2.1.239 added a `typeof Bun<"u"&&` guard in front of the property test.
    A pattern that only knew the 2.1.222 shape would silently find no gate on
    macOS and produce an unshimmed artifact with no error."""
    out, counts = postprocess.transform(_module(gate_def, name))

    assert counts["gate_name"] == name
    assert counts["image_shim"] == 1
    assert counts["image_shim_reason"] is None
    assert postprocess.check(out, counts) == []


def test_the_declaration_itself_is_not_counted_as_a_call_site(postprocess):
    """`function CE()` contains the token `CE()`. Counting it would not break
    the invariant's arithmetic, but it would make the number printed at build
    time - and quoted in the docs - wrong by one."""
    _, counts = postprocess.transform(_module())

    assert counts["gate_calls_before"] == 3, "ripgrep + image + spread"


def test_a_spread_prefixed_call_site_is_counted(postprocess):
    """`[...CE()?[e]:[...]]`. A boundary rule that excluded a preceding `.`
    would drop three real call sites per binary from the count, and the safety
    invariant cannot notice a rewrite spreading into a site it never counted."""
    _, counts = postprocess.transform(_module(sites=(IMAGE_SITE, SPREAD_SITE)))

    assert counts["gate_calls_before"] == 2


def test_lookalike_identifiers_are_not_counted_as_call_sites(postprocess):
    """The real linux entry module contains `isGCE()` and `_checkIsGCE()`.
    Counting those as gate calls would make the before/after arithmetic
    meaningless - and a plain `CE()` search finds 4 of them."""
    noise = "class X{get isGCE(){return 1}}async function _checkIsGCE(){return this._checkIsGCE()}"
    _, counts = postprocess.transform(_module(sites=(IMAGE_SITE,)) + noise)

    assert counts["gate_calls_before"] == 1


# --- exactly one site, and it is the right one -------------------------------

def test_exactly_one_site_is_rewritten_and_it_is_the_one_before_the_anchor(postprocess):
    src = _module()

    out, counts = postprocess.transform(src)

    assert counts["image_shim"] == 1
    assert counts["gate_calls_after"] == counts["gate_calls_before"] - 1
    # the rewritten call is the one inside the image function, not either of
    # the other two
    assert 'if(true)try{let r=await Promise.resolve()' in out
    assert out.count("Native image processor not available") == 1


def test_every_other_gate_call_site_is_byte_identical(postprocess):
    """The strong form of "the rewrite did not spread": take the shimmed
    output, put `CE()` back where `true` went, and demand the result equal the
    unshimmed output byte for byte. Any second rewrite anywhere in the file
    makes the single-edit reconstruction fail."""
    src = _module()

    shimmed, counts = postprocess.transform(src)
    plain, plain_counts = postprocess.transform(src, image_shim=False)

    offset, removed, inserted = _single_edit(plain, shimmed)
    assert removed == "CE()"
    assert inserted == "true"
    assert shimmed[:offset] + removed + shimmed[offset + len(inserted):] == plain
    assert plain_counts["image_shim"] == 0
    assert plain_counts["gate_calls_after"] == plain_counts["gate_calls_before"]


def test_the_ripgrep_gate_site_survives_untouched(postprocess):
    """The named, measured harm of flipping the flag globally: with the
    ripgrep gate true, "embedded ripgrep" becomes "re-exec process.execPath
    with argv0 rg", process.execPath is bun, and a Grep for a string that
    exists answers `No matches found` (docs/findings.md §11). That is a wrong
    answer, not an error, so it is the one site this test names explicitly."""
    out, _ = postprocess.transform(_module())

    assert RIPGREP_SITE % "CE" in out
    assert 'if(true){let r={mode:"embedded"' not in out


# --- the refusals: warn, never fail ------------------------------------------

def test_no_anchor_means_no_rewrite_and_no_error(postprocess):
    """A future Claude that renames the string must degrade to exactly today's
    artifact, not fail the build. An outage is a much worse outcome than the
    image gap the shim closes."""
    src = _module(sites=(RIPGREP_SITE, SPREAD_SITE))

    out, counts = postprocess.transform(src)

    assert counts["gate_name"] == "CE"
    assert counts["image_shim"] == 0
    assert counts["gate_calls_after"] == counts["gate_calls_before"] == 2
    assert "anchor" in counts["image_shim_reason"]
    assert postprocess.check(out, counts) == []


def test_no_gate_declaration_at_all_means_no_rewrite_and_no_error(postprocess):
    """Nothing to capture, nothing to count, nothing to rewrite - and the
    output is still a perfectly good CommonJS module."""
    out, counts = postprocess.transform(HEAD + "var x=1;" + TAIL)

    assert counts["gate_name"] is None
    assert counts["image_shim"] == 0
    assert counts["gate_calls_before"] == counts["gate_calls_after"] == 0
    assert postprocess.check(out, counts) == []


def test_two_anchors_refuse_to_guess(postprocess):
    """With the anchor duplicated there is no way to tell which occurrence
    guards image processing, and picking the wrong one flips an unrelated
    gate. Refusing is the only safe answer, and it is still not fatal."""
    src = _module(sites=(RIPGREP_SITE, IMAGE_SITE, IMAGE_SITE.replace("uYe", "uYf")))

    out, counts = postprocess.transform(src)

    assert out.count("Native image processor not available") == 2
    assert counts["image_shim"] == 0
    assert counts["gate_calls_after"] == counts["gate_calls_before"]
    assert "2 times" in counts["image_shim_reason"]
    assert postprocess.check(out, counts) == []


def _image_site_with_gap(pad):
    """The image site with `pad` bytes of filler wedged between the gate call
    and the anchor. Unpadded, `CE()` starts 140 bytes before the anchor - the
    125 bytes measured between the call's END and the anchor in both real
    binaries, plus the 4-byte call itself and 11 bytes of `var pad='';`."""
    filler = "var pad=%r;" % ("x" * pad)
    return (IMAGE_SITE % "CE").replace("if(CE())try{", "if(CE())try{" + filler)


@pytest.mark.parametrize("pad,gap,rewritten", [
    (0, 140, 1),      # the real-world shape, unpadded
    (260, 400, 1),    # the call starts exactly on the window boundary
    (900, 1040, 0),   # comfortably outside it
])
def test_the_backwards_search_is_bounded(postprocess, pad, gap, rewritten):
    """The window is what stops the search wandering into a neighbouring
    function. Deliberately written against fixed byte distances rather than
    against IMAGE_SHIM_WINDOW itself: a test that derives its own padding from
    the constant passes for every value of the constant, including one wide
    enough to reach the ripgrep gate.

    Measured for scale: in the real linux entry module the next gate call up
    from the image one is 506,792 bytes away, and in the darwin one 1,732,905.
    """
    site = _image_site_with_gap(pad)
    call_start = site.index("if(CE())try{") + len("if(")
    assert site.index(IMAGE_ANCHOR_TEXT) - call_start == gap, "fixture drifted"

    out, counts = postprocess.transform(
        _module(sites=(RIPGREP_SITE,)).replace(TAIL, site + TAIL))

    assert counts["gate_name"] == "CE"
    assert counts["image_shim"] == rewritten
    if not rewritten:
        assert "no CE() call" in counts["image_shim_reason"]
    assert postprocess.check(out, counts) == []


# --- the invariant is fatal --------------------------------------------------

@pytest.mark.parametrize("before,after,applied,why", [
    (21, 19, 1, "the rewrite hit two sites while claiming one"),
    (21, 21, 1, "it claims a rewrite that did not happen"),
    (21, 20, 0, "a call site vanished with no rewrite claimed"),
    (21, 0, 1, "the global flip this shim exists to avoid"),
    (21, 20, 2, "more rewrites than the one site the shim may touch"),
])
def test_a_broken_count_invariant_is_fatal(postprocess, before, after, applied, why):
    """This arithmetic is the entire safety argument for a text rewrite of a
    23 MB minified file. In every one of these outcomes the bookkeeping and
    the file disagree, so nobody can say which gates are still false - and the
    first one the substitution would reach is embedded ripgrep. Fatal: the
    build stops and cli.original.cjs is never written."""
    out, counts = postprocess.transform(_module())
    counts.update(gate_calls_before=before, gate_calls_after=after,
                  image_shim=applied)

    errors = postprocess.check(out, counts)

    assert any("image shim accounting" in e for e in errors), (why, errors)
    assert any("ripgrep" in e for e in errors), "the message must name the harm"


def test_the_honest_counts_are_not_reported_as_a_violation(postprocess):
    """The companion to the parametrised failures above: without this, a
    check() that flagged every input would pass all of them."""
    out, counts = postprocess.transform(_module())
    assert (counts["gate_calls_before"], counts["gate_calls_after"],
            counts["image_shim"]) == (3, 2, 1)

    assert postprocess.check(out, counts) == []


# --- the opt-out -------------------------------------------------------------

def test_the_opt_out_is_read_in_main_not_in_transform(postprocess, monkeypatch):
    """transform() must stay a pure function of its arguments: the A/B in
    docs/findings.md §11 is driven from one process, and an env var read down
    here would make the two halves depend on interpreter state."""
    monkeypatch.setenv("NRC_NO_IMAGE_SHIM", "1")

    _, counts = postprocess.transform(_module())

    assert counts["image_shim"] == 1, "transform() honoured the environment"


def test_the_opt_out_still_measures_the_gate(postprocess):
    """The unshimmed build must report the same numbers as the shimmed one,
    minus the rewrite. Reporting `None` for the gate would make the two halves
    of the A/B distinguishable by their build logs for the wrong reason."""
    _, counts = postprocess.transform(_module(), image_shim=False)

    assert counts["gate_name"] == "CE"
    assert counts["gate_calls_before"] == counts["gate_calls_after"] == 3
    assert counts["image_shim"] == 0
    assert "NRC_NO_IMAGE_SHIM" in counts["image_shim_reason"]


# --- against the real thing --------------------------------------------------
#
# Skipped, not failed, without a real binary - see tests/conftest.py's
# real_elf_binary fixture and pytest.ini's `integration` marker.

def _real_entry_source(extract_bun, path):
    import struct
    with open(path, "rb") as fh:
        buf = fh.read()
    off, size = extract_bun.find_bun_section(buf)
    payload, mod_off, _, entry = extract_bun.parse_payload(buf[off:off + size])
    size_of = extract_bun.MODULE_RECORD_SIZE
    rec = payload[mod_off + entry * size_of:mod_off + (entry + 1) * size_of]
    _, _, content_off, content_size = struct.unpack_from("<IIII", rec, 0)
    return payload[content_off:content_off + content_size].decode("utf-8", "replace")


@pytest.mark.integration
def test_real_elf_entry_module_gets_exactly_one_rewrite(postprocess, extract_bun,
                                                        real_elf_binary):
    """The synthetic modules above are shapes this file chose. This is the
    23 MB of real minified JavaScript the shim actually ships against."""
    src = _real_entry_source(extract_bun, real_elf_binary)

    out, counts = postprocess.transform(src)

    assert counts["gate_name"] is not None, "no isStandaloneExecutable gate found"
    assert counts["image_shim"] == 1
    assert counts["gate_calls_after"] == counts["gate_calls_before"] - 1
    assert counts["gate_calls_before"] > 1, "a lone call site proves nothing"
    assert postprocess.check(out, counts) == []


@pytest.mark.integration
def test_real_elf_entry_module_changes_in_exactly_one_place(postprocess, extract_bun,
                                                            real_elf_binary):
    """The byte-identical assertion, on the real file: reconstruct the shimmed
    output by undoing the single rewrite and demand it equal the unshimmed
    output. Every other gate call site in the binary - ripgrep, the seccomp
    sandbox, the installer identity, the two MCP self-spawns, the telemetry
    flag - is covered by this one comparison."""
    src = _real_entry_source(extract_bun, real_elf_binary)

    shimmed, counts = postprocess.transform(src)
    plain, _ = postprocess.transform(src, image_shim=False)

    offset, removed, inserted = _single_edit(plain, shimmed)
    assert removed == counts["gate_name"] + "()"
    assert inserted == "true"
    assert shimmed[:offset] + removed + shimmed[offset + len(inserted):] == plain
    # and the rewrite really is the image gate, not some other call. Measured
    # on linux-x64 2.1.222 the anchor sits 129 bytes past the start of the
    # rewritten call; the bound is loose so a minifier reshuffling the try body
    # does not turn this into a version tripwire.
    assert plain.index("Native image processor not available") - offset < 200

    # the specific measured harm, named again on the real artifact
    rg = re.compile(r'if\(r!=="rg"\)return\{mode:"system",command:r,args:\[\]\}\}'
                    r'if\(%s\(\)\)' % re.escape(counts["gate_name"]))
    assert rg.search(shimmed), "the embedded-ripgrep gate site was disturbed"


# --- main() and build.sh must say which artifact this is ---------------------
#
# The two builds differ in four bytes and behave identically until someone
# Reads a large image. Silence here is the failure mode that matters most:
# nobody would notice for weeks.

import pathlib
import subprocess
import sys

import fixtures

ROOT = pathlib.Path(__file__).resolve().parent.parent
BASE_ENV = {"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1"}


def _run_postprocess(d, env=None):
    (d / "cli.original.js").write_text(_module())
    return subprocess.run(
        [sys.executable, str(ROOT / "tools" / "postprocess.py"), str(d)],
        capture_output=True, text=True, env=dict(BASE_ENV, **(env or {})))


def test_main_prints_the_gate_name_and_shim_count_on_stdout(tmp_path):
    result = _run_postprocess(tmp_path)

    assert result.returncode == 0, result.stderr
    assert "image shim gate        : CE" in result.stdout
    assert "image shim applied     : 1" in result.stdout
    assert "3 -> 2" in result.stdout


def test_the_env_opt_out_skips_the_rewrite_and_warns(tmp_path):
    """NRC_NO_IMAGE_SHIM=1 regenerates the "as shipped" half of the A/B from
    this same tree. It must be visibly, noisily different in the build log,
    because the artifacts themselves are not."""
    shimmed = tmp_path / "on"
    plain = tmp_path / "off"
    shimmed.mkdir()
    plain.mkdir()

    on = _run_postprocess(shimmed)
    off = _run_postprocess(plain, env={"NRC_NO_IMAGE_SHIM": "1"})

    assert (on.returncode, off.returncode) == (0, 0), off.stderr
    assert "image shim applied     : 0" in off.stdout
    assert "image shim NOT applied" in off.stderr
    a = (shimmed / "cli.original.cjs").read_bytes()
    b = (plain / "cli.original.cjs").read_bytes()
    assert a != b
    assert len(a) == len(b), "CE() and true are both four bytes"
    assert sum(x != y for x, y in zip(a, b)) == 4


def _synthetic_binary(path, entry):
    payload = fixtures.build_payload([("/$bunfs/root/cli", entry, 1)])
    path.write_bytes(fixtures.build_elf(payload))
    return path


def _build(out_dir, native, env=None):
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "build.sh"), str(native)],
        capture_output=True, text=True,
        env=dict(BASE_ENV, HOME=str(out_dir), OUT_DIR=str(out_dir),
                 BUN_BIN="/nonexistent/bun", **(env or {})))


def test_build_sh_reports_the_shim_as_applied(tmp_path):
    native = _synthetic_binary(tmp_path / "native", _module().encode())

    result = _build(tmp_path / "out", native)

    assert result.returncode == 0, result.stderr
    assert "image shim APPLIED" in result.stdout
    assert "NOT APPLIED" not in result.stdout + result.stderr


def test_build_sh_reports_the_shim_as_not_applied(tmp_path):
    """No anchor in this entry module, so the shim finds nothing. The build
    still succeeds - that is the point - which is exactly why it has to say so
    out loud."""
    native = _synthetic_binary(
        tmp_path / "native", _module(sites=(RIPGREP_SITE,)).encode())

    result = _build(tmp_path / "out", native)

    assert result.returncode == 0, result.stderr
    assert "image shim NOT APPLIED" in result.stderr
    assert "image shim APPLIED" not in result.stdout


def test_build_sh_leaves_no_postprocess_log_in_the_output_dir(tmp_path):
    """The tee'd log lives inside the staging directory so the swap disposes
    of it. A stray file in OUT_DIR would also break the build script's own
    "nothing left behind" test."""
    native = _synthetic_binary(tmp_path / "native", _module().encode())
    out = tmp_path / "out"

    assert _build(out, native).returncode == 0

    assert sorted(p.name for p in out.iterdir()) == ["extract"]
    assert list(out.rglob(".postprocess.log")) == []
