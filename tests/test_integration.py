"""Tests against real 300 MB Claude binaries. Auto-skipped when absent.

Set NRC_TEST_ELF / NRC_TEST_MACHO to point at them; see tests/conftest.py for
the defaults and docs/runbook.md for where to get a Mach-O one. Without them a
clean host runs the hermetic suite only.

Two kinds of assertion live here and they mean opposite things, so they are
deliberately kept in separate tests rather than interleaved:

  INVARIANTS  - properties of the TOOLS. The pragma is stripped exactly once,
                exactly one IIFE is invoked, no /$bunfs/ reference survives,
                check() is clean. A failure means this repo is broken.
  MEASUREMENTS- facts about a PARTICULAR Claude build: how many /$bunfs/
                literals and build-time file:// URLs its entry module happens
                to contain, and how many `Bun.isStandaloneExecutable` gate call
                sites the scoped image shim finds and moves. A failure means
                Claude changed. That is the early warning this file exists to
                give, and the numbers are meant to be updated when it fires.

Measured on 2026-08-22 against the binaries named below; the gate counts on
2026-08-23, when they were added.
"""

import re
import struct

import pytest

pytestmark = pytest.mark.integration

# Counts that are properties of the Claude release, not of the tools.
#
# gate_calls_before/after and image_shim track the scoped image shim against
# the real thing. They are here and not in tests/test_image_shim.py because
# they are release facts, not tool contracts: the shim's own tests assert
# `after == before - 1` and `image_shim == 1` without naming a number. What
# this adds is the tripwire - a Claude build that grows, loses or renames gate
# call sites shows up as drift here instead of passing silently, and
# image_shim dropping to 0 is how a renamed anchor announces itself (that
# refusal is deliberately NOT fatal in postprocess.py, so nothing else fails).
#
# Keyed by the Claude version the entry module declares, so a host whose
# binary moves between recorded releases keeps passing, and one that reaches
# an UNRECORDED release says so instead of reporting drift against the wrong
# release's numbers. 2.1.231 was measured 2026-09-22 on the cached download
# (~/.cache/not-rusty-claude/claude-linux-x64-2.1.231.bin), once
# /usr/bin/claude had become a code-split build the legacy tests cannot take.
MEASURED = {
    "elf": {
        "2.1.222": {"version": "linux-x64 2.1.222", "assets": 5, "file_urls": 7,
                    "gate_calls_before": 21, "gate_calls_after": 20, "image_shim": 1},
        "2.1.231": {"version": "linux-x64 2.1.231", "assets": 7, "file_urls": 7,
                    "gate_calls_before": 24, "gate_calls_after": 23, "image_shim": 1},
    },
    "macho": {
        "2.1.239": {"version": "darwin-arm64 2.1.239", "assets": 9, "file_urls": 8,
                    "gate_calls_before": 23, "gate_calls_after": 22, "image_shim": 1},
    },
}

VERSION_RE = re.compile(r'VERSION:"(\d+\.\d+\.\d+)"')


def _entry_source(extract_bun, path):
    with open(path, "rb") as fh:
        buf = fh.read()
    off, size = extract_bun.find_bun_section(buf)
    payload, mod_off, mod_size, entry = extract_bun.parse_payload(buf[off:off + size])
    size_of = extract_bun.MODULE_RECORD_SIZE
    rec = payload[mod_off + entry * size_of:mod_off + (entry + 1) * size_of]
    _, _, content_off, content_size = struct.unpack_from("<IIII", rec, 0)
    return payload[content_off:content_off + content_size].decode("utf-8", "replace")


def _assert_invariants(postprocess, code):
    out, counts = postprocess.transform(code)

    assert counts["pragma"] == 1, "the `// @bun` pragma block was not stripped"
    assert counts["iife"] == 1, "the trailing IIFE was not invoked"
    assert counts["leftovers"] == [], "a /$bunfs/ reference survived the rewrite"
    assert postprocess.check(out, counts) == []
    return counts


def _assert_no_drift(counts, key, binary, code):
    """Report EVERY drifted measurement at once, with what to do about it.

    Asserting them one at a time short-circuits: a Claude release that shifts
    both counts shows only the first, so the maintainer fixes one number, re-runs,
    and is told about the next one.
    """
    m = VERSION_RE.search(code)
    version = m.group(1) if m else None
    if version not in MEASURED[key]:
        raise AssertionError(
            "no measurement is recorded for Claude %s (%s). The invariants passed; "
            "record this release's counts in MEASURED[%r][%r] once its artifact "
            "has been smoke-tested (docs/runbook.md). Measured now: %s"
            % (version, binary, key, version,
               {k: counts[k] for k in ("assets", "file_urls", "gate_calls_before",
                                       "gate_calls_after", "image_shim")}))
    expected = MEASURED[key][version]
    drifted = {name: (want, counts[name])
               for name, want in expected.items()
               if name != "version" and counts[name] != want}
    if not drifted:
        return
    lines = ["    %-10s expected %s, measured %s" % (name, want, got)
             for name, (want, got) in sorted(drifted.items())]
    raise AssertionError(
        "%d measured count(s) changed for %s.\n"
        "%s\n"
        "\n"
        "This does NOT mean the tools are broken - the invariants "
        "(pragma/IIFE/leftovers/check) are asserted separately and passed. It "
        "means the Claude build changed, which is what this tripwire is for.\n"
        "To clear it: confirm the new artifact still runs (docs/runbook.md's "
        "smoke test), then update MEASURED[%r] in this file to the measured "
        "values, and the counts in docs/status.md's verification matrix.\n"
        "Binary under test: %s"
        % (len(drifted), expected["version"], "\n".join(lines), key, binary))


def test_real_elf_binary_extracts(extract_bun, real_elf_binary, tmp_path):
    out = tmp_path / "x"
    extract_bun.extract(real_elf_binary, str(out))

    assert (out / "cli.original.js").stat().st_size > 10_000_000
    assets = sorted(p.name for p in (out / "assets").iterdir())
    assert "image-processor.node" in assets
    assert "mermaid.min.js" in assets
    # findings 5a: stored content is ALWAYS raw bytes, whatever the loader id
    # says (these addons are napi, id 10) - so this must be a real ELF
    assert (out / "assets" / "image-processor.node").read_bytes()[:4] == b"\x7fELF"


def test_real_elf_transform_invariants_hold(extract_bun, postprocess, real_elf_binary):
    _assert_invariants(postprocess, _entry_source(extract_bun, real_elf_binary))


def test_real_elf_measured_counts_have_not_drifted(extract_bun, postprocess,
                                                   real_elf_binary):
    code = _entry_source(extract_bun, real_elf_binary)
    counts = _assert_invariants(postprocess, code)

    _assert_no_drift(counts, "elf", real_elf_binary, code)


def test_real_macho_binary_extracts(extract_bun, real_macho_binary, tmp_path):
    out = tmp_path / "x"
    extract_bun.extract(real_macho_binary, str(out))

    assets = sorted(p.name for p in (out / "assets").iterdir())
    assert "computer-use-swift.node" in assets
    assert "payload.template.html.asset" in assets
    # universal Mach-O magic (0xCAFEBABE big-endian) or thin arm64 (0xFEEDFACF LE)
    head = (out / "assets" / "computer-use-swift.node").read_bytes()[:4]
    assert head in (b"\xca\xfe\xba\xbe", b"\xcf\xfa\xed\xfe")


def test_real_macho_transform_invariants_hold(extract_bun, postprocess,
                                              real_macho_binary):
    _assert_invariants(postprocess, _entry_source(extract_bun, real_macho_binary))


def test_real_macho_measured_counts_have_not_drifted(extract_bun, postprocess,
                                                     real_macho_binary):
    code = _entry_source(extract_bun, real_macho_binary)
    counts = _assert_invariants(postprocess, code)

    _assert_no_drift(counts, "macho", real_macho_binary, code)


# --- the code-split shape (2.1.280+, docs/findings.md 14) ---------------------
#
# Same split as above: the invariants are tool contracts, MEASURED_ESM is what a
# particular release happens to contain. Measured 2026-09-22 on the host's
# /usr/bin/claude.
MEASURED_ESM = {
    "2.1.280": {"modules": 2196, "js": 1975, "text": 84, "copied": 137,
                "specifier": 138195, "expression": 485, "text_refs": 84,
                "prefix_constants": 1},
}


def _tree_version(out):
    entry = (out / "original" / "cli").read_text(encoding="latin-1")
    m = VERSION_RE.search(entry) or re.search(r"// Version: (\d+\.\d+\.\d+)", entry)
    return m.group(1) if m else None


def test_real_esm_binary_extracts_to_a_tree(extract_bun, real_esm_binary, tmp_path):
    out = tmp_path / "x"
    extract_bun.extract(real_esm_binary, str(out))

    manifest = __import__("json").loads((out / "manifest.json").read_text())
    loaders = {m["loader"] for m in manifest["modules"]}
    assert {"js", "text", "file", "napi"} <= loaders
    assert manifest["entry"] == "cli"
    # every module on disk, byte count as recorded
    for m in manifest["modules"]:
        assert (out / "original" / m["path"]).stat().st_size == m["size"], m["path"]
    # findings 5a still holds: stored content is raw bytes - an ELF addon
    addon = next(m for m in manifest["modules"] if m["loader"] == "napi")
    assert (out / "original" / addon["path"]).read_bytes()[:4] == b"\x7fELF"


def test_real_esm_tree_rewires_cleanly_and_counts_have_not_drifted(
        extract_bun, postprocess, real_esm_binary, tmp_path):
    out = tmp_path / "x"
    extract_bun.extract(real_esm_binary, str(out))
    totals, errors = postprocess.transform_tree(str(out))

    assert errors == [], errors                       # invariant
    assert totals["leftovers"] == []                  # invariant
    assert (out / "cli.js").is_file() and (out / "root" / "cli.js").is_file()

    version = _tree_version(out)
    if version not in MEASURED_ESM:
        raise AssertionError(
            "no measurement recorded for code-split Claude %s; invariants passed. "
            "Record it in MEASURED_ESM once the harness is green: %s"
            % (version, {k: totals[k] for k in MEASURED_ESM["2.1.280"]}))
    drifted = {k: (v, totals[k]) for k, v in MEASURED_ESM[version].items() if totals[k] != v}
    assert not drifted, "Claude %s changed shape (expected, measured): %s" % (version, drifted)
