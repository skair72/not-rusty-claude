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
  build      build.sh succeeds, runs its own parser gate, and its counts add
             up to the manifest (plus the Chrome self-spawn rewrite and the
             embedded-search gate rewrite applied)
  provenance the native version, and whether the artifact carries the polyfill
             now in scripts/ (a stale artifact fails)
  structure  every relative specifier and runtime path in root/ exists
  parse      scripts/verify-tree.js: Bun parses every module, and every module
             keeps exactly the import records it had before the rewrite
  text       every text module require()s to the native string
  cli        non-interactive commands, EVERY step's exit code, stdout and
             stderr; the Chrome MCP server; Claude's own Chrome-MCP config
             spawned exactly as Claude would spawn it
  agentic    mock-API turns (text, Bash, Read, Read of a large PNG, Grep,
             Write, Glob, function hooks, Bash grep/find): the tool results
             and the full request bodies each side sent up; plus
             search-optin, what native's own search opt-in changes on native
  tui        the interactive TUI under a pty, screens compared through
             scripts/vtscreen.py with styles and links: onboarding, a REPL
             turn and a REPL turn full of unicode, request bodies, clean exit
  bunant     Bun.ant: the polyfill against the native members, with a real
             peer process on the socket (the native binary honours
             BUN_OPTIONS=--preload, so probes run inside it)
  segmenter  CellSegmenter differential fuzz, native vs polyfill, every case
  pytest     the repo's own suite, pointed at the same native binary

Native runs every agentic and TUI session under Claude's own Glob/Grep opt-in,
NATIVE_SEARCH_OPTIN, because the artifact has the embedded-search gate
rewritten to false: bun has no embedded ugrep/bfs for Bash grep/find to run
(docs/findings.md 10). The comparison stays exact; agentic:search-optin pins
what the opt-in changes on native in a -p turn.

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
# Claude's own switch for its non-embedded search configuration, and nothing
# else. Naming Glob or Grep in --allowedTools sets searchToolsOptIn, which is
# read in one place: the embedded-search gate the artifact has rewritten to
# false (docs/findings.md 10). The rule itself allows Glob in a directory
# that does not exist, so it grants nothing; `=` keeps the variadic flag
# from swallowing the prompt that follows it.
NATIVE_SEARCH_OPTIN = "--allowedTools=Glob(/nonexistent-nrc-search-optin/**)"
GROUPS = ["build", "provenance", "structure", "parse", "text", "cli", "agentic", "tui",
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

    def session_argv(self, side):
        """argv for a Claude SESSION - an agentic turn or the TUI - which is
        where the embedded-search gate shows. The artifact runs it false
        (postprocess.py), so native is compared under its own opt-in to the
        same configuration; agentic:search-optin pins what that opt-in changes
        on native. Subcommands do not take the flag and do not need it."""
        if side == "native":
            return self.argv(side) + [NATIVE_SEARCH_OPTIN]
        return self.argv(side)

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


def run(argv, env, cwd=None, timeout=120, stdin=subprocess.DEVNULL, input_data=None):
    t0 = time.time()
    try:
        kw = {"input": input_data} if input_data is not None else {"stdin": stdin}
        p = subprocess.run(argv, env=env, cwd=cwd, timeout=timeout,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, **kw)
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

    def __init__(self, ctx, tag, tool="none", tool_input=None, text="MOCK-DONE", extra_argv=()):
        d = ctx.scratch("mock", tag)
        self.ready = os.path.join(d, "ready")
        self.log = os.path.join(d, "requests.log")
        self.bodies = os.path.join(d, "bodies.jsonl")
        argv = [ctx.bun, os.path.join(HERE, "mock-messages-api.mjs"),
                "--tool", tool, "--text", text, "--ready-file", self.ready,
                "--log", self.log, "--log-bodies", self.bodies]
        if tool_input is not None:
            argv += ["--tool-input", json.dumps(tool_input)]
        argv += list(extra_argv)
        self.proc = subprocess.Popen(argv, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, env={"PATH": "/usr/bin:/bin"})
        try:
            for _ in range(200):
                if os.path.exists(self.ready) and open(self.ready).read().strip():
                    break
                time.sleep(0.05)
            self.port = int(open(self.ready).read().strip())
        except (OSError, ValueError):
            self.stop()   # the caller's finally has not started yet
            raise RuntimeError("the mock did not come up within 10 s")

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
    try:
        return _drive(proc, master, steps, rows, cols, total_timeout)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        try:
            os.close(master)
        except OSError:
            pass


def _drive(proc, master, steps, rows, cols, total_timeout):
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
            snaps[step[1] + ":styled"] = scr.styled_text()
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
    return rc, scr, snaps, timeline


# ------------------------------------------------------------- the checks

def check_build(ctx):
    if ctx.artifact:
        return [Result("build", "SKIP", "--artifact given: %s" % ctx.artifact)]
    out_dir = os.path.join(ctx.out, "build")
    # the parse group re-runs verify-tree.js, which reads original/
    env = dict(os.environ, BUN_BIN=ctx.bun, OUT_DIR=out_dir, NRC_KEEP_ORIGINAL="1")
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
        m = re.match(r"^\s*([a-zA-Z/$' -]+?)\s*: (.*)$", re.sub(r"\x1b\[[0-9;]*m", "", line))
        if m and len(m.group(1)) < 30:
            counts[m.group(1).strip()] = m.group(2).strip()
    problems = []
    manifest = _manifest(ctx)
    if manifest:
        # the counts add up, and describe THIS artifact
        m = re.match(r"(\d+) \((\d+) js rewritten, (\d+) text wrapped, (\d+) copied\)",
                     counts.get("modules", ""))
        if not m:
            problems.append("no modules line in the build log")
        else:
            total, js, text, copied = map(int, m.groups())
            if total != js + text + copied or total != len(manifest["modules"]):
                problems.append("module counts do not add up: %s against %d in the manifest"
                                % (counts["modules"], len(manifest["modules"])))
        if not str(counts.get("self-spawns given entry", "")).startswith("1"):
            problems.append("the Claude-in-Chrome self-spawn was not given the entry")
        if "verifying the rewired tree" not in log:
            problems.append("build.sh did not run scripts/verify-tree.js")
    # Both graph shapes print it (docs/findings.md 10). Anything but 1 fails:
    # without the rewrite the native side's search opt-in compares the
    # artifact with a configuration it no longer has.
    search = str(counts.get("embedded search off", ""))
    if "not applicable" in search:
        problems.append("this Claude release has no embedded-search gate: re-measure "
                        "NATIVE_SEARCH_OPTIN and agentic:search-optin before trusting them")
    elif not search.startswith("1"):
        problems.append("the embedded-search gate was not rewritten (%r), so Bash grep/find "
                        "would run bun as ugrep/bfs" % (search or "no line"))
    return [Result("build", "FAIL" if problems else "PASS",
                   "artifact %s%s" % (entry, "; " + "; ".join(problems) if problems else ""),
                   {"counts": counts})]


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


def check_parse(ctx):
    """Bun's own parser over the whole tree: every module parses and keeps
    exactly the import records it had before the rewrite
    (scripts/verify-tree.js, which build.sh also runs)."""
    if not _manifest(ctx):
        r = run([ctx.bun, "build", "--no-bundle", "--target=bun",
                 os.path.join(ctx.extract_dir, "cli.original.cjs"), "--outfile=/dev/null"],
                {"PATH": "/usr/bin:/bin"}, timeout=600)
        return [Result("parse", "PASS" if r["rc"] == 0 else "FAIL",
                       "legacy artifact: bun build --no-bundle rc=%s" % r["rc"],
                       {"stderr": r["stderr"][-1500:]})]
    tree = os.path.join(ctx.extract_dir, _manifest(ctx).get("tree", "original"))
    if not os.path.isdir(tree):
        return [Result("parse", "FAIL", "%s is gone: build.sh removed it after its own check; "
                       "rebuild with NRC_KEEP_ORIGINAL=1 to check this artifact" % tree)]
    r = run([ctx.bun, os.path.join(HERE, "verify-tree.js"), ctx.extract_dir],
            {"PATH": "/usr/bin:/bin"}, timeout=600)
    try:
        res = json.loads(r["stdout"].strip().split("\n")[-1])
    except (ValueError, IndexError):
        return [Result("parse", "FAIL", "verify-tree.js did not report (rc=%s)" % r["rc"],
                       {"stderr": r["stderr"][-2000:]})]
    ok = r["rc"] == 0 and not res["problemCount"] and not res["rejectedCount"] and res["modules"] > 0
    return [Result("parse", "PASS" if ok else "FAIL",
                   "%d files parsed by Bun, %d import records kept across %d modules, "
                   "%d problems, %d rejected" % (res["parsed"], res["records"], res["modules"],
                                                 res["problemCount"], res["rejectedCount"]),
                   {"problems": res["problems"], "rejected": res["rejected"]})]


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


def _cli_pair(ctx, name, steps, allow=(), input_data=None):
    """Run each step on both sides, one throwaway HOME per side, in order.
    EVERY step's exit code, stdout and stderr must match (lines matching
    `allow` excepted) - a round trip is judged step by step, not by its end
    state, which an add/get/remove that did nothing would reach as well."""
    steps = steps if isinstance(steps[0], list) else [steps]
    runs = {}
    for side in ("native", "artifact"):
        home = ctx.scratch("cli", name, side)
        work = os.path.join(home, "work")
        os.makedirs(work)
        env = ctx.base_env(home)
        paths = [(work, "<WORK>"), (home, "<HOME>")]
        runs[side] = []
        for sub in steps:
            r = run(ctx.argv(side) + sub, env, cwd=work, timeout=120, input_data=input_data)

            def keep(text):
                return "\n".join(l for l in normalize(text, ctx, paths).split("\n")
                                 if not any(re.search(p, l) for p in allow))
            runs[side].append({"argv": sub, "rc": r["rc"], "out": keep(r["stdout"]),
                               "err": keep(r["stderr"]), "secs": r["secs"]})
    diffs = []
    for i, (n, a) in enumerate(zip(runs["native"], runs["artifact"])):
        for k in ("rc", "out", "err"):
            if n[k] != a[k]:
                diffs.append({"step": i, "argv": n["argv"], "what": k,
                              "diff": [n["rc"], a["rc"]] if k == "rc" else first_diff(n[k], a[k])})
    timeout = any(x["rc"] == "timeout" for x in runs["native"] + runs["artifact"])
    empty = all(not x["out"] and not x["err"] for x in runs["native"])
    status = "PASS" if not diffs and not timeout and not empty else "FAIL"
    what = ("%d step(s): exit code, stdout and stderr equal" % len(steps) if not diffs
            else "step %d (%s) %s DIFFERS" % (diffs[0]["step"], " ".join(diffs[0]["argv"]), diffs[0]["what"]))
    if empty:
        what += "; native printed NOTHING, so equality proves nothing"
    return Result("cli:" + name, status, what,
                  {"diffs": diffs[:10], "rc": [[x["rc"] for x in runs[s_]] for s_ in ("native", "artifact")],
                   "secs": [[x["secs"] for x in runs[s_]] for s_ in ("native", "artifact")],
                   "allowed_patterns": list(allow),
                   "native_out": [x["out"][-800:] for x in runs["native"]]})


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

MCP_INITIALIZE = (b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":'
                  b'"2025-06-18","capabilities":{},"clientInfo":{"name":"harness","version":"1"}}}\n')

# Claude's own Chrome-MCP config function, called on each side, and what it
# returns spawned exactly as Claude would spawn it. Natively the command is
# claude itself; in the artifact it is bun, and the entry must be in args or
# `bun --claude-in-chrome-mcp` prints Bun's help (postprocess.py SELF_SPAWNS).
CHROME_PROBE = r"""
const { spawn } = require("child_process");
const mod = await import(process.env.NRC_CHUNK);
const cfg = mod[process.env.NRC_EXPORT]();
const env = { ...process.env };
delete env.BUN_OPTIONS;   // or a native child would run this probe again
const child = spawn(cfg.command, cfg.args, { env, stdio: ["pipe", "pipe", "ignore"] });
let out = "";
const done = (why) => {
  child.kill();
  console.log(JSON.stringify({ args: cfg.args.map((a) => a === process.argv[1] ? "<ENTRY>" : a),
    why, response: out.split("\n").find((l) => l.includes('"id":1')) || null }));
  process.exit(0);
};
child.stdout.on("data", (d) => { out += d; if (out.includes('"id":1')) done("answered"); });
child.on("exit", (code) => done("exited " + code));
child.stdin.write(process.env.NRC_INPUT);   // stdin stays open: EOF would race the reply
setTimeout(() => done("timeout"), 30000);
"""


def mcp_handshake(argv, env, cwd=None, timeout=30):
    """Start an MCP stdio server, send initialize, return the id:1 reply line
    (or None). stdin stays open until the reply: closing it at once races the
    server's answer against its EOF handling."""
    proc = subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    buf, reply = b"", None
    try:
        proc.stdin.write(MCP_INITIALIZE)
        proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline and reply is None:
            ready, _, _ = select.select([proc.stdout], [], [], 0.2)
            if ready:
                chunk = os.read(proc.stdout.fileno(), 65536)
                if not chunk:
                    break
                buf += chunk
                for line in buf.split(b"\n"):
                    if b'"id":1' in line:
                        reply = line.decode("utf-8", "replace")
    finally:
        proc.kill()
        proc.wait()
    return reply


def _chrome_mcp_export(ctx):
    """(chunk file name, export name) of the Chrome-MCP config function in the
    artifact, found by its shape - never by calling exports to see."""
    root = os.path.join(ctx.extract_dir, "root")
    fn = re.compile(r"function ([\w$]+)\(\)\{return\{type:\"stdio\",command:process\.execPath,"
                    r"args:\[(?:process\.argv\[1\],)?\"--claude-in-chrome-mcp\"")
    for path in _js_modules(ctx):
        if os.path.dirname(path) != root:
            continue
        name = os.path.basename(path)
        code = open(path, encoding="utf-8").read()
        m = fn.search(code)
        if not m:
            continue
        local = m.group(1)
        for clause in re.findall(r"export\{([^}]*)\}", code):
            for item in clause.split(","):
                parts = item.strip().split(" as ")
                if parts[0] == local:
                    return name, parts[-1]
    return None, None


def check_chrome_mcp_server(ctx):
    """The Chrome MCP server itself, started by hand the way the legacy sibling
    started it: an initialize must get the same reply on both sides."""
    got = {}
    for side in ("native", "artifact"):
        home = ctx.scratch("chrome-server", side)
        got[side] = mcp_handshake(ctx.argv(side) + ["--claude-in-chrome-mcp"], ctx.base_env(home))
    ok = got["native"] is not None and got["native"] == got["artifact"]
    return Result("cli:chrome-mcp-server", "PASS" if ok else "FAIL",
                  "initialize answered %s" % ("identically" if ok else "DIFFERENTLY (or not at all)"), got)


def check_chrome_mcp_spawn(ctx):
    chunk, export = _chrome_mcp_export(ctx)
    if not chunk:
        return Result("cli:chrome-mcp-spawn", "FAIL", "no Chrome-MCP config function found in root/")
    d = ctx.scratch("chrome")
    probe = os.path.join(d, "probe.mjs")
    open(probe, "w").write(CHROME_PROBE)
    got = {}
    for side in ("native", "artifact"):
        home = ctx.scratch("chrome", side)
        env = ctx.base_env(home, extra={"NRC_EXPORT": export,
                                        "NRC_INPUT": MCP_INITIALIZE.decode()})
        if side == "native":
            env["NRC_CHUNK"] = "/$bunfs/root/" + chunk
            env["BUN_OPTIONS"] = "--preload " + probe
            r = run([ctx.native, "--version"], env, timeout=120)
        else:
            env["NRC_CHUNK"] = os.path.join(ctx.extract_dir, "root", chunk)
            r = run([ctx.bun, "--preload", probe, ctx.artifact, "--version"], env, timeout=120)
        try:
            got[side] = json.loads(r["stdout"].strip().split("\n")[-1])
        except (ValueError, IndexError):
            got[side] = {"error": (r["stdout"] + r["stderr"])[-600:]}
    n, a = got["native"], got["artifact"]
    answered = '"serverInfo":{"name":"Claude in Chrome"' in str(a.get("response"))
    same = n.get("response") == a.get("response") and n.get("response") is not None
    return Result("cli:chrome-mcp-spawn", "PASS" if answered and same else "FAIL",
                  "Claude's own config spawned on each side: %s, response %s" % (
                      "server answered" if answered else "NO SERVER", "equal" if same else "DIFFERS"),
                  {"native": n, "artifact": a, "export": [chunk, export]})


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
    out.append(check_chrome_mcp_server(ctx))
    if _manifest(ctx):
        out.append(check_chrome_mcp_spawn(ctx))
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


# The bash-search case's command. Run from a directory built to tell grep
# implementations apart (_search_fixture): the embedded ugrep native shadows
# grep with skips the .gitignore'd and the binary file, a system grep does
# not. `${PIPESTATUS[0]}` is grep's and find's own status, not sort's.
SEARCH_COMMAND = ("cd search && grep -rn NEEDLE-12345 . | sort; echo \"grep_rc=${PIPESTATUS[0]}\"; "
                  "find . -name '*.txt' | sort; echo \"find_rc=${PIPESTATUS[0]}\"")

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
    # Bash grep and find with NO --allowedTools: the case that would have
    # caught every Bash grep printing Bun's help (docs/findings.md 10). The
    # grep and glob cases above opt in, which switches the shadowing off.
    ("bash-search", "Bash", {"command": SEARCH_COMMAND, "description": "search probe"},
     None, None),
]

# What a Bash grep answered while it ran bun: `bun -G ...` is an invalid
# argument, followed by Bun's help. Neither may appear in any search result.
_BUN_AS_GREP = ("Invalid Argument", "Bun is a fast JavaScript runtime")


def _agentic_workdir(work, ctx, name):
    open(os.path.join(work, "note.txt"), "w").write("alpha\nNOTE-CONTENT-42\nomega\n")
    os.makedirs(os.path.join(work, "hay"))
    open(os.path.join(work, "hay", "a.txt"), "w").write("NEEDLE-12345\n")
    shutil.copyfile(ctx.png, os.path.join(work, "big.png"))
    if name == "bash-search":
        _search_fixture(os.path.join(work, "search"))


def _search_fixture(d):
    """The needle four times: plain, hidden, .gitignore'd and in a binary file."""
    for rel, data in (("hay/a.txt", b"NEEDLE-12345\n"), (".hidden/y.txt", b"NEEDLE-12345\n"),
                      ("ignored/x.txt", b"NEEDLE-12345\n"), ("bin.dat", b"\0\1NEEDLE-12345\0\n"),
                      (".gitignore", b"ignored/\n")):
        os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
        with open(os.path.join(d, rel), "wb") as fh:
            fh.write(data)


def _agentic_side(ctx, name, side, argv, tool, tool_input, allowed, extra_env):
    """One side of one case: a scratch HOME, the mock, one `-p` turn."""
    home = ctx.scratch("agentic", name, side)
    work = os.path.join(home, "work")
    os.makedirs(work)
    _agentic_workdir(work, ctx, name)
    ti = json.loads(json.dumps(tool_input).replace("<WORK>", work)) if tool_input else None
    mock = Mock(ctx, "%s-%s" % (name, side), tool=tool, tool_input=ti)
    try:
        env = ctx.base_env(home, mock.port, extra_env)
        argv = argv + ["-p", "run the harness case", "--output-format",
                       "stream-json", "--verbose", "--dangerously-skip-permissions"]
        if allowed:
            argv += ["--allowedTools", allowed]
        r = run(argv, env, cwd=work, timeout=180)
        paths = [(work, "<WORK>"), (home, "<HOME>")]
        final = None
        for line in r["stdout"].split("\n"):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "result":
                final = {"subtype": ev.get("subtype"), "is_error": ev.get("is_error"),
                         "result": ev.get("result")}
        return {
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


def _result_text(side):
    return "\n".join(p.get("text", "") for tr in side["tool_results"] for p in tr["content"])


def check_agentic(ctx):
    ctx.png = os.path.join(ctx.scratch("png"), "big.png")
    make_png(ctx.png)
    results = []
    search_native = None
    for name, tool, tool_input, allowed, extra_env in AGENTIC_CASES:
        sides = {side: _agentic_side(ctx, name, side, ctx.session_argv(side), tool,
                                     tool_input, allowed, extra_env)
                 for side in ("native", "artifact")}
        n, a = sides["native"], sides["artifact"]
        nb, ab = n.pop("bodies"), a.pop("bodies")
        if name == "bash-search":
            search_native = dict(n, bodies=nb)
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
                and n["files"] == a["files"] and len(n["requests"]) == len(a["requests"]))
        # No relaxation for the image turn: measured, both Bun.Image builds
        # produce the same resized bytes, so they are compared like everything
        # else, inside the tool result and inside the request body.
        ok_turn = a["final"] is not None and a["final"].get("result") == "MOCK-DONE"
        # Equal to native is not enough on its own for the search case: it
        # must also not be the bug, whatever native did.
        sane = not any(m in _result_text(a) for m in _BUN_AS_GREP)
        status = "PASS" if same and ok_turn and bodies_equal and nb and sane else "FAIL"
        results.append(Result("agentic:" + name, status,
                              "rc %s/%s, %d/%d requests, results %s, request bodies %s%s" % (
                                  n["rc"], a["rc"], len(n["requests"]), len(a["requests"]),
                                  "equal" if same else "DIFFER",
                                  "equal" if bodies_equal else "DIFFER",
                                  "" if sane else ", and a search ANSWERED BY BUN"),
                              {"native": {k: v for k, v in n.items() if k != "stderr"},
                               "artifact": a, "body_diff": body_diff}))
    if search_native is not None:
        results.append(_search_optin(ctx, search_native))
    return results


def _tool_names(body_text):
    try:
        return sorted(t.get("name") for t in json.loads(body_text).get("tools", []))
    except (ValueError, AttributeError, TypeError):
        return None


def _without_search_tools(body_text):
    body = json.loads(body_text)
    body["tools"] = [t for t in body.get("tools", []) if t.get("name") not in ("Glob", "Grep")]
    return json.dumps(body, sort_keys=True, ensure_ascii=False)


def _search_optin(ctx, optin):
    """What NATIVE_SEARCH_OPTIN changes on native itself, pinned.

    The agentic and TUI checks compare the artifact with native under that
    opt-in, which is only honest while the opt-in changes exactly what
    docs/findings.md 10 says: native's default offers the same tools less
    Glob and Grep, sends the same first request otherwise, and answers a Bash
    grep through its embedded ugrep, which skips the .gitignore'd and binary
    files a system grep reports. If Anthropic changes either configuration,
    this goes red and the claim is re-measured."""
    name, tool, tool_input, allowed, extra_env = next(c for c in AGENTIC_CASES
                                                       if c[0] == "bash-search")
    default = _agentic_side(ctx, name, "native-default", ctx.argv("native"), tool,
                            tool_input, allowed, extra_env)
    problems = []
    db, ob = default["bodies"], optin["bodies"]
    dn, on = (_tool_names(db[0]) if db else None), (_tool_names(ob[0]) if ob else None)
    if dn is None or on is None:
        problems.append("no first request body to compare")
    else:
        if sorted(set(on) - set(dn)) != ["Glob", "Grep"] or set(dn) - set(on):
            problems.append("the opt-in changed the tools by +%s -%s, not by exactly +Glob +Grep"
                            % (sorted(set(on) - set(dn)), sorted(set(dn) - set(on))))
        elif _without_search_tools(db[0]) != _without_search_tools(ob[0]):
            problems.append("the first request bodies differ beyond the Glob and Grep tools")
    dt, ot = _result_text(default), _result_text(optin)
    # the grep half only: find lists ignored/x.txt on every side, as it should
    dg, og = dt.split("grep_rc=")[0], ot.split("grep_rc=")[0]
    if "hay/a.txt:1:NEEDLE-12345" not in dg or "ignored/x.txt" in dg or "bin.dat" in dg:
        problems.append("native's default Bash grep no longer reads like its embedded ugrep "
                        "(.gitignore'd and binary files skipped)")
    if "ignored/x.txt:1:NEEDLE-12345" not in og or "bin.dat" not in og:
        problems.append("native's opt-in Bash grep no longer reads like a system grep")
    if any(m in dt + ot for m in _BUN_AS_GREP):
        problems.append("a native search answered with Bun's help")
    return Result("agentic:search-optin", "FAIL" if problems else "PASS",
                  "native default vs %s: %s" % (
                      NATIVE_SEARCH_OPTIN,
                      "; ".join(problems) or "tools +Glob +Grep and nothing else in the first "
                      "request; Bash grep embedded ugrep vs system grep"),
                  {"tools_default": dn, "tools_optin": on,
                   "grep_default": dt[-2000:], "grep_optin": ot[-2000:]})


# ------------------------------------------------------------------ the TUI

def _seed_repl_config(env, work):
    """An onboarded, trusted, API-key-approved config - and no random motion.

    The mascot's startup entrance is drawn at random from skip/jump/look/spin
    whenever the config records no entrance for this version, so two
    identical runs animate differently (measured: one side mid-jump in the
    ready snapshot). Both switches are Claude's own: lastClawdEntranceVersion
    in the global config says the entrance was seen, and prefersReducedMotion
    - a SETTING, read from settings.json, not from .claude.json - turns the
    animations off."""
    cfg_dir = env["CLAUDE_CONFIG_DIR"]
    json.dump({"hasCompletedOnboarding": True, "theme": "dark", "numStartups": 3,
               "lastClawdEntranceVersion": "999.0.0",
               "customApiKeyResponses": {"approved": [FAKE_KEY[-20:]], "rejected": []},
               "projects": {work: {"hasTrustDialogAccepted": True,
                                   "hasCompletedProjectOnboarding": True}}},
              open(os.path.join(cfg_dir, ".claude.json"), "w"))
    json.dump({"prefersReducedMotion": True}, open(os.path.join(cfg_dir, "settings.json"), "w"))


def _below(plain, styled, marker):
    """The styled rows from the first row whose PLAIN text holds `marker` on:
    style markers may split the words, so the anchor is found without them."""
    rows = (plain or "").split("\n")
    for i, row in enumerate(rows):
        if marker in row:
            return "\n".join((styled or "").split("\n")[i:])
    return None


# Terminal hyperlinks are only emitted when the terminal claims support;
# forcing it on both sides puts OSC 8 - and so the segmenter's link runs -
# on the screen being compared.
TUI_ENV = {"FORCE_HYPERLINK": "1", "COLORTERM": "truecolor"}


def check_tui(ctx):
    results = []
    # 1. onboarding: a fresh config shows the theme picker
    screens = {}
    for side in ("native", "artifact"):
        home = ctx.scratch("tui", "onboarding", side)
        env = ctx.base_env(home, extra=TUI_ENV)
        rc, scr, snaps, tl = pty_session(ctx.session_argv(side), env, home, [
            ("until", "Choose the text style", 30), ("wait", 1.5), ("snap", "picker"),
            ("send", "\x03"), ("wait", 0.5), ("send", "\x03"), ("wait", 1)], total_timeout=60)
        screens[side] = {"rc": rc, "plain": snaps.get("picker", ""),
                         "picker": snaps.get("picker:styled", ""), "found": tl}
    # below the animated logo, whose sparkles are placed at random
    n = _below(screens["native"]["plain"], screens["native"]["picker"], "Let's get started")
    a = _below(screens["artifact"]["plain"], screens["artifact"]["picker"], "Let's get started")
    same_rc = screens["native"]["rc"] == screens["artifact"]["rc"]
    ok = n is not None and n == a and same_rc
    results.append(Result("tui:onboarding", "PASS" if ok else "FAIL",
                          "theme picker %s below the logo (text, styles, links); exit %s/%s" % (
                              "identical" if n is not None and n == a else "DIFFERS",
                              screens["native"]["rc"], screens["artifact"]["rc"]),
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
# chosen at random ("Brewed", "Churned", ...) and the clock is the clock. It is
# recognised on the text with style markers removed, and replaced by its style
# markers alone - so its colour is still compared, its words are not.
_TUI_VOLATILE = re.compile(r"^\s*\S\s+\w+ for [0-9hms ]+(?:· done \d{1,2}:\d{2}(?: [AP]M)?)?\s*$")
_STYLE_MARK = re.compile(r"\{[^}]*\}")


def _tui_normalize(text):
    out = []
    for line in (text or "").split("\n"):
        if _TUI_VOLATILE.match(_STYLE_MARK.sub("", line)):
            line = "<STATUS %s>" % "".join(_STYLE_MARK.findall(line))
        out.append(line)
    return "\n".join(out)


def _tui_repl(ctx, name, reply):
    screens = {}
    for side in ("native", "artifact"):
        home = ctx.scratch("tui", name, side)
        work = os.path.join(home, "work")
        os.makedirs(work)
        mock = Mock(ctx, "tui-%s-%s" % (name, side), tool="none", text=reply)
        try:
            env = ctx.base_env(home, mock.port, TUI_ENV)
            _seed_repl_config(env, work)
            rc, scr, snaps, tl = pty_session(ctx.session_argv(side), env, work, [
                ("until", "for shortcuts", 40), ("wait", 1.0), ("snap", "ready"),
                ("send", "say hi"), ("wait", 0.5), ("send", "\r"),
                ("until", "MOCK-DONE", 40), ("wait", 2.0), ("snap", "answered"),
                ("send", "\x03"), ("wait", 0.5), ("send", "\x03"), ("wait", 2)], total_timeout=120)
            screens[side] = {"rc": rc, "snaps": snaps, "found": tl, "final": scr.text(),
                             "requests": mock.requests(), "alt": scr.alt_active,
                             "cursor_visible": scr.cursor_visible,
                             "bodies": normalize_bodies(mock.post_bodies(), ctx,
                                                        [(work, "<WORK>"), (home, "<HOME>")])}
        finally:
            mock.stop()
    n, a = screens["native"], screens["artifact"]
    ns = _tui_normalize(n["snaps"].get("answered:styled"))
    as_ = _tui_normalize(a["snaps"].get("answered:styled"))
    answered = "MOCK-DONE" in as_
    same = ns == as_
    nr = _tui_normalize(n["snaps"].get("ready:styled"))
    ar = _tui_normalize(a["snaps"].get("ready:styled"))
    ready_same = nr == ar
    clean_exit = a["rc"] == n["rc"] == 0 and a["cursor_visible"] and not a["alt"]
    resume = "--resume" in a["final"]
    bodies = n["bodies"] == a["bodies"] and len(n["bodies"]) > 0
    ok = (answered and same and ready_same and clean_exit and resume and bodies
          and len(a["requests"]) == len(n["requests"]))
    return Result("tui:" + name, "PASS" if ok else "FAIL",
                  "answered=%s screen %s, ready screen %s (text, styles, links), request bodies %s, "
                  "exit rc %s/%s restored=%s resume-hint=%s" % (
                      answered, "identical" if same else "DIFFERS",
                      "identical" if ready_same else "DIFFERS", "equal" if bodies else "DIFFER",
                      n["rc"], a["rc"], clean_exit, resume),
                  {"first_diff": first_diff(ns, as_), "ready_diff": first_diff(nr, ar),
                   "native_answered": n["snaps"].get("answered", "")[-4000:],
                   "artifact_answered": a["snaps"].get("answered", "")[-4000:],
                   "artifact_final": a["final"][-1500:],
                   "requests": [n["requests"], a["requests"]]})


# ------------------------------------------------------------------ Bun.ant

BUNANT_JS = r"""
// The peer is a SEPARATE process (python3), so "the peer's pid" cannot be
// confused with "my own pid" - an implementation that never asks the kernel
// and answers process.pid fails here.
const net = require("net"), os = require("os"), path = require("path"), fs = require("fs"), cp = require("child_process");
const A = Bun.ant, out = {};
function t(k, fn) {
  try { const r = fn(); out[k] = r === null ? null : r === child?.pid ? "CHILD" : r === process.getuid() ? "UID"
    : r === process.pid ? "SELF" : typeof r === "boolean" ? r : "other"; }
  catch (e) { out[k] = "THREW " + e.message; }
}
let child;
t("mem", () => A.memoryPressureLevel());
t("dump_true", () => A.setDumpable(true));
t("dump_false", () => A.setDumpable(false));
for (const bad of [-1, 999, Infinity, 2 ** 32]) t("peerPid:" + bad, () => A.getPeerPid(bad));
const sock = path.join(os.tmpdir(), "nrc-h-" + process.pid + ".sock");
const srv = net.createServer((c) => {
  const fd = c._handle.fd;
  const cases = { num: fd, str: String(fd), float: fd + 0.7, obj: { valueOf() { return fd; } }, arr: [fd],
    none: undefined, nul: null, junk: fd + "x", neg_float: -0.5, bool: true };
  for (const [k, v] of Object.entries(cases)) {
    t("getPeerPid:" + k, () => A.getPeerPid(v));
    t("getPeerUid:" + k, () => A.getPeerUid(v));
  }
  c.destroy(); srv.close(); child.kill(); try { fs.unlinkSync(sock); } catch {}
  console.log(JSON.stringify(out)); process.exit(0);
});
srv.listen(sock, () => {
  child = cp.spawn("/usr/bin/python3", ["-c", "import socket,sys,time; s=socket.socket(socket.AF_UNIX); s.connect(sys.argv[1]); time.sleep(10)", sock], { stdio: "ignore" });
});
setTimeout(() => { console.log(JSON.stringify(out)); process.exit(1); }, 8000);
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
    real_peer = a.get("getPeerPid:num") == "CHILD"
    ok = not diff and real_peer
    return [Result("bunant", "PASS" if ok else "FAIL",
                   "%d probes, %d differ%s" % (len(a), len(diff),
                                               "" if real_peer else "; the native side never saw the peer"),
                   {"differ": diff, "native": a})]


def check_segmenter(ctx):
    fuzz = os.path.join(REPO, "tests", "cell_segmenter_fuzz.cjs")
    oracle = os.path.join(REPO, "tests", "cell_segmenter_oracle.cjs")
    polyfill = os.path.join(ctx.extract_dir, "bun-ant.mjs")
    if not (os.path.isfile(fuzz) and os.path.isfile(oracle)):
        return [Result("segmenter", "SKIP", "fuzz corpus generator not present yet")]
    if not os.path.isfile(polyfill):
        return [Result("segmenter", "SKIP", "legacy artifact: no Bun.ant polyfill")]
    d = ctx.scratch("segmenter")
    cases = os.path.join(d, "cases.json")
    # Case i is generated from index i alone, so a seed is a disjoint window
    # of indices and any failing case can be regenerated by its index.
    start = ctx.args.fuzz_seed * 10_000_000
    r = run([ctx.bun, fuzz, str(start), str(ctx.args.fuzz_cases), cases],
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
    # split on "\n" only: JSON.stringify leaves U+2028/2029 raw, and
    # str.splitlines() would break a case in two there (and on \x1c-\x1e, \x85)
    def lines(path):
        return [l for l in open(path, encoding="utf-8").read().split("\n") if l]
    nl, al = lines(nat_out), lines(art_out)
    ncases = len(json.load(open(cases)))
    if not (ncases == ctx.args.fuzz_cases == len(nl) == len(al)) or ncases == 0:
        return [Result("segmenter", "FAIL", "case count mismatch: generated %d of %d, native answered "
                       "%d, port %d" % (ncases, ctx.args.fuzz_cases, len(nl), len(al)),
                       {"native": nat["stderr"][-800:], "artifact": art["stderr"][-800:]})]
    bad = [i for i in range(ncases) if nl[i] != al[i]]
    detail = {}
    if bad:
        i = bad[0]
        detail = {"first_case_index": start + i,
                  "first_case": json.load(open(cases))[i],
                  "native": nl[i][:3000] if i < len(nl) else None,
                  "artifact": al[i][:3000] if i < len(al) else None}
    return [Result("segmenter", "FAIL" if bad else "PASS",
                   "%d random cases (indices %d..%d), %d mismatch native" % (
                       len(nl), start, start + len(nl) - 1, len(bad)),
                   detail)]


def check_pytest(ctx):
    env = dict(os.environ, BUN_BIN=ctx.bun)
    # the real-binary tests use the binary this harness was pointed at
    env["NRC_TEST_ESM" if _manifest(ctx) else "NRC_TEST_ELF"] = ctx.native
    # The Node tests drive a LEGACY artifact (cli.original.cjs); hand them the
    # one just built only if that is what it is.
    legacy = os.path.join(ctx.extract_dir, "cli.original.cjs") if ctx.artifact else ""
    if legacy and os.path.isfile(legacy):
        env["NRC_TEST_ARTIFACT"] = legacy
    r = run([sys.executable, "-m", "pytest", os.path.join(REPO, "tests"), "-q", "-p", "no:cacheprovider"],
            env, cwd=REPO, timeout=1800)
    tail = r["stdout"].strip().splitlines()[-1:] or [""]
    return [Result("pytest", "PASS" if r["rc"] == 0 else "FAIL", tail[0],
                   {"failures": [l for l in r["stdout"].splitlines() if l.startswith("FAILED")][:30]})]


def check_provenance(ctx):
    """What was compared: the native version, and whether the artifact's
    polyfill is the one in scripts/ - an artifact built before the last edit
    to it would otherwise be judged under a name it no longer earns."""
    r = run([ctx.native, "--version"], ctx.base_env(ctx.scratch("provenance")), timeout=60)
    native_version = r["stdout"].strip()
    stale = []
    if _manifest(ctx):
        for name in ("bun-ant.mjs", "bun-ant-cell-segmenter.mjs"):
            a, b = os.path.join(ctx.extract_dir, name), os.path.join(HERE, name)
            if not (os.path.isfile(a) and open(a, "rb").read() == open(b, "rb").read()):
                stale.append(name)
    return [Result("provenance", "FAIL" if stale else "PASS",
                   "native %s; artifact %s%s" % (native_version or "(no version)", ctx.artifact,
                                                 "; STALE copies of " + ", ".join(stale) if stale else
                                                 "; polyfill identical to scripts/"),
                   {"native_version": native_version, "stale": stale})]


CHECKS = {
    "build": check_build, "structure": check_structure, "parse": check_parse,
    "text": check_text, "cli": check_cli, "agentic": check_agentic, "tui": check_tui,
    "bunant": check_bunant, "segmenter": check_segmenter, "pytest": check_pytest,
    "provenance": check_provenance,
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
    wanted = args.only.split(",") if args.only else list(GROUPS)
    unknown = [g for g in wanted if g not in CHECKS]
    if unknown:
        print("unknown group(s) %s (see --list)" % ", ".join(unknown), file=sys.stderr)
        return 2
    # canonical order whatever the spelling: `--only cli,build` must still build first
    groups = [g for g in GROUPS if g in wanted]
    if args.skip:
        groups = [g for g in groups if g not in args.skip.split(",")]
    if "build" not in groups and not args.artifact:
        default = os.path.join(args.out, "build", "extract", "cli.js")
        if os.path.isfile(default):
            args.artifact = default
    ctx = Ctx(args)
    os.makedirs(ctx.out, exist_ok=True)
    for g in groups:
        t0 = time.time()
        if g != "build" and not ctx.artifact:
            # a requested check that could not run is not a pass
            res = [Result(g, "FAIL", "not run: no artifact (the build failed, or pass --artifact)")]
        else:
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
