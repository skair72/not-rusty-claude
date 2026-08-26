"use strict";
/*
 * nrc-bun-shim - a globalThis.Bun stand-in so Claude Code's extracted bundle
 * runs under stock Node.js. Load it with `node --require`.
 *
 *   node --require scripts/bun-shim.cjs build/extract/cli.original.cjs --version
 *
 * Node >= 24 only. The bundle uses ES explicit resource management - 33 `using`
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
 * THE CONTRACT, STATED HONESTLY. Not "this shim matches Bun". It is: on
 * well-formed input it answers what Bun answers, and on input it cannot
 * match it refuses out loud - with the two documented exceptions below,
 * where it quietly answers something else instead. The first cannot be
 * matched at all from outside Bun; the second could be imitated only by
 * statting a different string from the one it would then hand back. Both are
 * measured, both are pinned case by case by
 * test_known_divergences_are_exactly_what_was_measured, which fails if
 * EITHER side moves, and neither is reachable from the artifact's own call
 * sites. A refusal is measured too: each guard refuses every spelling Bun
 * reads as the unsupported setting, not just the literal one. stringWidth
 * additionally APPROXIMATES on pathological combining sequences; that
 * residual is described at the function and bounded by the suite.
 *
 * DOCUMENTED DIVERGENCE 1 - stringWidth's CSI terminator is representation-
 * dependent. Bun carries two CSI scanners and picks between them by how JSC is
 * storing the string, not by what is in it: a Latin-1 string ends a CSI only on
 * 0x40..0x7E, a 16-bit one ends it on any code point >= 0x40 except 0x7F. The
 * same characters therefore get two answers depending on what else is in the
 * string. Measured, Bun 1.3.14 (shim's answer second):
 *     "A" ESC "[" CJK "B"                   Bun 2, shim 1
 *     "A" ESC "[" U+00FF "B" CJK            Bun 4, shim 3
 *     "A" ESC "[" U+00E9 "B"                Bun 1, shim 1   (Latin-1: agrees)
 * Direction: Bun's 16-bit terminator set is a superset of the Latin-1 one, so
 * Bun stops eating no later than the shim does. The shim's answer is never too
 * big, only too small - a box drawn a column or two narrow, never wrong data.
 * NOT FIXABLE from outside Bun, and this is not a guess: `new
 * TextDecoder("utf-16le").decode(...)` builds a string that is `===` to the
 * literal one above and Bun answers 2 for it and 1 for the literal. Two
 * strings that compare equal, two answers - so no function of the argument's
 * value can produce both, and JS offers no way to ask JSC which representation
 * it chose. Reaching it needs a truncated or malformed CSI in a string JSC is
 * holding as 16-bit; a well-formed SGR sequence never trips it. Do NOT read
 * that as "needs a code point >= U+0100" - the content does not decide it, the
 * PROVENANCE does. Measured: `"A" ESC "[" U+00E9 "B"` is Latin-1 throughout and
 * agrees (1/1) as a source literal, but the `===`-identical string coming out
 * of TextDecoder, Buffer.toString("utf8") or fs.readFileSync(p,"utf8") reads
 * Bun 2 / shim 1. Anything decoded from bytes can be 16-bit whatever its code
 * points, so text read from a file is exposed and a literal is not.
 * Measured on the artifact: of the 840 distinct strings the CLI passes to
 * stringWidth across --version, --help, mcp list, config ls and doctor, NONE
 * contains an ESC "[" at all.
 *
 * DOCUMENTED DIVERGENCE 2 - which() and a NUL in the command name. Bun hands
 * the name to the OS as a NUL-terminated C string, so the NUL truncates it at
 * the syscall while the JS string Bun returns keeps everything after it:
 * which("<dir>/exe1\0zzz") is "<dir>/exe1\0zzz" when <dir>/exe1 is executable.
 * Node's fs rejects any path argument containing a NUL outright, so the stat
 * throws and the shim answers null. Direction: the shim under-reports - null
 * means "not installed", which is the answer a caller can fall back from,
 * where Bun's is a path that Node could not then execute anyway. This one is
 * not representation-dependent and could be emulated - stat the text before
 * the NUL, return the text with it - but that means deliberately statting a
 * different string from the one handed back, so it is documented rather than
 * imitated. Unreachable from the artifact: both Bun.which call sites take a
 * command name, and neither argv nor the environment can carry a NUL (they are
 * C strings too); measured across the five commands above, the artifact asks
 * for exactly two names, "git" and "rg".
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
 * What is NOT: the agentic path - a real conversation with tool use - has
 * never been run under Node. The interactive path has: onboarding on Linux and
 * the authenticated REPL on Apple Silicon, both 2026-08-26. Getting there
 * needed wrapAnsi and YAML.parse, which the renderer and the skill loader
 * cannot do without; both are implemented here and measured against Bun.
 * YAML.parse still refuses anchors, tags, complex keys, multi-document input,
 * tab indentation, explicit block scalar indents and over-indented sequence
 * entries - by name, never by guessing.
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

// Bun coerces a non-string argument with ToString. Measured on Bun 1.3.14:
// stripANSI/stringWidth/hash/which all take an object's toString (not its
// valueOf), and all four throw a TypeError on a symbol. JS String() would
// answer "Symbol(s)" instead of throwing, so symbols are rejected explicitly.
function toStringArg(v) {
  if (typeof v === "symbol") throw new TypeError("Cannot convert a symbol to a string");
  return String(v);
}

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
 *   - every one of the 1,114,112 code points, one at a time: 0 mismatches
 *   - 24 realistic lines (help text, box drawing, CJK, emoji,
 *     colour codes) in tests/bun_shim_probe.cjs:            0 mismatches
 *   - 4,235 adversarial concatenations of the awkward atoms: 236 mismatches
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
const ZWJ = "\u200d";
// A ZWJ sequence made only of pictographs, joiners and variation selectors.
const ZWJ_PICTOGRAPHIC =
  /^\p{Extended_Pictographic}[\u{fe0e}\u{fe0f}\u{1f3fb}-\u{1f3ff}]*(?:\u200d\p{Extended_Pictographic}[\u{fe0e}\u{fe0f}\u{1f3fb}-\u{1f3ff}]*)+$/u;
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
        // ST has two spellings and Bun accepts both here, measured on 1.3.14:
        // ESC \ and the one-character C1 U+009C. Missing the C1 one made the
        // rest of the line vanish - stringWidth(ESC "]0;t" U+009C "TAIL") is 4
        // in Bun and was 0 here. stripANSI below already knew this.
        let j = i + 2;
        while (j < n) {
          const c = s.charCodeAt(j);
          if (c === 0x07 || c === 0x9c) { j++; break; }
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
  let sum = 0, count = 0, keycap = false, variationSelector = false;
  for (const ch of cluster) {
    const cp = ch.codePointAt(0);
    count++;
    if (cp === CP_KEYCAP) keycap = true;
    else if (cp === CP_VS16 || cp === CP_VS15) variationSelector = true;
    sum += codePointWidth(cp);
  }
  if (keycap) return 2;
  // Only for real clusters. A SINGLE code point must come from WIDTHS, which is
  // Bun's own answer: \p{RGI_Emoji} is ICU's, and ICU moves. Measured over all
  // 1,114,112 single code points - asking RGI_Emoji first put 7 of them (Node
  // 24.0.0) and 14 (Node 26.7.0) one column wider than Bun; deferring it here
  // makes every single code point match Bun on both, and changes nothing on the
  // probe's realistic or adversarial corpora.
  if (count > 1 && RGI_EMOJI.test(cluster)) return 2;
  // ICU's RGI_Emoji is a curated LIST, and it moves: on Node 24.19 it rejects
  // the man+woman family while accepting man+woman+girl, so that one cluster
  // fell through to 2+0+2 = 4 where Bun says 2. Bun's rule is structural
  // rather than curated - a ZWJ sequence whose parts are all pictographic is
  // one glyph of width 2 - so ask that instead of trusting the list.
  //
  // Measured: emoji+ZWJ+emoji is 2 for every pair tried, including ones the
  // list rejects, while non-pictographic parts are SUMMED - CJK+ZWJ+CJK is 4,
  // emoji+ZWJ+letter is 3. So the test is on the parts, not on the joiner.
  if (count > 1 && cluster.includes(ZWJ) && ZWJ_PICTOGRAPHIC.test(cluster)) return 2;
  if (variationSelector && count > 1 && sum < 1) return 1;
  return sum;
}

// How Bun READS the two options - measured on Bun 1.3.14, 32 spellings each
// (tests/bun_shim_probe.cjs asks the oracle the same 32),
// and the two do not agree with each other:
//   countAnsiEscapeCodes  plain ToBoolean. 1, "no", "0", {}, [] and even
//                         `new Boolean(false)` (an object, therefore truthy)
//                         all turn it ON; only false/undefined/null/0/-0/NaN/
//                         0n/"" leave it off.
//   ambiguousIsNarrow     ToBoolean with undefined, null AND "" pulled back to
//                         the default, so it goes WIDE only for `false` and for
//                         a zero-or-NaN number or bigint. "" is the one that
//                         rules out plain ToBoolean: Bun keeps NARROW for it.
// Both guards used to be spelled `=== false` / `=== true`, which fired on the
// literal boolean and on nothing else - so every other spelling of the same
// intent got answered from the default table instead of refused. Refusing is
// not "matching Bun" on these inputs; the option is still unsupported. It is
// the difference between a wrong number and a named error.
function bunReadsAmbiguousAsWide(v) {
  if (v === false) return true;
  if (typeof v === "number") return v === 0 || v !== v;   // 0, -0 and NaN
  if (typeof v === "bigint") return v === 0n;             // 0n and -0n
  return false;   // undefined, null, "" and everything else: Bun stays narrow
}

function stringWidth(input, options) {
  if (arguments.length === 0 || input === undefined) return 0;  // measured: Bun answers 0
  const s = typeof input === "string" ? input : toStringArg(input);
  // Measured: an empty string is 0 and Bun never touches `options` for it - a
  // throwing getter does not fire. So this has to come before the guards.
  if (s === "") return 0;
  if (options !== undefined && options !== null) {
    // countAnsiEscapeCodes first: that is the order Bun reads them in, measured
    // with two counting getters, and it decides which one a throwing getter
    // reports.
    if (options.countAnsiEscapeCodes) {
      unsupported("stringWidth({countAnsiEscapeCodes:true})",
        "not measured; the bundle never asks for it. Bun turns this on for any " +
        "truthy value, so any truthy value is refused");
    }
    if (bunReadsAmbiguousAsWide(options.ambiguousIsNarrow)) {
      unsupported("stringWidth({ambiguousIsNarrow:false})",
        "only the default (narrow) table was measured against Bun; guessing at " +
        "ambiguous-width characters is a silent layout error. Bun switches to " +
        "the wide table for `false` and for a zero-or-NaN number or bigint, so " +
        "each of those is refused");
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
 *
 * There is no undefined case: measured, Bun.stripANSI() and
 * Bun.stripANSI(undefined) both answer the STRING "undefined". stripANSI
 * coerces its argument where stringWidth short-circuits undefined to 0 - the
 * two really do differ, and returning "" here was a value Bun never returns.
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
  // No undefined special case: measured, Bun.stripANSI() and
  // Bun.stripANSI(undefined) both return the STRING "undefined" - stripANSI
  // coerces where stringWidth short-circuits to 0.
  const s = typeof input === "string" ? input : toStringArg(input);
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
 *
 * REFUSED, not guessed: a non-string, non-binary input (Bun stringifies it -
 * Bun.hash(12345) === Bun.hash("12345"), measured) and every Number seed
 * outside [0, 2^51). Bun's Number seed is not a function of the value alone:
 * measured on Bun 1.3.14, an integer >= 2^51 seeds with 0 (boundary measured
 * exactly: 2^51-1 is used, 2^51 is not), a non-integer and NaN and Infinity
 * seed with 0, and a NEGATIVE integer seeds with 0 when the engine happens to
 * box it as a double but with its two's complement when it boxes it as an
 * int32 - the same value, two answers, in one Bun process (measured:
 * hash("abc", -1) differs depending on whether -1 arrives from an array of
 * doubles or as a plain argument). Nothing here can reproduce that, so those
 * seeds throw. Verified on 601 distinct seeds in [0, 2^51): 0 mismatches. Pass a
 * BigInt for anything larger; BigInt seeds wrap mod 2^64 and are exact.
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
const HASH_SEED_LIMIT = 2251799813685248; // 2^51, measured boundary
function hash(input, seed) {
  let bytes;
  if (typeof input === "string") bytes = UTF8.encode(input);
  else if (ArrayBuffer.isView(input)) bytes = new Uint8Array(input.buffer, input.byteOffset, input.byteLength);
  else if (input instanceof ArrayBuffer) bytes = new Uint8Array(input);
  else unsupported("hash(" + typeof input + ")",
    "Bun stringifies this input; hashing the wrong bytes writes the wrong hash " +
    "into a file, so pass the string you mean");
  let s = 0n;
  if (seed !== undefined) {
    if (typeof seed === "bigint") s = seed & U64;
    else if (typeof seed === "number" && Number.isInteger(seed) && seed >= 0 && seed < HASH_SEED_LIMIT) s = BigInt(seed);
    else unsupported("hash(_, seed)",
      "only a bigint, or a Number integer in [0, 2^51), reproduces Bun. Outside " +
      "that range Bun's own answer depends on how the value is boxed, or is " +
      "silently the unseeded hash - see the note above this function");
  }
  return wyhash(bytes, s);
}

/* ---------------------------------------------------------------- *
 * Bun.which  [IMPLEMENTED]
 *
 * Stands in for: locating helper executables (editors, git, node...). Measured
 * contract, Bun 1.3.14, and every clause here was checked against it:
 *
 *   - the argument is ToString'd (a symbol throws); no argument at all throws
 *     "which: expected 1 argument, got 0".
 *   - a name containing "/" is not looked up: an absolute one is used as
 *     spelled, otherwise it is glued to options.cwd (else process.cwd()) with
 *     exactly one leading "./" removed. NOTHING is normalized - Bun answered
 *     "<dir>/cwd/./myexe" for "././myexe", "<dir>/cwd//myexe" for ".//myexe",
 *     and null for "a/../myexe" because it stats that path literally and "a"
 *     does not exist. nodePath.resolve() used to collapse all three.
 *   - otherwise each options.PATH (else PATH) entry is glued on as
 *     entry + "/" + name, again raw: a trailing slash in the entry really does
 *     come back as "<dir>/bin//exe1", and a relative entry comes back relative
 *     ("./myexe" for PATH ".").
 *   - options.PATH and options.cwd are ToString'd when present, so an array
 *     PATH works (Bun found the binary through one); options itself is ignored
 *     unless it is an object.
 *   - the hit must be a regular file with the execute bit. Not found, a
 *     directory, or a non-executable file gives null.
 *
 * The PATH is the one the PROCESS STARTED WITH, not the current
 * process.env.PATH. Measured: Bun launched with PATH=/usr/sbin still answers
 * /usr/sbin/adduser after `process.env.PATH = "/usr/bin"`, and answers null
 * after `delete process.env.PATH`; launched with no PATH at all (env -i) it
 * answers null for "sh" - there is no built-in fallback search path. So the
 * snapshot below is taken at load, which under `node --require` is before any
 * bundle code can touch the environment. Reading process.env.PATH live was the
 * bug: a daemon or hook that clears it mid-run got null, which is
 * indistinguishable from "the binary is not installed".
 * ---------------------------------------------------------------- */
const LAUNCH_PATH = process.env.PATH;
function isExecutableFile(p) {
  try {
    if (!fs.statSync(p).isFile()) return false;
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch { return false; }
}
function joinCwd(cwd, name) {
  const rest = name.startsWith("./") ? name.slice(2) : name;
  return cwd.endsWith("/") ? cwd + rest : cwd + "/" + rest;
}
function which(command, options) {
  if (arguments.length === 0) throw new Error("which: expected 1 argument, got 0");
  const name = toStringArg(command);
  const opts = options !== null && (typeof options === "object" || typeof options === "function")
    ? options : undefined;
  if (name.includes("/")) {
    const cwd = opts && opts.cwd !== undefined ? toStringArg(opts.cwd) : process.cwd();
    const candidate = name.startsWith("/") ? name : joinCwd(cwd, name);
    return isExecutableFile(candidate) ? candidate : null;
  }
  const raw = opts && opts.PATH !== undefined ? toStringArg(opts.PATH) : LAUNCH_PATH;
  if (!raw) return null;
  for (const dir of raw.split(nodePath.delimiter)) {
    if (dir === "") continue;
    const candidate = dir + "/" + name;
    if (isExecutableFile(candidate)) return candidate;
  }
  return null;
}

/* ---------------------------------------------------------------- *
 * Bun.semver.order  [IMPLEMENTED for the grammar below, THROWS outside it]
 * Bun.semver.satisfies  [THROWS]
 *
 * order stands in for the bundle's version comparisons (is this CLI newer than
 * that one). SemVer 2.0.0 precedence: numeric major/minor/patch, then
 * prerelease, where having one loses to having none, numeric identifiers
 * compare numerically and lose to alphanumeric ones, and build metadata is
 * ignored.
 *
 * satisfies is a whole range grammar (^ ~ || hyphen x-ranges, and prerelease
 * rules that differ between implementations). Approximating it would silently
 * enable or disable features, so it throws instead.
 *
 * ARITY. Fewer than two arguments is `Expected two arguments` - Bun's own
 * message, thrown before either argument is looked at: measured, even
 * order(Symbol()) says that rather than complaining about the symbol. This
 * entry used to answer `Invalid SemVer: undefined`, which blames the input for
 * a mistake in the call, the same defect deepEquals had.
 *
 * COERCION. Bun ToStrings both arguments before parsing either - measured,
 * order("garbage", Symbol()) is the symbol TypeError, not Invalid SemVer for
 * "garbage". order(1, 2) is -1, a {toString(){return "1.0.0"}} object works
 * (toString, not valueOf: an object with both is read through toString),
 * ["1.0.0"] works and ["1","0","0"] does not - it stringifies to "1,0,0" - and
 * a symbol is the same TypeError the other four coercing entries throw.
 *
 * THE SPACE TERMINATOR. Once the version has started a plain U+0020 ends it,
 * and everything after it is ignored. Measured against "1.2.3": "1.2.3 x",
 * "1.2.3  x", "1.2.3 1.2.4", "1.2.3 \t" and "1.2.3 (Claude Code)" are all 0,
 * and "1.2.3-a b" compares as "1.2.3-a". That shape is not academic - it is
 * this artifact's own --version output, `2.1.231 (Claude Code)`, on which this
 * entry used to throw. Other trailing whitespace is NOT a terminator:
 * "1.2.3\t", "1.2.3\n", "1.2.3\r", "1.2.3\v" and "1.2.3\f" each throw under
 * Bun, and a version read from a file or from command output carries exactly
 * that trailing newline, so a .trim() here would answer 0 where Bun refuses to
 * answer at all. Leading whitespace IS skipped, all six characters of it -
 * space, tab, newline, vertical tab, form feed and carriage return.
 *
 * PARTIAL VERSIONS. Bun answers "1" and "1.2", and a missing component sorts
 * ABOVE every concrete one: measured, "1" is above "1.9999.9999", above "1.2"
 * and equal to "1"; "1.2" is above "1.2.3"; "0" is above "0.0.0"; and "1" is
 * below "2". That is the comparison implemented below - a missing component is
 * not zero.
 *
 * WHAT IS REFUSED, AND WHY. Bun's parser is a RANGE parser, and outside the
 * grammar above its answers stop being an order at all. Measured on Bun
 * 1.3.14:
 *   - a string containing ANY code point above U+007F compares 0 against every
 *     version. "1.2.3\u00a0" is 0 against "0.0.0" and 0 against "2.0.0" at the
 *     same time, while those two are not equal to each other; "1.2.3 \u00a0x"
 *     is too, so even a tail the terminator is supposed to discard poisons it.
 *     Every code point in U+0080..U+20FF was tried - all 8320 degenerate. This
 *     is what looks from the outside like "Bun swallows U+00A0 mid-version": it
 *     does not swallow anything, it stops comparing.
 *   - "^1.2.3" is ABOVE "2.0.0", and so is "^0.0.1": a caret makes the whole
 *     thing maximal. "=1.2.3" is 0. "~1.2.3" and ">=1.2.3" throw.
 *   - a wildcard is maximal: "x", "*", "1.x" - and a string of nothing but
 *     spaces - all sort above every real version.
 *   - a trailing tail that is not space-separated is PARSED, not ignored:
 *     "1.2.3junk" is below "1.2.3" (an implicit prerelease), "1.2.3x" and
 *     "1.2.3xx" are 0, "1.2.3xy" is below, and "1.2.30x" is 1.2.30.
 *   - "1.2-rc" is accepted while "1-rc" throws.
 * None of that is an order, so each of them throws here instead.
 *
 * NUMBERS. The components are compared as BigInt, not Number: Bun orders
 * 9007199254740993 above 9007199254740992 (measured, both as a major and as a
 * numeric prerelease identifier) where Number() collapses them to equal. Bun
 * agrees with BigInt on every pair drawn from 0..2^64-1 that was tried (121
 * release pairs, 81 prerelease pairs, 0 mismatches). At 2^64 and beyond it
 * stops making sense - order("18446744073709551616.0.0", "1.0.0") is -1, and a
 * 20-digit prerelease identifier compares ABOVE a 23-digit one - so a
 * component that big is refused here rather than answered.
 * ---------------------------------------------------------------- */
const SEMVER_FULL = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$/;
const SEMVER_PARTIAL = /^v?(\d+)(?:\.(\d+))?$/;
const SEMVER_LEADING_WS = /^[ \t\n\v\f\r]+/;
const SEMVER_NON_ASCII = /[^\u0000-\u007f]/;
const U64_LIMIT = 1n << 64n;
function semverNumber(digits) {
  const n = BigInt(digits);
  if (n >= U64_LIMIT) {
    unsupported("semver.order",
      "the numeric identifier " + digits + " does not fit in 64 bits, and Bun's " +
      "own ordering there is not reproducible (it puts 2^64 below 1)");
  }
  return n;
}
// A missing component - the "0.0" of "1" - is null, and null sorts ABOVE every
// number. Measured: "1" is above "1.9999.9999" and "0" is above "0.0.0".
function semverComponent(x, y) {
  if (x === null) return y === null ? 0 : 1;
  if (y === null) return -1;
  return x === y ? 0 : (x < y ? -1 : 1);
}
function parseSemver(s) {
  if (SEMVER_NON_ASCII.test(s)) {
    unsupported("semver.order",
      "the input contains a code point above U+007F, and Bun does not order " +
      "those - measured, such a string compares 0 against every version, " +
      "0.0.0 and 2.0.0 at the same time");
  }
  // Leading whitespace is skipped; after that the first plain space ends the
  // version and the rest of the string is discarded.
  const trimmed = s.replace(SEMVER_LEADING_WS, "");
  const space = trimmed.indexOf(" ");
  const body = space === -1 ? trimmed : trimmed.slice(0, space);
  const full = SEMVER_FULL.exec(body);
  if (full) {
    return {
      parts: [semverNumber(full[1]), semverNumber(full[2]), semverNumber(full[3])],
      pre: full[4] === undefined ? null : full[4].split("."),
    };
  }
  const partial = SEMVER_PARTIAL.exec(body);
  if (partial) {
    return {
      parts: [semverNumber(partial[1]),
        partial[2] === undefined ? null : semverNumber(partial[2]), null],
      pre: null,
    };
  }
  throw new Error("[nrc-bun-shim] Invalid SemVer: " + s);
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
    if (xn && yn) {
      const nx = semverNumber(x), ny = semverNumber(y);
      if (nx !== ny) return nx < ny ? -1 : 1;
      continue;
    }
    if (xn !== yn) return xn ? -1 : 1;
    if (x !== y) return x < y ? -1 : 1;
  }
  return 0;
}
function semverOrder(a, b) {
  // Arity, not value, and checked before either argument is touched: measured,
  // Bun answers this for order(), order("1.2.3") and order(Symbol()) alike.
  if (arguments.length < 2) throw new Error("Expected two arguments");
  // BOTH arguments are stringified before EITHER is parsed - measured, a bad
  // first argument does not shield a symbol in the second.
  const sa = typeof a === "string" ? a : toStringArg(a);
  const sb = typeof b === "string" ? b : toStringArg(b);
  const x = parseSemver(sa), y = parseSemver(sb);
  for (let i = 0; i < 3; i++) {
    const c = semverComponent(x.parts[i], y.parts[i]);
    if (c !== 0) return c;
  }
  return comparePrerelease(x.pre, y.pre);
}

/* ---------------------------------------------------------------- *
 * Bun.deepEquals  [IMPLEMENTED for JSON values, THROWS otherwise]
 *
 * Stands in for: comparing a settings object against the one already loaded.
 * Bun's default (non-strict) mode is looser than util.isDeepStrictEqual in
 * ways that matter - measured on Bun 1.3.14: {a:undefined} equals {}, [1,
 * undefined] equals [1], a class instance equals a plain object with the same
 * fields, NaN equals NaN, and 0 does not equal -0. isDeepStrictEqual answers
 * the opposite on the first three, so it is not used.
 *
 * Fewer than two arguments is a TypeError, message and all - Bun's arity
 * check, not a value comparison against undefined.
 *
 * This covers exactly what those call sites pass: null, booleans, numbers,
 * strings, arrays and plain objects whose own properties are all enumerable
 * data properties - which is every object JSON.parse can build. Dates, Maps,
 * Sets, RegExps, typed arrays, class instances and cycles all THROW rather
 * than get an approximate answer, and the third (strict) argument is not
 * accepted.
 *
 * WHY THE OWN-PROPERTY SHAPE IS CHECKED, AND NOT GUESSED AT. Bun does not run
 * one algorithm over objects. JSC picks between a fast walk of the object's
 * structure and a generic walk of its property-name list, and the two do not
 * agree - so the answer depends on how the object is REPRESENTED, not only on
 * what it holds. All measured on Bun 1.3.14:
 *
 *   - a non-enumerable property on the right-hand side is read straight
 *     through: deepEquals({a:1}, ne) is TRUE where ne has a non-enumerable
 *     a:1, while deepEquals(ne, {a:1}) is false. Reversing the arguments
 *     changes the answer.
 *   - and that rule does not survive contact with anything else. Over an
 *     exhaustive corpus of 2,401 two-key pairs - each key absent, or present
 *     with one of three values, enumerable or not - "walk the left object's
 *     enumerable keys and read the right one through [[Get]]" is exact on all
 *     784 pairs whose LEFT object has no non-enumerable property, and wrong on
 *     60 of the other 1,617. The rival walk - a property-name list indexed
 *     straight through, so which of the right object's extra keys get checked
 *     depends on how many keys the left one had - is exact on all 1,089 pairs
 *     where BOTH sides carry a non-enumerable property, and wrong on 32 of the
 *     other 1,312. There is no one rule, because there is no one algorithm.
 *   - the same split shows up without any non-enumerable property at all.
 *     deepEquals({x:1}, {y:undefined, x:1}) is true; add an identical
 *     integer-index key to BOTH sides - {x:1,0:7} against {y:undefined,x:1,0:7}
 *     - and the same comparison is false. So is the getter-bearing version.
 *     An index key or an accessor moves JSC off the fast walk, and the slow
 *     one answers by the order the keys were inserted.
 *
 * None of that is reproducible from JavaScript, so this entry refuses the
 * shapes that reach it: any own property that is not an enumerable data
 * property, and an integer-index key in the same comparison as an
 * undefined-valued property. JSON.parse produces neither, which is why the
 * call sites are unaffected; what the refusal replaces is a confident boolean
 * that was measurably the wrong one.
 * ---------------------------------------------------------------- */
function isPlainObject(v) {
  if (typeof v !== "object" || v === null || Array.isArray(v)) return false;
  const proto = Object.getPrototypeOf(v);
  return proto === Object.prototype || proto === null;
}
function definedKeys(o) {
  return Object.keys(o).filter((k) => o[k] !== undefined);
}
// An array-index key in JSC's sense: the canonical spelling of a number below
// 2^32-1. "01" and "1e2" are ordinary string keys and do not count.
const INDEX_KEY = /^(?:0|[1-9][0-9]{0,9})$/;
const isIndexKey = (k) => INDEX_KEY.test(k) && Number(k) < 4294967295;
// The two facts about an object's own properties that Bun's answer turns on.
// Refuses here rather than returning them when the shape is one where Bun
// stops being reproducible; see the block comment above.
function objectShape(o) {
  const names = Object.getOwnPropertyNames(o);
  let index = false, undef = false;
  for (let i = 0; i < names.length; i++) {
    const d = Object.getOwnPropertyDescriptor(o, names[i]);
    if (!d.enumerable || !("value" in d)) {
      unsupported("deepEquals",
        "the property " + JSON.stringify(names[i]) + " is " +
        (d.enumerable ? "an accessor" : "not enumerable") + ", which JSON.parse " +
        "never produces and which moves Bun onto a different comparison " +
        "algorithm - measured, one that answers differently depending on which " +
        "argument holds it");
    }
    if (d.value === undefined) undef = true;
    if (isIndexKey(names[i])) index = true;
  }
  return { index, undef };
}
// null | boolean | number | string | undefined | array | object; anything else
// is a shape these call sites never pass and this function will not guess at.
function jsonKind(v) {
  if (v === null) return "null";
  const t = typeof v;
  if (t === "boolean" || t === "number" || t === "string" || t === "undefined") return t;
  if (Array.isArray(v)) return "array";
  if (isPlainObject(v)) {
    // Object.keys below cannot see symbol keys, but Bun compares them:
    // measured, Bun.deepEquals({[Symbol.for("s")]: 1}, {}) is false and
    // {[s]:1} vs {[s]:2} is false, where ignoring them answers true both
    // times. A symbol key is not a JSON value, so it takes the same exit as
    // every other non-JSON shape instead of a confident wrong boolean.
    if (Object.getOwnPropertySymbols(v).length > 0) {
      unsupported("deepEquals", "an object with symbol keys is not a JSON value, " +
        "and Bun does compare those keys");
    }
    return "object";
  }
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
    // "An undefined property is an absent property" applies to INDICES too, not
    // just to keys - measured on Bun 1.3.14, and the rule is positional rather
    // than a filter: [1, undefined] equals [1] and [1, 2] equals
    // [1, 2, undefined, undefined], while [undefined, 1] does NOT equal [1] and
    // [1, undefined, 2] does not equal [1, 2]. A hole reads as undefined, so
    // new Array(2) equals [] and [1, , 3] equals [1, undefined, 3] but not
    // [1, 3]. Comparing up to the LONGER length, with out-of-range as
    // undefined, is exactly that rule; a length check first is not.
    //
    // Non-index own properties are ignored: measured, [1] with .x = 2 equals
    // [1] and equals [1] with .x = 3. Only indices are compared below.
    const n = a.length > b.length ? a.length : b.length;
    for (let i = 0; i < n; i++) if (!deepEqualsJson(a[i], b[i], depth + 1)) return false;
    return true;
  }
  const shapeA = objectShape(a), shapeB = objectShape(b);
  if ((shapeA.index || shapeB.index) && (shapeA.undef || shapeB.undef)) {
    unsupported("deepEquals",
      "one side carries an integer-index key and one carries a property whose " +
      "value is undefined. Measured, that combination takes Bun off its fast " +
      "comparison and onto one that answers by key insertion order: {x:1,0:7} " +
      "and {y:undefined,x:1,0:7} are NOT equal, while the same pair without the " +
      "0 key is");
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
  // Arity, not value: measured, Bun throws this exact TypeError for
  // deepEquals(), deepEquals({}) and even deepEquals(undefined). Answering
  // `true` to a zero-argument call - which this did - is the plausible wrong
  // value the rule at the top of this file exists to prevent.
  if (arguments.length < 2) throw new TypeError("Expected 2 values to compare");
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


/* ----------------------------------------------------------------
 * Bun.wrapAnsi  [IMPLEMENTED]
 *
 * ANSI-aware line wrapping. The TUI cannot render a frame without it: the
 * bundle calls it as a straight passthrough, `Bun.wrapAnsi(text, cols, opts)`,
 * and a refusal here throws inside a React render where an error boundary
 * swallows it - the process then idles, paints nothing, and never crashes.
 * That failure cost a day of investigation across two machines before a trace
 * named it, which is why this is implemented rather than refused.
 *
 * It is built on TWO STAGES, because that is what the oracle does and a single
 * unified tokenizer cannot express it (measured in the notes below):
 *
 *   1. WORD PLACEMENT measures whole words with the grapheme-aware
 *      stringWidth above. A ZWJ emoji family is width 2 and survives whole at
 *      columns 2, even though it is five code points.
 *   2. BREAKING an over-long word walks CODE POINTS with a per-code-point
 *      width. That same family becomes one member per row. Two different width
 *      machineries, deliberately.
 *   3. ESCAPE STATE is re-emitted at join time, per input line: every row is
 *      opened with what was in force and closed at its end, and state does not
 *      cross a newline in the input.
 *
 * The first implementation used one cluster tokenizer for both stages. That
 * mismatch - not any single missing rule - is what produced defect after
 * defect: thirteen found by review, then more by the generative differential,
 * each "fixed" by a patch that the next case invalidated.
 *
 * ⚠️ NOT byte-equal to Bun everywhere. tests/test_fuzz_differential.py pins the
 * remaining gap as a count that may only fall, and reports it per seed.
 * Unlike YAML.parse below, this function cannot refuse, so its contract is
 * enforceable only by comparison - never by a check in the code.
 * ---------------------------------------------------------------- */

const wrap_ESC = "\u001b";
const wrap_BEL = "\u0007";

// --- scanning ---------------------------------------------------------------

// Length of the escape sequence starting at i, or 0 if there is none.
function wrap_escapeLength(text, i) {
  if (text[i] !== wrap_ESC) return 0;
  const next = text[i + 1];
  if (next === "[") {
    let j = i + 2;
    while (j < text.length && text.charCodeAt(j) >= 0x20 && text.charCodeAt(j) <= 0x3f) j++;
    return j < text.length ? j - i + 1 : text.length - i;
  }
  if (next === "]") {
    let j = i + 2;
    while (j < text.length) {
      if (text[j] === wrap_BEL) return j - i + 1;
      if (text[j] === wrap_ESC && text[j + 1] === "\\") return j - i + 2;
      j++;
    }
    return text.length - i;
  }
  return next === undefined ? 1 : 2;
}

// --- escape state -----------------------------------------------------------

const wrap_CLOSERS = new Map([
  [1, 22], [2, 22], [3, 23], [4, 24], [5, 25], [7, 27], [8, 28], [9, 29], [53, 55],
]);

function wrap_closerFor(code) {
  if (wrap_CLOSERS.has(code)) return wrap_CLOSERS.get(code);
  if ((code >= 30 && code <= 38) || (code >= 90 && code <= 97)) return 39;
  if ((code >= 40 && code <= 48) || (code >= 100 && code <= 107)) return 49;
  return null;
}

// --- the word splitter ------------------------------------------------------
//
// A "word" is a run between single spaces. Escapes belong to the word they
// precede; the space itself is a separator, not part of either side.

function wrap_splitWords(line) {
  const words = [];
  let current = "";
  let i = 0;
  while (i < line.length) {
    const esc = wrap_escapeLength(line, i);
    if (esc) { current += line.slice(i, i + esc); i += esc; continue; }
    if (line[i] === " ") { words.push(current); current = ""; i++; continue; }
    const cp = String.fromCodePoint(line.codePointAt(i));
    current += cp;
    i += cp.length;
  }
  words.push(current);
  return words;
}

// Visible width of a string, ignoring escapes.
function wrap_visibleWidth(text) {
  let out = "";
  let i = 0;
  while (i < text.length) {
    const esc = wrap_escapeLength(text, i);
    if (esc) { i += esc; continue; }
    const cp = String.fromCodePoint(text.codePointAt(i));
    out += cp;
    i += cp.length;
  }
  return stringWidth(out);
}

// --- breaking one over-long word --------------------------------------------
//
// Stage two: walk code points, not clusters. `rows` is mutated in place; the
// caller has already placed whatever precedes this word.

// Width of the first visible code point at or after `from`, or 0 if there is
// none. Escapes are skipped; they place no glyph.
function wrap_nextVisibleWidth(word, from) {
  let i = from;
  while (i < word.length) {
    const esc = wrap_escapeLength(word, i);
    if (esc) { i += esc; continue; }
    const cp = String.fromCodePoint(word.codePointAt(i));
    const w = stringWidth(cp);
    if (w > 0) return w;
    i += cp.length;
  }
  return 0;
}

// Width as the BREAKER counts it: the sum of each code point's own width,
// which is not the cluster width. Measured, they disagree on every emoji
// cluster - a rainbow flag is 2 as a cluster but 3 summed, a wave with a skin
// tone is 2 versus 4. Stage one places whole words by cluster width; stage two
// walks code points and must count them the same way it walks them, or a row
// that looks full to one stage looks roomy to the other.
// A combining mark measures ZERO when the engine walks code points, whatever
// the width table says about the mark on its own. The table is not wrong:
// asked in isolation, Bun answers 2 for the enclosing keycap U+20E3, and we
// match it. But the walk does not charge the row for it.
//
// Measured: "1<VS16><KEYCAP>xy" fits on ONE row at width 3 - four visible
// columns in three - while "<CJK>xy" does not, so the keycap's two columns
// cost the row nothing and the ideograph's two cost it everything. That is why
// "1<VS16><KEYCAP>" survives whole at width 1 where the rainbow flag splits:
// the flag's second half is a BASE reached through a joiner, not a mark.
//
// Only Mn and Me are marks here. Skin-tone modifiers are Sk, and measured,
// they do cost their width - thumbs-up and its modifier land on separate rows
// at width 1. A rule that zeroed every cluster continuation would merge them.
const wrap_COMBINING_MARK = /^[\p{Mn}\p{Me}]$/u;

function wrap_pointCellWidth(cp) {
  return wrap_COMBINING_MARK.test(cp) ? 0 : stringWidth(cp);
}

function wrap_pointWidth(text) {
  let total = 0;
  let i = 0;
  while (i < text.length) {
    const esc = wrap_escapeLength(text, i);
    if (esc) { i += esc; continue; }
    const cp = String.fromCodePoint(text.codePointAt(i));
    total += wrap_pointCellWidth(cp);
    i += cp.length;
  }
  return total;
}

function wrap_breakWord(rows, word, columns) {
  // An ST-terminated OSC makes everything after it in this word unbreakable -
  // including text past the link's close. Measured: a linked word plus trailing
  // text stays on one row, while a following SEPARATE word wraps normally, so
  // the state is per word rather than per line.
  // The glue starts AT the ST-terminated link, not at the start of the word:
  // measured, halfwidth kana before such a link still breaks per character
  // while everything from the link onward stays on one row. An early return
  // for the whole word - which is what this did first - glued the prefix too.
  let glueFrom = -1;
  for (let scan = 0; scan < word.length; ) {
    const esc = wrap_escapeLength(word, scan);
    if (!esc) { scan += String.fromCodePoint(word.codePointAt(scan)).length; continue; }
    const text = word.slice(scan, scan + esc);
    if (text.startsWith(wrap_ESC + "]") && text.endsWith(wrap_ESC + "\\")) {
      glueFrom = scan;
      break;
    }
    scan += esc;
  }
  if (glueFrom === 0) {
    rows[rows.length - 1] += word;
    return;
  }
  if (glueFrom > 0) {
    wrap_breakWord(rows, word.slice(0, glueFrom), columns);
    rows[rows.length - 1] += word.slice(glueFrom);
    return;
  }

  let i = 0;
  let pending = "";
  let taken = null;
  while (i < word.length) {
    const esc = wrap_escapeLength(word, i);
    if (esc) {
      // Held back, not emitted: an escape at a break belongs to the row that
      // follows it. Measured at width 1, where the sequence opening a word
      // leads the next row instead of trailing the previous one.
      pending += word.slice(i, i + esc);
      i += esc;
      continue;
    }
    const cp = String.fromCodePoint(word.codePointAt(i));
    const w = wrap_pointCellWidth(cp);
    // A running counter, NOT a re-measurement. Whatever stage one placed on
    // this row was measured by cluster; every code point stage two adds is
    // measured on its own. Re-measuring the row string picks one rule for both
    // and gets the other case wrong.
    if (taken === null) taken = wrap_visibleWidth(rows[rows.length - 1]);

    // A zero-width code point with nothing visible after it stays where it is:
    // measured, a trailing tab ends the row it is on rather than opening one.
    const rest = word.slice(i + cp.length);
    const nothingVisibleAfter = w === 0 && wrap_pointWidth(rest) === 0;

    // Two INDEPENDENT reasons to break, and both can fire for one code point.
    // A row that is exactly full yields to whatever comes next; a glyph too
    // wide for what remains breaks again. Written as `else if` - as the first
    // draft of this engine had it - a wide glyph following a full row loses the
    // empty row Bun puts between them.
    if (!nothingVisibleAfter) {
      if (taken === columns) { rows.push(""); taken = 0; }
      if (w > columns - taken) {
        // A held-back escape is emitted BEFORE the break it precedes, in two
        // cases. Measured at width 1: an SGR followed by a wide character
        // leaves the SGR on the first row rather than opening with a blank
        // one. And a link's CLOSE stays on the row the link occupies - held
        // past the break it would re-open a link that has already ended, on a
        // row that carries nothing.
        const closesLink = pending.includes(wrap_ESC + "]8;;" + wrap_BEL) ||
          pending.includes(wrap_ESC + "]8;;" + wrap_ESC + "\\");
        if (pending !== "" && (taken === 0 || taken >= columns || closesLink)) {
          rows[rows.length - 1] += pending;
          pending = "";
        }
        rows.push("");
        taken = 0;
      }
    }

    // NOTE: zero-width code points are NOT discarded here. They land on their
    // own row, and it is TRIMMING that empties that row afterwards - measured
    // by varying only the options: with trim:false the flag's variation
    // selector and joiner are visible on their own row, with trim:true the row
    // is empty. Deleting them in the breaker got the trim:true case right for
    // the wrong reason and the trim:false case wrong.

    rows[rows.length - 1] += pending + cp;
    pending = "";
    taken += w;
    i += cp.length;
  }
  rows[rows.length - 1] += pending;
}

// --- wrapping one line ------------------------------------------------------

function wrap_wrapLine(rawLine, columns, opts) {
  let line = rawLine;
  // A line made only of spaces and tabs collapses to one empty row when
  // trimming. NOT JS trim(): that counts NBSP as whitespace, and measured, a
  // lone NBSP survives (it is width 1 and prints) while a lone space does not.
  if (opts.trim !== false && /^[ \t]*$/.test(line)) return [""];

  const words = wrap_splitWords(line);
  const rows = [""];

  for (let index = 0; index < words.length; index++) {
    const word = words[index];

    // Leading whitespace is stripped from the CURRENT ROW once per word
    // iteration - not once per line. That timing is the whole rule: " \tx"
    // keeps its tab because no later iteration runs, while " \t x" loses it
    // because the "x" iteration strips the row holding it. Seven measured
    // shapes follow from this with no special cases.
    //
    // Spaces and tabs only, NOT JS trimStart(): that also removes NBSP, which
    // Bun keeps because it is width 1 and prints. ESC is not stripped either,
    // so a leading escape shelters what follows it.
    if (opts.trim !== false) {
      // Leading ESCAPES do not shelter the whitespace behind them. Measured:
      // an SGR followed by " \t " loses all three, exactly as the bare form
      // does, so the strip has to step over the escapes first rather than
      // anchor at the row's very start.
      const row = rows[rows.length - 1];
      let head = 0;
      for (;;) {
        const esc = wrap_escapeLength(row, head);
        if (!esc) break;
        head += esc;
      }
      rows[rows.length - 1] =
        row.slice(0, head) + row.slice(head).replace(/^[ \t]+/, "");
    }

    const wordWidth = wrap_visibleWidth(word);
    let taken = wrap_visibleWidth(rows[rows.length - 1]);

    if (index !== 0) {
      // The separator space, unless the row is empty and we are trimming.
      if (taken >= columns && (opts.wordWrap === false || opts.trim === false)) {
        rows.push("");
        taken = 0;
      }
      if (taken > 0 || opts.trim === false) {
        rows[rows.length - 1] += " ";
        taken += 1;
      }
    }

    if (opts.hard && wordWidth > columns) {
      const remaining = columns - taken;
      const breaksHere = 1 + Math.floor((wordWidth - remaining - 1) / columns);
      const breaksNext = Math.floor((wordWidth - 1) / columns);
      if (breaksNext < breaksHere) rows.push("");
      wrap_breakWord(rows, word, columns);
      continue;
    }

    if (taken + wordWidth > columns) {
      if (opts.wordWrap === false && taken < columns) {
        wrap_breakWord(rows, word, columns);
        continue;
      }
      if (taken > 0 && wordWidth > 0) rows.push("");
      if (opts.wordWrap === false) {
        wrap_breakWord(rows, word, columns);
        continue;
      }
    }

    if (taken + wordWidth > columns && opts.wordWrap === false) {
      wrap_breakWord(rows, word, columns);
      continue;
    }

    rows[rows.length - 1] += word;
  }

  if (opts.trim !== false) {
    for (let i = 0; i < rows.length; i++) rows[i] = wrap_trimRow(rows[i]);
  }

  return rows;
}

// Remove spaces at the row's edges, keeping escapes. A trailing escape does not
// shelter the spaces in front of it.
function wrap_trimRow(row) {
  // Leading whitespace is handled in the word loop, per iteration. Only the
  // trailing side is left here.
  const head = "";
  let body = row;

  let tail = "";
  for (;;) {
    let cut = body.length;
    let scan = 0;
    let lastEscStart = -1;
    while (scan < body.length) {
      const esc = wrap_escapeLength(body, scan);
      if (esc) { lastEscStart = scan; scan += esc; continue; }
      lastEscStart = -1;
      scan += String.fromCodePoint(body.codePointAt(scan)).length;
    }
    if (lastEscStart >= 0 && lastEscStart + wrap_escapeLength(body, lastEscStart) === body.length) {
      tail = body.slice(lastEscStart) + tail;
      body = body.slice(0, lastEscStart);
      continue;
    }
    // Trailing mirrors leading: a run that BEGINS with a space goes, tabs
    // inside it included, while a tab not preceded by a space survives.
    // Measured on "- \t ", "ab\t", "ab \t", "ab\t ", "ab\t\t", "ab  ",
    // "ab \t\t", "ab\t \t". Applied per ROW, not per line: stripping the
    // line first removed the separators that produce an empty final row at
    // narrow widths.
    const stripped = body.replace(/ [ \t]*$/, "");
    if (stripped !== body) { body = stripped; continue; }
    // A trailing tab goes when the row holds no VISIBLE character - escapes do
    // not count as visible. Measured: "\u001b[9m\t" trims to "\u001b[9m",
    // while "\u001b[9mx\t" and "a\u001b[9m\t" keep the tab because something
    // printable precedes it.
    if (body.endsWith("\t") && wrap_visibleWidth(body) === 0) {
      body = body.slice(0, -1);
      continue;
    }
    // A row of zero-width code points - joiners, variation selectors,
    // combining marks - trims to nothing. They print no glyph, so trimming
    // treats the row as blank. Escapes are kept: they are state, not content.
    if (body !== "" && wrap_pointWidth(body) === 0 && !/\u001b/.test(body)) {
      body = "";
      continue;
    }
    cut = body.length;
    break;
  }
  return head + body + tail;
}

// --- join-time escape pass --------------------------------------------------

function wrap_render(rows) {
  let code;
  let link = null;
  const out = [];

  for (let r = 0; r < rows.length; r++) {
    const opener = (code === undefined ? "" : wrap_ESC + "[" + code + "m") + (link || "");
    const row = rows[r];

    // Update state from every escape this row contains.
    let i = 0;
    while (i < row.length) {
      const esc = wrap_escapeLength(row, i);
      if (!esc) { i += String.fromCodePoint(row.codePointAt(i)).length; continue; }
      const text = row.slice(i, i + esc);
      if (text.startsWith(wrap_ESC + "]8;")) {
        const uri = text.slice(4).split(";").slice(1).join(";")
          .replace(/(|\\)$/, "");
        link = uri ? wrap_ESC + "]8;;" + uri + wrap_BEL : null;
      } else if (text.startsWith(wrap_ESC + "[") && text.endsWith("m")) {
        const params = text.slice(2, -1).split(";");
        if (params.length === 1) {
          const n = Number(params[0] === "" ? 0 : params[0]);
          if (!Number.isNaN(n)) code = (n === 0 || n === 39) ? undefined : n;
        }
      }
      i += esc;
    }

    let closer = "";
    if (r !== rows.length - 1) {
      // Link close FIRST, then the SGR closer. Measured with both in force at
      // once: the row ends with the link's close followed by the colour reset,
      // and the reverse order was what this emitted. The opener at row start
      // uses the opposite order - SGR then link - so the two are not simply
      // mirrored.
      if (link) closer += wrap_ESC + "]8;;" + wrap_BEL;
      if (code !== undefined) {
        const c = wrap_closerFor(code);
        if (c !== null && c !== code) closer += wrap_ESC + "[" + c + "m";
      }
    }
    out.push(opener + row + closer);
  }
  return out.join("\n");
}

function wrapAnsi(string, columns, options) {
  const opts = Object.assign({ hard: false, trim: true, wordWrap: true }, options || {});
  const text = String(string);
  if (!(columns > 0)) return text;

  // Render per input line, with fresh escape state each time. Carrying state
  // across a newline added a closer to a row Bun leaves alone and reopened it
  // on the next; measured on a lone SGR sequence followed by a newline, which
  // Bun returns byte for byte.
  return text.replace(/\r\n?/g, "\n").split("\n")
    .map((line) => wrap_render(wrap_wrapLine(line, columns, opts)))
    .join("\n");
}

/* ----------------------------------------------------------------
 * Bun.YAML.parse  [IMPLEMENTED, for a measured subset]
 *
 * Skill, agent and command frontmatter. Node ships no YAML parser and this
 * file ships no dependency, so this is a parser written against Bun 1.3.14 as
 * the oracle over 150 probes - not a port of anything, and not a guess.
 *
 * The contract is deliberately narrower than YAML: every input it ACCEPTS
 * produces exactly what Bun produces, and everything else THROWS. A wrong
 * parse is somebody's skill silently misconfigured; a refusal is a message
 * naming what is unsupported. Measured over the curated corpus: 141 match Bun
 * exactly, 18 refuse, 0 differ - and over 6,000 generated inputs across three
 * seeds, 0 differ.
 *
 * Refused on purpose, each because matching Bun could not be verified:
 * anchors and aliases, tags, explicit complex keys (`? `), more than one
 * document in a string, tabs used for indentation, and explicit block scalar
 * indent indicators (`|2`). Frontmatter using any of those will not load, and
 * will say which one.
 *
 * The typing rules are YAML 1.2 core schema as Bun implements it, and several
 * read like bugs unless you know they were measured:
 *   - `yes` / `no` / `on` / `off` stay STRINGS; only true/True/TRUE are boolean
 *   - `.inf` and `.nan` come back as null, not Infinity and NaN
 *   - `.5` is 0.5 but `+.5` is the string "+.5"
 *   - `0x10` is 16, `0o17` is 15, `007` is 7, but `0b101` and `1_000` are strings
 *   - `2026-08-26` and `12:30` stay strings
 *   - `a#b` keeps the hash; `a #b` treats it as a comment
 *   - a plain scalar containing ': ' is a parse error, but `12:30` is fine
 *
 * tests/test_yaml_parse.py re-runs the whole corpus with Bun as the oracle.
 * ---------------------------------------------------------------- */

const yaml_refuse = (why) => unsupported("YAML.parse", why);

// --- scalar typing ---------------------------------------------------------
//
// YAML 1.2 core schema as Bun implements it. Every branch below was measured;
// the surprising ones are marked, because they read like bugs otherwise.

const yaml_NULLS = /^(null|Null|NULL|~)$/;
const yaml_TRUES = /^(true|True|TRUE)$/;
const yaml_FALSES = /^(false|False|FALSE)$/;
// Bun answers Infinity/-Infinity/NaN here. An earlier version of this file
// claimed it answered null "measured" - it did not: the probe compared two
// JSON.stringify outputs, and JSON.stringify maps Infinity and NaN to null, so
// the channel destroyed the value before the comparison saw it. The corpus
// probe now encodes types explicitly for exactly this reason.
const yaml_INF = /^[+-]?\.inf$/i;
const yaml_NAN = /^[+-]?\.nan$/i;
const yaml_HEX = /^[+-]?0x[0-9a-fA-F]+$/;
const yaml_OCTAL = /^[+-]?0o[0-7]+$/;
const yaml_INTEGER = /^[+-]?[0-9]+$/;
// A sign is allowed before a digit-led float but NOT before a bare `.5`:
// measured, `.5` is 0.5 and `+.5` is the string "+.5".
const yaml_FLOAT_SIGNED = /^[+-]?[0-9]+\.[0-9]*([eE][+-]?[0-9]+)?$/;
const yaml_FLOAT_BARE = /^\.[0-9]+([eE][+-]?[0-9]+)?$/;
const yaml_EXPONENT = /^[+-]?[0-9]+[eE][+-]?[0-9]+$/;

function yaml_typeScalar(text) {
  if (text === "") return null;
  if (yaml_NULLS.test(text)) return null;
  if (yaml_TRUES.test(text)) return true;
  if (yaml_FALSES.test(text)) return false;
  if (yaml_INF.test(text)) return text[0] === "-" ? -Infinity : Infinity;
  if (yaml_NAN.test(text)) return NaN;
  // parseInt keeps the leading sign once "0x"/"0o" is removed, so the sign must
  // NOT be applied a second time: "-0x10" -> "-10" -> parseInt(...,16) is -16.
  if (yaml_HEX.test(text)) return parseInt(text.replace("0x", ""), 16);
  if (yaml_OCTAL.test(text)) return parseInt(text.replace("0o", ""), 8);
  if (yaml_INTEGER.test(text) || yaml_FLOAT_SIGNED.test(text) || yaml_FLOAT_BARE.test(text) || yaml_EXPONENT.test(text)) {
    return Number(text); // -0 stays -0: measured with Object.is, not JSON
  }
  return text;
}

// --- quoted scalars --------------------------------------------------------

const yaml_DQ_ESCAPES = {
  "0": "\0", a: "\x07", b: "\b", t: "\t", n: "\n", v: "\v", f: "\f",
  r: "\r", e: "", " ": " ", '"': '"', "/": "/", "\\": "\\",
  N: "", _: " ", L: " ", P: " ",
};

function yaml_readQuoted(s, i) {
  const quote = s[i];
  let out = "";
  let j = i + 1;
  while (j < s.length) {
    const ch = s[j];
    if (quote === "'") {
      if (ch === "'") {
        if (s[j + 1] === "'") { out += "'"; j += 2; continue; }
        return [out, j + 1];
      }
      out += ch;
      j++;
      continue;
    }
    if (ch === "\\") {
      const esc = s[j + 1];
      if (esc === "x" || esc === "u" || esc === "U") {
        const len = esc === "x" ? 2 : esc === "u" ? 4 : 8;
        const hex = s.slice(j + 2, j + 2 + len);
        if (!/^[0-9a-fA-F]+$/.test(hex) || hex.length !== len) yaml_refuse("a malformed \\" + esc + " escape");
        out += String.fromCodePoint(parseInt(hex, 16));
        j += 2 + len;
        continue;
      }
      if (!(esc in yaml_DQ_ESCAPES)) yaml_refuse("an unsupported escape \\" + esc);
      out += yaml_DQ_ESCAPES[esc];
      j += 2;
      continue;
    }
    if (ch === '"') return [out, j + 1];
    out += ch;
    j++;
  }
  yaml_refuse("an unterminated quoted scalar");
}

// --- flow collections ------------------------------------------------------

function yaml_parseFlow(s, i) {
  const open = s[i];
  const isSeq = open === "[";
  const close = isSeq ? "]" : "}";
  const items = isSeq ? [] : {};
  let j = i + 1;

  for (;;) {
    while (j < s.length && /\s/.test(s[j])) j++;
    if (j >= s.length) yaml_refuse("an unterminated flow collection");
    if (s[j] === close) return [items, j + 1];

    let value;
    let key = null;
    let hasValue = true;
    if (!isSeq) {
      // Bun stringifies a collection used as a key ("[object Object]", "1").
      // That is a shape worth refusing rather than reproducing.
      if (s[j] === "[" || s[j] === "{") yaml_refuse("a flow collection used as a key");
      [key, j] = yaml_readFlowScalarOrNested(s, j);
      while (j < s.length && /\s/.test(s[j])) j++;
      if (s[j] === ":") {
        j++;
        while (j < s.length && /\s/.test(s[j])) j++;
      } else {
        // No colon means the whole entry was the key, and its value is null.
        hasValue = false;
        value = null;
      }
    }
    if (hasValue) [value, j] = yaml_readFlowScalarOrNested(s, j);
    if (isSeq) items.push(value);
    else items[String(key)] = value;

    while (j < s.length && /\s/.test(s[j])) j++;
    if (s[j] === ",") { j++; continue; }
    if (s[j] === close) return [items, j + 1];
    yaml_refuse("a flow collection separator that is neither ',' nor '" + close + "'");
  }
}

function yaml_readFlowScalarOrNested(s, j) {
  if (s[j] === "[" || s[j] === "{") return yaml_parseFlow(s, j);
  if (s[j] === '"' || s[j] === "'") {
    const [text, next] = yaml_readQuoted(s, j);
    return [text, next];
  }
  let end = j;
  while (end < s.length) {
    const ch = s[end];
    if (ch === "," || ch === "]" || ch === "}") break;
    // In flow context a colon only ends a plain scalar when a space or a
    // terminator follows it. Measured: `{a:1}` is the single key "a:1" with a
    // null value, while `{a: 1}` is the mapping a -> 1, and `{a: b:c}` has the
    // value "b:c". Breaking on every colon merged those three into one wrong
    // shape.
    if (ch === ":" && (end + 1 >= s.length || /[\s,\]}]/.test(s[end + 1]))) break;
    end++;
  }
  const raw = s.slice(j, end).trim();
  if (raw === "") yaml_refuse("an empty flow entry");
  return [yaml_typeScalar(raw), end];
}

// --- line handling ---------------------------------------------------------

function yaml_stripComment(line) {
  let inQuote = null;
  // A quote only opens a quoted scalar where a value or key may begin: at the
  // start of the line, or after ": " or "- ". Anywhere else - the apostrophe in
  // "Don't" - it is an ordinary character. Treating every quote as an opener
  // silently swallowed the rest of the line, comment included, into the value.
  let canOpen = true;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (inQuote) {
      if (ch === inQuote) {
        if (inQuote === "'" && line[i + 1] === "'") { i++; continue; }
        inQuote = null;
      } else if (inQuote === '"' && ch === "\\") i++;
      continue;
    }
    if ((ch === '"' || ch === "'") && canOpen) { inQuote = ch; continue; }
    if (!/\s/.test(ch)) {
      canOpen = ch === ":" || ch === "-";
    }
    // Measured: `a#b` keeps the hash, `a #b` does not. A comment needs
    // whitespace before it, or the start of the line.
    if (ch === "#" && (i === 0 || /\s/.test(line[i - 1]))) return line.slice(0, i);
  }
  return line;
}

const yaml_RESERVED_START = /^[@`%]/;

function yaml_checkUnsupportedMarkers(raw) {
  const t = raw.trim();
  if (t.startsWith("&") || t.startsWith("*")) yaml_refuse("anchors and aliases");
  if (t.startsWith("!")) yaml_refuse("tags");
  if (t.startsWith("? ")) yaml_refuse("explicit complex keys");
  if (t === "...") yaml_refuse("an explicit document end marker");
}

// --- block parsing ---------------------------------------------------------

function yaml_indentOf(line) {
  let n = 0;
  while (n < line.length && line[n] === " ") n++;
  if (line[n] === "\t") yaml_refuse("a tab used for indentation");
  return n;
}

function yaml_isBlank(line) { return line.trim() === ""; }

const yaml_KEY_RE = /^(?:("(?:[^"\\]|\\.)*")|('(?:[^']|'')*')|([^:\n]*?))\s*:(\s|$)/;

function yaml_parseKey(content) {
  const m = yaml_KEY_RE.exec(content);
  if (!m) return null;
  let key;
  if (m[1] !== undefined) key = yaml_readQuoted(m[1], 0)[0];
  else if (m[2] !== undefined) key = yaml_readQuoted(m[2], 0)[0];
  else {
    key = m[3].trim();
    if (key === "") return null;
    if (key.includes(": ")) yaml_refuse("a plain key containing ': '");
    // `{a` and `[a` are not keys - they are the start of a flow collection
    // that happens to contain a colon. Treating them as plain keys turned
    // `{a: 1}` into {"{a": "1}"}, which is a wrong answer rather than a
    // refusal. The caller handles the flow case; this just declines to claim
    // the text is a mapping key.
    if (/^[{[]/.test(key)) return null;
    // A quote that opens but never closes is a parse error in Bun, and a key
    // with a quoted section followed by bare text is too.
    if (/^["']/.test(key)) yaml_refuse("a key that starts with a quote but is not a quoted scalar");
  }
  return { key: String(key), rest: content.slice(m[0].length - (m[4] === "\n" ? 1 : m[4].length)).trim(), consumed: m[0].length };
}

function yaml_readBlockScalar(lines, start, header, parentIndent) {
  const style = header[0];
  const rest = header.slice(1);
  let chomp = "clip";
  let explicit = 0;
  for (const ch of rest) {
    if (ch === "-") chomp = "strip";
    else if (ch === "+") chomp = "keep";
    else if (/[1-9]/.test(ch)) yaml_refuse("an explicit block scalar indent indicator");
    else if (/\s/.test(ch)) {
      // Bun rejects `| junk`; silently ignoring the junk produced a value for
      // a document Bun refuses to parse at all.
      if (rest.slice(rest.indexOf(ch)).trim() !== "") {
        yaml_refuse("trailing text after a block scalar header");
      }
      break;
    }
    else yaml_refuse("an unsupported block scalar header '" + header + "'");
  }

  let i = start;
  const body = [];
  let baseIndent = explicit ? parentIndent + explicit : 0;
  while (i < lines.length) {
    const line = lines[i];
    if (yaml_isBlank(line)) { body.push(""); i++; continue; }
    const ind = yaml_indentOf(line);
    if (ind <= parentIndent) break;
    if (!baseIndent) baseIndent = ind;
    if (ind < baseIndent) break;
    body.push(line.slice(baseIndent));
    i++;
  }
  let trailingBlanks = 0;
  while (body.length && body[body.length - 1] === "") { body.pop(); trailingBlanks++; }
  // An empty block scalar is a parse error in Bun, not an empty string.
  if (body.length === 0) yaml_refuse("a block scalar with no body");

  let text;
  if (style === "|") {
    text = body.join("\n");
    if (body.length) text += "\n";
  } else {
    // A line indented further than the block's base indent is NOT folded - it
    // keeps its own line and its extra indent. Folding it produced a different
    // string from Bun's while still accepting the input.
    const folded = [];
    let previousWasLiteral = false;
    for (const line of body) {
      if (line === "") { folded.push("\n"); previousWasLiteral = false; continue; }
      // Indentation is SPACES, not \s. JavaScript's \s matches NBSP,
      // ideographic space and friends, so a folded line merely starting
      // with one of those was mistaken for a more-indented line and kept
      // literal instead of folded.
      const literal = line.startsWith(" ");
      if (folded.length) {
        if (literal || previousWasLiteral) folded.push("\n");
        else if (folded[folded.length - 1] !== "\n") folded.push(" ");
      }
      folded.push(line);
      previousWasLiteral = literal;
    }
    text = folded.join("");
    if (body.length) text += "\n";
  }

  if (chomp === "strip") text = text.replace(/\n+$/, "");
  if (chomp === "keep") text += "\n".repeat(trailingBlanks);
  return [text, i];
}

function yaml_parseNode(lines, start, indent) {
  let i = start;
  while (i < lines.length && (yaml_isBlank(lines[i]) || yaml_stripComment(lines[i]).trim() === "")) i++;
  if (i >= lines.length) return [null, i];

  const ind = yaml_indentOf(lines[i]);
  if (ind < indent) return [null, i];

  const first = yaml_stripComment(lines[i]).trim();
  yaml_checkUnsupportedMarkers(first);

  if (first === "-" || first.startsWith("- ")) return yaml_parseSequence(lines, i, ind);
  return yaml_parseMapping(lines, i, ind);
}

function yaml_parseSequence(lines, start, indent) {
  const out = [];
  let i = start;
  while (i < lines.length) {
    const raw = lines[i];
    if (yaml_isBlank(raw) || yaml_stripComment(raw).trim() === "") { i++; continue; }
    const ind = yaml_indentOf(raw);
    if (ind < indent) break;
    if (ind > indent) yaml_refuse("an over-indented sequence entry");
    const content = yaml_stripComment(raw).trim();
    if (content !== "-" && !content.startsWith("- ")) break;
    yaml_checkUnsupportedMarkers(content);

    const inline = content === "-" ? "" : content.slice(2).trim();
    const childIndent = indent + (content === "-" ? 2 : raw.indexOf("-") - indent + 2);

    if (inline === "") {
      // An empty sequence entry takes the following node as its value - but
      // Bun captures that node even when it is OUTDENTED, swallowing what
      // reads like a sibling key at a lower level. Measured, and it interacts
      // with duplicate-key merging in ways that change which key wins.
      //
      // Matching that would mean inferring how far the capture extends, in a
      // corner no frontmatter file contains. Refuse instead: the contract
      // prefers a named refusal over a guess, and this converts every observed
      // wrong answer in this shape into one.
      let peek = i + 1;
      while (peek < lines.length &&
             (yaml_isBlank(lines[peek]) || yaml_stripComment(lines[peek]).trim() === "")) {
        peek++;
      }
      if (peek < lines.length && yaml_indentOf(lines[peek]) <= indent) {
        yaml_refuse("an empty sequence entry followed by outdented content");
      }
      const [child, next] = yaml_parseNode(lines, i + 1, indent + 1);
      out.push(child);
      i = next;
      continue;
    }
    // A sequence entry's content is re-read as if it were indented to line up
    // after the dash. That needs one line rewritten, not a copy of the whole
    // document: an earlier version did `lines.slice()` per entry, which made a
    // block sequence of mappings quadratic - 40k lines took 7s where Bun takes
    // 21ms. The recursive call has fully returned by the time the original is
    // put back, so nothing observes the temporary value.
    const restoreIndex = i;
    const restore = lines[restoreIndex];
    lines[restoreIndex] = " ".repeat(childIndent) + inline;
    try {
      // `- - 1` opens a nested sequence, indented to line up after the dash.
      if (inline === "-" || inline.startsWith("- ")) {
        const [child, next] = yaml_parseSequence(lines, i, childIndent);
        out.push(child);
        i = next;
        continue;
      }
      if (yaml_parseKey(inline)) {
        // `- a: 1` starts a mapping whose remaining keys line up with the text
        // after the dash.
        const [child, next] = yaml_parseMapping(lines, i, childIndent);
        out.push(child);
        i = next;
        continue;
      }
    } finally {
      lines[restoreIndex] = restore;
    }
    out.push(yaml_scalarValue(inline, lines, i, indent)[0]);
    i++;
  }
  return [out, i];
}

function yaml_scalarValue(text, lines, i, indent) {
  if (text === "") return [null, i + 1];
  if (text[0] === "[" || text[0] === "{") {
    const [value, end] = yaml_parseFlow(text, 0);
    if (text.slice(end).trim() !== "") yaml_refuse("trailing content after a flow collection");
    return [value, i + 1];
  }
  if (text[0] === '"' || text[0] === "'") {
    const [value, end] = yaml_readQuoted(text, 0);
    if (text.slice(end).trim() !== "") yaml_refuse("trailing content after a quoted scalar");
    return [value, i + 1];
  }
  if (yaml_RESERVED_START.test(text)) yaml_refuse("a scalar starting with a reserved indicator");
  if (text === "-" || text.endsWith(":")) yaml_refuse("an ambiguous plain scalar");
  // `a: - item` is a parse error in Bun, not the string "- item".
  if (text.startsWith("- ")) yaml_refuse("a sequence entry on the same line as its key");
  if (text.startsWith("&") || text.startsWith("*")) yaml_refuse("anchors and aliases");
  if (text.startsWith("!")) yaml_refuse("tags");
  // Measured: Bun rejects a plain scalar containing ": ". `12:30` is fine,
  // `with: an inner colon` is a parse error, and guessing which the writer
  // meant is exactly the guess this shim does not make.
  if (text.includes(": ")) yaml_refuse("a plain scalar containing ': '");
  return [yaml_typeScalar(text.trim()), i + 1];
}

function yaml_parseMapping(lines, start, indent) {
  const out = {};
  let i = start;
  while (i < lines.length) {
    const raw = lines[i];
    if (yaml_isBlank(raw) || yaml_stripComment(raw).trim() === "") { i++; continue; }
    const ind = yaml_indentOf(raw);
    if (ind < indent) break;
    if (ind > indent) yaml_refuse("an unexpected indent inside a mapping");

    const content = yaml_stripComment(raw).trim();
    yaml_checkUnsupportedMarkers(content);
    if (content.startsWith("- ") || content === "-") break;

    const parsed = yaml_parseKey(content);
    if (!parsed) yaml_refuse("a line that is neither a mapping entry nor a sequence entry");

    const { key, rest } = parsed;
    if (rest.startsWith("|") || rest.startsWith(">")) {
      const [text, next] = yaml_readBlockScalar(lines, i + 1, rest, ind);
      out[key] = text;
      i = next;
      continue;
    }
    if (rest === "") {
      // A block sequence is allowed to sit at the same indentation as the key
      // that owns it - `tools:` then `- Read` in column zero is ordinary YAML
      // and common in frontmatter.
      let peek = i + 1;
      while (peek < lines.length && (yaml_isBlank(lines[peek]) || yaml_stripComment(lines[peek]).trim() === "")) peek++;
      const peeked = peek < lines.length ? yaml_stripComment(lines[peek]).trim() : "";
      const sameLevelSeq = peek < lines.length && yaml_indentOf(lines[peek]) === ind &&
        (peeked === "-" || peeked.startsWith("- "));
      const [child, next] = sameLevelSeq
        ? yaml_parseSequence(lines, peek, ind)
        : yaml_parseNode(lines, i + 1, ind + 1);
      out[key] = child;
      i = next === i + 1 ? i + 1 : next;
      continue;
    }
    const [value, next] = yaml_scalarValue(rest, lines, i, ind);
    out[key] = value;
    i = next;
  }
  return [out, i];
}

// --- entry point -----------------------------------------------------------

function yamlParse(input) {
  const src = String(input).replace(/\r\n/g, "\n");
  let lines = src.split("\n");

  // A single leading document marker is fine; a second document is not, and
  // guessing which one the caller wanted is exactly the wrong move.
  const markers = lines.filter((l) => l.trim() === "---" || l.trim().startsWith("--- ")).length;
  if (markers > 1) yaml_refuse("multiple documents in one string");
  // The marker need not be the first LINE - comments and blank lines may sit
  // above it. Looking only at line 0 left `# comment` then `---` unrecognised,
  // and the marker then fell through and was read as a bare scalar "---".
  let markerAt = 0;
  while (markerAt < lines.length &&
         (yaml_isBlank(lines[markerAt]) ||
          yaml_stripComment(lines[markerAt]).trim() === "")) {
    markerAt++;
  }
  if (markerAt < lines.length &&
      (lines[markerAt].trim() === "---" || lines[markerAt].trim().startsWith("--- "))) {
    // Content may sit on the marker line itself: `--- scalar` is a document
    // whose value is "scalar", not an empty one. Dropping the whole line
    // returned null for a document that has content, which is config silently
    // missing rather than config loudly refused.
    const inline = lines[markerAt].trim().slice(3).trim();
    const after = lines.slice(markerAt + 1);
    lines = inline === "" ? after : [inline].concat(after);
  }

  const meaningful = lines.filter((l) => !yaml_isBlank(l) && yaml_stripComment(l).trim() !== "");
  if (meaningful.length === 0) return null;

  const firstContent = yaml_stripComment(meaningful[0]).trim();
  yaml_checkUnsupportedMarkers(firstContent);

  // A whole document can be one flow collection. It has to be handled before
  // the mapping path, because `{a: 1}` looks like a key to a line-oriented
  // reader. Only the single-line form is accepted: a flow spanning lines was
  // not measured, so it refuses rather than guesses.
  if (/^[{[]/.test(firstContent)) {
    if (meaningful.length > 1) yaml_refuse("a flow collection spanning several lines");
    const [value, end] = yaml_parseFlow(firstContent, 0);
    if (firstContent.slice(end).trim() !== "") {
      yaml_refuse("trailing content after a flow collection");
    }
    return value;
  }
  if (!yaml_parseKey(firstContent) && !(firstContent === "-" || firstContent.startsWith("- "))) {
    // A bare top-level scalar.
    if (meaningful.length > 1) yaml_refuse("a bare scalar followed by more content");
    return yaml_scalarValue(firstContent, lines, 0, 0)[0];
  }

  const [node, consumed] = yaml_parseNode(lines, 0, 0);
  for (let k = consumed; k < lines.length; k++) {
    if (!yaml_isBlank(lines[k]) && yaml_stripComment(lines[k]).trim() !== "") {
      yaml_refuse("content the indentation left unattached to any node");
    }
  }
  return node;
}

/* ---------------------------------------------------------------- *
 * The object this file installs as globalThis.Bun. The entries above go in
 * first; everything below them is a reachable call with no honest Node
 * equivalent here, and each one names itself so a failing run says which Bun
 * API it needed.
 * ---------------------------------------------------------------- */
const bun = {
  stringWidth,
  wrapAnsi,
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
  // agent frontmatter; a wrong parse would be silently wrong config, so
  // YAML.parse implements the subset it can match exactly and refuses the
  // rest by name. stringify has no caller on any path measured here.
  YAML: {
    parse: yamlParse,
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
  generateHeapSnapshot: () => unsupported("generateHeapSnapshot", "Node's v8.getHeapSnapshot has a different shape"),

  // Native-binary-only surfaces. Bun's own error text for the gateway is
  // "requires the native binary"; these keep that true and say which piece.
  SQL: function () { unsupported("SQL", "the gateway needs Bun's native SQL client"); },
  Transpiler: function () { unsupported("Transpiler", "no JS transpiler stand-in"); },
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
 *   Bun.ant                   the odd one out: it is not feature-detected, it
 *                             is patched into the Bun that ships inside the
 *                             binary. Measured: typeof Bun.ant is "undefined"
 *                             in stock Bun 1.3.14, so a stub here would be the
 *                             one place this file claims a surface the ORACLE
 *                             does not have. All three call sites
 *                             (getPeerUid/getPeerPid/setDumpable) are bare
 *                             `Bun.ant.x(...)` inside try/catch, so leaving it
 *                             undefined throws the same TypeError Bun throws,
 *                             at the same place.
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
