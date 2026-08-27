"""The diagnostic preload, scripts/node-trace.cjs.

It exists because the artifact hangs before painting anything under Node on
macOS, and this Linux host cannot reproduce that. The instrument is the only
thing that crosses the gap, so it has to hold two properties that are easy to
claim and easy to get wrong:

  - it does not change what it measures. A trace that perturbs startup would
    make every conclusion drawn from it worthless, and an instrument that keeps
    a finished process alive would manufacture the hang it went looking for.
  - a call that never returns leaves a "before" line with no "after", and that
    line survives the kill. The whole protocol rests on the last line written
    before the silence, so it is tested against a call that really does block
    and a process that really is killed - not against a mock.

Needs a Node >= 24; the byte-identical comparison also needs a built artifact.
See tests/conftest.py.
"""

import os
import pathlib
import re
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
TRACE = ROOT / "scripts" / "node-trace.cjs"
SHIM = ROOT / "scripts" / "bun-shim.cjs"

TIMEOUT = 300


def _env(home, log=None, node_path=None, **extra):
    """A run that cannot touch the developer's real config or network."""
    home.mkdir(parents=True, exist_ok=True)
    cfg = home / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "CLAUDE_CONFIG_DIR": str(cfg),
        "DISABLE_AUTOUPDATER": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "TERM": "xterm-256color",
    }
    if log is not None:
        env["NRC_TRACE"] = str(log)
    if node_path:
        env["NODE_PATH"] = node_path
    env.update(extra)
    return env


def test_the_trace_does_not_change_what_the_run_produces(node_env, tmp_path):
    """Same stdout, same stderr, same exit code with the instrument attached.

    This is the property every conclusion drawn from a trace depends on. It is
    checked against the artifact rather than a toy script because the artifact
    is what the instrument is for.
    """
    runs = {}
    for name, preloads in (
        ("plain", ["--require", str(SHIM)]),
        ("traced", ["--require", str(TRACE), "--require", str(SHIM)]),
    ):
        home = tmp_path / name
        log = tmp_path / f"{name}.log"
        runs[name] = subprocess.run(
            [node_env["node"], *preloads, node_env["artifact"], "mcp", "list"],
            env=_env(home, log=log, node_path=node_env["modules"]),
            capture_output=True, timeout=TIMEOUT)

    plain, traced = runs["plain"], runs["traced"]
    assert traced.returncode == plain.returncode, (
        f"exit {traced.returncode} with the trace, {plain.returncode} without; "
        f"traced stderr:\n{traced.stderr.decode('utf-8', 'replace')[-2000:]}")
    assert traced.stdout == plain.stdout, (
        "the trace changed stdout - it must never write to the streams it "
        f"watches: {plain.stdout!r} -> {traced.stdout!r}")
    assert traced.stderr == plain.stderr, (
        f"the trace leaked into stderr: {plain.stderr!r} -> {traced.stderr!r}")


def test_the_trace_records_the_startup_facts_a_hang_report_needs(node_env, tmp_path):
    """The log has to answer the questions asked of a machine we cannot reach."""
    log = tmp_path / "trace.log"
    subprocess.run(
        [node_env["node"], "--require", str(TRACE), "--require", str(SHIM),
         node_env["artifact"], "mcp", "list"],
        env=_env(tmp_path / "home", log=log, node_path=node_env["modules"]),
        capture_output=True, timeout=TIMEOUT)

    text = log.read_text()
    # Which runtime, on which machine - a trace mailed in from another host is
    # useless without this line.
    assert "preload start" in text and "platform=" in text and "node=v" in text
    # "nothing is drawn" has two very different causes, and these three lines
    # are what separate a renderer that never started from one that started
    # without knowing the terminal size.
    for stream in ("stdin", "stdout", "stderr"):
        assert f"{stream} isTTY=" in text, f"no isTTY line for {stream}:\n{text[:600]}"
    assert "columns=" in text and "rows=" in text
    assert "TERM=" in text
    assert "exit code=0" in text, "the run finished but the trace never said so"


def test_a_call_that_never_returns_is_the_last_line_in_the_log(node_bin, tmp_path):
    """The instrument's whole diagnostic claim, tested against a real block.

    A child that blocks for far longer than we wait, killed the way the user
    will kill theirs. The "before" line must be on disk - fs.writeSync, not a
    buffered stream - and the "after" line must be absent.
    """
    log = tmp_path / "blocked.log"
    script = tmp_path / "blocker.js"
    script.write_text("require('child_process').execSync('sleep 120')\n")

    proc = subprocess.Popen(
        [node_bin, "--require", str(TRACE), str(script)],
        env=_env(tmp_path / "home", log=log),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        proc.wait(timeout=8)
        pytest.fail("the blocker returned; it was supposed to still be blocked")
    except subprocess.TimeoutExpired:
        pass
    finally:
        proc.kill()
        proc.wait(timeout=30)

    lines = [ln for ln in log.read_text().splitlines() if ln.strip()]
    assert any("> execSync" in ln for ln in lines), (
        "the blocking call was never announced:\n" + "\n".join(lines[-10:]))
    assert not any("< execSync" in ln for ln in lines), (
        "the log claims a call returned that never did:\n" + "\n".join(lines[-10:]))
    assert "> execSync" in lines[-1], (
        "the blocking call must be the LAST line in the log - that is what makes "
        "a trace readable without knowing what to look for; last line was:\n"
        + lines[-1])
    # The heartbeat has to stop when the main thread does - a ticking log next
    # to a blocked process would say the opposite of the truth.
    ticks_after = [ln for ln in lines[lines.index(
        next(ln for ln in lines if "> execSync" in ln)):] if "tick " in ln]
    assert not ticks_after, (
        "the heartbeat kept ticking while the main thread was blocked: "
        f"{ticks_after[:3]}")


def test_the_heartbeat_cannot_keep_a_finished_process_alive(node_bin, tmp_path):
    """An instrument that hangs the process is worse than no instrument.

    setInterval holds the event loop open unless it is unref'd. Without the
    unref this exits never; with it, immediately.
    """
    log = tmp_path / "exit.log"
    proc = subprocess.run(
        [node_bin, "--require", str(TRACE), "-e", "0"],
        env=_env(tmp_path / "home", log=log),
        capture_output=True, timeout=20)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert "preload end" in log.read_text()


def test_the_trace_writes_nowhere_near_the_streams_it_watches(node_bin, tmp_path):
    """stdout belongs to the TUI; a chatty instrument would corrupt the frame."""
    log = tmp_path / "quiet.log"
    proc = subprocess.run(
        [node_bin, "--require", str(TRACE), "-e", "process.stdout.write('X')"],
        env=_env(tmp_path / "home", log=log),
        capture_output=True, timeout=20)

    assert proc.stdout == b"X", f"instrument added output: {proc.stdout!r}"
    assert proc.stderr == b"", f"instrument added stderr: {proc.stderr!r}"
    # and it still saw the write it stayed quiet about
    assert "write#1 stdout bytes=1" in log.read_text()


def test_an_unwritable_log_does_not_take_the_run_down(node_bin, tmp_path):
    """A diagnostic must never become the reason the thing fails to start."""
    proc = subprocess.run(
        [node_bin, "--require", str(TRACE), "-e", "process.stdout.write('ok')"],
        env=_env(tmp_path / "home", log=tmp_path / "no" / "such" / "dir" / "t.log"),
        capture_output=True, timeout=20)

    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")
    assert proc.stdout == b"ok"


def test_a_child_that_never_exits_is_visible_as_one_with_no_exit_line(node_bin, tmp_path):
    """The async spawn family needs following to its exit, not just its call.

    `< spawn` says the CALL returned - it says nothing about the child. A hook
    or a scan that hangs would otherwise leave no trace at all, which is the
    exact failure this instrument was built to catch.
    """
    log = tmp_path / "children.log"
    script = tmp_path / "spawner.js"
    script.write_text(
        "const cp = require('child_process');\n"
        "cp.spawn('sleep', ['0.2']);\n"
        "cp.spawn('sleep', ['600']);\n")

    proc = subprocess.Popen([node_bin, "--require", str(TRACE), str(script)],
                            env=_env(tmp_path / "home", log=log),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        proc.wait(timeout=6)
        pytest.fail("the spawner exited; the long child was supposed to hold it")
    except subprocess.TimeoutExpired:
        pass
    finally:
        proc.kill()
        proc.wait(timeout=30)

    text = log.read_text()
    pids = dict(re.findall(r"< spawn pid=(\d+)", text) and
                [(m[1], m[0]) for m in re.findall(r"> spawn sleep (\S+)\n.*?< spawn pid=(\d+)",
                                                  text, re.S)])
    assert len(pids) == 2, f"expected two spawns, parsed {pids}:\n{text}"
    short_pid = next(p for p, arg in pids.items() if arg == "0.2")
    long_pid = next(p for p, arg in pids.items() if arg == "600")

    assert f"child pid={short_pid} exited" in text, (
        "the short child's exit was never recorded:\n" + text)
    assert f"child pid={long_pid} exited" not in text, (
        "the log claims the hung child exited; it did not:\n" + text)


def test_a_refused_bun_api_names_itself_even_when_something_swallows_it(node_bin, tmp_path):
    """The failure mode no other probe in this file can see.

    A shim that refuses an api throws; a throw inside a React render is caught
    by an error boundary. Nothing paints, nothing is logged, and the process
    idles looking healthy. The trace has to name the api anyway - so it watches
    the object the shim installs, and records the throw before the catch.
    """
    log = tmp_path / "bun.log"
    fake_shim = tmp_path / "fake-shim.cjs"
    fake_shim.write_text(
        "Object.defineProperty(globalThis, 'Bun', { value: {\n"
        "  stringWidth: (s) => s.length,\n"
        "  YAML: { parse: () => { throw new Error('YAML is not implemented'); } },\n"
        "}, writable: true, configurable: true });\n")
    script = tmp_path / "user.js"
    script.write_text(
        "globalThis.Bun.stringWidth('ab');\n"
        "try { globalThis.Bun.YAML.parse('x: 1'); } catch (e) { /* swallowed */ }\n")

    proc = subprocess.run(
        [node_bin, "--require", str(TRACE), "--require", str(fake_shim), str(script)],
        env=_env(tmp_path / "home", log=log), capture_output=True, timeout=30)
    assert proc.returncode == 0, proc.stderr.decode("utf-8", "replace")

    text = log.read_text()
    assert "shim installed globalThis.Bun" in text
    assert "Bun.stringWidth read" in text
    # the throw is the point: the script caught it, the log must still have it
    assert "!! Bun.YAML.parse THREW" in text, (
        "a swallowed refusal left no trace:\n" + text)
    assert "YAML is not implemented" in text
    assert "Bun surface touched:" in text and "YAML.parse" in text
