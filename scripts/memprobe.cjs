// memprobe.cjs - sample a Claude process's memory from inside it.
//
// Loaded with BUN_OPTIONS="--preload scripts/memprobe.cjs", which both the
// native binary and a stock Bun honour, so the same probe measures both
// sides of scripts/memsoak.py - and a real session on macOS, where the leak
// was reported. Every NRC_MEMPROBE_MS (default 5000) it appends one JSON line
// to NRC_MEMPROBE_OUT:
//
//   rss        resident set size BEFORE the forced GC (what `ps` sees)
//   rssGc      resident set size after Bun.gc(true)
//   footprint  macOS only: phys_footprint, what Activity Monitor calls
//              "Memory" (compressed and swapped pages included, which RSS
//              leaves out); null where it cannot be read
//   heapSize   JSC's live heap after the GC - what JavaScript retains
//   extra      JSC's extraMemorySize: buffers and strings owned outside the
//              cell heap but reported to the GC
//   malloc     bun:jsc memoryUsage(), which is mimalloc's process info:
//              `current` is the process RSS again, `commit` what mimalloc
//              itself holds committed - Bun's native side (sockets, fetch and
//              stream buffers). Measured under 1.3.14: it does not move for
//              JS strings, objects, Buffers or Response bodies, which JSC
//              allocates elsewhere (libpas). Compare it within one Bun only:
//              on 1.4 it also counts purged pages.
//   objects    live cell count after the GC
//   types      every object type with its live count, for diffing two samples
//
// Every line carries the pid: BUN_OPTIONS reaches every bun a session spawns,
// so a log can hold more than one process. (`bun --preload` on the command
// line does not propagate; that is the way to probe one real session.)
//
// The GC is forced so that a sample measures what is retained, not how far
// behind the collector happens to be. Main thread only; the timer is unref'd,
// so the probe never keeps a process alive that would otherwise exit.

"use strict";

const out = process.env.NRC_MEMPROBE_OUT;
let isMain = true;
try {
  isMain = require("worker_threads").isMainThread;
} catch {
  // no worker_threads: we are the main thread
}

// proc_pid_rusage(getpid(), RUSAGE_INFO_V0, &info): struct rusage_info_v0 is
// uuid[16], then u64 user_time, system_time, pkg_idle_wkups, interrupt_wkups,
// pageins, wired_size, resident_size, phys_footprint, ... - so phys_footprint
// is the u64 at byte 72. Written from the SDK's <sys/resource.h>; it has not
// been run on a Mac from this repo, so any failure reads as null, never as
// a number.
function darwinFootprint() {
  if (process.platform !== "darwin") return null;
  try {
    const { dlopen, FFIType, ptr } = require("bun:ffi");
    const lib = dlopen("/usr/lib/libSystem.B.dylib", {
      proc_pid_rusage: { args: [FFIType.i32, FFIType.i32, FFIType.ptr], returns: FFIType.i32 },
    });
    const buf = new BigUint64Array(64); // 512 bytes, well past v0's 96
    return () => {
      if (lib.symbols.proc_pid_rusage(process.pid, 0, ptr(buf)) !== 0) return null;
      return Number(buf[9]);
    };
  } catch {
    return null;
  }
}

if (out && isMain) {
  const fs = require("fs");
  const jsc = require("bun:jsc");
  const footprint = darwinFootprint();
  const t0 = Date.now();
  const every = Number(process.env.NRC_MEMPROBE_MS) || 5000;
  const sample = () => {
    const rss = process.memoryUsage.rss();
    Bun.gc(true);
    const h = jsc.heapStats();
    const m = typeof jsc.memoryUsage === "function" ? jsc.memoryUsage() : null;
    fs.appendFileSync(out, JSON.stringify({
      pid: process.pid,
      t: Date.now() - t0,
      rss,
      rssGc: process.memoryUsage.rss(),
      footprint: footprint ? footprint() : null,
      heapSize: h.heapSize,
      heapCapacity: h.heapCapacity,
      extra: h.extraMemorySize,
      malloc: m && { current: m.current, commit: m.currentCommit, peakCommit: m.peakCommit },
      objects: h.objectCount,
      protected: h.protectedObjectCount,
      types: h.objectTypeCounts,
    }) + "\n");
  };
  setInterval(sample, every).unref();
  setTimeout(sample, 1000).unref();
}
