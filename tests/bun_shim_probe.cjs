// Differential probe for scripts/bun-shim.cjs.
//
// Runs under EITHER runtime and prints one JSON line per case, so the same file
// can be executed by Bun (which answers with the real Bun.*) and by Node (which
// answers with the shim), and the two outputs compared. Bun is the oracle; this
// file hardcodes no expected values.
//
//   bun  tests/bun_shim_probe.cjs <fixture-dir>
//   node --require scripts/bun-shim.cjs tests/bun_shim_probe.cjs <fixture-dir>
//
// <fixture-dir> is created by the caller and must be the SAME path for both
// runs: Bun.which answers with the path it was given, so the two runtimes have
// to be asked about the same directory. The which section chdirs into
// <fixture-dir>/cwd, so relative answers are comparable too.
"use strict";

const FIXTURE = process.argv[2];
const out = [];
const emit = (group, key, value) => out.push(JSON.stringify([group, key, value]));
// Record a throw as a value, so one unsupported case does not hide the rest and
// "Bun answers, the shim throws" shows up as an ordinary mismatch.
const attempt = (fn) => { try { return fn(); } catch (e) { return "throw:" + (e && e.bunApi ? e.bunApi : "other"); } };

// A deterministic PRNG so both runtimes see byte-identical corpora.
let seed = 12345;
const rnd = () => ((seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff);

const ESC = String.fromCharCode(27);
const BEL = String.fromCharCode(7);
const ST = ESC + "\\";                 // the spec-preferred OSC terminator
const C1ST = "\u009c";                 // ...and the one-character C1 spelling of it

// --- corpora -------------------------------------------------------------
// "realistic": what the CLI actually renders - help lines, tables, prose with
// CJK and emoji, colour codes. The shim claims to be exact here.
const realistic = [
  "",
  "  -h, --help                    display help for command",
  "Usage: claude [options] [command]",
  ESC + "[1mBold heading" + ESC + "[0m",
  ESC + "[38;5;204mcolour" + ESC + "[39m and " + ESC + "[2mdim" + ESC + "[22m",
  ESC + "]8;;https://example.com" + BEL + "a link" + ESC + "]8;;" + BEL,
  // The same hyperlink written the way the OSC 8 spec prefers, ESC \ instead of
  // BEL. Nothing else in this file terminates an OSC that way, and dropping the
  // ST check made stringWidth eat the rest of the line.
  ESC + "]8;;https://example.com" + ST + "a link" + ESC + "]8;;" + ST,
  ESC + "]0;window title" + ST + "after the title",
  // The SAME two terminated with the one-character C1 ST instead. A shim that
  // knows only the ESC-backslash spelling swallows the rest of the line and
  // answers 0 - and the ESC-backslash cases above cannot see that, which is
  // how the gap survived a wave of fixes aimed at it.
  ESC + "]0;window title" + C1ST + "after the title",
  ESC + "]8;;https://example.com" + C1ST + "a link" + ESC + "]8;;" + C1ST,
  ESC + "]8;;" + C1ST + "|",
  ESC + "]8;;" + ST + "|",
  "┌───────────────┬──────────┐",
  "│ name          │ status   │",
  "└───────────────┴──────────┘",
  "日本語のテキストです",
  "混ざった text with 中文 and ASCII",
  "\u{1f44d} done, ❤️ liked, \u{1f469}‍\u{1f4bb} coding",
  "1️⃣ first  2️⃣ second",
  "café naïve résumé",
  "\u{1f1fa}\u{1f1f8} \u{1f1eb}\u{1f1f7} \u{1f1ef}\u{1f1f5}",
  "tabs\there\tand newline",
  "…ellipsis, em—dash, arrow→",
  "\u{1f44d}\u{1f3fd} \u{1f44d}\u{1f3ff}",
];
for (const s of realistic) emit("stringWidth-realistic", s, attempt(() => Bun.stringWidth(s, { ambiguousIsNarrow: true })));

// Every code point, in blocks of 1024, as a string of per-code-point widths.
// The whole WIDTHS table is Bun's answer to exactly this question, so this is
// the corpus that pins it: 1,114,112 code points, 1088 lines. A regeneration
// error anywhere - including in the private-use planes nothing else here
// touches - shows up as one differing block, and the caller reports the code
// point. Costs about 8s under Node, 0.7s under Bun.
{
  const BLOCK = 1024;
  for (let base = 0; base < 0x110000; base += BLOCK) {
    let widths = "";
    for (let cp = base; cp < base + BLOCK; cp++) {
      widths += Bun.stringWidth(String.fromCodePoint(cp), { ambiguousIsNarrow: true });
    }
    emit("stringWidth-codepoints", base.toString(16), widths);
  }
}

// "adversarial": random concatenations of the awkward atoms. NOT expected to be
// exact - the caller pins the mismatch count rather than requiring zero.
const atoms = [
  "a", "Z", "1", " ", "\t", "\n", "\r", ESC, ESC + "[31m", ESC + "[0m",
  ESC + "]8;;u" + BEL, ESC + "]8;;u" + ST, ESC + "]" + ST, ST,
  ESC + "]8;;u" + C1ST, ESC + "]" + C1ST, C1ST,
  "" + "31m", "中", "Ａ", "é", "é",
  "́", "ً", "\u{1f600}", "❤", "❤️", "\u{1f44d}",
  "\u{1f3fd}", "‍", "\u{1f469}‍\u{1f4bb}", "️", "︎",
  "⃣", "\u{1f1fa}", "\u{1f1f8}", "ั", "–", "→", "●",
  "█", " ", "​", "　", "­",
];
const seen = new Set();
for (const a of atoms) for (const b of atoms) seen.add(a + b);
for (let i = 0; i < 4000; i++) {
  let s = "";
  const n = 1 + Math.floor(rnd() * 5);
  for (let j = 0; j < n; j++) s += atoms[Math.floor(rnd() * atoms.length)];
  seen.add(s);
}
for (const s of seen) emit("stringWidth-adversarial", s, attempt(() => Bun.stringWidth(s, { ambiguousIsNarrow: true })));

// --- stringWidth options -------------------------------------------------
// How Bun READS the two options, which is a different question from what it
// does with them. Both refusals in the shim were once spelled `=== false` and
// `=== true`, so they fired on the literal boolean and on nothing else: every
// other spelling of the same intent got answered from the default table while
// Bun had switched tables. The oracle answers each spelling here; the shim has
// to give Bun's number or throw.
const AMBIGUOUS = "α";                   // ambiguous width: 1 narrow, 2 wide
const SGR = ESC + "[31mfoo" + ESC + "[0m";    // 3 with the escapes skipped, more counted
const optionValues = [
  ["true", true], ["false", false], ["undefined", undefined], ["null", null],
  ["0", 0], ["-0", -0], ["1", 1], ["-1", -1], ["0.5", 0.5], ["NaN", NaN],
  ["Infinity", Infinity], ["Number.MIN_VALUE", Number.MIN_VALUE],
  ["0n", 0n], ["-0n", -0n], ["1n", 1n], ["-1n", -1n],
  ['""', ""], ['" "', " "], ['"0"', "0"], ['"false"', "false"], ['"no"', "no"],
  ["{}", {}], ["[]", []], ["[0]", [0]],
  // Boxed falsy primitives are OBJECTS, so ToBoolean says true for all three.
  // Assuming "ToBoolean" without measuring gets these backwards.
  ["new Boolean(false)", new Boolean(false)], ["new Number(0)", new Number(0)],
  ["new String('')", new String("")],
  ["function", function () {}], ["Object.create(null)", Object.create(null)],
  ["Symbol()", Symbol("s")], ["Date", new Date(0)],
  // No ToPrimitive happens: measured, neither valueOf nor toString is called.
  ["valueOf=>false", { valueOf() { return false; } }],
];
for (const [key, value] of optionValues) {
  emit("stringWidth-options", "ambiguousIsNarrow " + key,
    attempt(() => Bun.stringWidth(AMBIGUOUS, { ambiguousIsNarrow: value })));
  emit("stringWidth-options", "countAnsiEscapeCodes " + key,
    attempt(() => Bun.stringWidth(SGR, { countAnsiEscapeCodes: value })));
}
// The SHAPE of the options object. Measured: Bun walks the prototype chain,
// reads each key once, reads countAnsiEscapeCodes BEFORE ambiguousIsNarrow,
// treats a non-object as no options at all, and for an empty string answers 0
// without reading the object - a throwing getter never fires. The last four
// rows are the ones that catch a guard moved ahead of that short-circuit.
const optionShapes = [
  ["no options", () => Bun.stringWidth(AMBIGUOUS)],
  ["empty options", () => Bun.stringWidth(AMBIGUOUS, {})],
  ["options null", () => Bun.stringWidth(AMBIGUOUS, null)],
  ["options is a string", () => Bun.stringWidth(AMBIGUOUS, "nonsense")],
  ["options is a number", () => Bun.stringWidth(AMBIGUOUS, 1)],
  ["options is a boolean", () => Bun.stringWidth(AMBIGUOUS, true)],
  ["options is a symbol", () => Bun.stringWidth(AMBIGUOUS, Symbol("s"))],
  ["inherited ambiguousIsNarrow:false",
    () => Bun.stringWidth(AMBIGUOUS, Object.create({ ambiguousIsNarrow: false }))],
  ["inherited countAnsiEscapeCodes:true",
    () => Bun.stringWidth(SGR, Object.create({ countAnsiEscapeCodes: true }))],
  ["getter ambiguousIsNarrow:false",
    () => Bun.stringWidth(AMBIGUOUS, { get ambiguousIsNarrow() { return false; } })],
  ["supported pair", () => Bun.stringWidth(SGR, { ambiguousIsNarrow: true, countAnsiEscapeCodes: false })],
  ["both unsupported", () => Bun.stringWidth(SGR, { ambiguousIsNarrow: false, countAnsiEscapeCodes: true })],
  ["empty input, ambiguousIsNarrow:false", () => Bun.stringWidth("", { ambiguousIsNarrow: false })],
  ["empty input, countAnsiEscapeCodes:true", () => Bun.stringWidth("", { countAnsiEscapeCodes: true })],
  ["undefined input, countAnsiEscapeCodes:true", () => Bun.stringWidth(undefined, { countAnsiEscapeCodes: true })],
  ["empty input, throwing getter",
    () => Bun.stringWidth("", { get countAnsiEscapeCodes() { throw new RangeError("read"); } })],
];
for (const [key, fn] of optionShapes) emit("stringWidth-options", "shape: " + key, attempt(fn));

// --- stringWidth and the CSI terminator: DOCUMENTED DIVERGENCE 1 ---------
// Bun picks one of two CSI scanners by how JSC is storing the string, not by
// what is in it: a Latin-1 string (every code point <= U+00FF) ends a CSI only
// on 0x40..0x7E, a 16-bit one ends it on any code point >= 0x40 except 0x7F.
// The shim implements the Latin-1 rule for both, because no JS expression can
// ask JSC which representation it chose - two strings that are `===` get two
// answers from Bun. So these rows are pinned as the KNOWN divergence they are,
// with their exact pair of answers, next to the well-formed sequences and the
// Latin-1 controls that must still agree. A row moving either way fails.
const CJK = "中";
const DEL = String.fromCharCode(0x7f);
const csiCases = [
  ["latin1, unterminated CSI", "A" + ESC + "[éB"],
  ["latin1, U+00FF after", "A" + ESC + "[éBÿ"],
  ["16-bit, CJK inside the CSI", "A" + ESC + "[" + CJK + "B"],
  ["16-bit, U+00FF inside and CJK after", "A" + ESC + "[ÿB" + CJK],
  ["16-bit, U+0100 after (the boundary)", "A" + ESC + "[éBĀ"],
  ["16-bit, nothing after the CSI", ESC + "[" + CJK],
  ["16-bit, truncated CSI at the end", ESC + "[3" + CJK],
  ["16-bit, DEL inside the CSI", "A" + ESC + "[" + DEL + "B" + CJK],
  ["well-formed SGR then CJK", ESC + "[0m" + CJK],
  ["well-formed SGR, latin1", ESC + "[1mé"],
  ["well-formed SGR pair around CJK", ESC + "[31m" + CJK + ESC + "[0m"],
];
for (const [key, s] of csiCases) {
  emit("stringWidth-csi-representation", key, attempt(() => Bun.stringWidth(s, { ambiguousIsNarrow: true })));
}

// --- stripANSI -----------------------------------------------------------
const stripCases = [ESC + "[31mx" + ESC + "[0m", ESC + "]8;;u" + BEL + "x", "" + "31mx",
  ESC + "Pq" + ESC + "\\x", ESC + "(Bx", ESC + "Z", ESC, "plain", ESC + "[", "",
  ESC + "]8;;u" + ST + "x", ESC + "]8;;u" + C1ST + "x", C1ST + "x"];
for (const s of stripCases) emit("stripANSI", s, attempt(() => Bun.stripANSI(s)));
for (const s of seen) emit("stripANSI", s, attempt(() => Bun.stripANSI(s)));
// Argument coercion, which is a different question from the escape grammar:
// measured, Bun.stripANSI() answers the STRING "undefined" rather than "" or
// undefined, and everything non-string goes through toString.
emit("stripANSI-coercion", "no argument", attempt(() => Bun.stripANSI()));
for (const [key, value] of [["undefined", undefined], ["null", null], ["number", 123],
  ["boolean", true], ["object", {}], ["array", []], ["array of strings", ["a", "b"]]]) {
  emit("stripANSI-coercion", key, attempt(() => Bun.stripANSI(value)));
  emit("stringWidth-coercion", key, attempt(() => Bun.stringWidth(value)));
}
emit("stringWidth-coercion", "no argument", attempt(() => Bun.stringWidth()));

// --- hash ----------------------------------------------------------------
const alphabet = "abcXYZ019 \n\t{}[]:,\"'/\\-_.中文\u{1f600}éÿ";
const strings = [];
for (let n = 0; n <= 140; n++) {
  let s = "";
  for (let i = 0; i < n; i++) s += alphabet[Math.floor(rnd() * alphabet.length)];
  strings.push(s);
  emit("hash", s, attempt(() => Bun.hash(s).toString()));
  emit("hash-seeded", s, attempt(() => Bun.hash(s, BigInt(n * 7919)).toString()));
}
// Seeds that actually use the width of a u64. The loop above never went past
// 1,108,660, so a shim that truncated the seed to 32 bits - or dropped its top
// bits any other way - answered every case correctly.
const bigSeeds = [0n, 1n, 0xffffffffn, 0x100000000n, 0x100000001n,
  (1n << 48n) + 12345n, (1n << 51n), (1n << 52n) + 7n, (1n << 63n),
  (1n << 64n) - 1n, (1n << 64n), (1n << 64n) + 9n, -1n];
for (const s of bigSeeds) {
  for (const text of ["", "hello", "session-key", strings[97]]) {
    emit("hash-seeded", "0x" + (s < 0n ? "-" + (-s).toString(16) : s.toString(16)) + " " + text,
      attempt(() => Bun.hash(text, s).toString()));
  }
}
// The Number-seed branch, which nothing used to exercise at all. Bun reproduces
// a Number seed only for integers in [0, 2^51); the refused group below pins
// where that stops.
for (const s of [0, 1, 255, 65535, 2147483647, 2147483648, 4294967296, 4294967297,
  2 ** 50, 2 ** 51 - 1]) {
  emit("hash-seeded-number", String(s), attempt(() => Bun.hash("seeded-" + (s % 3), s).toString()));
}
// Inputs and seeds Bun answers and the shim refuses. Counted, like the
// malformed versions below, so the refusal is visible instead of invisible.
const refused = [
  ["number input", () => Bun.hash(12345)],
  ["object input", () => Bun.hash({})],
  ["no argument", () => Bun.hash()],
  ["seed 2^51", () => Bun.hash("abc", 2 ** 51)],
  ["seed 2^53", () => Bun.hash("abc", 2 ** 53)],
  ["seed -1", () => Bun.hash("abc", -1)],
  ["seed 1.5", () => Bun.hash("abc", 1.5)],
  ["seed NaN", () => Bun.hash("abc", NaN)],
  ["seed string", () => Bun.hash("abc", "5")],
];
for (const [key, fn] of refused) {
  emit("hash-refused", key, attempt(() => { const v = fn(); return typeof v === "bigint" ? v.toString() : v; }));
}

// --- semver.order --------------------------------------------------------
const versions = ["0.0.0", "0.0.1", "0.1.0", "1.0.0", "1.0.1", "1.1.0", "2.0.0",
  "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2",
  "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0+build", "1.0.0-rc.1+build.5",
  "10.0.0", "1.10.0", "1.2.10", "2.1.231", "2.1.241", "v1.2.3",
  // Numeric identifiers past Number precision but inside u64, where Number()
  // arithmetic silently calls neighbours equal.
  "9007199254740992.0.0", "9007199254740993.0.0",
  "18446744073709551614.0.0", "18446744073709551615.0.0",
  "1.0.0-9007199254740992", "1.0.0-9007199254740993", "1.0.0-18446744073709551615",
  "1.0.0-007"];
for (const a of versions) {
  for (const b of versions) {
    let r;
    try { r = Bun.semver.order(a, b); } catch (e) { r = "throw"; }
    emit("semver.order", a + " vs " + b, r);
  }
}
// Inputs Bun and the shim may legitimately disagree about - but only ever in
// the direction of the shim refusing. The caller asserts that direction.
const malformed = ["", "x", "1.2.3.4", "1.2.-3", "not a version", " 1.2.3 ",
  "   ", "1.2.3\n", "1.2.3\t", "1.2.3\r", "1.2.3\v", "1.2.3\f", "\t1.2.3", "\n1.2.3",
  "1.2.3 \t", "1.2.3\u00a0", "1.2.3- rc", "1 .2.3", "  v1.2.3  ",
  "18446744073709551616.0.0", "1.0.0-18446744073709551616", "1.0.0-123456789012345678901234567890",
  // The range grammar leaking into a version comparison. Bun answers every one
  // of these and not one of the answers is an order: "^1.2.3" comes out ABOVE
  // "2.0.0", "1.2.3junk" is read as a prerelease while "1.2.3xx" is read as two
  // wildcards, "1.2.30x" is 1.2.30, and "1-rc" throws where "1.2-rc" does not.
  // The shim names each one instead of picking an answer.
  "^1.2.3", "^0.0.1", "=1.2.3", "~1.2.3", ">=1.2.3", "*", "1.x", "1.2.x", "X",
  "1.2.3x", "1.2.3xx", "1.2.3xy", "1.2.3junk", "1.2.30x", "1.2.3-", "1.2.3+",
  "1-rc", "1.2-rc", "1.2+b", "1.", "1.2.", "V1.2.3", "vv1.2.3", "v 1.2.3",
  "1.2.3\tx", "1.2.3.x", "1.2.3_", "1.2.3;"];
for (const bad of malformed) {
  let r;
  try { r = Bun.semver.order(bad, "1.2.3"); } catch (e) { r = "throw"; }
  emit("semver.order-invalid", bad, r);
}

// --- which ---------------------------------------------------------------
if (FIXTURE) {
  const bin = FIXTURE + "/bin";
  const rel = (v) => (typeof v === "string" && v.startsWith(FIXTURE) ? "<FIXTURE>" + v.slice(FIXTURE.length) : v);
  const w = (key, fn) => emit("which", key, rel(attempt(fn)));
  w("exe on PATH", () => Bun.which("exe1", { PATH: bin }));
  w("non-executable", () => Bun.which("noexec", { PATH: bin }));
  w("directory", () => Bun.which("adir", { PATH: bin }));
  w("missing", () => Bun.which("nope", { PATH: bin }));
  w("empty name", () => Bun.which("", { PATH: bin }));
  w("empty PATH", () => Bun.which("exe1", { PATH: "" }));
  w("PATH with empty entry", () => Bun.which("exe1", { PATH: ":" + bin }));
  w("absolute executable", () => Bun.which(bin + "/exe1"));
  w("absolute non-executable", () => Bun.which(bin + "/noexec"));
  w("absolute missing", () => Bun.which(FIXTURE + "/zzz"));
  w("relative with cwd", () => Bun.which("./rel", { cwd: FIXTURE }));
  w("name with slash and cwd", () => Bun.which("bin/exe1", { cwd: FIXTURE }));
  w("first PATH entry wins", () => Bun.which("exe1", { PATH: bin + ":" + FIXTURE + "/bin2" }));
  w("second PATH entry", () => Bun.which("exe2", { PATH: bin + ":" + FIXTURE + "/bin2" }));

  // Everything below is about the SHAPE of the answer, not just which file was
  // found. Bun glues the path together and stats it: it does not resolve, does
  // not collapse "//" or "..", and does not make a relative answer absolute.
  // From inside <fixture>/cwd, so relative answers are the same string in both
  // runtimes.
  process.chdir(FIXTURE + "/cwd");
  w("PATH dot", () => Bun.which("myexe", { PATH: "." }));
  w("PATH dot, name not there", () => Bun.which("exe1", { PATH: "." }));
  w("PATH relative parent", () => Bun.which("exe1", { PATH: "../bin" }));
  w("PATH trailing slash", () => Bun.which("exe1", { PATH: bin + "/" }));
  w("PATH with dotdot inside", () => Bun.which("exe1", { PATH: FIXTURE + "/bin2/../bin" }));
  w("PATH is an array", () => Bun.which("exe1", { PATH: [bin] }));
  w("PATH is null", () => Bun.which("exe1", { PATH: null }));
  w("PATH is a number", () => Bun.which("exe1", { PATH: 123 }));
  w("PATH dot is not cwd", () => Bun.which("exe1", { PATH: ".", cwd: bin }));
  w("name dot slash", () => Bun.which("./myexe"));
  w("name dot slash twice", () => Bun.which("././myexe"));
  w("name dot slash slash", () => Bun.which(".//myexe"));
  w("name via parent", () => Bun.which("../cwd/myexe"));
  w("name with dotdot, no such dir", () => Bun.which("a/../myexe"));
  w("name with double slash", () => Bun.which("bin//exe1", { cwd: FIXTURE }));
  w("cwd trailing slash", () => Bun.which("./myexe", { cwd: FIXTURE + "/cwd/" }));
  w("cwd is null", () => Bun.which("./myexe", { cwd: null }));
  w("cwd is a number", () => Bun.which("./myexe", { cwd: 123 }));
  w("cwd is an array", () => Bun.which("./myexe", { cwd: [FIXTURE + "/cwd"] }));
  w("cwd ignored for absolute name", () => Bun.which(bin + "/exe1", { cwd: "/nonexistent" }));
  w("name is a number", () => Bun.which(123));
  w("name is undefined", () => Bun.which(undefined));
  w("no arguments", () => Bun.which());
  w("name is a symbol", () => Bun.which(Symbol("s")));
  w("options is a string", () => Bun.which("exe1", "nonsense"));
  w("options is null", () => Bun.which("exe1", null));

  // DOCUMENTED DIVERGENCE 2. Bun hands the name to the OS as a NUL-terminated
  // C string, so the NUL truncates it at the syscall while the string Bun
  // hands back keeps the tail. Node's fs rejects any path containing a NUL, so
  // the shim's stat throws and it answers null. Pinned with its exact pair of
  // answers, together with the NUL placements where the two already agree.
  const NUL = String.fromCharCode(0);
  const nul = (key, fn) => emit("which-nul", key, rel(attempt(fn)));
  nul("absolute name, NUL and a tail", () => Bun.which(bin + "/exe1" + NUL + "zzz"));
  nul("absolute name, trailing NUL", () => Bun.which(bin + "/exe1" + NUL));
  nul("PATH lookup, NUL and a tail", () => Bun.which("exe1" + NUL + "zzz", { PATH: bin }));
  nul("NUL before the name", () => Bun.which(NUL + "exe1", { PATH: bin }));
  nul("missing name with a NUL", () => Bun.which(bin + "/nope" + NUL + "zzz"));
  nul("NUL in PATH", () => Bun.which("exe1", { PATH: bin + NUL + "/zzz" }));
  nul("NUL in cwd", () => Bun.which("./exe1", { cwd: bin + NUL + "/zzz" }));
  process.chdir(FIXTURE);
}

// --- deepEquals ----------------------------------------------------------
const values = [null, true, false, 0, -0, 1, -1, 1.5, NaN, Infinity, "", "a", "b",
  [], [1], [1, 2], [[1]], {}, { a: 1 }, { a: 1, b: 2 }, { a: undefined },
  { a: { b: [1, { c: null }] } }, { b: 2, a: 1 }, [1, [2, [3]]]];
for (let i = 0; i < values.length; i++) {
  for (let j = 0; j < values.length; j++) {
    emit("deepEquals", i + "," + j, attempt(() => Bun.deepEquals(values[i], values[j])));
  }
}
// Arrays, where "an undefined property is an absent property" also applies -
// to INDICES. Bun's rule is positional, not a filter: a TRAILING undefined (or
// hole) is absent, an interior one is not. Pinned as named pairs rather than
// folded into the matrix above so a failure says which half of that rule broke.
const holeInterior = [1, , 3];
const holeTrailing = [1, 2, ,];
const withExtraProp = [1];
withExtraProp.x = 2;
const arrayPairs = [
  ["[undefined] vs []", [undefined], []],
  ["[] vs [undefined]", [], [undefined]],
  ["[1,undefined] vs [1]", [1, undefined], [1]],
  ["[undefined,1] vs [1]", [undefined, 1], [1]],
  ["[1,undefined,2] vs [1,2]", [1, undefined, 2], [1, 2]],
  ["[1,undefined,2] vs [1,2,undefined]", [1, undefined, 2], [1, 2, undefined]],
  ["[undefined,undefined] vs []", [undefined, undefined], []],
  ["[undefined,1,undefined] vs [undefined,1]", [undefined, 1, undefined], [undefined, 1]],
  ["[1,2] vs [1,2,undefined,undefined]", [1, 2], [1, 2, undefined, undefined]],
  ["[1,2,undefined,3] vs [1,2,3]", [1, 2, undefined, 3], [1, 2, 3]],
  ["{a:[undefined]} vs {a:[]}", { a: [undefined] }, { a: [] }],
  ["[[undefined]] vs [[]]", [[undefined]], [[]]],
  ["[[1,undefined]] vs [[1],undefined]", [[1, undefined]], [[1], undefined]],
  ["new Array(1) vs []", new Array(1), []],
  ["new Array(2) vs [undefined]", new Array(2), [undefined]],
  ["[1,,3] vs [1,3]", holeInterior, [1, 3]],
  ["[1,,3] vs [1,undefined,3]", holeInterior, [1, undefined, 3]],
  ["[1,2,,] vs [1,2]", holeTrailing, [1, 2]],
  ["[undefined] vs [null]", [undefined], [null]],
  ["[1,undefined] vs [1,null]", [1, undefined], [1, null]],
  ["[undefined] vs {}", [undefined], {}],
  // Own properties that are not indices: Bun ignores them on an array, so an
  // implementation that reached for Object.keys here would answer false.
  ["[1]+x vs [1]", withExtraProp, [1]],
];
for (const [key, a, b] of arrayPairs) {
  emit("deepEquals-arrays", key, attempt(() => Bun.deepEquals(a, b)));
  emit("deepEquals-arrays", key + " (swapped)", attempt(() => Bun.deepEquals(b, a)));
}

// Fewer than two arguments is an arity error in Bun, not a comparison against
// undefined. The MESSAGE is compared, not just the fact of a throw: answering
// `true` to Bun.deepEquals() is precisely the plausible wrong value this shim
// exists to refuse, and "it threw something" would not have caught the shim
// throwing for its own unrelated reason.
const message = (fn) => {
  try { const v = fn(); return "returned:" + String(v); }
  catch (e) { return (e && e.constructor ? e.constructor.name : "?") + ": " + (e && e.message); }
};
for (const [key, fn] of [
  ["no arguments", () => Bun.deepEquals()],
  ["one object", () => Bun.deepEquals({})],
  ["one undefined", () => Bun.deepEquals(undefined)],
  ["one number", () => Bun.deepEquals(1)],
]) {
  emit("deepEquals-arity", key, message(fn));
}

// Symbol keys are invisible to Object.keys but not to Bun.
const SYM = Symbol.for("nrc-probe");
const symValues = [{}, { [SYM]: 1 }, { [SYM]: 2 }, { [SYM]: undefined }, { a: 1, [SYM]: 1 }];
for (let i = 0; i < symValues.length; i++) {
  for (let j = 0; j < symValues.length; j++) {
    emit("deepEquals-symbols", i + "," + j, attempt(() => Bun.deepEquals(symValues[i], symValues[j])));
  }
}

// --- semver.order: arity, coercion and the measured grammar --------------
// Everything this entry never had a contract for - how it is CALLED, what it
// accepts, and where Bun's answers stop being an order. Each is asked of the
// oracle here; the shim has to give the same answer or throw.

// Fewer than two arguments is Bun's own arity error, thrown before either
// argument is even stringified - order(Symbol()) says "Expected two arguments",
// not "cannot convert a symbol". The MESSAGE is compared: this entry used to
// blame the input ("Invalid SemVer: undefined") for a mistake in the call.
for (const [key, fn] of [
  ["no arguments", () => Bun.semver.order()],
  ["one string", () => Bun.semver.order("1.2.3")],
  ["one undefined", () => Bun.semver.order(undefined)],
  ["one symbol", () => Bun.semver.order(Symbol("s"))],
  ["three arguments", () => Bun.semver.order("1.2.3", "1.2.4", "ignored")],
  ["symbol in the second slot", () => Bun.semver.order("garbage", Symbol("s"))],
]) {
  emit("semver.order-arity", key, message(fn));
}

// ToString coercion, the treatment the other four coercing entries already
// had. A TypeError is compared by message - that is the symbol contract - and
// a plain Error is only compared as "it threw", because Bun's Invalid SemVer
// text and the shim's are not meant to be identical.
const semverOutcome = (fn) => {
  try { return "returned:" + String(fn()); }
  catch (e) { return e instanceof TypeError ? "TypeError: " + e.message : "throw"; }
};
const coercible = [
  ["number 1", 1], ["number 2", 2], ["number 1.5", 1.5], ["number 10", 10],
  ["number 0", 0], ["negative", -1], ["NaN", NaN], ["Infinity", Infinity],
  ["true", true], ["false", false], ["null", null], ["undefined", undefined],
  ["bigint", 1n], ["array of one", ["1.0.0"]], ["array of three", ["1", "0", "0"]],
  ["empty array", []], ["plain object", {}], ["date", new Date(0)],
  ["toString object", { toString() { return "1.0.0"; } }],
  // toString wins over valueOf - the same way stripANSI and Bun.which read it.
  ["valueOf and toString", { valueOf() { return "9.9.9"; }, toString() { return "1.0.0"; } }],
  ["symbol", Symbol("s")],
];
for (const [key, value] of coercible) {
  for (const other of ["1.0.0", "2.0.0"]) {
    emit("semver.order-coercion", key + " vs " + other, semverOutcome(() => Bun.semver.order(value, other)));
    emit("semver.order-coercion", other + " vs " + key, semverOutcome(() => Bun.semver.order(other, value)));
  }
}

// The grammar itself: a space ends the version and the rest is discarded, and
// a partial version sorts ABOVE the same version with the missing components
// filled in. `2.1.231 (Claude Code)` is this artifact's own --version output,
// on which the shim used to throw.
const grammarPairs = [
  ["2.1.231 (Claude Code)", "2.1.230"], ["2.1.231 (Claude Code)", "2.1.231"],
  ["2.1.231 (Claude Code)", "2.1.232"], ["2.1.231 (Claude Code)", "2.1.231 (Claude Code)"],
  ["1.2.3 x", "1.2.3"], ["1.2.3  x", "1.2.3"], ["1.2.3 1.2.4", "1.2.3"],
  ["1.2.3 \t", "1.2.3"], ["1.2.3 \n", "1.2.3"], ["1.2.3 ", "1.2.3"], ["1.2.3  ", "1.2.3"],
  ["1.2.3-a b", "1.2.3"], ["1.2.3-a b", "1.2.3-a"], ["1.2.3+b c", "1.2.3"],
  ["1.2.3-rc.1+b.5 and more", "1.2.3-rc.1"], [" 1.2.3", "1.2.3"], ["  v1.2.3  ", "1.2.3"],
  ["\t1.2.3", "1.2.3"], ["\n1.2.3", "1.2.3"], ["\r1.2.3", "1.2.3"], ["\v1.2.3", "1.2.3"],
  ["\f1.2.3", "1.2.3"], [" \t\n1.2.3 tail", "1.2.3"],
  ["1", "1.0.0"], ["1", "1.2"], ["1", "1.2.3"], ["1", "1"], ["1", "2"], ["1", "0.9.9"],
  ["1", "1.9999.9999"], ["1.2", "1.2.0"], ["1.2", "1.2.3"], ["1.2", "1.2"], ["1.2", "1.3"],
  ["1.2", "1.9.9"], ["1.2", "1.1.9"], ["0", "0.0.0"], ["0", "1"], ["v1", "1.0.0"],
  ["v1.2", "1.2.0"], ["2", "1.9999.9999"], ["1 tail", "1.0.0"], ["01", "1.0.0"],
  ["1.2", "1.2.0-rc"], ["1", "1.0.0-rc"], ["10", "9.9.9"], ["1.10", "1.9.9"],
];
for (const [a, b] of grammarPairs) {
  emit("semver.order-grammar", JSON.stringify(a) + " vs " + JSON.stringify(b),
    semverOutcome(() => Bun.semver.order(a, b)));
  emit("semver.order-grammar", JSON.stringify(b) + " vs " + JSON.stringify(a),
    semverOutcome(() => Bun.semver.order(b, a)));
}

// One code point above U+007F anywhere in the string and Bun stops ordering:
// it answers 0 against every version at once. Asked here in the shape that
// proves it is not an order - the same string against two versions that are
// NOT equal to each other - so the row that would have to change for the shim
// to start answering is visible in the corpus.
const NBSP = "\u00a0";
const ascii = (v) => JSON.stringify(v).replace(/[\u0080-\uffff]/g,
  (c) => "\\u" + c.charCodeAt(0).toString(16).padStart(4, "0"));
for (const s of ["1.2.3" + NBSP, NBSP + "1.2.3", "1." + NBSP + "2.3", "1" + NBSP + "2.3.4",
  "1.2.3 " + NBSP + "tail", "1.2.3\u3000", "\ufeff1.2.3", "1.2.3\u2028", "1.2.3\u202f",
  "1.2.3-rc" + NBSP, "1.2.3 (Claude" + NBSP + "Code)", "1.2.3\u0085"]) {
  for (const other of ["0.0.0", "1.2.3", "2.0.0"]) {
    // The key is escaped rather than JSON.stringify'd raw: U+2028 and U+0085
    // are line terminators to the caller that reads this output line by line.
    emit("semver.order-nonascii", ascii(s) + " vs " + JSON.stringify(other),
      semverOutcome(() => Bun.semver.order(s, other)));
  }
}

// --- deepEquals and the property shapes Bun's answer depends on ----------
// A non-enumerable property is read straight through when it is on the RIGHT
// and is invisible when it is on the left, so the same two objects compare
// differently depending on the argument order. The shim refuses instead of
// picking one - the count is pinned by the caller, and the direction (refuse,
// never a different boolean) is what that assertion is really about.
const nonEnum = (props, hidden) => {
  const o = Object.assign({}, props);
  for (const k of Object.keys(hidden)) {
    Object.defineProperty(o, k, { value: hidden[k], enumerable: false, writable: true, configurable: true });
  }
  return o;
};
const nonEnumPairs = [
  ["{a:1} vs hidden a:1", { a: 1 }, nonEnum({}, { a: 1 })],
  ["{a:2} vs hidden a:1", { a: 2 }, nonEnum({}, { a: 1 })],
  ["{} vs hidden a:1", {}, nonEnum({}, { a: 1 })],
  ["{a:undefined} vs hidden a:1", { a: undefined }, nonEnum({}, { a: 1 })],
  ["{a:1} vs hidden a:undefined", { a: 1 }, nonEnum({}, { a: undefined })],
  ["{a:1,b:2} vs {b:2}+hidden a:1", { a: 1, b: 2 }, nonEnum({ b: 2 }, { a: 1 })],
  ["{a:1} vs {b:9}+hidden a:1", { a: 1 }, nonEnum({ b: 9 }, { a: 1 })],
  ["{a:1}+hidden b:2 vs {b:9}+hidden a:1", nonEnum({ a: 1 }, { b: 2 }), nonEnum({ b: 9 }, { a: 1 })],
  ["nested {o:{a:1}} vs {o:hidden a:1}", { o: { a: 1 } }, { o: nonEnum({}, { a: 1 }) }],
  ["{a:1} vs getter a:1 (non-enumerable)", { a: 1 },
    Object.defineProperty({}, "a", { get() { return 1; }, enumerable: false, configurable: true })],
];
for (const [key, a, b] of nonEnumPairs) {
  emit("deepEquals-nonenum", key, attempt(() => Bun.deepEquals(a, b)));
  emit("deepEquals-nonenum", key + " (swapped)", attempt(() => Bun.deepEquals(b, a)));
}

// The same split without a non-enumerable property in sight. An integer-index
// key or an accessor moves JSC off its fast comparison, and the slow one
// answers by the order the keys were inserted: the first pair here is equal,
// and the second - the same values plus an identical "0" key on BOTH sides -
// is not. The control rows must MATCH the oracle; the ones carrying the index
// key or the getter are the refusals.
const representationPairs = [
  ["control: {x:1} vs {y:undefined,x:1}", { x: 1 }, { y: undefined, x: 1 }],
  ["control: {x:1} vs {x:1,y:undefined}", { x: 1 }, { x: 1, y: undefined }],
  ["control: {x:1,0:7} vs {x:1,0:7}", { x: 1, 0: 7 }, { x: 1, 0: 7 }],
  ["index key: {x:1,0:7} vs {y:undefined,x:1,0:7}", { x: 1, 0: 7 }, { y: undefined, x: 1, 0: 7 }],
  ["index key: {x:1,0:7} vs {x:1,0:7,y:undefined}", { x: 1, 0: 7 }, { x: 1, 0: 7, y: undefined }],
  ["index key: {0:1} vs {y:undefined,0:1}", { 0: 1 }, { y: undefined, 0: 1 }],
  ["getter: {x:1,g} vs {y:undefined,x:1,g}",
    { x: 1, get g() { return 0; } }, { y: undefined, x: 1, get g() { return 0; } }],
  ["getter: {x:1} vs {x:1,g}", { x: 1 }, { x: 1, get g() { return 1; } }],
];
for (const [key, a, b] of representationPairs) {
  emit("deepEquals-representation", key, attempt(() => Bun.deepEquals(a, b)));
  emit("deepEquals-representation", key + " (swapped)", attempt(() => Bun.deepEquals(b, a)));
}

// --- the surface itself --------------------------------------------------
// Which names exist, asked of the oracle rather than assumed. The shim leaves
// some of Bun's names undefined ON PURPOSE - the bundle feature-detects them -
// and the caller pins exactly that set. What it must never do is define a name
// stock Bun does not have.
for (const name of ["stringWidth", "stripANSI", "hash", "which", "deepEquals", "gc",
  "semver", "YAML", "TOML", "spawn", "file", "serve", "listen", "connect", "wrapAnsi",
  "generateHeapSnapshot", "SQL", "Transpiler", "ant", "Terminal", "WebView", "JSONL",
  "version", "isStandaloneExecutable", "stdin"]) {
  emit("surface", name, typeof globalThis.Bun[name]);
}

process.stdout.write(out.join("\n") + "\n");
