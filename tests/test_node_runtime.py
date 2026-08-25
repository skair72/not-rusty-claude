"""Running the extracted artifact under stock Node instead of Bun.

Bun is the oracle everywhere here: every assertion compares two runs of the
same input rather than a hardcoded expectation, so a Claude release that
changes its help text moves both sides at once and nothing has to be edited.

What this pins:
  - `--version`, `mcp list` and `--help` produce byte-identical stdout and the
    same exit code under Node + scripts/bun-shim.cjs as under Bun.
  - the shim's Bun.* stand-ins answer exactly what Bun answers, api by api,
    through tests/bun_shim_probe.cjs - one file run by both runtimes.
  - the APIs the shim does NOT implement throw an error naming themselves, and
    the ones it deliberately leaves undefined stay undefined, because the
    bundle feature-detects those and a stub would take the wrong branch.

Needs a Node >= 24 plus `ws` and `undici`; each is a separate skip naming the
env var that fixes it. See tests/conftest.py.
"""

import json
import os
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SHIM = ROOT / "scripts" / "bun-shim.cjs"
PROBE = ROOT / "tests" / "bun_shim_probe.cjs"

TIMEOUT = 300

# stringWidth is the one entry that approximates. Measured 2026-08-25 against
# Bun 1.3.14 with the probe's own corpora, identically on Node 24.0.0, 24.19.0,
# 25.0.0, 25.9.0 and 26.7.0: 0 mismatches on the realistic corpus, 287 on the
# adversarial one. The bound is an upper bound - getting closer to Bun is fine,
# drifting away is the regression this catches.
ADVERSARIAL_MISMATCH_BOUND = 287

# Bun accepts partial and malformed versions ("1", "1.2", "x", "1.2.-3") and
# sorts them above everything; the shim rejects them instead. Measured: 4 of the
# probe's 8 malformed inputs. Loud beats lenient, but it is a real difference,
# so it is pinned rather than hidden.
SEMVER_INVALID_DIVERGENCES = 4

EXACT_GROUPS = ("stringWidth-realistic", "stripANSI", "hash", "hash-seeded",
                "semver.order", "which", "deepEquals")


def _env(home, config_dir, node_path=None):
    """A run environment that touches no real config and opens no sockets.

    CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC is not decoration: without it the
    CLI opens non-loopback sockets on startup, which scripts/ab-equivalence.sh
    exists to prove it does not do here.
    """
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(config_dir),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    if node_path:
        env["NODE_PATH"] = node_path
    return env


def _run(argv, env, cwd=None):
    return subprocess.run(argv, env=env, cwd=cwd, capture_output=True,
                          timeout=TIMEOUT)


@pytest.fixture(scope="session")
def node_env(node_bin, ws_module, undici_module, built_artifact):
    """Everything the Node side needs, or a skip naming what is missing."""
    assert ws_module == undici_module, "ws and undici must share one node_modules"
    return {"node": node_bin, "modules": ws_module, "artifact": built_artifact}


def _both(node_env, bun_bin, tmp_path, args):
    """Run the same CLI command under Bun and under Node + shim.

    Separate HOME and CLAUDE_CONFIG_DIR per side so neither run can see state
    the other left behind - otherwise the second run could match the first for
    the wrong reason.
    """
    results = {}
    for name, argv in (
        ("bun", [bun_bin, node_env["artifact"], *args]),
        ("node", [node_env["node"], "--require", str(SHIM),
                  node_env["artifact"], *args]),
    ):
        home = tmp_path / name / "home"
        cfg = tmp_path / name / "config"
        home.mkdir(parents=True, exist_ok=True)
        cfg.mkdir(parents=True, exist_ok=True)
        node_path = node_env["modules"] if name == "node" else None
        results[name] = _run(argv, _env(home, cfg, node_path))
    return results["bun"], results["node"]


def _assert_same(bun, node, args):
    assert node.returncode == bun.returncode, (
        f"`{' '.join(args)}` exited {node.returncode} under Node and "
        f"{bun.returncode} under Bun; node stderr:\n"
        f"{node.stderr.decode('utf-8', 'replace')[-2000:]}")
    assert node.stdout == bun.stdout, (
        f"`{' '.join(args)}` stdout differs: {len(bun.stdout)} bytes under Bun, "
        f"{len(node.stdout)} under Node")


@pytest.mark.parametrize("args", [["--version"], ["mcp", "list"], ["--help"]])
def test_stdout_is_byte_identical_to_bun(node_env, bun_bin, tmp_path, args):
    """The whole point: Node produces exactly what Bun produces.

    All three, because they need different amounts of the shim. Measured on
    2026-08-25 by running each with `globalThis.Bun = {}`: `--version` prints
    its 22 bytes and exits 0 with no Bun at all, `mcp list` exits 0 but prints
    NOTHING, and `--help` exits 1. So the exit code alone would wave `mcp list`
    through with a completely broken shim - the stdout comparison is what
    catches it.

    What these do NOT pin is the width table. All 16,890 bytes of --help are
    ASCII, where character count and column count are the same number, so
    replacing stringWidth with `s.length` still renders it byte-identically -
    confirmed by mutation. test_shim_api_matches_bun_exactly is what catches
    that; these three catch the shim being absent, broken or throwing.
    """
    bun, node = _both(node_env, bun_bin, tmp_path, args)
    assert bun.returncode == 0, bun.stderr.decode("utf-8", "replace")[-2000:]
    assert bun.stdout, "the Bun oracle printed nothing; nothing to compare"
    _assert_same(bun, node, args)


def test_config_ls_fails_the_same_way(node_env, bun_bin, tmp_path):
    """A NON-zero exit has to match too.

    Comparing only successful commands would let a shim that crashes early look
    fine on anything that was going to fail anyway. `config ls` without
    credentials exits 1 under both, and the message must be the same message.
    """
    args = ["config", "ls"]
    bun, node = _both(node_env, bun_bin, tmp_path, args)
    assert bun.returncode != 0, "expected `config ls` to fail without credentials"
    _assert_same(bun, node, args)


@pytest.fixture(scope="session")
def which_fixture(tmp_path_factory):
    """One directory tree, shared by both probe runs.

    Bun.which returns absolute paths, so the two runtimes have to be asked
    about the same paths or every answer would differ for a boring reason.
    """
    root = tmp_path_factory.mktemp("which")
    (root / "bin" / "adir").mkdir(parents=True)
    (root / "bin2").mkdir()
    for path in (root / "bin" / "exe1", root / "bin2" / "exe1",
                 root / "bin2" / "exe2", root / "rel"):
        path.write_text("#!/bin/sh\n")
        path.chmod(0o755)
    noexec = root / "bin" / "noexec"
    noexec.write_text("x")
    noexec.chmod(0o644)
    return root


@pytest.fixture(scope="session")
def probe_results(node_bin, bun_bin, which_fixture, tmp_path_factory):
    """tests/bun_shim_probe.cjs run under both runtimes, parsed and zipped.

    Deliberately does not need the artifact: this measures the shim itself, so
    it still runs on a host that has never built anything.
    """
    home = tmp_path_factory.mktemp("probe")
    env = _env(home, home)
    out = {}
    for name, argv in (
        ("bun", [bun_bin, str(PROBE), str(which_fixture)]),
        ("node", [node_bin, "--require", str(SHIM), str(PROBE), str(which_fixture)]),
    ):
        proc = _run(argv, env)
        assert proc.returncode == 0, (
            f"the probe failed under {name}: "
            f"{proc.stderr.decode('utf-8', 'replace')[-2000:]}")
        out[name] = [json.loads(line) for line in proc.stdout.decode("utf-8").splitlines()]
    assert out["bun"], "the probe emitted nothing under Bun"
    assert len(out["bun"]) == len(out["node"])
    pairs = []
    for expected, got in zip(out["bun"], out["node"]):
        assert expected[:2] == got[:2], "the two runs disagree about the corpus"
        pairs.append((expected[0], expected[1], expected[2], got[2]))
    return pairs


def _group(pairs, group):
    rows = [p for p in pairs if p[0] == group]
    assert rows, f"the probe emitted no {group} cases"
    return rows


@pytest.mark.parametrize("group", EXACT_GROUPS)
def test_shim_api_matches_bun_exactly(probe_results, group):
    """The entries the shim claims to implement faithfully, api by api.

    Split per group rather than asserted as one number so a failure says which
    stand-in drifted instead of just "something differs".
    """
    rows = _group(probe_results, group)
    wrong = [(key, want, got) for _, key, want, got in rows if want != got]
    assert not wrong, (
        f"{len(wrong)} of {len(rows)} {group} cases differ from Bun; "
        f"first three: {wrong[:3]}")


def test_string_width_residual_is_no_worse_than_measured(probe_results):
    """stringWidth approximates on pathological input; hold the line at what was measured.

    Bun resolves some (base, combining mark) pairs from a two-dimensional table
    the shim does not have. On text the CLI actually renders this never shows
    up - the realistic corpus and --help are exact - so the residual is pinned
    rather than required to be zero, and a widening one fails here.
    """
    rows = _group(probe_results, "stringWidth-adversarial")
    wrong = [r for r in rows if r[2] != r[3]]
    assert len(wrong) <= ADVERSARIAL_MISMATCH_BOUND, (
        f"{len(wrong)} of {len(rows)} adversarial stringWidth cases differ from "
        f"Bun, was {ADVERSARIAL_MISMATCH_BOUND} when measured; "
        f"first three: {[(r[1], r[2], r[3]) for r in wrong[:3]]}")


def test_semver_rejects_what_bun_tolerates_and_nothing_more(probe_results):
    """The shim is stricter than Bun on malformed versions, by exactly this much.

    Bun sorts "1", "1.2" and "x" above every real version instead of refusing
    them. The shim throws. That is the safe direction, but it IS a difference,
    so it is counted here - and any well-formed version must still agree, which
    is what the semver.order group asserts.
    """
    rows = _group(probe_results, "semver.order-invalid")
    wrong = [r for r in rows if r[2] != r[3]]
    assert len(wrong) == SEMVER_INVALID_DIVERGENCES, (
        f"{len(wrong)} malformed-version divergences, expected "
        f"{SEMVER_INVALID_DIVERGENCES}: {[(r[1], r[2], r[3]) for r in wrong]}")
    for _, key, want, got in wrong:
        assert got == "throw", f"{key!r}: expected the shim to refuse it, got {got!r}"


# Every Bun API the artifact can reach, and what the shim must do about it.
# A stub that returned a plausible value instead of throwing is the failure
# this repo exists to prevent, so the two lists are asserted, not documented.
THROWING = [
    ("YAML.parse", "Bun.YAML.parse('a: 1')"),
    ("YAML.stringify", "Bun.YAML.stringify({})"),
    ("TOML.parse", "Bun.TOML.parse('a = 1')"),
    ("semver.satisfies", "Bun.semver.satisfies('1.2.3', '^1.0.0')"),
    ("wrapAnsi", "Bun.wrapAnsi('a b c', 3)"),
    ("spawn", "Bun.spawn(['true'])"),
    ("file", "Bun.file('/etc/hostname')"),
    ("serve", "Bun.serve({})"),
    ("listen", "Bun.listen({})"),
    ("connect", "Bun.connect({})"),
    ("generateHeapSnapshot", "Bun.generateHeapSnapshot('v8')"),
    ("SQL", "new Bun.SQL('x')"),
    ("Transpiler", "new Bun.Transpiler({})"),
    ("ant.getPeerUid", "Bun.ant.getPeerUid(0)"),
    ("ant.getPeerPid", "Bun.ant.getPeerPid(0)"),
    ("ant.setDumpable", "Bun.ant.setDumpable(false)"),
    ("stringWidth({ambiguousIsNarrow:false})",
     "Bun.stringWidth('a', {ambiguousIsNarrow: false})"),
    ("stringWidth({countAnsiEscapeCodes:true})",
     "Bun.stringWidth('a', {countAnsiEscapeCodes: true})"),
    ("deepEquals", "Bun.deepEquals(new Date(), new Date())"),
]

# Left undefined on purpose: the bundle feature-detects each one and takes a
# fallback when it is missing. Defining a throwing stub here would be WORSE
# than nothing - `"WebView" in Bun` and `Bun.JSONL?.parseChunk` would both
# start saying yes and then explode down a path that had a working answer.
ABSENT = ["Terminal", "WebView", "JSONL", "version", "isStandaloneExecutable", "stdin"]


@pytest.mark.parametrize("api,expression", THROWING, ids=[t[0] for t in THROWING])
def test_unimplemented_apis_throw_naming_themselves(node_bin, api, expression):
    """No silent placeholder: the error has to say which Bun API was missing.

    Needs Node only - no artifact, no Bun - so this half of the contract is
    checked even on a host that cannot run the CLI at all.
    """
    script = (
        "require(process.argv[1]);"
        "try { " + expression + "; console.log('NO-THROW'); }"
        "catch (e) { console.log(e && e.bunApi ? 'API:' + e.bunApi : 'OTHER:' + e.message); }"
    )
    proc = _run([node_bin, "-e", script, str(SHIM)],
                {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    got = proc.stdout.decode("utf-8").strip()
    assert got == "API:" + api, f"expected Bun.{api} to name itself, got {got!r}"


def test_gc_really_collects_without_expose_gc(node_bin):
    """Bun.gc is the one entry that has to DO something rather than answer.

    It is called on a 1-second timer, so a version that threw would take the
    process down a second after startup, and one that silently did nothing
    would be a lie. Plain `node`, no --expose-gc: the shim reaches V8's hook
    itself, and heapUsed has to actually move after allocating garbage.
    """
    script = (
        "require(process.argv[1]);"
        "let junk = []; for (let i = 0; i < 200000; i++) junk.push({ i, s: 'x'.repeat(50) });"
        "const before = process.memoryUsage().heapUsed;"
        "junk = null;"
        "const returned = globalThis.Bun.gc(true);"
        "console.log(JSON.stringify([before > process.memoryUsage().heapUsed, returned === undefined]));"
    )
    proc = _run([node_bin, "-e", script, str(SHIM)],
                {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    collected, returns_undefined = json.loads(proc.stdout)
    assert collected, "Bun.gc did not actually collect; it is a no-op, not a stand-in"
    assert returns_undefined, ("Bun.gc must return undefined here - Bun returns a heap "
                               "size, and inventing one is the failure this repo is about")


def test_absent_apis_stay_undefined(node_bin):
    """The bundle's own fallbacks depend on these being missing."""
    script = (
        "require(process.argv[1]);"
        "console.log(JSON.stringify(" + json.dumps(ABSENT) +
        ".map((k) => [k, k in globalThis.Bun])));"
    )
    proc = _run([node_bin, "-e", script, str(SHIM)],
                {"PATH": os.environ.get("PATH", "/usr/bin:/bin")})
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    present = [k for k, defined in json.loads(proc.stdout) if defined]
    assert not present, (
        f"{present} are defined on the shim's Bun; the artifact feature-detects "
        f"them and a stub sends it down a path that has no working answer")
