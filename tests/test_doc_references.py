"""Cross-references into docs/findings.md, checked mechanically.

A section number is a moving target: docs/findings.md has been renumbered
twice, and the code, scripts and docs that cite it by number do not move with
it. That failed silently in both directions - references to a section that no
longer existed dangled harmlessly for a while, and then a new section 11 was
added and 32 references written for the OLD section 11 (the equivalence gap)
started resolving to it, sending the reader to the wrong page for this
project's central safety caveat. `scripts/build.sh` printed one of them.

So: every citation must name a section that exists, and the files whose
citations are all about one topic must still land on that topic's section.
Static text analysis only - no network, no bun, no binaries.
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent
FINDINGS = ROOT / "docs" / "findings.md"
SELF = pathlib.Path(__file__).resolve()

# Every spelling in use: "findings.md §10", "findings.md section 10",
# "findings.md 10", "findings §10", "[findings.md](../../findings.md) §10".
REF_RE = re.compile(r"findings(?:\.md)?\)?\s*(?:§\s*|section\s+)?(\d+[a-z]?)\b")

SCANNED_SUFFIXES = {".py", ".sh", ".cjs", ".mjs", ".md"}
SKIPPED_DIRS = {"build", ".git", "__pycache__", ".pytest_cache", ".superpowers"}

# Files whose findings citations are all about one subject. The value is the
# set of section titles they may resolve to, matched as substrings.
ALLOWED = {
    "scripts/build.sh": {"equivalence gap", "Post-processing"},
    "scripts/ab-equivalence.sh": {"equivalence gap"},
    "scripts/mock-messages-api.mjs": {"equivalence gap"},
    "tools/postprocess.py": {"equivalence gap", "Post-processing"},
    "tests/test_image_shim.py": {"equivalence gap"},
    "tests/test_build_script.py": {"equivalence gap"},
}


def _sections():
    """{number -> title} for every numbered heading in findings.md."""
    out = {}
    for line in FINDINGS.read_text().splitlines():
        m = re.match(r"^#{2,3} (\d+[a-z]?)\.\s+(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _references():
    """(repo-relative path, line number, section number) for every citation."""
    found = []
    for path in ROOT.rglob("*"):
        if path.suffix not in SCANNED_SUFFIXES or not path.is_file():
            continue
        if path.resolve() == SELF or SKIPPED_DIRS & set(path.relative_to(ROOT).parts):
            continue
        rel = str(path.relative_to(ROOT))
        for n, line in enumerate(path.read_text().splitlines(), 1):
            for m in REF_RE.finditer(line):
                found.append((rel, n, m.group(1)))
    return found


def test_findings_has_numbered_sections_to_cite():
    sections = _sections()
    assert "10" in sections and len(sections) >= 10, sections


def test_every_findings_cross_reference_names_a_section_that_exists():
    sections = _sections()
    refs = _references()
    assert refs, "no cross-references found - the regex stopped matching"
    dangling = [(f, n, s) for f, n, s in refs if s not in sections]
    assert not dangling, "citations of sections findings.md does not have: %s" % dangling


def test_topic_specific_files_still_cite_their_topic():
    """The renumbering failure, caught by content rather than by existence: a
    citation that still resolves must resolve to the right subject."""
    sections = _sections()
    wrong = []
    for path, line, number in _references():
        allowed = ALLOWED.get(path)
        if allowed is None:
            continue
        title = sections[number]
        if not any(word in title for word in allowed):
            wrong.append("%s:%d cites §%s (%r), expected one of %s"
                         % (path, line, number, title, sorted(allowed)))
    assert not wrong, "\n".join(wrong)
