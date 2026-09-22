// bun-ant.mjs - Bun.ant for a runtime that lacks it.
//
// Claude Code 2.1.280 is built with Anthropic's private Bun fork
// ("@anthropic-ai/bun-internal"), which adds a Bun.ant namespace. Stock Bun
// has none. The bundle touches five members (docs/findings.md 14):
//
//   CellSegmenter        Ink's text -> screen-cell engine. HARD requirement:
//                        without it nothing renders, and even a non-interactive
//                        command dies on exit when Ink unmounts (measured:
//                        `mcp list` SIGKILLs itself - its forceExit() catches
//                        the throw from process.exit()).
//   getPeerPid/Uid(fd)   SO_PEERCRED on a connected unix socket; both call
//                        sites catch and degrade.
//   setDumpable(bool)    prctl(PR_SET_DUMPABLE); the call site catches.
//   memoryPressureLevel  macOS only; the call site catches.
//
// postprocess.py copies this file next to the artifact's entry, which imports
// it before any of Claude's modules evaluate. It never replaces a Bun.ant that
// already exists, so under the real bun-internal it is inert.
//
// Every behaviour below was measured against the native 2.1.280 runtime (the
// binary honours BUN_OPTIONS=--preload, so a probe can run inside it); see
// docs/findings.md 14 and tests/test_bun_ant.py.

import { CellSegmenter } from "./bun-ant-cell-segmenter.mjs";

const SOL_SOCKET = 1;       // Linux, every architecture Bun ships
const SO_PEERCRED = 17;     // Linux x86_64 and aarch64
const PR_SET_DUMPABLE = 4;

let libc;   // undefined = not tried yet, null = unavailable

function loadLibc() {
  if (libc !== undefined) return libc;
  libc = null;
  // bun:ffi only exists under Bun; Node has no synchronous FFI at all, and
  // then every member below behaves as its "lookup failed" case.
  if (typeof Bun === "undefined" || typeof import.meta.require !== "function") return libc;
  if (process.platform !== "linux") return libc;
  let ffi;
  try {
    ffi = import.meta.require("bun:ffi");
  } catch {
    return libc;
  }
  const { dlopen, FFIType } = ffi;
  for (const name of ["libc.so.6", "libc.so"]) {
    try {
      const lib = dlopen(name, {
        getsockopt: { args: [FFIType.i32, FFIType.i32, FFIType.i32, FFIType.ptr, FFIType.ptr], returns: FFIType.i32 },
        prctl: { args: [FFIType.i32, FFIType.u64, FFIType.u64, FFIType.u64, FFIType.u64], returns: FFIType.i32 },
      });
      libc = { ffi, sym: lib.symbols };
      break;
    } catch {
      // try the next name
    }
  }
  return libc;
}

// struct ucred { pid_t pid; uid_t uid; gid_t gid; } - or null when the fd is
// not a connected unix socket, which is what the native member returns there
// (measured: fd -1, an unopened fd and a string all give null).
function peerCred(fd) {
  const c = loadLibc();
  if (!c) return null;
  // Native coerces a missing argument to fd 0 (measured: getPeerPid() and
  // getPeerPid(0) agree) and answers null for a non-number.
  if (fd === undefined) fd = 0;
  if (typeof fd !== "number" || !Number.isInteger(fd) || fd < 0 || fd > 0x7fffffff) return null;
  const cred = new Int32Array(3);
  const len = new Uint32Array([12]);
  const rc = c.sym.getsockopt(fd, SOL_SOCKET, SO_PEERCRED, c.ffi.ptr(cred), c.ffi.ptr(len));
  if (rc !== 0 || len[0] < 12) return null;
  return cred;
}

function getPeerPid(fd) {
  const cred = peerCred(fd);
  return cred === null ? null : cred[0];
}

function getPeerUid(fd) {
  const cred = peerCred(fd);
  return cred === null ? null : cred[1] >>> 0;
}

function setDumpable(dumpable) {
  const c = loadLibc();
  if (!c) {
    // Throwing, not returning false: the call site reports a throw as
    // "prctl unavailable", and false as "prctl returned nonzero" - which
    // would claim a syscall that never ran.
    throw new Error("Bun.ant.setDumpable() needs bun:ffi and Linux libc; neither is available here");
  }
  return c.sym.prctl(PR_SET_DUMPABLE, dumpable ? 1 : 0, 0, 0, 0) === 0;
}

function memoryPressureLevel() {
  // The native message, verbatim. On macOS the native member reads the kernel's
  // memorystatus level; nothing here can be measured against a Mac, so it
  // stays unimplemented there rather than guessed - the caller treats a throw
  // as "no data" and reports low memory as false.
  throw new Error("Bun.ant.memoryPressureLevel() is only supported on macOS");
}

export const bunAnt = { CellSegmenter, getPeerPid, getPeerUid, setDumpable, memoryPressureLevel };

export function installBunAnt(target) {
  if (!target) return false;
  const existing = target.ant;
  if (existing && typeof existing.CellSegmenter === "function") return false;
  if (existing && typeof existing === "object") {
    for (const [k, v] of Object.entries(bunAnt)) if (!(k in existing)) existing[k] = v;
    return true;
  }
  Object.defineProperty(target, "ant", { value: { ...bunAnt }, configurable: true, enumerable: false, writable: true });
  return true;
}

installBunAnt(globalThis.Bun);
