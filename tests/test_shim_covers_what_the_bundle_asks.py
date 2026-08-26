"""Cross-reference: what the bundle asks of Bun, against what the shim refuses.

This file exists because of a specific, expensive failure. The extracted build
would not paint its REPL under Node. That was chased through native addons,
Bun.Terminal, a fullscreen renderer, a keychain freeze, MCP servers, ripgrep
and an eight-version bundle gap - across two machines and most of a day -
before a runtime trace showed the cause: the bundle calls Bun.YAML.parse and
Bun.wrapAnsi, the shim refuses both, and a React error boundary swallowed the
throws so nothing was printed and nothing crashed.

Every fact needed to predict that was already on disk. `Bun.wrapAnsi` occurs
once in the bundle; the shim's own header names wrapAnsi and YAML as the first
entries an interactive run would hit. Nobody had to run anything.

So: enumerate what the bundle references, enumerate what the shim will not
answer, and pin the intersection. A referenced-and-refused api is a runtime
failure waiting for whichever code path reaches it first, and this test is
where that list is visible without a debugger.
"""

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIM = ROOT / "scripts" / "bun-shim.cjs"

# Every api the shim refuses that the bundle also mentions, measured against
# linux-x64 2.1.231 on 2026-08-26. It is the whole refusal list: the bundle
# mentions all of them somewhere. That is the honest limit of a static
# cross-reference - it narrows to fifteen candidates, it does not name one.
# Its value is drift: a new Claude version that reaches for a sixteenth, or an
# api implemented here, both change this set and force a look.
KNOWN_REFUSED_AND_REFERENCED = {
    "SQL", "TOML.parse", "Transpiler", "YAML.parse", "YAML.stringify",
    "connect", "deepEquals", "file", "generateHeapSnapshot", "listen",
    "semver.order", "semver.satisfies", "serve", "spawn",
}

# What a running interactive session reaches and can still be refused by.
# Measured on darwin-arm64 2026-08-26 with scripts/node-trace.cjs watching the
# object the shim installs: the REPL called YAML.parse 44 times and wrapAnsi 5
# times, and a React error boundary swallowed every throw - the TUI showed
# nothing and did not crash.
#
# Both are implemented now. wrapAnsi answers unconditionally, byte-equal to Bun
# over 2,800 cases, so it is gone from here entirely. YAML.parse answers the
# subset frontmatter uses and still refuses anchors, tags, complex keys,
# multi-document input, tab indentation and explicit block scalar indents - so
# it stays, because a frontmatter file using one of those will still fail.
REACHED_ON_THE_INTERACTIVE_PATH = {"YAML.parse"}


def _bundle_references(artifact):
    """Every `Bun.x` and `Bun.x.y` the bundle mentions."""
    text = pathlib.Path(artifact).read_text(encoding="utf-8", errors="replace")
    found = set()
    for match in re.finditer(r"\bBun\.([A-Za-z_$][\w$]*)(?:\.([A-Za-z_$][\w$]*))?", text):
        head, tail = match.group(1), match.group(2)
        found.add(head)
        if tail:
            found.add(f"{head}.{tail}")
    return found


def _shim_refusals():
    """Every api the shim answers by throwing, named as it names itself."""
    text = SHIM.read_text()
    return {m.group(1) for m in re.finditer(r'unsupported\(\s*"([^"(]+)"', text)}


def test_the_refusals_the_bundle_actually_reaches_are_exactly_the_known_ones(built_artifact):
    referenced = _bundle_references(built_artifact)
    refused = _shim_refusals()
    collided = {api for api in refused if api in referenced}

    assert collided == KNOWN_REFUSED_AND_REFERENCED, (
        "the set of Bun apis that the bundle calls AND the shim refuses has "
        f"changed.\n  now:      {sorted(collided)}\n  pinned:   "
        f"{sorted(KNOWN_REFUSED_AND_REFERENCED)}\n"
        "Additions are new runtime failures on whichever path reaches them "
        "first, and they will be silent if a caller catches. Removals mean an "
        "api was implemented - update the pin and say so in the shim header.")


def test_every_pinned_refusal_is_still_refused_by_the_shim():
    """Guards the other direction: the pin cannot outlive the refusal."""
    refused = _shim_refusals()
    stale = KNOWN_REFUSED_AND_REFERENCED - refused
    assert not stale, (
        f"pinned as refused but the shim no longer refuses: {sorted(stale)}. "
        "If it is implemented now, drop it from the pin.")


def test_what_a_running_session_reaches_is_a_subset_of_what_is_pinned():
    """The runtime evidence has to stay consistent with the static pin.

    If an api is measured as reached but is no longer refused, it was
    implemented and this constant is stale - which would quietly claim the
    interactive path is still blocked when it is not.
    """
    stale = REACHED_ON_THE_INTERACTIVE_PATH - _shim_refusals()
    assert not stale, (
        f"measured as reached and refused, but no longer refused: {sorted(stale)}. "
        "Implemented? Then drop it here and from the shim header's prediction.")


def test_the_shim_header_names_the_apis_an_interactive_run_hits_first():
    """The header made this prediction; keep it true or keep it updated.

    It was correct and ignored for a day. If the pin changes and the header
    does not, the next reader is misled by the same sentence that would have
    saved this one.
    """
    text = SHIM.read_text()
    header = text[:text.index("*/") + 2]
    for api in REACHED_ON_THE_INTERACTIVE_PATH:
        root = api.split(".")[0]
        assert root in header, (
            f"{root} is refused and reached by the bundle, but the shim's "
            "header does not mention it")
