import importlib.util
import os
import pathlib
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


def _real(env_var, default):
    path = os.environ.get(env_var, default)
    return path if path and os.path.isfile(path) else None


@pytest.fixture(scope="session")
def real_elf_binary():
    path = _real("NRC_TEST_ELF", "/usr/bin/claude")
    if not path:
        pytest.skip("no ELF Claude binary; set NRC_TEST_ELF")
    return path


@pytest.fixture(scope="session")
def real_macho_binary():
    path = _real("NRC_TEST_MACHO", "/tmp/ccmac/package/claude")
    if not path:
        pytest.skip("no Mach-O Claude binary; set NRC_TEST_MACHO")
    return path
