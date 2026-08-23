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
    return path


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
    return path


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


@pytest.fixture
def patch_claude():
    """Fresh per test, unlike the tool fixtures above: these tests monkeypatch
    the module's run() to stand in for codesign, and a session-scoped module
    would carry a stub from one test into the next."""
    return _load("patch_claude")
