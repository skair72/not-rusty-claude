"""scripts/bun-ant.mjs - the Bun.ant polyfill, against what the native runtime answers.

Every expectation here was measured INSIDE the native 2.1.280 runtime - its
bun-internal build honours BUN_OPTIONS=--preload, so a probe runs there with
the real Bun.ant (docs/findings.md 14) - and is asserted here against the
polyfill under stock Bun, so the suite needs no native binary.
scripts/harness.py's `bunant` group repeats the comparison live against the
binary itself.
"""

import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLYFILL = os.path.join(ROOT, "scripts", "bun-ant.mjs")

PROBE = r"""
import { bunAnt, installBunAnt } from "%s";
import net from "node:net"; import os from "node:os"; import path from "node:path"; import fs from "node:fs";
const out = {};
function t(k, fn) { try { const v = fn(); out[k] = v; } catch (e) { out[k] = "THREW " + e.message; } }
out.installedOnStockBun = typeof Bun.ant === "object" && typeof Bun.ant.CellSegmenter === "function";
out.members = Object.keys(Bun.ant).sort();
out.lengths = Object.fromEntries(["getPeerPid", "getPeerUid", "setDumpable", "memoryPressureLevel"].map((k) => [k, Bun.ant[k].length]));
const real = { CellSegmenter: function Native() {}, marker: 1 };
const target = { ant: real };
out.keepsANativeOne = installBunAnt(target) === false && target.ant === real;
t("mem", () => Bun.ant.memoryPressureLevel());
t("peerPid_-1", () => Bun.ant.getPeerPid(-1));
t("peerPid_999", () => Bun.ant.getPeerPid(999));
t("peerPid_str", () => Bun.ant.getPeerPid("3"));
t("peerUid_-1", () => Bun.ant.getPeerUid(-1));
t("dump_true", () => Bun.ant.setDumpable(true));
t("dump_false", () => Bun.ant.setDumpable(false));
const sock = path.join(os.tmpdir(), "nrc-t-" + process.pid + ".sock");
const srv = net.createServer((c) => {
  const fd = c._handle && c._handle.fd;
  t("peerPid_conn_is_self", () => Bun.ant.getPeerPid(fd) === process.pid);
  t("peerUid_conn_is_self", () => Bun.ant.getPeerUid(fd) === process.getuid());
  c.end(); srv.close(); try { fs.unlinkSync(sock); } catch {}
  console.log(JSON.stringify(out)); process.exit(0);
});
srv.listen(sock, () => { net.connect(sock); });
"""

# Measured in the native 2.1.280 runtime on Linux x64, 2026-09-22.
NATIVE = {
    "mem": "THREW Bun.ant.memoryPressureLevel() is only supported on macOS",
    "peerPid_-1": None,
    "peerPid_999": None,
    "peerPid_str": None,
    "peerUid_-1": None,
    "dump_true": True,
    "dump_false": True,
    "peerPid_conn_is_self": True,
    "peerUid_conn_is_self": True,
}


@pytest.fixture(scope="module")
def probe(bun_bin, tmp_path_factory):
    d = tmp_path_factory.mktemp("bunant")
    script = d / "probe.mjs"
    script.write_text(PROBE % POLYFILL)
    r = subprocess.run([bun_bin, str(script)], capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="measured on Linux")
@pytest.mark.parametrize("key", sorted(NATIVE))
def test_member_answers_what_native_answers(probe, key):
    assert probe[key] == NATIVE[key]


def test_installs_the_five_members_the_bundle_calls(probe):
    assert probe["installedOnStockBun"]
    assert probe["members"] == ["CellSegmenter", "getPeerPid", "getPeerUid",
                                "memoryPressureLevel", "setDumpable"]


def test_arities_match_native(probe):
    # native: getPeerPid.length 1, getPeerUid 1, setDumpable 1, memoryPressureLevel 0
    assert probe["lengths"] == {"getPeerPid": 1, "getPeerUid": 1, "setDumpable": 1,
                                "memoryPressureLevel": 0}


def test_never_replaces_a_native_bun_ant(probe):
    assert probe["keepsANativeOne"]
