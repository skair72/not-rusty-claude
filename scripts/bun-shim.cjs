"use strict";
/*
 * nrc-bun-shim - a globalThis.Bun stand-in so Claude Code's extracted bundle
 * runs under stock Node.js. Load it with `node --require`.
 *
 *   node --require scripts/bun-shim.cjs build/extract/cli.original.cjs --version
 *
 * Node >= 24 only. The bundle uses ES explicit resource management - 35 `using`
 * declarations in the 2.1.231 artifact - and that is a PARSE error before Node
 * 24. Measured 2026-08-25 on that artifact: `node --check` fails on 22.23.2 and
 * 23.11.1 with "SyntaxError: Unexpected identifier", and passes on 24.0.0 and
 * 26.7.0. `ws` and `undici` must also be resolvable: the bundle imports them as
 * Bun builtins Node does not have. `make node-run` arranges both.
 *
 * THE RULE THIS FILE OBEYS. Every entry either does what Bun does, or throws an
 * error naming the API. Nothing returns a plausible-looking value it cannot
 * stand behind: this repo exists because a silently wrong answer once made Grep
 * report "No matches found" for a string that was there.
 *
 * Three kinds of entry, marked on each one:
 *   IMPLEMENTED  differentially tested against Bun 1.3.14; the test that pins
 *                it is tests/test_node_runtime.py.
 *   THROWS       defined only so the failure names itself.
 *   ABSENT       deliberately left undefined, because the bundle feature-detects
 *                it and "not here" is the true answer. Listed at the bottom.
 *
 * What IS verified (2026-08-25, Bun 1.3.14 vs Node 24.0.0 and 26.7.0, same
 * artifact and a throwaway config dir on both sides): --version, --help,
 * mcp list and config ls print byte-identical stdout with equal exit codes;
 * doctor matches except for its "Path:" line, which correctly names the
 * interpreter actually running.
 *
 * What is NOT: the interactive and agentic paths have never been run under
 * Node. They will reach a THROWS entry - wrapAnsi or YAML first - and say so.
 */

const fs = require("node:fs");
const nodePath = require("node:path");

if (process.versions.bun !== undefined) return; // already Bun; nothing to stand in for

const NODE_MAJOR = Number(process.versions.node.split(".")[0]);
if (NODE_MAJOR < 24) {
  throw new Error(
    "[nrc-bun-shim] Node " + process.versions.node + " cannot parse the Claude " +
    "Code bundle: it uses `using` declarations, which arrived in Node 24 " +
    "(V8 13.6). Use Node >= 24."
  );
}

class BunShimUnsupportedError extends Error {
  constructor(api, why) {
    super("[nrc-bun-shim] Bun." + api + " is not implemented" + (why ? ": " + why : "") +
      ". The Node shim refuses to guess an answer here.");
    this.name = "BunShimUnsupportedError";
    this.bunApi = api;
  }
}
const unsupported = (api, why) => { throw new BunShimUnsupportedError(api, why); };

/* ---------------------------------------------------------------- *
 * Bun.stringWidth  [IMPLEMENTED, with a stated residual]
 *
 * Stands in for: the bundle's or() helper - every help line, table cell and
 * truncation decision. The bundle only ever calls it as
 * stringWidth(s, {ambiguousIsNarrow:true}), which is also Bun's default
 * (measured: default and ambiguousIsNarrow:true agree on all 8 ambiguous-width
 * probes tried, default and ambiguousIsNarrow:false differ on 5).
 *
 * Model: skip CSI and OSC escapes (Bun's countAnsiEscapeCodes:false does only
 * those two - it counts a bare ESC and DCS payloads), then sum over
 * Intl.Segmenter grapheme clusters. A cluster matching \p{RGI_Emoji} is 2; a
 * cluster containing U+20E3 KEYCAP is 2; a multi-code-point cluster containing
 * a variation selector is at least 1; otherwise it is the sum of the
 * per-code-point widths in WIDTHS below.
 *
 * WIDTHS is Bun's own answer, not ICU's. It was built by asking Bun 1.3.14
 * stringWidth(String.fromCodePoint(cp)) for all 1,114,112 code points and
 * run-length encoding the result: 376 runs, 1615 bytes, values 0/1/2 only.
 * Node's own table (process.binding("icu").getStringWidth) was measured against
 * it and disagrees on 1,917 code points, so it is not used - and unlike ICU
 * this table does not shift when Node's bundled ICU does (77.1 on Node 24.0,
 * 78.3 on Node 26.7 - measured, 2,059 disagreements there).
 *
 * NOT FAITHFUL: Bun resolves some combining sequences from a two-dimensional
 * (base, combiner) table this model does not have. Measured 2026-08-25 against
 * Bun 1.3.14, identically on Node 24.0.0, 24.19.0, 25.0.0, 25.9.0 and 26.7.0:
 *   - the 840 distinct calls the real CLI makes across --version, --help,
 *     mcp list, config ls and doctor:                       0 mismatches
 *   - 18 realistic lines (help text, box drawing, CJK, emoji,
 *     colour codes) in tests/bun_shim_probe.cjs:            0 mismatches
 *   - 3,907 adversarial concatenations of the awkward atoms: 287 mismatches
 * The survivors are things like a lone variation selector after a TAB. They
 * are layout errors - a border off by a column - never wrong data, which is
 * why this entry is allowed to approximate at all.
 *
 * --help does render byte-identically to Bun's, all 16,890 bytes, but that is
 * NOT strong evidence and is not offered as such: its output is pure ASCII,
 * where character count and column count are the same number, so
 * `return s.length` renders it identically too - confirmed by mutating this
 * function. The corpus comparison is what actually holds this entry to Bun.
 * ---------------------------------------------------------------- */

// "<startDelta base36>.<width>," run-length encoding of the per-code-point width.
const WIDTHS_RLE = "0.0,w.1,2n.0,x.1,d.0,1.1,gi.0,34.1,i8.0,6.1,5z.0,1.1,1d.0,1.1,cy.0,1.1,t.0,3.1,1j.0,3.1,1.0,g.1,3.0,7.1,a.0,2.1,s.0,3.1,1j.0,3.1,1.0,g.1,3.0,7.1,a.0,2.1,s.0,3.1,1j.0,3.1,1.0,g.1,3.0,7.1,a.0,2.1,s.0,3.1,1j.0,3.1,1.0,g.1,3.0,7.1,a.0,2.1,s.0,3.1,1j.0,3.1,1.0,g.1,3.0,7.1,a.0,2.1,s.0,3.1,1j.0,3.1,1.0,g.1,3.0,7.1,a.0,2.1,s.0,3.1,1j.0,3.1,1.0,g.1,3.0,7.1,a.0,2.1,s.0,3.1,1j.0,3.1,1.0,g.1,3.0,7.1,a.0,2.1,s.0,3.1,1j.0,3.1,1.0,g.1,6b.0,1.1,2.0,7.1,c.0,8.1,2q.0,1.1,2.0,9.1,b.0,6.1,fm.2,2o.1,1u8.0,28.1,jk.0,1s.1,ej.0,5.1,28.0,5.1,2z.0,j.2,1.0,s.1,ey.2,2.1,d.2,2.1,5a.2,4.1,3.2,1.1,2.2,1.1,eh.2,2.1,l.2,2.1,1e.2,c.1,17.2,1.1,j.2,1.1,d.2,1.1,8.2,2.1,h.2,2.1,5.2,2.1,8.2,1.1,5.2,1.1,l.2,1.1,7.2,2.1,1.2,1.1,4.2,1.1,2.2,1.1,7.2,1.1,4.2,2.1,s.2,1.1,z.2,1.1,1.2,1.1,4.2,3.1,1.2,1.1,1p.2,3.1,o.2,1.1,e.2,1.1,nv.2,2.1,1f.2,1.1,4.2,1.1,mi.2,q.1,1.2,2h.1,c.2,5y.1,q.2,27.1,2.2,2e.1,2.2,2v.1,5.2,17.1,1.2,2m.1,1.2,2c.1,b.2,1c.1,1.2,14.1,8.2,5f4.1,1s.2,h3h.1,3.2,1j.1,wp.2,t.1,hv.2,8mc.1,2k.0,1kw.1,4xs.2,e8.1,lc.0,g.2,a.1,6.0,g.2,z.1,1.2,j.1,1.2,4.1,43.0,1.1,1.2,2o.1,3j.2,7.1,m49.2,5.1,b.2,2.1,e.2,4qg.1,8.2,ye.1,16.2,9.1,6w7.2,4.1,1.2,7.1,1.2,2.1,1.2,83.1,f.2,1.1,t.2,3.1,2.2,1.1,e.2,4.1,8.2,b0.1,c20.2,1.1,5m.2,1.1,5a.2,1.1,2.2,a.1,2t.2,3.1,d.2,18.1,4.2,9.1,7.2,2.1,e.2,6.1,4a.2,x.1,c.2,9.1,1.2,1y.1,1.2,m.1,c.2,17.1,4.2,5.1,c.2,h.1,3.2,1.1,3.2,1z.1,1.2,1.1,1.2,57.1,2.2,1r.1,d.2,4.1,1.2,o.1,i.2,1.1,q.2,2.1,d.2,1.1,2e.2,2d.1,1c.2,1y.1,6.2,1.1,3.2,3.1,2.2,3.1,4.2,4.1,b.2,2.1,7.2,9.1,6b.2,c.1,4.2,1.1,7v.2,1b.1,1.2,a.1,1.2,55.1,34.2,d.1,3.2,9.1,7.2,1a.1,1.2,7.1,8.2,e.1,4.2,9.1,7.2,9.1,zr.2,1eke.1,2.2,1eke.1,e1oi.0,3k.1,3k.0,6o.1";

const WIDTH_STARTS = new Int32Array(WIDTHS_RLE.split(",").length);
const WIDTH_VALUES = new Uint8Array(WIDTH_STARTS.length);
{
  const parts = WIDTHS_RLE.split(",");
  let cp = 0;
  for (let i = 0; i < parts.length; i++) {
    const dot = parts[i].indexOf(".");
    cp += parseInt(parts[i].slice(0, dot), 36);
    WIDTH_STARTS[i] = cp;
    WIDTH_VALUES[i] = parts[i].charCodeAt(dot + 1) - 48;
  }
}
function codePointWidth(cp) {
  let lo = 0, hi = WIDTH_STARTS.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if (WIDTH_STARTS[mid] <= cp) lo = mid; else hi = mid - 1;
  }
  return WIDTH_VALUES[lo];
}

const GRAPHEMES = new Intl.Segmenter("en", { granularity: "grapheme" });
const RGI_EMOJI = /^\p{RGI_Emoji}$/v;
const CP_KEYCAP = 0x20e3;
const CP_VS16 = 0xfe0f;
const CP_VS15 = 0xfe0e;

// Only CSI and OSC, matching countAnsiEscapeCodes:false. stripANSI below is a
// different, wider grammar - measured, the two really do differ in Bun.
function skipCsiAndOsc(s) {
  if (s.indexOf("\u001b") === -1) return s;
  let out = "", i = 0;
  const n = s.length;
  while (i < n) {
    if (s.charCodeAt(i) === 0x1b && i + 1 < n) {
      const next = s.charCodeAt(i + 1);
      if (next === 0x5b) {            // ESC [ ... final byte 0x40-0x7E
        let j = i + 2;
        while (j < n) { const c = s.charCodeAt(j); j++; if (c >= 0x40 && c <= 0x7e) break; }
        i = j; continue;
      }
      if (next === 0x5d) {            // ESC ] ... BEL or ST
        let j = i + 2;
        while (j < n) {
          const c = s.charCodeAt(j);
          if (c === 0x07) { j++; break; }
          if (c === 0x1b && s.charCodeAt(j + 1) === 0x5c) { j += 2; break; }
          j++;
        }
        i = j; continue;
      }
    }
    out += s[i]; i++;
  }
  return out;
}

function clusterWidth(cluster) {
  if (RGI_EMOJI.test(cluster)) return 2;
  let sum = 0, count = 0, keycap = false, variationSelector = false;
  for (const ch of cluster) {
    const cp = ch.codePointAt(0);
    count++;
    if (cp === CP_KEYCAP) keycap = true;
    else if (cp === CP_VS16 || cp === CP_VS15) variationSelector = true;
    sum += codePointWidth(cp);
  }
  if (keycap) return 2;
  if (variationSelector && count > 1 && sum < 1) return 1;
  return sum;
}

function stringWidth(input, options) {
  if (arguments.length === 0 || input === undefined) return 0;
  const s = typeof input === "string" ? input : String(input);
  if (options !== undefined && options !== null) {
    if (options.ambiguousIsNarrow === false) {
      unsupported("stringWidth({ambiguousIsNarrow:false})",
        "only the default (narrow) table was measured against Bun; guessing at " +
        "ambiguous-width characters is a silent layout error");
    }
    if (options.countAnsiEscapeCodes === true) {
      unsupported("stringWidth({countAnsiEscapeCodes:true})",
        "not measured; the bundle never asks for it");
    }
  }
  let total = 0;
  for (const { segment } of GRAPHEMES.segment(skipCsiAndOsc(s))) total += clusterWidth(segment);
  return total;
}

/* ---------------------------------------------------------------- *
 * Bun.stripANSI  [IMPLEMENTED]
 *
 * Stands in for: the bundle's wi() helper. Wider than the skip above, and
 * measured from Bun 1.3.14: removes CSI (ESC "[" or 0x9B up to a byte in
 * 0x40-0x7E), OSC (ESC "]" or 0x9D up to BEL or ST), DCS/SOS/PM/APC (ESC
 * P/X/^/_ or 0x90/0x98/0x9E/0x9F up to ST), and a bare ESC plus one unit -
 * two units when the byte after ESC is one of the nine designators below,
 * measured one at a time. Unterminated sequences eat the rest of the string.
 * ---------------------------------------------------------------- */
const ESC_TAKES_ONE_MORE = new Set([0x20, 0x23, 0x25, 0x28, 0x29, 0x2a, 0x2b, 0x2e, 0x2f]);
function skipCsiTail(s, j) {
  while (j < s.length) { const c = s.charCodeAt(j); j++; if (c >= 0x40 && c <= 0x7e) break; }
  return j;
}
function skipToStringTerminator(s, j, allowBell) {
  while (j < s.length) {
    const c = s.charCodeAt(j);
    if (allowBell && c === 0x07) return j + 1;
    if (c === 0x9c) return j + 1;
    if (c === 0x1b && s.charCodeAt(j + 1) === 0x5c) return j + 2;
    j++;
  }
  return j;
}
function stripANSI(input) {
  if (arguments.length === 0 || input === undefined) return "";
  const s = typeof input === "string" ? input : String(input);
  let out = "", i = 0;
  const n = s.length;
  while (i < n) {
    const c = s.charCodeAt(i);
    if (c === 0x9b) { i = skipCsiTail(s, i + 1); continue; }
    if (c === 0x9d) { i = skipToStringTerminator(s, i + 1, true); continue; }
    if (c === 0x90 || c === 0x98 || c === 0x9e || c === 0x9f) { i = skipToStringTerminator(s, i + 1, false); continue; }
    if (c !== 0x1b) { out += s[i]; i++; continue; }
    if (i + 1 >= n) { i++; continue; }
    const next = s.charCodeAt(i + 1);
    if (next === 0x5b) { i = skipCsiTail(s, i + 2); continue; }
    if (next === 0x5d) { i = skipToStringTerminator(s, i + 2, true); continue; }
    if (next === 0x50 || next === 0x58 || next === 0x5e || next === 0x5f) { i = skipToStringTerminator(s, i + 2, false); continue; }
    i += ESC_TAKES_ONE_MORE.has(next) ? 3 : 2;
  }
  return out;
}

/* ---------------------------------------------------------------- *
 * Bun.hash  [IMPLEMENTED]
 *
 * Stands in for: content hashes and cache keys - skill contentHash, tool-result
 * ids, message dedup. Those hashes get written into files, so a different-but-
 * plausible number is exactly the silent-wrong-answer failure this repo is
 * about; it has to be the same function or none.
 *
 * Bun.hash is wyhash (final v3) with the default secret, over the UTF-8 bytes,
 * returning a u64 as a BigInt. Second argument is the seed. This is that
 * algorithm in BigInt arithmetic - it is the published construction, not a
 * guess: verified equal to Bun 1.3.14 on 3,402 inputs (lengths 0-300, seeded
 * and unseeded, ASCII and non-BMP), 0 mismatches, measured 2026-08-25.
 *
 * NOT faithful: Bun.hash.crc32 and the eleven other named algorithms are not
 * provided - they are separate functions and the bundle does not call them.
 * ---------------------------------------------------------------- */
const U64 = (1n << 64n) - 1n;
const WY0 = 0xa0761d6478bd642fn, WY1 = 0xe7037ed1a0b428dbn,
      WY2 = 0x8ebc6af09c88c6e3n, WY3 = 0x589965cc75374cc3n;
function wyMix(a, b) { const r = a * b; return ((r & U64) ^ (r >> 64n)) & U64; }
function wyRead8(p, i) {
  let v = 0n;
  for (let k = 7; k >= 0; k--) v = (v << 8n) | BigInt(p[i + k]);
  return v;
}
function wyRead4(p, i) {
  return BigInt(((p[i] | (p[i + 1] << 8) | (p[i + 2] << 16) | (p[i + 3] << 24)) >>> 0));
}
function wyRead3(p, i, k) {
  return (BigInt(p[i]) << 16n) | (BigInt(p[i + (k >> 1)]) << 8n) | BigInt(p[i + k - 1]);
}
function wyhash(bytes, seed) {
  const len = bytes.length;
  let s = (seed ^ wyMix(seed ^ WY0, WY1)) & U64;
  let a, b;
  if (len <= 16) {
    if (len >= 4) {
      a = ((wyRead4(bytes, 0) << 32n) | wyRead4(bytes, (len >> 3) << 2)) & U64;
      b = ((wyRead4(bytes, len - 4) << 32n) | wyRead4(bytes, len - 4 - ((len >> 3) << 2))) & U64;
    } else if (len > 0) { a = wyRead3(bytes, 0, len); b = 0n; }
    else { a = 0n; b = 0n; }
  } else {
    let i = len, p = 0;
    if (i > 48) {
      let s1 = s, s2 = s;
      while (i > 48) {
        s = wyMix((wyRead8(bytes, p) ^ WY1) & U64, (wyRead8(bytes, p + 8) ^ s) & U64);
        s1 = wyMix((wyRead8(bytes, p + 16) ^ WY2) & U64, (wyRead8(bytes, p + 24) ^ s1) & U64);
        s2 = wyMix((wyRead8(bytes, p + 32) ^ WY3) & U64, (wyRead8(bytes, p + 40) ^ s2) & U64);
        p += 48; i -= 48;
      }
      s = (s ^ s1 ^ s2) & U64;
    }
    while (i > 16) {
      s = wyMix((wyRead8(bytes, p) ^ WY1) & U64, (wyRead8(bytes, p + 8) ^ s) & U64);
      i -= 16; p += 16;
    }
    a = wyRead8(bytes, p + i - 16);
    b = wyRead8(bytes, p + i - 8);
  }
  const prod = ((a ^ WY1) & U64) * ((b ^ s) & U64);
  return wyMix(((prod & U64) ^ WY0 ^ BigInt(len)) & U64, ((prod >> 64n) & U64) ^ WY1);
}
const UTF8 = new TextEncoder();
function hash(input, seed) {
  let bytes;
  if (typeof input === "string") bytes = UTF8.encode(input);
  else if (ArrayBuffer.isView(input)) bytes = new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  else if (input instanceof ArrayBuffer) bytes = new Uint8Array(input);
  else unsupported("hash(" + typeof input + ")", "only strings and binary views were measured");
  let s = 0n;
  if (seed !== undefined) {
    if (typeof seed === "bigint") s = seed & U64;
    else if (typeof seed === "number" && Number.isInteger(seed)) s = BigInt(seed) & U64;
    else unsupported("hash(_, seed)", "seed must be an integer or bigint");
  }
  return wyhash(bytes, s);
}

/* ---------------------------------------------------------------- *
 * Bun.which  [IMPLEMENTED]
 *
 * Stands in for: locating helper executables (editors, git, node...). Measured
 * contract, Bun 1.3.14: a name containing a separator resolves against
 * options.cwd (else process.cwd()); anything else is looked up in options.PATH
 * (else process.env.PATH), empty entries skipped, and PATH entries are NOT
 * resolved against options.cwd. The hit must be a regular file with the
 * execute bit. Anything not found, a directory, or a non-executable file gives
 * null - including an empty or undefined name.
 * ---------------------------------------------------------------- */
function isExecutableFile(p) {
  try {
    if (!fs.statSync(p).isFile()) return false;
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch { return false; }
}
function which(command, options) {
  if (typeof command !== "string" || command === "") return null;
  const cwd = options && typeof options.cwd === "string" ? options.cwd : process.cwd();
  if (command.includes(nodePath.sep) || command.includes("/")) {
    const abs = nodePath.resolve(cwd, command);
    return isExecutableFile(abs) ? abs : null;
  }
  const raw = options && typeof options.PATH === "string" ? options.PATH : process.env.PATH;
  if (!raw) return null;
  for (const dir of raw.split(nodePath.delimiter)) {
    if (dir === "") continue;
    const candidate = nodePath.resolve(dir, command);
    if (isExecutableFile(candidate)) return candidate;
  }
  return null;
}

/* ---------------------------------------------------------------- *
 * Bun.semver.order  [IMPLEMENTED]   Bun.semver.satisfies  [THROWS]
 *
 * order stands in for the bundle's version comparisons (is this CLI newer than
 * that one). Plain SemVer 2.0.0 precedence: numeric major/minor/patch, then
 * prerelease, where having one loses to having none, numeric identifiers
 * compare numerically and lose to alphanumeric ones, and build metadata is
 * ignored. Bun throws on a string it cannot parse and so does this. Verified
 * against Bun 1.3.14 - see tests/test_node_runtime.py.
 *
 * satisfies is a whole range grammar (^ ~ || hyphen x-ranges, and prerelease
 * rules that differ between implementations). Approximating it would silently
 * enable or disable features, so it throws instead.
 * ---------------------------------------------------------------- */
const SEMVER_RE = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$/;
function parseSemver(v) {
  const m = typeof v === "string" ? SEMVER_RE.exec(v.trim()) : null;
  if (!m) throw new Error("[nrc-bun-shim] Invalid SemVer: " + String(v));
  return {
    parts: [Number(m[1]), Number(m[2]), Number(m[3])],
    pre: m[4] === undefined ? null : m[4].split("."),
  };
}
function comparePrerelease(a, b) {
  if (a === null && b === null) return 0;
  if (a === null) return 1;
  if (b === null) return -1;
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    const x = a[i], y = b[i];
    if (x === undefined) return -1;
    if (y === undefined) return 1;
    const xn = /^\d+$/.test(x), yn = /^\d+$/.test(y);
    if (xn && yn) { const d = Number(x) - Number(y); if (d !== 0) return d < 0 ? -1 : 1; continue; }
    if (xn !== yn) return xn ? -1 : 1;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}
function semverOrder(a, b) {
  const x = parseSemver(a), y = parseSemver(b);
  for (let i = 0; i < 3; i++) if (x.parts[i] !== y.parts[i]) return x.parts[i] < y.parts[i] ? -1 : 1;
  return comparePrerelease(x.pre, y.pre);
}

/* ---------------------------------------------------------------- *
 * Bun.deepEquals  [IMPLEMENTED for JSON values, THROWS otherwise]
 *
 * Stands in for: comparing a settings object against the one already loaded.
 * Bun's default (non-strict) mode is looser than util.isDeepStrictEqual in
 * ways that matter - measured on Bun 1.3.14: {a:undefined} equals {}, a class
 * instance equals a plain object with the same fields, NaN equals NaN, and 0
 * does not equal -0. isDeepStrictEqual answers the opposite on the first two,
 * so it is not used.
 *
 * This covers exactly what those call sites pass: null, booleans, numbers,
 * strings, arrays and plain objects. Dates, Maps, Sets, RegExps, typed arrays,
 * class instances and cycles all THROW rather than get an approximate answer,
 * and the third (strict) argument is not accepted.
 * ---------------------------------------------------------------- */
function isPlainObject(v) {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return false;
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}
function definedKeys(o) {
  return Object.keys(o).filter((k) => o[k] !== undefined);
}
// null | boolean | number | string | undefined | array | object; anything else
// is a shape these call sites never pass and this function will not guess at.
function jsonKind(v) {
  if (v === null) return "null";
  const t = typeof v;
  if (t === "boolean" || t === "number" || t === "string" || t === "undefined") return t;
  if (Array.isArray(v)) return "array";
  if (isPlainObject(v)) return "object";
  unsupported("deepEquals", "only JSON values are supported, not " +
    (t === "object" ? "class instances, Map, Set, Date, RegExp or typed arrays" : t));
}
function deepEqualsJson(a, b, depth) {
  if (depth > 100) unsupported("deepEquals", "input nested deeper than 100 levels, or cyclic");
  const ka = jsonKind(a), kb = jsonKind(b);
  if (ka !== kb) return false;
  if (ka === "number") {
    if (Number.isNaN(a) && Number.isNaN(b)) return true;
    return a === b && (a !== 0 || Object.is(a, b));
  }
  if (ka !== "array" && ka !== "object") return a === b;
  if (ka === "array") {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) if (!deepEqualsJson(a[i], b[i], depth + 1)) return false;
    return true;
  }
  const keysA = definedKeys(a), keysB = definedKeys(b);
  if (keysA.length !== keysB.length) return false;
  for (const k of keysA) {
    if (!Object.prototype.hasOwnProperty.call(b, k)) return false;
    if (!deepEqualsJson(a[k], b[k], depth + 1)) return false;
  }
  return true;
}
function deepEquals(a, b, strict) {
  if (strict !== undefined) unsupported("deepEquals(_, _, strict)", "only the default loose mode was measured");
  return deepEqualsJson(a, b, 0);
}

/* ---------------------------------------------------------------- *
 * Bun.gc  [IMPLEMENTED, with one difference stated]
 *
 * Stands in for: a 1-second timer that nudges the collector, and one call after
 * writing a heap snapshot. Runs a real collection through V8's own hook rather
 * than pretending to.
 *
 * NOT faithful: Bun.gc returns the heap size after collecting; this returns
 * undefined, because Node's gc() returns nothing and inventing a byte count is
 * exactly the kind of plausible number this file refuses to produce. Both call
 * sites in the bundle discard the value. If --expose-gc is not available the
 * collection is skipped - a hint, not a guarantee, in Bun too.
 * ---------------------------------------------------------------- */
let collect = null;
function gc() {
  if (collect === null) {
    if (typeof globalThis.gc === "function") collect = globalThis.gc;
    else {
      try {
        const v8 = require("node:v8");
        const vm = require("node:vm");
        v8.setFlagsFromString("--expose-gc");
        collect = vm.runInNewContext("gc");
        v8.setFlagsFromString("--no-expose-gc");
      } catch { collect = false; }
    }
  }
  if (collect) collect();
  return undefined;
}

/* ---------------------------------------------------------------- *
 * The object this file installs as globalThis.Bun. The entries above go in
 * first; everything below them is a reachable call with no honest Node
 * equivalent here, and each one names itself so a failing run says which Bun
 * API it needed.
 * ---------------------------------------------------------------- */
const bun = {
  stringWidth,
  stripANSI,
  hash,
  which,
  deepEquals,
  gc,
  semver: {
    order: semverOrder,
    satisfies: () => unsupported("semver.satisfies", "the range grammar is not implemented"),
  },

  // Text formats Bun parses natively and Node does not. Used for skill and
  // agent frontmatter; a wrong parse would be silently wrong config.
  YAML: {
    parse: () => unsupported("YAML.parse", "Node has no YAML parser and this file ships none"),
    stringify: () => unsupported("YAML.stringify", "Node has no YAML serialiser and this file ships none"),
  },
  TOML: { parse: () => unsupported("TOML.parse", "Node has no TOML parser and this file ships none") },

  // Process and I/O primitives. Portable in principle, unverified in practice:
  // the agentic path has never been run under Node, so these are left loud.
  spawn: () => unsupported("spawn", "the subprocess path has not been verified under Node"),
  file: () => unsupported("file", "the BunFile object has not been verified under Node"),
  serve: () => unsupported("serve", "no HTTP server stand-in"),
  listen: () => unsupported("listen", "no TCP server stand-in"),
  connect: () => unsupported("connect", "no TCP client stand-in"),
  wrapAnsi: () => unsupported("wrapAnsi", "the wrapping algorithm was not verified against Bun; the TUI needs it"),
  generateHeapSnapshot: () => unsupported("generateHeapSnapshot", "Node's v8.getHeapSnapshot has a different shape"),

  // Native-binary-only surfaces. Bun's own error text for the gateway is
  // "requires the native binary"; these keep that true and say which piece.
  SQL: function () { unsupported("SQL", "the gateway needs Bun's native SQL client"); },
  Transpiler: function () { unsupported("Transpiler", "no JS transpiler stand-in"); },
  ant: {
    getPeerUid: () => unsupported("ant.getPeerUid", "no SO_PEERCRED binding"),
    getPeerPid: () => unsupported("ant.getPeerPid", "no SO_PEERCRED binding"),
    setDumpable: () => unsupported("ant.setDumpable", "no prctl binding; the process stays dumpable"),
  },
};

/* ---------------------------------------------------------------- *
 * ABSENT on purpose - the bundle feature-detects each of these, and under Node
 * "not here" is the true answer, so leaving them undefined takes the fallback
 * the upstream code already has:
 *
 *   Bun.Terminal              gated by a spawnPty capability; the bundle's own
 *                             message for its absence is "Bun.Terminal
 *                             unavailable (running under Node?)"
 *   Bun.WebView               gated by `"WebView" in Bun`
 *   Bun.JSONL                 read as `Bun.JSONL?.parseChunk`
 *   Bun.isStandaloneExecutable  measured: stock Bun 1.3.14 running a script
 *                             also leaves this undefined, and the bundle asks
 *                             `=== true`
 *   Bun.version               defining it would claim to be Bun; the bundle's
 *                             runtime probe is `typeof Bun.version`, and
 *                             process.versions.bun is genuinely absent here
 *   Bun.stdin                 appears only inside a documentation string
 *
 * Known inaccuracy that cannot be fixed from here: code testing `typeof Bun`
 * alone sees "object" and concludes it is on Bun. That flag reaches telemetry
 * metadata and the gateway's "requires the native binary" guard, which then
 * fails at Bun.SQL above instead of at the guard.
 * ---------------------------------------------------------------- */

Object.defineProperty(globalThis, "Bun", {
  value: bun, writable: true, enumerable: false, configurable: true,
});

module.exports = bun;
