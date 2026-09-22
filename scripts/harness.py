#!/usr/bin/env python3
"""harness.py - verify an extracted Claude Code artifact against the native binary.

The definition of done for a Claude release (docs/superpowers/specs/
2026-09-22-esm-split-build-design.md): build the artifact from the native
binary, then run the SAME scenario through both and compare what each one
did. Every check prints PASS / FAIL / SKIP with its evidence, and the whole
run is written as JSON. The development loop is: run it, fix the first
failure, run it again.

    scripts/harness.py                       # everything, /usr/bin/claude
    scripts/harness.py --only cli,agentic    # a subset (see --list)
    scripts/harness.py --artifact build/extract/cli.js   # skip the build

What it compares, by group:
  build      build.sh succeeds and reports the counts it should
  structure  every relative specifier and runtime path in root/ exists
  parse      stock Bun parses every emitted module
  text       every text module require()s to the native string
  cli        stdout + exit code of non-interactive commands, native vs artifact
  agentic    mock-API turns (text, Bash, Read, Read of a large PNG, Grep,
             Write): the tool_result each side sent back up, compared
  tui        the interactive TUI under a pty, screens compared through
             scripts/vtscreen.py: onboarding, and a REPL turn plus a clean exit
  bunant     Bun.ant: the polyfill against the native members (the native
             binary honours BUN_OPTIONS=--preload, so probes run inside it)
  segmenter  CellSegmenter differential fuzz, native vs polyfill
  pytest     the repo's own suite

SAFETY. The native binary is executed here, unlike in the build pipeline -
that is the point of an A/B. Every run gets a throwaway HOME and
CLAUDE_CONFIG_DIR, `env -i` semantics (only what is set below reaches it),
DISABLE_AUTOUPDATER=1, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1, and an
ANTHROPIC_BASE_URL pointing at scripts/mock-messages-api.mjs on loopback, so
no case talks to Anthropic. Standard library only.
"""

import argparse
import base64
import fcntl
import json
import os
import re
import select
import shutil
import struct
import subprocess
import sys
import termios
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import vtscreen  # noqa: E402

FAKE_KEY = "sk-ant-harness-fake-key-000000000000"
GROUPS = ["build", "structure", "parse", "text", "cli", "agentic", "tui",
          "bunant", "segmenter", "pytest"]


# ------------------------------------------------------------------ plumbing

class Result:
    def __init__(self, name, status, summary, details=None):
        self.name, self.status, self.summary = name, status, summary
        self.details = details or {}

    def as_dict(self):
        return {"name": self.name, "status": self.status,
                "summary": self.summary, "details": self.details}


class Ctx:
    def __init__(self, args):
        self.args = args
        self.native = os.path.abspath(args.native)
        self.bun = os.path.abspath(os.path.expanduser(args.bun))
        self.out = os.path.abspath(args.out)
        self.artifact = os.path.abspath(args.artifact) if args.artifact else None
        self.results = []
        self._mock = None
        self.png = None

    @property
    def extract_dir(self):
        return os.path.dirname(self.artifact)

    def scratch(self, *parts):
        path = os.path.join(self.out, "scratch", *parts)
        if os.path.isdir(path):
            shutil.rmtree(path)
        os.makedirs(path)
        return path

    def argv(self, side):
        if side == "native":
            return [self.native]
        return [self.bun, "--no-install", self.artifact]

    def base_env(self, home, mock_port=None, extra=None):
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": home,
            "CLAUDE_CONFIG_DIR": os.path.join(home, "config"),
            "DISABLE_AUTOUPDATER": "1",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "LANG": "C.UTF-8",
            "TERM": "xterm-256color",
        }
        # never inherit a real endpoint: point at the mock, or at a closed port
        env["ANTHROPIC_BASE_URL"] = "http://127.0.0.1:%d" % (mock_port or 9)
        env["ANTHROPIC_API_KEY"] = FAKE_KEY
        env.update(extra or {})
        os.makedirs(env["CLAUDE_CONFIG_DIR"], exist_ok=True)
        return env


def run(argv, env, cwd=None, timeout=120, stdin=subprocess.DEVNULL):
    t0 = time.time()
    try:
        p = subprocess.run(argv, env=env, cwd=cwd, stdin=stdin, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        rc, out, err = p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as e:
        rc, out, err = "timeout", e.stdout or b"", e.stderr or b""
    return {"rc": rc, "stdout": out.decode("utf-8", "replace"),
            "stderr": err.decode("utf-8", "replace"), "secs": round(time.time() - t0, 2)}


def normalize(text, ctx, side_paths):
    """Replace per-run paths so the two sides can be compared line by line."""
    for path, token in side_paths:
        if path:
            text = text.replace(path, token)
    text = text.replace(ctx.bun, "<EXEC>").replace(ctx.native, "<EXEC>")
    if ctx.artifact:
        text = text.replace(ctx.artifact, "<ENTRY>")
    return text


def first_diff(a, b):
    al, bl = a.splitlines(), b.splitlines()
    for i in range(max(len(al), len(bl))):
        x = al[i] if i < len(al) else "<missing>"
        y = bl[i] if i < len(bl) else "<missing>"
        if x != y:
            return {"line": i + 1, "native": x, "artifact": y}
    return None


# ---------------------------------------------------------------- the mock

class Mock:
    """scripts/mock-messages-api.mjs on an ephemeral loopback port."""

    def __init__(self, ctx, tag, tool="none", tool_input=None, text="MOCK-DONE"):
        d = ctx.scratch("mock", tag)
        self.ready = os.path.join(d, "ready")
        self.log = os.path.join(d, "requests.log")
        self.bodies = os.path.join(d, "bodies.jsonl")
        argv = [ctx.bun, os.path.join(HERE, "mock-messages-api.mjs"),
                "--tool", tool, "--text", text, "--ready-file", self.ready,
                "--log", self.log, "--log-bodies", self.bodies]
        if tool_input is not None:
            argv += ["--tool-input", json.dumps(tool_input)]
        self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"})
        for _ in range(100):
            if os.path.exists(self.ready) and open(self.ready).read().strip():
                break
            time.sleep(0.05)
        self.port = int(open(self.ready).read().strip())

    def reset_logs(self):
        for p in (self.log, self.bodies):
            if os.path.exists(p):
                os.remove(p)

    def requests(self):
        if not os.path.exists(self.log):
            return []
        return [l for l in open(self.log).read().splitlines() if l.startswith("REQ")]

    def tool_results(self):
        """Every tool_result block the client sent up, in order."""
        out = []
        if not os.path.exists(self.bodies):
            return out
        for line in open(self.bodies):
            try:
                body = json.loads(json.loads(line)["body"])
            except (ValueError, KeyError, TypeError):
                continue
            for msg in body.get("messages", []):
                content = msg.get("content")
                if isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            out.append(block)
        return out

    def post_bodies(self):
        """Every POST body the client sent, parsed (the HEAD probe has none)."""
        out = []
        if not os.path.exists(self.bodies):
            return out
        for line in open(self.bodies):
            try:
                rec = json.loads(line)
                if rec.get("method") == "POST" and rec.get("body"):
                    out.append(json.loads(rec["body"]))
            except ValueError:
                continue
        return out

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


# ------------------------------------------------------------------ the pty

def pty_session(argv, env, cwd, steps, rows=30, cols=100, total_timeout=120):
    """Drive argv under a pty through `steps`; returns (rc, screen, snapshots).

    Steps: ("until", text, secs)   wait until the screen shows text
           ("wait", secs)          pump output for a while
           ("send", str)           type
           ("snap", name)          record the current screen
    """
    master, slave = os.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    proc = subprocess.Popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env,
                            cwd=cwd, start_new_session=True, close_fds=True)
    os.close(slave)
    scr = vtscreen.Screen(rows, cols)
    snaps, timeline = {}, []
    deadline = time.time() + total_timeout

    def pump(secs):
        end = min(time.time() + secs, deadline)
        while time.time() < end:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    data = os.read(master, 65536)
                except OSError:
                    return False
                if not data:
                    return False
                scr.feed(data)
                resp = scr.take_responses()
                if resp:
                    os.write(master, resp)
            elif proc.poll() is not None:
                return False
        return True

    for step in steps:
        kind = step[0]
        if kind == "wait":
            pump(step[1])
        elif kind == "send":
            try:
                os.write(master, step[1].encode())
            except OSError:
                pass
        elif kind == "snap":
            snaps[step[1]] = scr.text()
        elif kind == "until":
            end = time.time() + step[2]
            found = False
            while time.time() < end:
                if step[1] in scr.text():
                    found = True
                    break
                if not pump(0.1):
                    found = step[1] in scr.text()
                    break
            timeline.append((step[1], found))
    pump(2.0)
    try:
        rc = proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        rc = "killed"
    try:
        os.close(master)
    except OSError:
        pass
    return rc, scr, snaps, timeline


# ------------------------------------------------------------- the checks

def check_build(ctx):
    if ctx.artifact:
        return [Result("build", "SKIP", "--artifact given: %s" % ctx.artifact)]
    out_dir = os.path.join(ctx.out, "build")
    env = dict(os.environ, BUN_BIN=ctx.bun, OUT_DIR=out_dir)
    r = run([os.path.join(HERE, "build.sh"), ctx.native], env, timeout=900)
    log = r["stdout"] + r["stderr"]
    with open(os.path.join(ctx.out, "build.log"), "w") as fh:
        fh.write(log)
    entry = os.path.join(out_dir, "extract", "cli.js")
    if r["rc"] != 0 or not os.path.isfile(entry):
        errors = [l for l in log.splitlines() if "error" in l.lower()][:8]
        return [Result("build", "FAIL", "build.sh rc=%s" % r["rc"], {"errors": errors})]
    ctx.artifact = entry
    counts = {}
    for line in log.splitlines():
        m = re.match(r"^\s*([a-zA-Z/$ ]+?)\s*: (.*)$", re.sub(r"\x1b\[[0-9;]*m", "", line))
        if m and len(m.group(1)) < 30:
            counts[m.group(1).strip()] = m.group(2).strip()
    return [Result("build", "PASS", "artifact %s" % entry, {"counts": counts})]


_SPEC = re.compile(r"""(?:\bfrom|\bimport|\bimport\s*\(|\brequire\s*\()\s*(["'])(\.{1,2}/[^"']+)\1""")
_ABS = re.compile(r"""\(import\.meta\.dirname\+(["'])(/[^"']+)\1\)""")


def _manifest(ctx):
    p = os.path.join(ctx.extract_dir, "manifest.json")
    return json.load(open(p)) if os.path.isfile(p) else None


def _js_modules(ctx):
    """Absolute paths of every JavaScript MODULE in the artifact: the manifest's
    js modules under root/, plus our entry and polyfill. Not every *.js file
    under root/ is a module: mermaid.min.js and friends are `file` assets that
    Claude reads as data (and they open with a UTF-8 BOM)."""
    manifest = _manifest(ctx)
    root = os.path.join(ctx.extract_dir, "root")
    out = []
    for mod in manifest["modules"]:
        if mod["loader"] in ("js", "jsx", "ts", "tsx"):
            rel = mod["path"] + (".js" if "." not in os.path.basename(mod["path"]) else "")
            out.append(os.path.join(root, *rel.split("/")))
    for name in ("cli.js", "bun-ant.mjs", "bun-ant-cell-segmenter.mjs"):
        out.append(os.path.join(ctx.extract_dir, name))
    return out


def check_structure(ctx):
    root = os.path.join(ctx.extract_dir, "root")
    if not _manifest(ctx):
        return [Result("structure", "SKIP", "legacy (single-module) artifact: no root/")]
    missing, specs, paths, files = [], 0, 0, 0
    for path in _js_modules(ctx):
        dp = os.path.dirname(path)
        files += 1
        if not os.path.isfile(path):
            missing.append("module itself: %s" % path)
            continue
        code = open(path, encoding="utf-8").read()
        for m in _SPEC.finditer(code):
            specs += 1
            if not os.path.isfile(os.path.normpath(os.path.join(dp, m.group(2)))):
                missing.append("%s -> %s" % (os.path.relpath(path, root), m.group(2)))
        for m in _ABS.finditer(code):
            paths += 1
            if not os.path.exists(os.path.normpath(dp + m.group(2))):
                missing.append("%s -> dirname%s" % (os.path.relpath(path, root), m.group(2)))
    status = "FAIL" if missing else "PASS"
    return [Result("structure", status,
                   "%d modules, %d specifiers, %d runtime paths, %d unresolved"
                   % (files, specs, paths, len(missing)), {"unresolved": missing[:30]})]


PARSE_JS = r"""
const fs = require("fs"), path = require("path");
const root = process.argv[2];
const files = JSON.parse(fs.readFileSync(process.argv[3], "utf8"));
const t = new Bun.Transpiler({ loader: "js" });
let n = 0; const bad = [];
for (const p of files) {
  n++;
  try { t.transformSync(fs.readFileSync(p, "utf8")); }
  catch (err) { bad.push(path.relative(root, p) + ": " + String(err && err.message || err).slice(0, 200)); }
}
console.log(JSON.stringify({ n, bad }));
"""


def check_parse(ctx):
    d = ctx.scratch("parse")
    script = os.path.join(d, "parse.js")
    open(script, "w").write(PARSE_JS)
    if not _manifest(ctx):
        files = [os.path.join(ctx.extract_dir, "cli.original.cjs")]
    else:
        files = _js_modules(ctx)
        # the text wrappers are ours and are JavaScript too
        root = os.path.join(ctx.extract_dir, "root")
        files += [os.path.join(dp, f) for dp, _, fs in os.walk(root) for f in fs if f.endswith(".cjs")]
    listing = os.path.join(d, "files.json")
    json.dump(files, open(listing, "w"))
    r = run([ctx.bun, script, ctx.extract_dir, listing], {"PATH": "/usr/bin:/bin"}, timeout=600)
    try:
        res = json.loads(r["stdout"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [Result("parse", "FAIL", "parser run failed rc=%s" % r["rc"],
                       {"stderr": r["stderr"][-2000:]})]
    status = "FAIL" if res["bad"] else "PASS"
    return [Result("parse", status, "%d files parsed by Bun, %d rejected" % (res["n"], len(res["bad"])),
                   {"rejected": res["bad"][:20]})]


TEXT_JS = r"""
const fs = require("fs"), crypto = require("crypto");
const m = JSON.parse(fs.readFileSync(process.env.NRC_MANIFEST, "utf8"));
const base = process.env.NRC_BASE;
const out = {};
for (const mod of m.modules) {
  if (mod.loader !== "text") continue;
  const p = base ? base + "/" + mod.path + ".cjs" : "/$bunfs/root/" + mod.path;
  try {
    const v = require(p);
    out[mod.path] = typeof v === "string"
      ? crypto.createHash("sha256").update(v, "utf8").digest("hex") + ":" + v.length
      : "NOT-A-STRING:" + typeof v;
  } catch (e) { out[mod.path] = "THREW:" + e.message; }
}
console.log(JSON.stringify(out));
process.exit(0);
"""


def native_probe(ctx, script_path, env_extra=None, timeout=300):
    """Run a probe INSIDE the native runtime via BUN_OPTIONS=--preload.

    The probe must process.exit() itself so Claude's own main never starts.
    """
    home = ctx.scratch("probe-home", os.path.basename(script_path))
    env = ctx.base_env(home)
    env["BUN_OPTIONS"] = "--preload " + script_path
    env.update(env_extra or {})
    return run([ctx.native, "--version"], env, timeout=timeout)


def check_text(ctx):
    manifest = _manifest(ctx)
    if not manifest:
        return [Result("text", "SKIP", "legacy artifact: no text modules")]
    d = ctx.scratch("text")
    script = os.path.join(d, "textmods.js")
    open(script, "w").write(TEXT_JS)
    mpath = os.path.join(ctx.extract_dir, "manifest.json")
    nat = native_probe(ctx, script, {"NRC_MANIFEST": mpath, "NRC_BASE": ""})
    art = run([ctx.bun, script], {"PATH": "/usr/bin:/bin", "NRC_MANIFEST": mpath,
                                  "NRC_BASE": os.path.join(ctx.extract_dir, "root")})
    try:
        a = json.loads(nat["stdout"].strip().splitlines()[-1])
        b = json.loads(art["stdout"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [Result("text", "FAIL", "probe did not report",
                       {"native": nat["stderr"][-800:], "artifact": art["stderr"][-800:]})]
    diff = sorted(k for k in a if a[k] != b.get(k))
    status = "FAIL" if diff or not a else "PASS"
    return [Result("text", status, "%d text modules, %d differ from native" % (len(a), len(diff)),
                   {"differ": diff[:20]})]


def _cli_pair(ctx, name, argv, cwd_files=None, setup=None, allow=()):
    """Run argv on both sides; compare rc and normalized stdout."""
    sides = {}
    for side in ("native", "artifact"):
        home = ctx.scratch("cli", name, side)
        work = os.path.join(home, "work")
        os.makedirs(work)
        env = ctx.base_env(home)
        if setup:
            setup(env, work)
        r = None
        for sub in argv if isinstance(argv[0], list) else [argv]:
            r = run(ctx.argv(side) + sub, env, cwd=work, timeout=120)
            r["norm"] = normalize(r["stdout"], ctx, [(work, "<WORK>"), (home, "<HOME>")])
            sides.setdefault("steps_" + side, []).append({"argv": sub, "rc": r["rc"]})
        sides[side] = r
    n, a = sides["native"], sides["artifact"]
    lines_n = [l for l in n["norm"].splitlines() if not any(re.search(p, l) for p in allow)]
    lines_a = [l for l in a["norm"].splitlines() if not any(re.search(p, l) for p in allow)]
    same_out = lines_n == lines_a
    same_rc = n["rc"] == a["rc"]
    status = "PASS" if same_out and same_rc and a["rc"] != "timeout" else "FAIL"
    details = {"rc": [n["rc"], a["rc"]], "secs": [n["secs"], a["secs"]],
               "first_diff": first_diff("\n".join(lines_n), "\n".join(lines_a)),
               "allowed_patterns": list(allow)}
    if status == "FAIL":
        details["artifact_stderr"] = a["stderr"][-1500:]
    return Result("cli:" + name, status,
                  "rc %s/%s, stdout %s" % (n["rc"], a["rc"], "equal" if same_out else "DIFFERS"),
                  details)


# doctor lines that name the interpreter or the install, which differ by
# construction (docs/findings.md 10): Running/Path/Invoked name bun and the
# entry, install method and search are the equivalence gap itself.
# Measured on 2.1.280 installed from the .deb: native says "Running:
# package-manager", "Package manager: deb", "Search: OK (bundled)" and
# "Auto-updates: Managed by package manager"; the artifact says "unknown",
# nothing, the system rg and the DISABLE_AUTOUPDATER line. Every one of them is
# install identity or search, i.e. the documented gap - nothing else may differ.
DOCTOR_ALLOWED = (r"^Running: ", r"^Package manager: ", r"^Path: ", r"^Invoked: ",
                  r"install method", r"^Search: ", r"^Auto-updates: ")


def check_cli(ctx):
    out = [
        _cli_pair(ctx, "version", ["--version"]),
        _cli_pair(ctx, "help", ["--help"]),
        _cli_pair(ctx, "mcp-list", ["mcp", "list"]),
        _cli_pair(ctx, "mcp-roundtrip", [
            ["mcp", "add", "harness-echo", "--", "/bin/echo", "hi"],
            ["mcp", "get", "harness-echo"],
            ["mcp", "remove", "harness-echo"],
            ["mcp", "list"],
        ]),
        _cli_pair(ctx, "plugin-list", ["plugin", "list"]),
        _cli_pair(ctx, "auth-status", ["auth", "status"]),
        _cli_pair(ctx, "doctor", ["doctor"], allow=DOCTOR_ALLOWED),
    ]
    return out


# ------------------------------------------------------------- agentic turns

def make_png(path, w=3000, h=3000):
    """A w x h RGB gradient PNG, written with zlib only."""
    xs = bytes((x * 255) // max(1, w - 1) for x in range(w))
    rows = bytearray()
    for y in range(h):
        row = bytearray(3 * w)
        row[0::3] = xs
        row[1::3] = bytes([(y * 255) // max(1, h - 1)]) * w
        row[2::3] = b"\x80" * w
        rows.append(0)          # filter type: none
        rows.extend(row)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(rows), 6)) + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)


def image_dims(b64, media):
    data = base64.b64decode(b64)
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return "jpeg", (w, h)
            i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    return media, None


_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_DEVICE = re.compile(r'(\\"device_id\\":\\")[0-9a-f]+')


def normalize_bodies(bodies, ctx, paths):
    """Request bodies as comparable text: per-run paths (and the slug form
    Claude derives from them for its memory directory), session uuids and the
    random per-config device id replaced. What is left is everything the model
    would see - system prompt, tools, messages - and must be equal."""
    out = []
    for body in bodies:
        text = json.dumps(body, sort_keys=True, ensure_ascii=False)
        for path, token in paths:
            text = text.replace(path, token).replace(path.replace("/", "-"), token)
        text = _UUID.sub("<UUID>", _DEVICE.sub(r"\1<DEVICE>", text))
        out.append(normalize(text, ctx, []))
    return out


def summarize_tool_result(block, ctx, paths):
    content = block.get("content")
    parts = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
    out = []
    for p in parts:
        if p.get("type") == "image":
            src = p.get("source", {})
            kind, dims = image_dims(src.get("data", ""), src.get("media_type"))
            out.append({"image": src.get("media_type"), "decoded": kind, "dims": dims,
                        "bytes": len(src.get("data", "")) * 3 // 4})
        else:
            out.append({"text": normalize(str(p.get("text", "")), ctx, paths)})
    return {"is_error": bool(block.get("is_error")), "content": out}


AGENTIC_CASES = [
    # name, tool, tool_input (<WORK> is substituted), --allowedTools, extra env
    ("text", "none", None, None, None),
    ("bash", "bash", None, None, None),
    ("read-text", "Read", {"file_path": "<WORK>/note.txt"}, None, None),
    ("read-png", "Read", {"file_path": "<WORK>/big.png"}, None, None),
    ("grep", "grep", None, "Grep,Bash,Read", None),
    ("write", "Write", {"file_path": "<WORK>/out.txt", "content": "WRITTEN-BY-HARNESS\n"}, None, None),
    ("glob", "Glob", {"pattern": "**/*.txt"}, "Glob,Bash,Read", None),
    # the plugin hooks-modules rollout, forced on: GrowthBook is off in this
    # sandbox, and outside a standalone the built-in plugins' hooks resolve
    # to <chunk dir>/hooks/register.ts instead (docs/findings.md 14)
    ("bash-function-hooks", "bash", None, None, {"CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1"}),
]


def _agentic_workdir(work, ctx):
    open(os.path.join(work, "note.txt"), "w").write("alpha\nNOTE-CONTENT-42\nomega\n")
    os.makedirs(os.path.join(work, "hay"))
    open(os.path.join(work, "hay", "a.txt"), "w").write("NEEDLE-12345\n")
    shutil.copyfile(ctx.png, os.path.join(work, "big.png"))


def check_agentic(ctx):
    ctx.png = os.path.join(ctx.scratch("png"), "big.png")
    make_png(ctx.png)
    results = []
    for name, tool, tool_input, allowed, extra_env in AGENTIC_CASES:
        sides = {}
        for side in ("native", "artifact"):
            home = ctx.scratch("agentic", name, side)
            work = os.path.join(home, "work")
            os.makedirs(work)
            _agentic_workdir(work, ctx)
            ti = json.loads(json.dumps(tool_input).replace("<WORK>", work)) if tool_input else None
            mock = Mock(ctx, "%s-%s" % (name, side), tool=tool, tool_input=ti)
            try:
                env = ctx.base_env(home, mock.port, extra_env)
                argv = ctx.argv(side) + ["-p", "run the harness case", "--output-format",
                                         "stream-json", "--verbose", "--dangerously-skip-permissions"]
                if allowed:
                    argv += ["--allowedTools", allowed]
                r = run(argv, env, cwd=work, timeout=180)
                paths = [(work, "<WORK>"), (home, "<HOME>")]
                final = None
                for line in r["stdout"].splitlines():
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    if ev.get("type") == "result":
                        final = {"subtype": ev.get("subtype"), "is_error": ev.get("is_error"),
                                 "result": ev.get("result")}
                sides[side] = {
                    "rc": r["rc"], "secs": r["secs"], "final": final,
                    "requests": mock.requests(),
                    "bodies": normalize_bodies(mock.post_bodies(), ctx, paths),
                    "tool_results": [summarize_tool_result(b, ctx, paths) for b in mock.tool_results()],
                    "files": sorted(os.listdir(work)),
                    "written": open(os.path.join(work, "out.txt")).read()
                    if os.path.exists(os.path.join(work, "out.txt")) else None,
                    "stderr": r["stderr"][-1200:],
                }
            finally:
                mock.stop()
        n, a = sides["native"], sides["artifact"]
        nb, ab = n.pop("bodies"), a.pop("bodies")
        bodies_equal = nb == ab
        body_diff = None
        if not bodies_equal:
            for i, (x, y) in enumerate(zip(nb, ab)):
                if x != y:
                    k = next(j for j in range(min(len(x), len(y)) + 1)
                             if j == min(len(x), len(y)) or x[j] != y[j])
                    body_diff = {"request": i, "native": x[max(0, k - 150):k + 150],
                                 "artifact": y[max(0, k - 150):k + 150]}
                    break
        same = (n["rc"] == a["rc"] and n["final"] == a["final"]
                and n["tool_results"] == a["tool_results"] and n["written"] == a["written"]
                and len(n["requests"]) == len(a["requests"]))
        note = ""
        if not same and name == "read-png":
            # the resize path runs through two different Bun.Image builds; equal
            # decoded dimensions and media type is the equivalence that matters
            def shape(side):
                return [[{k: v for k, v in c.items() if k != "bytes"} for c in tr["content"]]
                        for tr in side["tool_results"]]
            if (n["rc"] == a["rc"] and n["final"] == a["final"] and shape(n) == shape(a)):
                same, note = True, " (image bytes differ, dimensions and type equal)"
        ok_turn = a["final"] is not None and a["final"].get("result") == "MOCK-DONE"
        # the read-png turn sends the image back up, so its second body carries
        # the resized bytes: judged by the dimension rule above, not verbatim
        bodies_ok = bodies_equal or (name == "read-png" and same and nb[:1] == ab[:1])
        status = "PASS" if same and ok_turn and bodies_ok else "FAIL"
        results.append(Result("agentic:" + name, status,
                              "rc %s/%s, %d/%d requests, results %s, request bodies %s%s" % (
                                  n["rc"], a["rc"], len(n["requests"]), len(a["requests"]),
                                  "equal" if same else "DIFFER",
                                  "equal" if bodies_equal else ("equal bar the image" if bodies_ok else "DIFFER"),
                                  note),
                              {"native": {k: v for k, v in n.items() if k != "stderr"},
                               "artifact": a, "body_diff": body_diff}))
    return results


# ------------------------------------------------------------------ the TUI

def _seed_repl_config(env, work):
    cfg = os.path.join(env["CLAUDE_CONFIG_DIR"], ".claude.json")
    json.dump({"hasCompletedOnboarding": True, "theme": "dark", "numStartups": 3,
               "customApiKeyResponses": {"approved": [FAKE_KEY[-20:]], "rejected": []},
               "projects": {work: {"hasTrustDialogAccepted": True,
                                   "hasCompletedProjectOnboarding": True}}},
              open(cfg, "w"))


def _below(text, marker):
    i = text.find(marker)
    return text[i:] if i >= 0 else None


def check_tui(ctx):
    results = []
    # 1. onboarding: a fresh config shows the theme picker
    screens = {}
    for side in ("native", "artifact"):
        home = ctx.scratch("tui", "onboarding", side)
        env = ctx.base_env(home)
        rc, scr, snaps, tl = pty_session(ctx.argv(side), env, home, [
            ("until", "Choose the text style", 30), ("wait", 1.5), ("snap", "picker"),
            ("send", "\x03"), ("wait", 0.5), ("send", "\x03"), ("wait", 1)], total_timeout=60)
        screens[side] = {"rc": rc, "picker": snaps.get("picker", ""), "found": tl}
    n = _below(screens["native"]["picker"], "Let's get started")
    a = _below(screens["artifact"]["picker"], "Let's get started")
    ok = n is not None and n == a
    results.append(Result("tui:onboarding", "PASS" if ok else "FAIL",
                          "theme picker %s" % ("identical below the logo" if ok else "DIFFERS"),
                          {"first_diff": first_diff(n or "", a or ""),
                           "rc": [screens["native"]["rc"], screens["artifact"]["rc"]],
                           "artifact_screen": screens["artifact"]["picker"][-3000:]}))

    # 2. the REPL: one turn through the mock, then Ctrl-C twice - once with a
    #    plain answer, once with an answer built to exercise every class of
    #    text the Bun.ant.CellSegmenter port handles, rendered by Ink the way a
    #    real reply is (markdown, a table, a code block, a link)
    for name, text in (("repl", "MOCK-DONE"), ("repl-unicode", UNICODE_REPLY)):
        results.append(_tui_repl(ctx, name, text))
    return results


# An answer that leaves the ASCII fast path everywhere: CJK (wide), a ZWJ
# family and a flag (multi-code-point graphemes), a precomposed and a
# combining e-acute, Arabic and Hebrew (bidi reordering), a tab, markdown
# emphasis, a table, a fenced code block, a link, and a line long enough to
# wrap at 100 columns. MOCK-DONE stays on its own line so the harness can
# tell the answer has arrived.
UNICODE_REPLY = (
    "MOCK-DONE\n\n"
    "Wide: \u4e2d\u6587\u5b57\u7b26 \uff21\uff22 | emoji: \U0001F469\u200d\U0001F469\u200d\U0001F467\u200d\U0001F466 "
    "\U0001F1FA\U0001F1E6 \u2764\ufe0f | caf\u00e9 cafe\u0301 | tab\tafter\n\n"
    "Bidi: \u0645\u0631\u062d\u0628\u0627 \u05e9\u05dc\u05d5\u05dd and back to LTR (123)\n\n"
    "**bold** *italic* `inline code` and a [link](https://example.com/path?q=1)\n\n"
    "| col | \u5217 |\n|---|---|\n| a | \u4e2d |\n| \U0001F600 | b |\n\n"
    "```python\nprint(\"\u4e2d\u6587\")  # comment\n```\n\n"
    + "A long line that must wrap: " + " ".join("word%d" % i for i in range(40)) + "\n")

# The one line two identical runs still draw differently: the spinner verb is
# chosen at random ("Brewed", "Churned", ...) and the clock is the clock.
_TUI_VOLATILE = re.compile(r"^(\s*\S)\s+\w+ for [0-9hms ]+(?: · done \d{1,2}:\d{2}(?: [AP]M)?)?\s*$")


def _tui_normalize(text):
    return "\n".join(_TUI_VOLATILE.sub(r"\1 <STATUS>", l) for l in (text or "").splitlines())


def _tui_repl(ctx, name, reply):
    screens = {}
    for side in ("native", "artifact"):
        home = ctx.scratch("tui", name, side)
        work = os.path.join(home, "work")
        os.makedirs(work)
        mock = Mock(ctx, "tui-%s-%s" % (name, side), tool="none", text=reply)
        try:
            env = ctx.base_env(home, mock.port)
            _seed_repl_config(env, work)
            rc, scr, snaps, tl = pty_session(ctx.argv(side), env, work, [
                ("until", "for shortcuts", 40), ("wait", 1.0), ("snap", "ready"),
                ("send", "say hi"), ("wait", 0.5), ("send", "\r"),
                ("until", "MOCK-DONE", 40), ("wait", 2.0), ("snap", "answered"),
                ("send", "\x03"), ("wait", 0.5), ("send", "\x03"), ("wait", 2)], total_timeout=120)
            screens[side] = {"rc": rc, "snaps": snaps, "found": tl, "final": scr.text(),
                             "requests": mock.requests(), "alt": scr.alt_active,
                             "cursor_visible": scr.cursor_visible}
        finally:
            mock.stop()
    n, a = screens["native"], screens["artifact"]
    ns, as_ = _tui_normalize(n["snaps"].get("answered")), _tui_normalize(a["snaps"].get("answered"))
    answered = "MOCK-DONE" in as_
    same = ns == as_
    ready_same = _tui_normalize(n["snaps"].get("ready")) == _tui_normalize(a["snaps"].get("ready"))
    clean_exit = a["rc"] == n["rc"] == 0 and a["cursor_visible"] and not a["alt"]
    resume = "--resume" in a["final"]
    ok = (answered and same and ready_same and clean_exit and resume
          and len(a["requests"]) == len(n["requests"]))
    return Result("tui:" + name, "PASS" if ok else "FAIL",
                  "answered=%s screen %s, ready screen %s, exit rc %s/%s restored=%s resume-hint=%s" % (
                      answered, "identical" if same else "DIFFERS",
                      "identical" if ready_same else "DIFFERS", n["rc"], a["rc"], clean_exit, resume),
                  {"first_diff": first_diff(ns, as_),
                   "native_answered": n["snaps"].get("answered", "")[-4000:],
                   "artifact_answered": a["snaps"].get("answered", "")[-4000:],
                   "artifact_final": a["final"][-1500:],
                   "requests": [n["requests"], a["requests"]]})


# ------------------------------------------------------------------ Bun.ant

BUNANT_JS = r"""
const net = require("net"), os = require("os"), path = require("path"), fs = require("fs");
const A = Bun.ant, out = {};
function t(k, fn) { try { const v = fn(); out[k] = v === null ? null : typeof v + ":" + (typeof v === "number" && k.includes("conn") ? (v === process.pid || v === process.getuid() ? "self" : "other") : v); } catch (e) { out[k] = "THREW " + e.message; } }
t("mem", () => A.memoryPressureLevel());
t("peerPid_-1", () => A.getPeerPid(-1));
t("peerPid_999", () => A.getPeerPid(999));
t("peerPid_str", () => A.getPeerPid("3"));
t("peerUid_-1", () => A.getPeerUid(-1));
t("dump_true", () => A.setDumpable(true));
t("dump_false", () => A.setDumpable(false));
const sock = path.join(os.tmpdir(), "nrc-h-" + process.pid + ".sock");
const srv = net.createServer((c) => {
  const fd = c._handle && c._handle.fd;
  t("peerPid_conn", () => A.getPeerPid(fd));
  t("peerUid_conn", () => A.getPeerUid(fd));
  c.end(); srv.close(); try { fs.unlinkSync(sock); } catch {}
  console.log(JSON.stringify(out)); process.exit(0);
});
srv.listen(sock, () => { net.connect(sock); });
setTimeout(() => { console.log(JSON.stringify(out)); process.exit(0); }, 5000);
"""


def check_bunant(ctx):
    d = ctx.scratch("bunant")
    script = os.path.join(d, "bunant.js")
    open(script, "w").write(BUNANT_JS)
    nat = native_probe(ctx, script)
    polyfill = os.path.join(ctx.extract_dir, "bun-ant.mjs")
    if not os.path.isfile(polyfill):
        return [Result("bunant", "SKIP", "legacy artifact: no Bun.ant polyfill")]
    art = run([ctx.bun, "--preload", polyfill, script], {"PATH": "/usr/bin:/bin"})
    try:
        a = json.loads(nat["stdout"].strip().splitlines()[-1])
        b = json.loads(art["stdout"].strip().splitlines()[-1])
    except (ValueError, IndexError):
        return [Result("bunant", "FAIL", "probe did not report",
                       {"native": nat["stderr"][-800:], "artifact": art["stderr"][-800:]})]
    diff = {k: [a.get(k), b.get(k)] for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)}
    return [Result("bunant", "FAIL" if diff else "PASS",
                   "%d probes, %d differ" % (len(a), len(diff)), {"differ": diff, "native": a})]


def check_segmenter(ctx):
    fuzz = os.path.join(REPO, "tests", "cell_segmenter_fuzz.cjs")
    oracle = os.path.join(REPO, "tests", "cell_segmenter_oracle.cjs")
    polyfill = os.path.join(ctx.extract_dir, "bun-ant.mjs")
    if not (os.path.isfile(fuzz) and os.path.isfile(oracle)):
        return [Result("segmenter", "SKIP", "fuzz corpus generator not present yet")]
    if not os.path.isfile(polyfill):
        return [Result("segmenter", "SKIP", "legacy artifact: no Bun.ant polyfill")]
    d = ctx.scratch("segmenter")
    cases = os.path.join(d, "cases.jsonl")
    r = run([ctx.bun, fuzz, cases, str(ctx.args.fuzz_cases), str(ctx.args.fuzz_seed)],
            {"PATH": "/usr/bin:/bin"}, timeout=600)
    if r["rc"] != 0:
        return [Result("segmenter", "FAIL", "case generation failed", {"stderr": r["stderr"][-1500:]})]
    nat_out, art_out = os.path.join(d, "native.jsonl"), os.path.join(d, "artifact.jsonl")
    nat = native_probe(ctx, oracle, {"NRC_CASES": cases, "NRC_OUT": nat_out}, timeout=900)
    art = run([ctx.bun, "--preload", polyfill, oracle],
              {"PATH": "/usr/bin:/bin", "NRC_CASES": cases, "NRC_OUT": art_out}, timeout=900)
    if not (os.path.isfile(nat_out) and os.path.isfile(art_out)):
        return [Result("segmenter", "FAIL", "oracle run failed",
                       {"native": nat["stderr"][-800:], "artifact": art["stderr"][-800:]})]
    nl, al = open(nat_out).read().splitlines(), open(art_out).read().splitlines()
    bad = [i for i in range(max(len(nl), len(al)))
           if (nl[i] if i < len(nl) else None) != (al[i] if i < len(al) else None)]
    detail = {}
    if bad:
        i = bad[0]
        detail = {"first_case": json.loads(open(cases).read().splitlines()[i]),
                  "native": nl[i][:3000] if i < len(nl) else None,
                  "artifact": al[i][:3000] if i < len(al) else None}
    return [Result("segmenter", "FAIL" if bad else "PASS",
                   "%d cases (seed %s), %d mismatch native" % (len(nl), ctx.args.fuzz_seed, len(bad)),
                   detail)]


def check_pytest(ctx):
    r = run([sys.executable, "-m", "pytest", os.path.join(REPO, "tests"), "-q", "-p", "no:cacheprovider"],
            dict(os.environ, NRC_TEST_ARTIFACT=ctx.artifact or ""), cwd=REPO, timeout=1800)
    tail = r["stdout"].strip().splitlines()[-1:] or [""]
    return [Result("pytest", "PASS" if r["rc"] == 0 else "FAIL", tail[0],
                   {"failures": [l for l in r["stdout"].splitlines() if l.startswith("FAILED")][:30]})]


CHECKS = {
    "build": check_build, "structure": check_structure, "parse": check_parse,
    "text": check_text, "cli": check_cli, "agentic": check_agentic, "tui": check_tui,
    "bunant": check_bunant, "segmenter": check_segmenter, "pytest": check_pytest,
}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--native", default="/usr/bin/claude")
    ap.add_argument("--bun", default="~/.bun-1.3.14/bun")
    ap.add_argument("--out", default=os.path.join(REPO, "build", "harness"))
    ap.add_argument("--artifact", help="an already-built extract/cli.js; skips the build")
    ap.add_argument("--only", help="comma-separated groups")
    ap.add_argument("--skip", help="comma-separated groups")
    ap.add_argument("--fuzz-cases", type=int, default=3000)
    ap.add_argument("--fuzz-seed", type=int, default=1)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()
    if args.list:
        print("\n".join(GROUPS))
        return 0
    groups = args.only.split(",") if args.only else list(GROUPS)
    if args.skip:
        groups = [g for g in groups if g not in args.skip.split(",")]
    if "build" not in groups and not args.artifact:
        default = os.path.join(args.out, "build", "extract", "cli.js")
        if os.path.isfile(default):
            args.artifact = default
    ctx = Ctx(args)
    os.makedirs(ctx.out, exist_ok=True)
    for g in groups:
        if g not in CHECKS:
            print("unknown group %r (see --list)" % g, file=sys.stderr)
            return 2
        if g != "build" and not ctx.artifact:
            ctx.results.append(Result(g, "SKIP", "no artifact (build failed or not requested)"))
            continue
        t0 = time.time()
        try:
            res = CHECKS[g](ctx)
        except Exception as e:  # a harness bug must be a FAIL, never a silent pass
            import traceback
            res = [Result(g, "FAIL", "harness error: %s" % e, {"traceback": traceback.format_exc()})]
        for r in res:
            r.details["group_secs"] = round(time.time() - t0, 1)
            ctx.results.append(r)
            mark = {"PASS": "\033[32mPASS\033[0m", "FAIL": "\033[31mFAIL\033[0m",
                    "SKIP": "\033[33mSKIP\033[0m"}[r.status]
            print("%s  %-22s %s" % (mark, r.name, r.summary), flush=True)
    report = {"native": ctx.native, "bun": ctx.bun, "artifact": ctx.artifact,
              "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "results": [r.as_dict() for r in ctx.results]}
    path = os.path.join(ctx.out, "report.json")
    with open(path, "w") as fh:
        json.dump(report, fh, indent=1)
    counts = {s: sum(1 for r in ctx.results if r.status == s) for s in ("PASS", "FAIL", "SKIP")}
    print("\n%d passed, %d failed, %d skipped - report: %s" % (counts["PASS"], counts["FAIL"],
                                                                counts["SKIP"], path))
    return 1 if counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
