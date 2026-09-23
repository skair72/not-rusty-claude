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
import cp from "node:child_process";
const out = {};
let child;
function t(k, fn) {
  try { const r = fn(); out[k] = r === null ? null : r === child?.pid ? "CHILD" : r === process.getuid() ? "UID"
    : r === process.pid ? "SELF" : r; }
  catch (e) { out[k] = "THREW " + e.message; }
}
out.installedOnStockBun = typeof Bun.ant === "object" && typeof Bun.ant.CellSegmenter === "function";
out.members = Object.keys(Bun.ant).sort();
out.lengths = Object.fromEntries(["getPeerPid", "getPeerUid", "setDumpable", "memoryPressureLevel"].map((k) => [k, Bun.ant[k].length]));
const real = { CellSegmenter: function Native() {}, marker: 1 };
const target = { ant: real };
out.keepsANativeOne = installBunAnt(target) === false && target.ant === real;
t("mem", () => Bun.ant.memoryPressureLevel());
t("dump_true", () => Bun.ant.setDumpable(true));
t("dump_false", () => Bun.ant.setDumpable(false));
for (const bad of [-1, 999, Infinity, 2 ** 32]) t("peerPid:" + bad, () => Bun.ant.getPeerPid(bad));
const sock = path.join(os.tmpdir(), "nrc-t-" + process.pid + ".sock");
const srv = net.createServer((c) => {
  const fd = c._handle.fd;
  const cases = { num: fd, str: String(fd), float: fd + 0.7, obj: { valueOf() { return fd; } }, arr: [fd],
    none: undefined, nul: null, junk: fd + "x", neg_float: -0.5, bool: true };
  for (const [k, v] of Object.entries(cases)) {
    t("getPeerPid:" + k, () => Bun.ant.getPeerPid(v));
    t("getPeerUid:" + k, () => Bun.ant.getPeerUid(v));
  }
  c.destroy(); srv.close(); child.kill(); try { fs.unlinkSync(sock); } catch {}
  console.log(JSON.stringify(out)); process.exit(0);
});
srv.listen(sock, () => {
  // the peer is ANOTHER process, so its pid cannot be mistaken for our own
  child = cp.spawn("/usr/bin/python3", ["-c", "import socket,sys,time; s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); time.sleep(10)", sock], { stdio: "ignore" });
});
setTimeout(() => { console.log(JSON.stringify(out)); process.exit(1); }, 8000);
"""

# Measured in the native 2.1.280 runtime on Linux x64, 2026-09-22/23, with a
# python3 process as the peer and stdin on /dev/null (so fd 0 is no socket).
# The argument is coerced like ToNumber: NaN counts as 0, it is truncated, and
# a negative or > 2^31-1 fd answers null.
NATIVE = {
    "mem": "THREW Bun.ant.memoryPressureLevel() is only supported on macOS",
    "dump_true": True, "dump_false": True,
    "peerPid:-1": None, "peerPid:999": None, "peerPid:Infinity": None, "peerPid:4294967296": None,
}
for _case in ("num", "str", "float", "obj", "arr"):
    NATIVE["getPeerPid:" + _case] = "CHILD"
    NATIVE["getPeerUid:" + _case] = "UID"
for _case in ("none", "nul", "junk", "neg_float", "bool"):   # fd 0 or 1: not sockets here
    NATIVE["getPeerPid:" + _case] = None
    NATIVE["getPeerUid:" + _case] = None


@pytest.fixture(scope="module")
def probe(bun_bin, tmp_path_factory):
    d = tmp_path_factory.mktemp("bunant")
    script = d / "probe.mjs"
    script.write_text(PROBE % POLYFILL)
    r = subprocess.run([bun_bin, str(script)], capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin"}, stdin=subprocess.DEVNULL)
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
