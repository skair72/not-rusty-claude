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

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import zipfile

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


def _make(*args, **kwargs):
    """`make -C <repo> <args>`. env= adds to (not replaces) the environment,
    which is how the setup tests point TMPDIR at a directory they can assert
    on afterwards. A value of None REMOVES a variable instead, which is how the
    node-deps tests get out from under the ones `make test` exports."""
    extra = kwargs.pop("env", None)
    assert not kwargs, kwargs
    env = None
    if extra:
        env = dict(os.environ)
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
            else:
                env[key] = value
    return subprocess.run(
        ["make", "-C", str(ROOT), *args],
        capture_output=True, text=True, timeout=120, env=env,
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


# --- `make setup` integrity, i.e. the executable that runs everything else ---
#
# `make binary` sha256-verifies the Claude binary it downloads; `make setup`
# used to verify nothing at all about the bun it downloads, unzips, chmod
# 0755s and then RUNS - the same bun that goes on to execute the extracted
# Claude code, `make smoke` and `make ab`. Its one check was `bun --version`,
# which any substituted executable can print.
#
# These tests are hermetic: BUN_URL is a file:// URL curl fetches from tmp_path
# (curl supports file://), PLATFORM is pinned to linux-x64 so the checksum in
# force is this repo's pinned constant rather than whatever the host maps to,
# and the fallback tests point BUN_SHASUMS_URL at a local list. No network, no
# real bun, and nothing is written outside tmp_path.

FAKE_BUN = b"#!/bin/sh\necho 1.3.14\n"


def _fake_bun_zip(path, member="bun-linux-x64/bun", body=FAKE_BUN):
    """A stand-in for a release asset: one */bun member, which is the only
    thing `unzip -j '*/bun'` in the recipe looks for."""
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(member, body)
    return path


def _setup(tmp_path, *extra, **kwargs):
    """`make setup` against a local zip, with its own BUN_DIR and TMPDIR."""
    zip_path = kwargs.pop("zip_path")
    assert not kwargs, kwargs
    bun_dir = tmp_path / "bundir"
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir(exist_ok=True)
    proc = _make("setup", "PLATFORM=linux-x64",
                 "BUN_URL=file://%s" % zip_path,
                 "BUN_DIR=%s" % bun_dir, *extra,
                 env={"TMPDIR": str(tmpdir)})
    return proc, bun_dir, tmpdir


@requires_make
def test_setup_refuses_a_bun_zip_whose_sha256_does_not_match(tmp_path):
    """The security asymmetry, at the point it matters: a downloaded bun whose
    bytes are not the published ones must never be installed, let alone run.

    No BUN_SHA256= override here on purpose - this exercises the constant the
    Makefile actually pins for linux-x64, so deleting or blanking that row
    fails the test too."""
    proc, bun_dir, tmpdir = _setup(
        tmp_path, zip_path=_fake_bun_zip(tmp_path / "bun.zip"))

    assert proc.returncode != 0, "an unverified bun was accepted:\n" + proc.stdout
    assert "CHECKSUM MISMATCH" in proc.stderr, proc.stderr
    assert not (bun_dir / "bun").exists(), "the unverified bun was installed"
    assert not bun_dir.exists(), "a failed setup left %s behind" % bun_dir
    assert sorted(p.name for p in tmpdir.iterdir()) == [], \
        "the rejected download was left in TMPDIR"


@requires_make
def test_setup_installs_a_bun_whose_sha256_matches(tmp_path):
    """The other half: verification must not be a blanket refusal. With the
    real digest of the asset it is about to fetch, setup completes and leaves
    an executable bun - and still nothing in TMPDIR."""
    zip_path = _fake_bun_zip(tmp_path / "bun.zip")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()

    proc, bun_dir, tmpdir = _setup(
        tmp_path, "BUN_SHA256=%s" % digest, zip_path=zip_path)

    assert proc.returncode == 0, proc.stderr
    assert "sha256 OK: %s" % digest in proc.stdout, proc.stdout
    assert os.access(str(bun_dir / "bun"), os.X_OK)
    assert sorted(p.name for p in tmpdir.iterdir()) == []


@requires_make
def test_setup_falls_back_to_the_published_shasums_list(tmp_path):
    """An unpinned version/asset (e.g. `make setup BUN_VERSION=...`) must still
    be verified, against the SHASUMS256.txt Bun publishes beside the assets.
    The decoy row is there because taking the FIRST hash in the file, rather
    than the row naming this asset, would also pass a one-row fixture."""
    zip_path = _fake_bun_zip(tmp_path / "bun.zip")
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    shasums = tmp_path / "SHASUMS256.txt"
    shasums.write_text("%s  bun-darwin-x64.zip\n%s  bun-linux-x64.zip\n"
                       % ("0" * 64, digest))

    proc, bun_dir, tmpdir = _setup(
        tmp_path, "BUN_SHA256=", "BUN_SHASUMS_URL=file://%s" % shasums,
        zip_path=zip_path)

    assert proc.returncode == 0, proc.stderr
    assert "sha256 OK: %s" % digest in proc.stdout, proc.stdout
    assert (bun_dir / "bun").is_file()


@requires_make
def test_setup_refuses_when_no_checksum_can_be_obtained(tmp_path):
    """No pin and no row in the list -> refuse. The failure mode this guards
    is a checksum step that degrades to "no expectation, so anything matches"
    the moment the list does not name the asset."""
    shasums = tmp_path / "SHASUMS256.txt"
    shasums.write_text("%s  bun-darwin-x64.zip\n" % ("0" * 64))

    proc, bun_dir, tmpdir = _setup(
        tmp_path, "BUN_SHA256=", "BUN_SHASUMS_URL=file://%s" % shasums,
        zip_path=_fake_bun_zip(tmp_path / "bun.zip"))

    assert proc.returncode != 0, proc.stdout
    assert "refusing to install an unverified bun" in proc.stderr, proc.stderr
    assert not bun_dir.exists()
    assert sorted(p.name for p in tmpdir.iterdir()) == []


@requires_make
def test_every_supported_platform_has_a_pinned_bun_checksum(tmp_path):
    """Drift guard for the pin table. `make doctor` reports the asset and the
    checksum in force, so a BUN_VERSION bump or a new PLATFORM that silently
    fell back to "fetch whatever the server says" is visible here. The list is
    exactly the set tests/test_platform_resolves_to_a_release_endpoint_string
    accepts."""
    for platform in ("darwin-arm64", "darwin-x64", "linux-x64", "linux-arm64",
                     "linux-x64-musl", "linux-arm64-musl"):
        out = _make("doctor", "PLATFORM=%s" % platform).stdout
        pinned = re.search(r"^\s+pinned sha256\s+(\S+)", out, re.M)
        assert pinned, "make doctor does not report a checksum:\n" + out
        assert re.match(r"^[0-9a-f]{64}$", pinned.group(1)), \
            "no pinned bun sha256 for PLATFORM=%s (got %r)" % (
                platform, pinned.group(1))


# --- `make node-deps`, i.e. the one target that writes outside this repo ---
#
# It runs npm in $(NODE_DIR) to fill $(NODE_MODULES). npm does not treat the
# cwd as the install root: it walks UP until it finds a package.json or a
# node_modules and installs THERE, so on a host whose $HOME has either (a stray
# `npm i` leaves a bare node_modules behind) ws and undici landed in $HOME and
# $(NODE_MODULES) was never created - measured, and the target still printed
# success. The recipe now writes a throwaway package.json into $(NODE_DIR) to
# pin the root, and verifies afterwards that the modules really arrived.
#
# The npm here is a stand-in that reproduces that walk-up rule, so these tests
# need no network and no real npm, and install nothing anywhere real.

FAKE_NPM = """#!/bin/sh
if [ -n "${NPM_ARGV_FILE:-}" ]; then echo "$@" > "$NPM_ARGV_FILE"; fi
root=$(pwd)
d=$root
while [ "$d" != "/" ]; do
  if [ -f "$d/package.json" ] || [ -d "$d/node_modules" ]; then root=$d; break; fi
  d=$(dirname "$d")
done
if [ "${FAKE_NPM_INSTALL:-1}" = 1 ]; then
  for m in ws undici; do
    mkdir -p "$root/node_modules/$m"
    echo '{"name":"'"$m"'","version":"0.0.0"}' > "$root/node_modules/$m/package.json"
  done
fi
echo "added 2 packages"
"""


def _fake_bin(tmp_path, name, body):
    """A directory holding one executable `name`, to be prepended to PATH."""
    d = tmp_path / ("bin-" + name)
    d.mkdir(exist_ok=True)
    p = d / name
    p.write_text(body)
    p.chmod(0o755)
    return d


# `make test` exports NRC_TEST_NODE_MODULES (and four siblings) so conftest.py
# can find this host's real ws/undici - see the `test` recipe. node-deps reads
# that same variable and short-circuits on it, so a suite launched through
# `make test` used to hand these tests a node-deps that reported "already in
# ~/.cache/not-rusty-claude/..." and installed nothing into the throwaway HOME
# they were about to assert on. Four tests passed under bare pytest and failed
# under the documented entry point. The tests were the wrong side: what they
# are about is the recipe's DEFAULT path, so they have to say so rather than
# inherit whatever launched them.
NODE_DEPS_INHERITED = ("NRC_TEST_NODE_MODULES", "NRC_TEST_NODE", "NRC_TEST_ELF",
                       "NRC_TEST_MACHO", "BUN_BIN")


def _node_deps(tmp_path, home, *extra, **kwargs):
    """`make node-deps` with a fake npm first on PATH and HOME=<home>.

    Explicitly out from under the environment `make test` exports, so the row
    these tests measure is the same one whether the suite was started by
    `make test` or by pytest directly.
    """
    env = dict.fromkeys(NODE_DEPS_INHERITED)
    env.update({"HOME": str(home), "NPM_ARGV_FILE": str(tmp_path / "npm-argv"),
                "PATH": "%s:%s" % (_fake_bin(tmp_path, "npm", FAKE_NPM),
                                   os.environ["PATH"])})
    env.update(kwargs.pop("env", {}))
    assert not kwargs, kwargs
    return _make("node-deps", *extra, env=env)


@requires_make
@pytest.mark.parametrize("ancestor", ["package.json", "node_modules"])
def test_node_deps_installs_into_the_cache_and_not_into_home(tmp_path, ancestor):
    """The install must be confined to $(NODE_MODULES) even when $HOME is the
    kind of directory npm would rather install into."""
    home = tmp_path / "home"
    home.mkdir()
    if ancestor == "package.json":
        (home / "package.json").write_text('{"name":"my-home","private":true}')
    else:
        (home / "node_modules").mkdir()

    proc = _node_deps(tmp_path, home)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    cache = home / ".cache" / "not-rusty-claude" / "node" / "node_modules"
    assert (cache / "ws" / "package.json").is_file(), \
        "ws did not land in the cache dir:\n" + proc.stdout
    assert (cache / "undici" / "package.json").is_file()
    escaped = sorted(p.name for p in (home / "node_modules").iterdir()) \
        if (home / "node_modules").is_dir() else []
    assert escaped == [], "the install escaped into $HOME/node_modules: %s" % escaped


@requires_make
def test_node_deps_installs_with_scripts_disabled(tmp_path):
    """These two tarballs run no lifecycle scripts today (checked), but the
    fetch is registry-integrity only - no pinned sha256 like `make setup` - so
    a substituted tarball must not get to execute at install time either."""
    home = tmp_path / "home"
    home.mkdir()
    proc = _node_deps(tmp_path, home)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    argv = (tmp_path / "npm-argv").read_text().split()
    assert "--ignore-scripts" in argv, "npm was invoked as: %s" % argv


@requires_make
def test_node_deps_fails_loudly_when_the_modules_do_not_arrive(tmp_path):
    """The silent-success half of the same bug: npm exiting 0 is not evidence
    that anything was installed where this repo said it would be."""
    home = tmp_path / "home"
    home.mkdir()
    proc = _node_deps(tmp_path, home, env={"FAKE_NPM_INSTALL": "0"})

    assert proc.returncode != 0, "node-deps reported success installing nothing:\n" + proc.stdout
    assert "installed somewhere else" in proc.stderr, proc.stderr


@requires_make
def test_node_deps_accepts_a_prepared_node_modules_instead_of_running_npm(tmp_path):
    """NRC_TEST_NODE_MODULES is what `make test` honours; node-deps and
    node-run honour it too, which is the only route a host with the modules but
    without npm has. The fake npm fails here, so calling it at all fails the
    test."""
    home = tmp_path / "home"
    home.mkdir()
    mods = tmp_path / "prepared"
    for m in ("ws", "undici"):
        (mods / m).mkdir(parents=True)
        (mods / m / "package.json").write_text('{"name":"%s"}' % m)

    proc = _node_deps(tmp_path, home, env={"FAKE_NPM_INSTALL": "0",
                                           "NRC_TEST_NODE_MODULES": str(mods)})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert str(mods) in proc.stdout, proc.stdout
    assert not (tmp_path / "npm-argv").exists(), "npm was run despite the modules being there"


@requires_make
def test_node_deps_tests_do_not_inherit_the_variables_make_test_exports(tmp_path, monkeypatch):
    """The same suite must answer the same way through `make test` as through
    pytest.

    `make test` exports NRC_TEST_NODE_MODULES pointing at this host's real
    ws/undici, and `make node-deps` short-circuits on exactly that variable. So
    four tests above - which assert on a throwaway HOME - passed under bare
    pytest and failed under the documented entry point, on nothing but how the
    run was started. This reproduces that launch environment deliberately: the
    variable is set here, and the default-path row must still hold.
    """
    home = tmp_path / "home"
    home.mkdir()
    elsewhere = tmp_path / "elsewhere"
    for m in ("ws", "undici"):
        (elsewhere / m).mkdir(parents=True)
        (elsewhere / m / "package.json").write_text('{"name":"%s"}' % m)
    monkeypatch.setenv("NRC_TEST_NODE_MODULES", str(elsewhere))

    proc = _node_deps(tmp_path, home)

    assert proc.returncode == 0, proc.stdout + proc.stderr
    cache = home / ".cache" / "not-rusty-claude" / "node" / "node_modules"
    assert (cache / "ws" / "package.json").is_file(), (
        "node-deps followed an inherited NRC_TEST_NODE_MODULES instead of its "
        "own default, so this test measured the launcher, not the recipe:\n"
        + proc.stdout)
    assert str(elsewhere) not in proc.stdout, proc.stdout


@requires_make
def test_node_run_puts_the_resolved_node_modules_on_node_path(tmp_path):
    """And the same directory has to reach the run itself: node-run used to
    hardcode $(NODE_MODULES) as NODE_PATH, so a prepared directory got the
    artifact as far as `Cannot find module 'ws'`. The node here is a stand-in
    that answers the version gate and then prints its NODE_PATH."""
    home = tmp_path / "home"
    home.mkdir()
    mods = tmp_path / "prepared"
    for m in ("ws", "undici"):
        (mods / m).mkdir(parents=True)
        (mods / m / "package.json").write_text('{"name":"%s"}' % m)
    out_dir = tmp_path / "out"
    (out_dir / "extract").mkdir(parents=True)
    (out_dir / "extract" / "cli.original.cjs").write_text("")
    fake_node = _fake_bin(tmp_path, "node",
                          '#!/bin/sh\n'
                          'case "$1" in -p) echo 24.0.0;; '
                          '*) echo "NODE_PATH=$NODE_PATH";; esac\n') / "node"

    proc = _make("node-run", "OUT_DIR=%s" % out_dir, "NODE_BIN=%s" % fake_node,
                 env={"HOME": str(home), "NRC_TEST_NODE_MODULES": str(mods)})

    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "NODE_PATH=%s" % mods in proc.stdout, proc.stdout
