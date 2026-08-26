import importlib.util
import os
import pathlib
import shutil
import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _load(name):
    path = ROOT / "tools" / (name + ".py")
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="session")
def extract_bun():
    return _load("extract_bun")


@pytest.fixture(scope="session")
def postprocess():
    return _load("postprocess")


MACHO_MAGIC_LE = b"\xcf\xfa\xed\xfe"   # MH_MAGIC_64, little-endian on disk
ELF_MAGIC = b"\x7fELF"


def _magic(path):
    try:
        with open(path, "rb") as fh:
            return fh.read(4)
    except OSError:
        return b""


def _usable(path, magic, kind, env_var):
    """Skip - loudly - when the specimen is present but not what it claims.

    The fixtures used to return any path that isfile(), so a stub sailed
    through and tools/extract_bun.py's die() ran sys.exit(1) INSIDE the tests,
    surfacing as `SystemExit: 1` in seven of them at once. Found on a real Mac
    2026-08-24: an interrupted auto-update had left a 0-byte
    versions/2.1.241 that `ls | sort -V | tail -1` selected over the working
    2.1.239. "The specimen is not what you think" has to be distinguishable
    from "the tools broke" - the same principle tests/test_pipeline.py applies
    to telling an upstream change apart from a regression.
    """
    head = _magic(path)
    if head != magic:
        pytest.skip(
            f"{path} is not a {kind} binary (first bytes {head!r}, "
            f"expected {magic!r}); a 0-byte or truncated file is what an "
            f"interrupted install leaves - set {env_var} to a working one")
    return path


def _real(env_var, *defaults):
    """The binary named by env_var, else the first default that exists.

    Both env vars are documented in README's test-count table and in
    docs/runbook.md; they are what make the integration tests runnable on a
    host that keeps its binaries somewhere else.
    """
    override = os.environ.get(env_var)
    for path in ([override] if override else list(defaults)):
        if path and os.path.isfile(path):
            return path
    return None


@pytest.fixture(scope="session")
def real_elf_binary():
    path = _real("NRC_TEST_ELF", "/usr/bin/claude")
    if not path:
        pytest.skip("no ELF Claude binary; set NRC_TEST_ELF")
    return _usable(path, ELF_MAGIC, "ELF", "NRC_TEST_ELF")


@pytest.fixture(scope="session")
def real_macho_binary():
    # findings.md's appendix now unpacks the darwin tarball under a name of its
    # own, because this repo creates no file called `claude`; the older path is
    # still accepted so an existing checkout keeps working.
    path = _real("NRC_TEST_MACHO",
                 "/tmp/ccmac/package/claude-darwin-arm64.bin",
                 "/tmp/ccmac/package/claude")
    if not path:
        pytest.skip("no Mach-O Claude binary; set NRC_TEST_MACHO")
    return _usable(path, MACHO_MAGIC_LE, "Mach-O", "NRC_TEST_MACHO")


@pytest.fixture(scope="session")
def bun_bin():
    """A stock external Bun to actually load our output with.

    Defaults to the unpacked 1.3.14 the runbook installs (not on PATH), then
    falls back to whatever `bun` is on PATH. Skips when neither exists.
    """
    candidates = [
        os.environ.get("BUN_BIN"),
        os.path.join(os.path.expanduser("~"), ".bun-1.3.14", "bun"),
        shutil.which("bun"),
    ]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    pytest.skip("no bun available; set BUN_BIN")


# --- running the artifact under Node instead of Bun -------------------------
#
# Three things the host may not have, one fixture each, all of them a SKIP with
# the env var that fixes it - never a failure. `make node-run` sets all three up.

MIN_NODE_MAJOR = 24  # the bundle uses `using`; Node 22/23 fail `node --check`

CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "not-rusty-claude")
NODE_MODULES = os.path.join(CACHE_DIR, "node", "node_modules")


@pytest.fixture(scope="session")
def node_bin():
    """A Node >= 24 to run the artifact with; Bun stays the oracle.

    A Node that is too old is reported as such rather than as "not found": the
    bundle's `using` declarations are a hard parse error before 24, so an
    otherwise healthy `node` on PATH is a specimen that is not what you think -
    the same distinction _usable() draws for the binaries above.
    """
    import subprocess
    candidates = [os.environ.get("NRC_TEST_NODE"), shutil.which("node")]
    too_old = None
    for path in candidates:
        if not (path and os.path.isfile(path) and os.access(path, os.X_OK)):
            continue
        try:
            out = subprocess.run([path, "-p", "process.versions.node"],
                                 capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        version = out.stdout.strip()
        try:
            major = int(version.split(".")[0])
        except ValueError:
            continue
        if major >= MIN_NODE_MAJOR:
            return path
        too_old = (path, version)
    if too_old:
        pytest.skip(
            f"{too_old[0]} is Node {too_old[1]}; the Claude bundle uses `using` "
            f"declarations and needs >= {MIN_NODE_MAJOR} - set NRC_TEST_NODE to one")
    pytest.skip(f"no Node >= {MIN_NODE_MAJOR}; set NRC_TEST_NODE")


def _node_module(name):
    """The directory to put on NODE_PATH so `require(name)` resolves.

    ws and undici are Bun builtins that Node lacks, so the artifact cannot load
    without them - and they are deliberately NOT a dependency of this repo:
    they live in a cache directory `make node-deps` fills, and nothing is
    installed globally or into the checkout.
    """
    root = os.environ.get("NRC_TEST_NODE_MODULES") or NODE_MODULES
    if os.path.isfile(os.path.join(root, name, "package.json")):
        return root
    pytest.skip(f"no `{name}` under {root} - run `make node-deps`, "
                f"or set NRC_TEST_NODE_MODULES to a node_modules holding it")


@pytest.fixture(scope="session")
def ws_module():
    return _node_module("ws")


@pytest.fixture(scope="session")
def undici_module():
    return _node_module("undici")


@pytest.fixture(scope="session")
def built_artifact():
    """The extracted CLI that `make build` produces."""
    path = os.environ.get("NRC_TEST_ARTIFACT") or str(
        ROOT / "build" / "extract" / "cli.original.cjs")
    if not os.path.isfile(path):
        pytest.skip(f"no artifact at {path} - run `make build`, "
                    f"or set NRC_TEST_ARTIFACT")
    return path
