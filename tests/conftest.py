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
