#!/usr/bin/env python3
"""memsoak.py - does a long REPL session grow without bound, native vs artifact?

Runs the same interactive session through the native binary and the
artifact, side by side, under a pty and against scripts/mock-messages-api.mjs
on loopback. Every turn is a Bash tool call followed by a markdown answer
streamed in pieces, which is the shape of real work. Between turns it
samples:

  - from outside: RSS of the Claude process and of its whole process tree
  - from inside (scripts/memprobe.cjs, preloaded through BUN_OPTIONS, which
    the native binary honours too): RSS, and the JSC heap after a forced
    full GC - what is retained - with a live count per object type

and at the end prints, per side, the growth per turn after warm-up and the
object types that grew the most. The question it answers is not "does memory
go up" (a transcript is legitimately kept in memory) but "does the artifact
grow faster than native under the same workload, and in what".

    scripts/memsoak.py --artifact build/extract/cli.js --turns 150
    scripts/memsoak.py --artifact build/extract/cli.js --bun ~/.bun-1.4.0/bun --sides artifact
    scripts/memsoak.py --turns 3 --hold 500     # three turns, each Bash step 500 s
    scripts/memsoak.py --mode workflow          # each turn runs a workflow of four agents
    scripts/memsoak.py --tls --reply-repeat 40 --deltas 1500 --delta-ms 20
                                                # 30 s of streaming per turn, over TLS
    scripts/memsoak.py --sides artifact,artifact@~/.bun-1.4.0/bun   # one artifact, two Buns

It runs on macOS too (ps(1) stands in for /proc; memprobe.cjs adds the
phys_footprint Activity Monitor shows): pass --native with the Mac's own
claude and --bun with its Bun.

--hold makes every Bash step a `sleep`, so the session spends its time INSIDE
a turn, spinner and timer animating, which is where a long working session
spends most of its time. Growth is reported per turn and per minute.

Same safety as scripts/harness.py: a throwaway HOME, only the environment
set here, DISABLE_AUTOUPDATER=1, and ANTHROPIC_BASE_URL on loopback.
"""

import argparse
import fcntl
import json
import os
import select
import struct
import subprocess
import sys
import termios
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import harness  # noqa: E402
import vtscreen  # noqa: E402

REPLY = (
    "SOAK-DONE-{turn}\n\n"
    "Here is what the command printed, summarised: **300 lines**, the last one `SOAK`.\n\n"
    "| step | result | note |\n|---|---|---|\n"
    "| seq | ok | 中文 wide text |\n| echo | ok | an emoji \U0001F600 |\n\n"
    "```python\nfor i in range(3):\n    print(i)  # a code block\n```\n\n"
    "A [link](https://example.com/soak?turn={turn}) and a paragraph long enough to wrap: "
    + " ".join("word%d" % i for i in range(60)) + "\n")
TOOL_INPUT = {"command": "seq 1 300; echo SOAK", "description": "soak step"}
# --reply-repeat N appends this N times, for answers that stream for a while
PARAGRAPH = ("- A list item with `code`, **bold** text and \u4e2d\u6587, then prose to wrap: "
             + " ".join("token%d" % i for i in range(40)) + "\n")
# --mode agent / workflow: the main turn starts subagents, which run in the
# same process; the mock recognises a subagent's transcript by this marker and
# has it run the Bash step before it answers.
SUB_MARKER = "SOAK-SUBAGENT"
AGENT_INPUT = {"description": "soak step", "prompt": SUB_MARKER + ": run the step",
               "subagent_type": "general-purpose"}
WORKFLOW_INPUT = {"script": (
    "export const meta = { name: 'soak', description: 'soak step' }\n"
    "const r = await parallel([1, 2, 3, 4].map(i => () => agent('%s: run the step ' + i)))\n"
    "return r.length\n" % SUB_MARKER)}


def _ps_table():
    """{pid: (ppid, rss bytes)} from ps(1), where there is no /proc (macOS)."""
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,rss="], capture_output=True, text=True).stdout
    table = {}
    for line in out.splitlines():
        f = line.split()
        if len(f) == 3 and all(x.isdigit() for x in f):
            table[int(f[0])] = (int(f[1]), int(f[2]) * 1024)
    return table


def tree_rss(pid):
    """RSS in bytes of pid and every descendant, from /proc (ps(1) without it)."""
    if not os.path.isdir("/proc/self"):
        table = _ps_table()
        total, stack = 0, [pid]
        while stack:
            p = stack.pop()
            total += table.get(p, (0, 0))[1]
            stack.extend(c for c, (pp, _) in table.items() if pp == p)
        return total
    total, stack, seen = 0, [pid], set()
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        try:
            with open("/proc/%d/status" % p) as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        total += int(line.split()[1]) * 1024
            for tid in os.listdir("/proc/%d/task" % p):
                with open("/proc/%d/task/%s/children" % (p, tid)) as fh:
                    stack.extend(int(c) for c in fh.read().split())
        except (OSError, ValueError):
            continue
    return total


def own_rss(pid):
    if not os.path.isdir("/proc/self"):
        return _ps_table().get(pid, (0, 0))[1]
    try:
        with open("/proc/%d/status" % pid) as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


class Side:
    def __init__(self, ctx, name, argv, args):
        self.name, self.argv, self.args = name, argv, args
        self.dir = ctx.scratch("memsoak", name)
        self.home = os.path.join(self.dir, "home")
        self.work = os.path.join(self.home, "work")
        os.makedirs(self.work)
        self.probe = os.path.join(self.dir, "probe.jsonl")
        self.rss_log = os.path.join(self.dir, "rss.jsonl")
        self.ctx = ctx
        self.turns_done = 0
        self.error = None
        self.rc = None

    def tls_files(self):
        """A throwaway self-signed certificate for 127.0.0.1, trusted by the
        client through NODE_EXTRA_CA_CERTS (Bun and Claude both honour it)."""
        cert, key = os.path.join(self.dir, "mock.crt"), os.path.join(self.dir, "mock.key")
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
                        "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
                        "-keyout", key, "-out", cert], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return cert, key

    def env(self, port):
        extra = dict(harness.TUI_ENV)
        if self.args.tls:
            extra["ANTHROPIC_BASE_URL"] = "https://127.0.0.1:%d" % port
            extra["NODE_EXTRA_CA_CERTS"] = self.cert
        extra["BUN_OPTIONS"] = "--preload " + os.path.join(HERE, "memprobe.cjs")
        extra["NRC_MEMPROBE_OUT"] = self.probe
        extra["NRC_MEMPROBE_MS"] = str(int(self.args.probe_secs * 1000))
        env = self.ctx.base_env(self.home, port, extra)
        harness._seed_repl_config(env, self.work)
        settings = {"permissions": {"allow": ["Bash", "Agent", "Workflow"]}}
        if self.args.reduced_motion:
            settings["prefersReducedMotion"] = True
        with open(os.path.join(env["CLAUDE_CONFIG_DIR"], "settings.json"), "w") as fh:
            json.dump(settings, fh)
        return env

    def run(self):
        try:
            self._run()
        except Exception as e:  # reported, never swallowed
            import traceback
            self.error = "%s\n%s" % (e, traceback.format_exc())

    def _run(self):
        a = self.args
        tool_input = dict(TOOL_INPUT)
        if a.hold:
            tool_input["command"] = "sleep %d; seq 1 300; echo SOAK" % a.hold
            # the Bash tool's own ceiling is 10 minutes; chain turns for longer
            tool_input["timeout"] = min((a.hold + 60) * 1000, 600000)
        extra = ["--every-turn", "1", "--deltas", str(a.deltas), "--delta-ms", str(a.delta_ms)]
        if a.tls:
            self.cert, key = self.tls_files()
            extra += ["--tls-cert", self.cert, "--tls-key", key]
        tool = "bash"
        if a.mode != "bash":
            tool, main_input = {"agent": ("Agent", AGENT_INPUT),
                                "workflow": ("Workflow", WORKFLOW_INPUT)}[a.mode]
            extra += ["--sub-marker", SUB_MARKER, "--sub-tool", "bash",
                      "--sub-tool-input", json.dumps(tool_input)]
            tool_input = main_input
        reply = REPLY + ("\n" + PARAGRAPH) * a.reply_repeat
        mock = harness.Mock(self.ctx, "memsoak-" + self.name, tool=tool, tool_input=tool_input,
                            text=reply, extra_argv=extra)
        try:
            self._session(self.env(mock.port))
        finally:
            mock.stop()

    def _session(self, env):
        a = self.args
        rows, cols = 40, 120
        master, slave = os.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        proc = subprocess.Popen(self.argv, stdin=slave, stdout=slave, stderr=slave, env=env,
                                cwd=self.work, start_new_session=True, close_fds=True)
        os.close(slave)
        scr = vtscreen.Screen(rows, cols)
        rss_fh = open(self.rss_log, "w")
        t0 = time.time()
        last_sample = [0.0]
        deadline = t0 + a.minutes * 60

        def sample(force=False):
            now = time.time()
            if force or now - last_sample[0] >= a.probe_secs:
                last_sample[0] = now
                rss_fh.write(json.dumps({"t": round(now - t0, 2), "turn": self.turns_done,
                                         "rss": own_rss(proc.pid), "tree": tree_rss(proc.pid)}) + "\n")
                rss_fh.flush()

        def pump(secs):
            end = time.time() + secs
            while time.time() < end:
                sample()
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

        def until(text, secs):
            end = time.time() + secs
            while time.time() < end:
                if text in scr.text():
                    return True
                if not pump(0.1):
                    return text in scr.text()
            return False

        try:
            if not until("for shortcuts", 60):
                raise RuntimeError("REPL never became ready:\n" + scr.text()[-2000:])
            pump(2.0)
            for k in range(1, a.turns + 1):
                if time.time() > deadline:
                    break
                os.write(master, ("turn %d: run the step" % k).encode())
                pump(0.3)
                os.write(master, b"\r")
                if not until("SOAK-DONE-%d" % k, 90 + a.hold):
                    raise RuntimeError("turn %d never answered:\n%s" % (k, scr.text()[-3000:]))
                self.turns_done = k
                pump(a.gap)
            sample(force=True)
            pump(a.idle_tail)
            sample(force=True)
        finally:
            if proc.poll() is None:
                os.write(master, b"\x03")
                pump(0.5)
                os.write(master, b"\x03")
                pump(3.0)
            if proc.poll() is None:
                proc.kill()
            self.rc = proc.wait()
            rss_fh.close()
            try:
                os.close(master)
            except OSError:
                pass


def load(path):
    rows = []
    if os.path.exists(path):
        for line in open(path):
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    return rows


def slope(xs, ys):
    n = len(xs)
    if n < 2:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den if den else 0.0


def summarize(side, warm):
    rss = load(side.rss_log)
    probe = load(side.probe)
    turns = side.turns_done
    out = {"side": side.name, "turns": turns, "rc": side.rc, "error": side.error,
           "samples": len(probe)}
    if not rss or not probe:
        return out
    # map probe samples (their own clock, started at process start) onto turns
    # through the outside log's clock: both are seconds since roughly the same start
    def turn_at(t):
        best = 0
        for r in rss:
            if r["t"] <= t:
                best = r["turn"]
        return best
    for p in probe:
        p["turn"] = turn_at(p["t"] / 1000.0)
    after = [p for p in probe if p["turn"] >= warm]
    ra = [r for r in rss if r["turn"] >= warm]
    mb = 1024 * 1024
    out.update({
        "rss_first_mb": round(rss[0]["rss"] / mb, 1), "rss_last_mb": round(rss[-1]["rss"] / mb, 1),
        "tree_last_mb": round(rss[-1]["tree"] / mb, 1),
        "heap_first_mb": round(probe[0]["heapSize"] / mb, 1),
        "heap_last_mb": round(probe[-1]["heapSize"] / mb, 1),
        "extra_last_mb": round(probe[-1]["extra"] / mb, 1),
        "objects_last": probe[-1]["objects"],
    })
    if len(after) >= 2:
        xs = [p["turn"] for p in after]
        out["heap_kb_per_turn"] = round(slope(xs, [p["heapSize"] for p in after]) / 1024, 1)
        out["extra_kb_per_turn"] = round(slope(xs, [p["extra"] for p in after]) / 1024, 1)
        out["rssgc_kb_per_turn"] = round(slope(xs, [p["rssGc"] for p in after]) / 1024, 1)
        out["objects_per_turn"] = round(slope(xs, [p["objects"] for p in after]), 1)
        first, last = after[0], after[-1]
        dturn = max(1, last["turn"] - first["turn"])
        grew = []
        for k, v in last["types"].items():
            d = v - first["types"].get(k, 0)
            if d > 0:
                grew.append((d, k))
        grew.sort(reverse=True)
        out["types_per_turn"] = [[k, round(d / dturn, 1)] for d, k in grew[:15]]
    # per minute, over the samples after the first minute
    late = [p for p in probe if p["t"] >= 60000]
    if len(late) >= 2:
        xs = [p["t"] / 60000.0 for p in late]
        out["heap_mb_per_min"] = round(slope(xs, [p["heapSize"] for p in late]) / mb, 2)
        out["rss_mb_per_min"] = round(slope(xs, [p["rss"] for p in late]) / mb, 2)
        out["rssgc_mb_per_min"] = round(slope(xs, [p["rssGc"] for p in late]) / mb, 2)
        out["objects_per_min"] = round(slope(xs, [p["objects"] for p in late]), 1)
        if all(p.get("footprint") for p in late):
            out["footprint_mb_per_min"] = round(slope(xs, [p["footprint"] for p in late]) / mb, 2)
            out["footprint_last_mb"] = round(late[-1]["footprint"] / mb, 1)
        if all(p.get("malloc") for p in late):
            out["mimalloc_commit_mb_per_min"] = round(
                slope(xs, [p["malloc"]["commit"] for p in late]) / mb, 2)
        first, last = late[0], late[-1]
        mins = max(1e-9, (last["t"] - first["t"]) / 60000.0)
        grew = sorted(((v - first["types"].get(k, 0), k) for k, v in last["types"].items()), reverse=True)
        out["types_per_min"] = [[k, round(d / mins, 1)] for d, k in grew[:15] if d > 0]
    if len(ra) >= 2:
        out["rss_kb_per_turn"] = round(slope([r["turn"] for r in ra], [r["rss"] for r in ra]) / 1024, 1)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--native", default="/usr/bin/claude")
    ap.add_argument("--artifact", default=os.path.join(harness.REPO, "build", "extract", "cli.js"))
    ap.add_argument("--bun", default="~/.bun-1.3.14/bun")
    ap.add_argument("--out", default=os.path.join(harness.REPO, "build", "memsoak"))
    ap.add_argument("--sides", default="native,artifact",
                    help="comma-separated: native, artifact, artifact@<another bun>")
    ap.add_argument("--turns", type=int, default=100)
    ap.add_argument("--minutes", type=float, default=30.0, help="stop starting new turns after this")
    ap.add_argument("--warm", type=int, default=10, help="turns ignored before fitting growth")
    ap.add_argument("--gap", type=float, default=1.0, help="seconds between turns")
    ap.add_argument("--idle-tail", type=float, default=15.0, help="seconds idle after the last turn")
    ap.add_argument("--deltas", type=int, default=40)
    ap.add_argument("--delta-ms", type=int, default=25)
    ap.add_argument("--probe-secs", type=float, default=5.0)
    ap.add_argument("--reduced-motion", action="store_true")
    ap.add_argument("--hold", type=int, default=0, help="seconds each Bash step sleeps (max 540)")
    ap.add_argument("--tls", action="store_true", help="serve the mock over HTTPS")
    ap.add_argument("--reply-repeat", type=int, default=0, help="append N list paragraphs to each answer")
    ap.add_argument("--mode", choices=("bash", "agent", "workflow"), default="bash",
                    help="each turn runs Bash itself, starts one subagent, or runs a workflow of four")
    args = ap.parse_args()
    args.artifact = os.path.abspath(args.artifact)
    if not 0 <= args.hold <= 540:
        ap.error("--hold must be 0..540: the Bash tool's timeout ceiling is 600 s")
    ctx = harness.Ctx(argparse.Namespace(native=args.native, bun=args.bun, out=args.out,
                                         artifact=args.artifact))
    os.makedirs(ctx.out, exist_ok=True)
    sides = []
    for name in args.sides.split(","):
        if name == "native":
            argv = ctx.session_argv("native")
        elif name == "artifact":
            argv = ctx.session_argv("artifact")
        elif name.startswith("artifact@"):
            # the same artifact under another Bun: artifact@/path/to/bun
            bun = os.path.abspath(os.path.expanduser(name.split("@", 1)[1]))
            ver = subprocess.run([bun, "--version"], capture_output=True, text=True).stdout.strip()
            name = "artifact-bun-" + (ver or "unknown")
            argv = [bun, "--no-install", ctx.artifact]
        else:
            ap.error("--sides takes native, artifact and artifact@<bun path>, got %r" % name)
        sides.append(Side(ctx, name, argv, args))
    threads = [threading.Thread(target=s.run) for s in sides]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    report = {"bun": ctx.bun, "native": ctx.native, "artifact": ctx.artifact,
              "args": vars(args), "sides": [summarize(s, args.warm) for s in sides]}
    with open(os.path.join(ctx.out, "summary.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps(report["sides"], indent=1))
    return 1 if any(s.error for s in sides) else 0


if __name__ == "__main__":
    sys.exit(main())
