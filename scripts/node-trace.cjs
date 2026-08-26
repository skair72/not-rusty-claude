// not-rusty-claude: a diagnostic preload for running the extracted CLI under Node.
//
// Load it BEFORE the Bun shim so it observes the shim too:
//
//   node --require .../scripts/node-trace.cjs \
//        --require .../scripts/bun-shim.cjs \
//        build/extract/cli.original.cjs
//
// It writes to NRC_TRACE (default /tmp/nrc-node-trace.log), never to stdout or
// stderr, because stdout belongs to the TUI. Every wrapped call logs both before
// and after, so a line with no matching "<" is the call that never returned.
// A 500 ms heartbeat runs alongside: if the ticks stop, the main thread is
// blocked; if they continue, the process is idle and waiting on something.
//
// The instrument must not change what it measures. The heartbeat timer is
// unref'd so it cannot keep an otherwise-finished process alive, and every
// wrapper forwards its return value and rethrows unchanged.

'use strict';

const fs = require('fs');
const path = require('path');

const LOG_PATH = process.env.NRC_TRACE || '/tmp/nrc-node-trace.log';
const PREVIEW_WRITES = Number(process.env.NRC_TRACE_WRITES || 40);
const HEARTBEAT_MS = Number(process.env.NRC_TRACE_HEARTBEAT_MS || 500);

let fd;
try {
  fd = fs.openSync(LOG_PATH, 'w');
} catch (e) {
  // A trace we cannot write is not worth crashing the run over.
  fd = -1;
}

const t0 = process.hrtime.bigint();

function ms() {
  return Number((process.hrtime.bigint() - t0) / 1000n) / 1000;
}

// fs.writeSync, not a stream: buffered lines vanish when the process is killed,
// and the last line before the silence is the whole answer.
function log(line) {
  if (fd < 0) return;
  try {
    fs.writeSync(fd, ms().toFixed(3).padStart(10) + 'ms ' + line + '\n');
  } catch (e) {
    /* a full disk must not become a second bug */
  }
}

function show(v, max) {
  const limit = max || 200;
  let s;
  try {
    s = typeof v === 'string' ? v : String(v);
  } catch (e) {
    return '<unstringifiable>';
  }
  s = s.replace(/[\x00-\x1f\x7f]/g, (c) => {
    if (c === '\n') return '\\n';
    if (c === '\r') return '\\r';
    if (c === '\t') return '\\t';
    if (c === '\x1b') return '\\e';
    return '\\x' + c.charCodeAt(0).toString(16).padStart(2, '0');
  });
  return s.length > limit ? s.slice(0, limit) + '…' : s;
}

function handleSummary() {
  try {
    const handles =
      typeof process._getActiveHandles === 'function' ? process._getActiveHandles() : [];
    const requests =
      typeof process._getActiveRequests === 'function' ? process._getActiveRequests() : [];
    const counts = new Map();
    for (const h of handles) {
      let name;
      try {
        name = (h && h.constructor && h.constructor.name) || typeof h;
      } catch (e) {
        name = '<throws>';
      }
      counts.set(name, (counts.get(name) || 0) + 1);
    }
    const parts = [...counts]
      .sort((a, b) => (a[0] < b[0] ? -1 : 1))
      .map(([n, c]) => (c > 1 ? n + 'x' + c : n));
    return 'handles=[' + parts.join(',') + '] requests=' + requests.length;
  } catch (e) {
    return 'handles=<unavailable: ' + e.message + '>';
  }
}

// Wrap fn so the log carries a "before" line and, only on return, an "after" one.
function wrap(owner, name, describe) {
  const original = owner && owner[name];
  if (typeof original !== 'function') {
    log('skip ' + name + ' (not a function on this runtime)');
    return;
  }
  const wrapped = function (...args) {
    let detail;
    try {
      detail = describe ? describe(args, this) : '';
    } catch (e) {
      detail = '<describe threw: ' + e.message + '>';
    }
    log('> ' + name + ' ' + detail);
    let result;
    try {
      result = original.apply(this, args);
    } catch (err) {
      log('! ' + name + ' threw ' + show(err && err.message, 160));
      throw err;
    }
    log('< ' + name);
    return result;
  };
  Object.defineProperty(wrapped, 'name', { value: name, configurable: true });
  try {
    owner[name] = wrapped;
  } catch (e) {
    log('skip ' + name + ' (not writable: ' + e.message + ')');
  }
}

// --- what the environment looks like before anything runs --------------------

log('preload start  node=' + process.version + ' platform=' + process.platform +
    ' arch=' + process.arch + ' pid=' + process.pid);
log('argv ' + show(process.argv.join(' '), 400));
log('cwd ' + process.cwd());
log('TERM=' + show(process.env.TERM) +
    ' TERM_PROGRAM=' + show(process.env.TERM_PROGRAM) +
    ' NODE_PATH=' + show(process.env.NODE_PATH, 300));

for (const name of ['stdin', 'stdout', 'stderr']) {
  try {
    const s = process[name];
    log(name + ' isTTY=' + !!(s && s.isTTY) +
        ' columns=' + (s && s.columns) + ' rows=' + (s && s.rows) +
        ' ctor=' + ((s && s.constructor && s.constructor.name) || '?'));
  } catch (e) {
    log(name + ' <threw: ' + e.message + '>');
  }
}

// --- native addon loading ----------------------------------------------------

wrap(process, 'dlopen', (args) => show(args[1], 300) + ' flags=' + args[2]);

// --- synchronous and asynchronous child processes ----------------------------

const cp = require('child_process');
const argvOf = (args) => {
  const cmd = show(args[0], 200);
  const rest = Array.isArray(args[1]) ? ' ' + show(args[1].join(' '), 200) : '';
  return cmd + rest;
};

// The synchronous family blocks, so before/after is the whole story.
for (const name of ['execSync', 'spawnSync', 'execFileSync']) {
  wrap(cp, name, argvOf);
}

// The asynchronous family does not: `< spawn` only means the CALL returned, and
// a child that never exits would leave no trace at all. Follow each one to its
// exit, so a hook or a scan that hangs is visible as a child with no exit line.
for (const name of ['spawn', 'exec', 'execFile']) {
  const original = cp[name];
  if (typeof original !== 'function') {
    log('skip ' + name + ' (not a function on this runtime)');
    continue;
  }
  cp[name] = function (...args) {
    const argv = argvOf(args);
    log('> ' + name + ' ' + argv);
    let child;
    try {
      child = original.apply(this, args);
    } catch (err) {
      log('! ' + name + ' threw ' + show(err && err.message, 160));
      throw err;
    }
    const pid = child && child.pid;
    log('< ' + name + ' pid=' + pid);
    if (child && typeof child.on === 'function') {
      const short = argv.slice(0, 60);
      child.on('exit', (code, signal) => {
        log('  child pid=' + pid + ' exited code=' + code +
            (signal ? ' signal=' + signal : '') + '  (' + short + ')');
      });
      child.on('error', (err) => {
        log('  child pid=' + pid + ' error ' + show(err && err.message, 120));
      });
    }
    return child;
  };
  Object.defineProperty(cp[name], 'name', { value: name, configurable: true });
}

// --- worker handshakes and Atomics.wait ---------------------------------------
//
// A blocking Atomics.wait or a synchronous worker handshake stops the main
// thread dead: nothing paints, nothing spins, signals are never delivered.
// That is exactly the reported macOS symptom, so both are worth naming.

try {
  const wt = require('worker_threads');
  wrap(wt, 'receiveMessageOnPort', () => '');
  const OriginalWorker = wt.Worker;
  if (typeof OriginalWorker === 'function') {
    class TracedWorker extends OriginalWorker {
      constructor(filename, options) {
        log('> new Worker ' + show(filename, 200));
        super(filename, options);
        log('< new Worker');
      }
    }
    wt.Worker = TracedWorker;
  }
} catch (e) {
  log('skip worker_threads (' + e.message + ')');
}

const realAtomicsWait = Atomics.wait;
Atomics.wait = function (ta, index, value, timeout) {
  log('> Atomics.wait timeout=' + timeout);
  const r = realAtomicsWait.call(Atomics, ta, index, value, timeout);
  log('< Atomics.wait -> ' + r);
  return r;
};

// --- terminal mode -----------------------------------------------------------

try {
  const tty = require('tty');
  wrap(tty.ReadStream.prototype, 'setRawMode', (args) => 'raw=' + args[0]);
} catch (e) {
  log('skip setRawMode (' + e.message + ')');
}

// --- the first writes, so "nothing drawn" can be told from "drawn but hidden" -

let writesSeen = 0;
for (const name of ['stdout', 'stderr']) {
  try {
    const stream = process[name];
    if (!stream || typeof stream.write !== 'function') continue;
    const original = stream.write.bind(stream);
    stream.write = function (chunk, ...rest) {
      writesSeen += 1;
      if (writesSeen <= PREVIEW_WRITES) {
        const len = chunk && chunk.length != null ? chunk.length : 0;
        log('write#' + writesSeen + ' ' + name + ' bytes=' + len + ' ' + show(chunk, 120));
      } else if (writesSeen === PREVIEW_WRITES + 1) {
        log('write#' + writesSeen + ' ' + name + ' (further writes not logged)');
      }
      return original(chunk, ...rest);
    };
  } catch (e) {
    log('skip ' + name + '.write (' + e.message + ')');
  }
}

// --- signal handlers, because "Ctrl-C does not exit" is a symptom ------------

const INTERESTING = new Set([
  'SIGINT', 'SIGTERM', 'SIGHUP', 'SIGQUIT', 'SIGWINCH',
  'uncaughtException', 'unhandledRejection',
]);
for (const method of ['on', 'once', 'addListener', 'prependListener']) {
  const original = process[method];
  if (typeof original !== 'function') continue;
  process[method] = function (event, listener) {
    if (INTERESTING.has(event)) log('listener ' + method + '(' + event + ')');
    return original.call(this, event, listener);
  };
}

// --- lifecycle ---------------------------------------------------------------

process.prependListener('uncaughtException', (err) => {
  log('uncaughtException ' + show(err && err.stack, 1200));
});
process.prependListener('unhandledRejection', (reason) => {
  log('unhandledRejection ' + show((reason && reason.stack) || reason, 1200));
});
process.prependListener('beforeExit', (code) => log('beforeExit code=' + code));
process.prependListener('exit', (code) => log('exit code=' + code + ' ' + handleSummary()));

// --- heartbeat ---------------------------------------------------------------
//
// Ticking = the event loop is alive and the process is idle, waiting on the
// handles listed. Ticks stopping right after a ">" line with no "<" = the main
// thread is blocked inside that call.

let tick = 0;
const beat = setInterval(() => {
  tick += 1;
  log('tick ' + tick + ' ' + handleSummary());
}, HEARTBEAT_MS);
// Must not keep a finished process alive: the instrument would then cause the
// hang it is meant to find.
beat.unref();

log('preload end (trace -> ' + path.resolve(LOG_PATH) + ')');
