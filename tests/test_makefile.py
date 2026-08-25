"""Tests for the Makefile itself - not for the pipeline it drives.

These exist because the Makefile is the first thing a new host runs and the
one file here that CANNOT be exercised on the machine it was written for:
macOS ships GNU Make 3.81 (2006) and a BSD userland, and this repo is
developed on Linux with GNU Make 4.3 and GNU coreutils. Every check below is
one of the two failure modes that difference produces - a construct 3.81
cannot parse, or a tool flag that only GNU's version accepts - plus the
help/target drift that would make `make help` quietly stop listing a target.

Everything here is static text analysis plus one `make help`, so it needs no
network, no bun and no Claude binary, and adds no new skip condition to the
suite: the only prerequisite is `make`, and `make` is how these tests are
normally reached in the first place.
"""

import pathlib
import re
import shutil
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
MAKEFILE = ROOT / "Makefile"


def _text():
    return MAKEFILE.read_text()


def _code():
    """The Makefile with whole-line `#` comments stripped.

    The header comment names the very constructs the checks below forbid, so
    the checks have to look at code only. Recipe lines can never be dropped
    by mistake here: they start with a hard tab, and a separate test forbids
    `#` comments inside a recipe.
    """
    return "\n".join(
        ln for ln in _text().splitlines() if not re.match(r"^\s*#", ln)
    )


def _recipe_lines():
    """Every line that belongs to a recipe (i.e. starts with a hard tab)."""
    return [ln for ln in _text().splitlines() if ln.startswith("\t")]


def _declared_targets():
    """Target names defined at column 0, e.g. `build:` or `distclean: clean`."""
    names = []
    for line in _text().splitlines():
        m = re.match(r"^([A-Za-z][A-Za-z0-9_-]*)\s*:(?!=)", line)
        if m:
            names.append(m.group(1))
    return names


def _phony_targets():
    for line in _text().splitlines():
        if line.startswith(".PHONY:"):
            return line.split(":", 1)[1].split()
    return []


def _make(*args):
    return subprocess.run(
        ["make", "-C", str(ROOT), *args],
        capture_output=True, text=True, timeout=120,
    )


requires_make = pytest.mark.skipif(
    shutil.which("make") is None, reason="no make on PATH"
)


def test_every_target_is_phony_and_listed_in_help():
    """Drift guard. `make help` is hand-written (a printf block, not a scrape
    of ## comments, because BSD and GNU sed/awk disagree about enough to make
    a scraper its own portability problem), so nothing but a test stops a new
    target from being invisible."""
    declared = _declared_targets()
    phony = _phony_targets()
    help_text = _code().split("\nhelp:", 1)[1].split("\ndoctor:", 1)[0]

    missing_phony = [t for t in declared if t not in phony]
    assert not missing_phony, "targets missing from .PHONY: %s" % missing_phony

    unlisted = [t for t in declared if not re.search(r"'\s+%s\s" % re.escape(t), help_text)]
    assert not unlisted, "targets not listed by `make help`: %s" % unlisted


@requires_make
def test_bare_make_prints_help_and_changes_nothing():
    bare = _make()
    explicit = _make("help")

    assert bare.returncode == 0, bare.stderr
    assert bare.stdout == explicit.stdout
    assert "make targets" in bare.stdout


@requires_make
def test_help_lists_every_phony_target_at_runtime():
    out = _make("help").stdout
    for target in _phony_targets():
        assert re.search(r"^\s+%s\s" % re.escape(target), out, re.M), \
            "`make help` does not mention %r" % target


def test_no_gnu_make_4_only_syntax():
    """macOS's 3.81 predates all of these; each one is a parse error or a
    silently different meaning there, not a warning."""
    text = _code()
    forbidden = {
        ".ONESHELL": "3.82+",
        ".RECIPEPREFIX": "3.82+",
        "$(file ": "4.0+",
        "$(guile ": "4.0+",
        "::=": "4.0+ (POSIX immediate assignment)",
        "&:": "4.3+ (grouped targets)",
    }
    for token, since in forbidden.items():
        assert token not in text, "%s is GNU Make %s, macOS ships 3.81" % (token, since)

    # `VAR != shell command` is 4.0+. Plain `!=` inside a recipe's test is
    # fine, so only column-0 assignments are checked.
    for line in text.splitlines():
        assert not re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*!=", line), \
            "`!=` shell assignment is GNU Make 4.0+: %s" % line


def test_no_gnu_only_tool_flags_in_recipes():
    """BSD userland equivalents of these either do not exist or reject the
    flag. The repo's answer is python3, which is already a hard dependency."""
    banned = [
        (r"\bsha256sum\b", "macOS has shasum -a 256, not sha256sum; use python3 hashlib"),
        (r"\bmd5sum\b", "macOS has md5, not md5sum; use python3 hashlib"),
        (r"\bstat\s+-c\b", "BSD stat has no -c; use python3 os.path.getsize"),
        (r"\bsed\s+-i\s", "BSD sed -i requires an extension argument"),
        (r"\btimeout\s+[0-9]", "macOS has no timeout(1); use a python3 watchdog"),
        (r"\breadlink\s+-f\b", "BSD readlink has no -f"),
        (r"\bgrep\s+-P\b", "BSD grep has no -P"),
        (r"\bdate\s+-d\b", "BSD date has no -d"),
        (r"\bfind\b[^|;]*-printf", "BSD find has no -printf"),
        (r"\bcp\s+--", "BSD cp has no long options"),
        (r"\bmktemp\s+-p\b", "BSD mktemp has no -p"),
        (r"\bxargs\s+-r\b", "BSD xargs has no -r"),
        (r"\bhead\s+-c\b.*\bsed\b", "combination not checked on BSD"),
    ]
    body = "\n".join(_recipe_lines())
    for pattern, why in banned:
        assert not re.search(pattern, body), "%s (matched %s)" % (why, pattern)


def test_recipes_contain_no_hash_comments():
    """Recipe lines are joined with trailing backslashes, and Make keeps the
    backslash-newline. A `#` comment would therefore swallow the continuation
    that follows it and silently drop the rest of the recipe."""
    for line in _recipe_lines():
        stripped = line.strip()
        assert not stripped.startswith("#"), "`#` comment inside a recipe: %s" % line


def test_clean_keeps_the_downloads_and_distclean_is_the_one_that_does_not():
    """A 325 MB download must survive `make clean`; only the target that
    advertises deleting it may delete it."""
    text = _code()
    clean = text.split("\nclean:", 1)[1].split("\ndistclean:", 1)[0]
    distclean = text.split("\ndistclean:", 1)[1]

    assert "$(CACHE_DIR)'" in clean, "clean should still mention what it keeps"
    assert not re.search(r"rm -rf\s+'\$\(CACHE_DIR\)'", clean)
    assert not re.search(r"rm -rf\s+'\$\(BUN_DIR\)'", clean)
    assert "$(CACHE_DIR)" in distclean and "$(BUN_DIR)" in distclean
    assert "rm -rf" in distclean


def test_nothing_is_ever_written_to_a_file_named_claude():
    """The repo's standing rule: a file called `claude` on disk (let alone on
    PATH) would shadow the real CLI. Downloads land as
    claude-<platform>-<version>.bin."""
    body = "\n".join(_recipe_lines())
    assert not re.search(r"-o\s+\S*/claude(\s|'|\"|$)", body)
    assert "claude-$(PLATFORM)-" in _code() or "claude-'$(PLATFORM)'-" in _code()


@requires_make
def test_platform_resolves_to_a_release_endpoint_string():
    """The uname -> platform mapping must produce one of the strings the
    download endpoint publishes, or `make binary` 404s at the last step."""
    out = _make("help").stdout
    m = re.search(r"^\s+PLATFORM=(\S+)", out, re.M)
    assert m, out
    assert m.group(1) in {
        "darwin-arm64", "darwin-x64",
        "linux-x64", "linux-arm64",
        "linux-x64-musl", "linux-arm64-musl",
    }, "unmapped platform %r" % m.group(1)


@requires_make
def test_ab_refuses_on_non_linux_with_one_clear_line():
    """scripts/ab-equivalence.sh needs /proc, so `make ab` must say so itself
    rather than let the script fail somewhere the user has to read 900 lines
    of shell to understand. Simulated by asking make to evaluate the guard
    with UNAME_S overridden."""
    proc = _make("ab", "UNAME_S=Darwin")
    assert proc.returncode != 0
    assert "Linux-only" in proc.stderr
    assert "/proc" in proc.stderr
