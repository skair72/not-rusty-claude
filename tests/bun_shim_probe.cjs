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
// runs, because Bun.which returns absolute paths.
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
  "┌───────────────┬──────────┐",
  "│ name          │ status   │",
  "└───────────────┴──────────┘",
  "日本語のテキストです",
  "混ざった text with 中文 and ASCII",
  "\u{1f44d} done, ❤️ liked, \u{1f469}‍\u{1f4bb} coding",
  "1️⃣ first  2️⃣ second",
  "café naïve résumé",
  "\u{1f1fa}\u{1f1f8} \u{1f1eb}\u{1f1f7} \u{1f1ef}\u{1f1f5}",
  "tabs\there\tand newline",
  "…ellipsis, em—dash, arrow→",
  "\u{1f44d}\u{1f3fd} \u{1f44d}\u{1f3ff}",
];
for (const s of realistic) emit("stringWidth-realistic", s, attempt(() => Bun.stringWidth(s, { ambiguousIsNarrow: true })));

// "adversarial": random concatenations of the awkward atoms. NOT expected to be
// exact - the caller pins the mismatch count rather than requiring zero.
const atoms = [
  "a", "Z", "1", " ", "\t", "\n", "\r", ESC, ESC + "[31m", ESC + "[0m",
  ESC + "]8;;u" + BEL, "" + "31m", "中", "Ａ", "é", "é",
  "́", "ً", "\u{1f600}", "❤", "❤️", "\u{1f44d}",
  "\u{1f3fd}", "‍", "\u{1f469}‍\u{1f4bb}", "️", "︎",
  "⃣", "\u{1f1fa}", "\u{1f1f8}", "ั", "–", "→", "●",
  "█", " ", "​", "　", "­",
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

// --- stripANSI -----------------------------------------------------------
const stripCases = [ESC + "[31mx" + ESC + "[0m", ESC + "]8;;u" + BEL + "x", "" + "31mx",
  ESC + "Pq" + ESC + "\\x", ESC + "(Bx", ESC + "Z", ESC, "plain", ESC + "[", ""];
for (const s of stripCases) emit("stripANSI", s, attempt(() => Bun.stripANSI(s)));
for (const s of seen) emit("stripANSI", s, attempt(() => Bun.stripANSI(s)));

// --- hash ----------------------------------------------------------------
const alphabet = "abcXYZ019 \n\t{}[]:,\"'/\\-_.中文\u{1f600}éÿ";
for (let n = 0; n <= 140; n++) {
  let s = "";
  for (let i = 0; i < n; i++) s += alphabet[Math.floor(rnd() * alphabet.length)];
  emit("hash", s, attempt(() => Bun.hash(s).toString()));
  emit("hash-seeded", s, attempt(() => Bun.hash(s, BigInt(n * 7919)).toString()));
}

// --- semver.order --------------------------------------------------------
const versions = ["0.0.0", "0.0.1", "0.1.0", "1.0.0", "1.0.1", "1.1.0", "2.0.0",
  "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-alpha.beta", "1.0.0-beta", "1.0.0-beta.2",
  "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0+build", "1.0.0-rc.1+build.5",
  "10.0.0", "1.10.0", "1.2.10", "2.1.231", "2.1.241", "v1.2.3"];
for (const a of versions) {
  for (const b of versions) {
    let r;
    try { r = Bun.semver.order(a, b); } catch (e) { r = "throw"; }
    emit("semver.order", a + " vs " + b, r);
  }
}
for (const bad of ["", "x", "1", "1.2", "1.2.3.4", "1.2.-3", "not a version", " 1.2.3 "]) {
  let r;
  try { r = Bun.semver.order(bad, "1.0.0"); } catch (e) { r = "throw"; }
  emit("semver.order-invalid", bad, r);
}

// --- which ---------------------------------------------------------------
if (FIXTURE) {
  const bin = FIXTURE + "/bin";
  const rel = (v) => (typeof v === "string" && v.startsWith(FIXTURE) ? "<FIXTURE>" + v.slice(FIXTURE.length) : v);
  emit("which", "exe on PATH", rel(attempt(() => Bun.which("exe1", { PATH: bin }))));
  emit("which", "non-executable", rel(attempt(() => Bun.which("noexec", { PATH: bin }))));
  emit("which", "directory", rel(attempt(() => Bun.which("adir", { PATH: bin }))));
  emit("which", "missing", rel(attempt(() => Bun.which("nope", { PATH: bin }))));
  emit("which", "empty name", rel(attempt(() => Bun.which("", { PATH: bin }))));
  emit("which", "empty PATH", rel(attempt(() => Bun.which("exe1", { PATH: "" }))));
  emit("which", "PATH with empty entry", rel(attempt(() => Bun.which("exe1", { PATH: ":" + bin }))));
  emit("which", "absolute executable", rel(attempt(() => Bun.which(bin + "/exe1"))));
  emit("which", "absolute non-executable", rel(attempt(() => Bun.which(bin + "/noexec"))));
  emit("which", "absolute missing", rel(attempt(() => Bun.which(FIXTURE + "/zzz"))));
  emit("which", "relative with cwd", rel(attempt(() => Bun.which("./rel", { cwd: FIXTURE }))));
  emit("which", "name with slash and cwd", rel(attempt(() => Bun.which("bin/exe1", { cwd: FIXTURE }))));
  emit("which", "PATH dot is not cwd", rel(attempt(() => Bun.which("exe1", { PATH: ".", cwd: bin }))));
  emit("which", "first PATH entry wins", rel(attempt(() => Bun.which("exe1", { PATH: bin + ":" + FIXTURE + "/bin2" }))));
  emit("which", "second PATH entry", rel(attempt(() => Bun.which("exe2", { PATH: bin + ":" + FIXTURE + "/bin2" }))));
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

process.stdout.write(out.join("\n") + "\n");
