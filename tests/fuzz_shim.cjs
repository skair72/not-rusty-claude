// Generative differential input for Bun.wrapAnsi and Bun.YAML.parse.
//
// The hand-written corpora next door are curated: somebody thought of each
// case. That is exactly how thirteen defects survived a "2,800 cases,
// byte-identical" claim - the corpus contained no multi-parameter SGR at all,
// so nothing in it could catch a carry model that fabricated a bare 208m carry.
//
// This file generates inputs from a grammar instead, so it produces shapes
// nobody chose. It is run under Bun and under Node + the shim, and the answers
// are compared; tests/test_fuzz_differential.py drives it.
//
// DETERMINISTIC BY CONSTRUCTION. A seeded PRNG, no Date, no Math.random: the
// same seed always produces the same inputs, so a failure is reproducible by
// re-running with the seed printed in the message. A flaky differential would
// be worse than none - nobody chases a failure they cannot reproduce.

"use strict";

// xorshift32: small, fast, and identical across both runtimes.
function makeRandom(seed) {
  let state = seed >>> 0 || 0x9e3779b9;
  return function random() {
    state ^= state << 13; state >>>= 0;
    state ^= state >>> 17;
    state ^= state << 5; state >>>= 0;
    return state / 0x100000000;
  };
}

function makePick(random) {
  return function pick(list) {
    return list[Math.floor(random() * list.length) % list.length];
  };
}

const ESC = "\u001b";
const BEL = "\u0007";

// --- wrapAnsi inputs ---------------------------------------------------------

const SGR = [
  ESC + "[31m", ESC + "[39m", ESC + "[1m", ESC + "[22m", ESC + "[4m",
  ESC + "[24m", ESC + "[41m", ESC + "[49m", ESC + "[0m",
  ESC + "[38;5;208m", ESC + "[48;5;20m", ESC + "[38;2;215;119;87m",
  ESC + "[1;31m", ESC + "[0;32;1m", ESC + "[m", ESC + "[7m", ESC + "[27m",
];

const WORDS = [
  "a", "ab", "abc", "word", "longer", "hyphen-ated", "x", "the", "quick",
  "日本", "日本語", "café", "👍", "👨‍👩‍👧",
  "é", "tab\there", "under_score", "CAPS", "1234",
];

const SEPARATORS = [" ", "  ", "   ", "\n", "\r", "\r\n", "\t"];

function wrapInput(random, pick) {
  const parts = [];
  const chunks = 1 + Math.floor(random() * 8);
  for (let i = 0; i < chunks; i++) {
    const roll = random();
    if (roll < 0.25) parts.push(pick(SGR));
    else if (roll < 0.32) {
      const uri = "https://x.example/" + Math.floor(random() * 100);
      const term = random() < 0.5 ? BEL : ESC + "\\";
      parts.push(ESC + "]8;;" + uri + term + pick(WORDS) + ESC + "]8;;" + term);
    } else if (roll < 0.5) parts.push(pick(SEPARATORS));
    else parts.push(pick(WORDS));
  }
  return parts.join("");
}

const WIDTHS = [0, 1, 2, 3, 4, 5, 7, 10, 16, 40];
const OPTIONS = [
  undefined, {}, { hard: true }, { hard: false }, { trim: false },
  { trim: true }, { wordWrap: false }, { hard: true, trim: false },
  { hard: true, wordWrap: false }, { trim: false, wordWrap: false },
];

// --- YAML inputs -------------------------------------------------------------

const SCALARS = [
  "1", "-1", "0x10", "-0x10", "0o17", "1.5", ".5", "+.5", "1e3", "-0",
  "true", "TRUE", "false", "yes", "no", "on", "off", "null", "~", "Null",
  ".inf", "-.inf", ".nan", "text", "two words", "with-dash", "a#b", "a #b",
  "'quoted'", '"double"', "'it''s"+"'", "12:30", "2026-08-26", "1_000",
  "[1, 2]", "{a: 1}", "{a:1}", "[]", "{}", "", "don't", 'say "hi"',
  "trailing   ", "  leading", "-", "x:", ":x", "a: b", "@at", "0b101",
];

const KEYS = [
  "name", "description", "tools", "allowed-tools", "model", "a", "b",
  '"quoted key"', "'single'", "key_under", "UPPER", "a b", "1", "true",
];

const BLOCK_HEADERS = ["|", ">", "|-", ">-", "|+", ">+"];

function yamlInput(random, pick) {
  const lines = [];
  const entries = 1 + Math.floor(random() * 5);
  for (let i = 0; i < entries; i++) {
    const key = pick(KEYS);
    const roll = random();
    if (roll < 0.55) {
      lines.push(key + ": " + pick(SCALARS));
    } else if (roll < 0.7) {
      lines.push(key + ":");
      const items = 1 + Math.floor(random() * 3);
      const indent = random() < 0.5 ? "  " : "";
      for (let k = 0; k < items; k++) lines.push(indent + "- " + pick(SCALARS));
    } else if (roll < 0.82) {
      lines.push(key + ":");
      const items = 1 + Math.floor(random() * 3);
      for (let k = 0; k < items; k++) lines.push("  " + pick(KEYS) + ": " + pick(SCALARS));
    } else if (roll < 0.92) {
      lines.push(key + ": " + pick(BLOCK_HEADERS));
      const items = 1 + Math.floor(random() * 3);
      for (let k = 0; k < items; k++) {
        lines.push("  " + (random() < 0.2 ? "  " : "") + pick(WORDS));
      }
    } else {
      lines.push("# " + pick(WORDS));
    }
  }
  if (random() < 0.1) lines.unshift("---");
  return lines.join("\n");
}

// --- driver ------------------------------------------------------------------

const seed = Number(process.env.NRC_FUZZ_SEED || 1);
const count = Number(process.env.NRC_FUZZ_COUNT || 400);
const mode = process.env.NRC_FUZZ_MODE || "wrap";

const random = makeRandom(seed);
const pick = makePick(random);

const results = [];
for (let i = 0; i < count; i++) {
  if (mode === "wrap") {
    const input = wrapInput(random, pick);
    const width = pick(WIDTHS);
    const options = pick(OPTIONS);
    let answer;
    try {
      answer = { ok: globalThis.Bun.wrapAnsi(input, width, options) };
    } catch (e) {
      answer = { err: String((e && e.message) || e).slice(0, 160) };
    }
    results.push({ input, width, options: options === undefined ? null : options, answer });
  } else {
    const input = yamlInput(random, pick);
    let answer;
    try {
      const value = globalThis.Bun.YAML.parse(input);
      answer = { ok: encode(value) };
    } catch (e) {
      answer = { err: String((e && e.message) || e).slice(0, 160) };
    }
    results.push({ input, answer });
  }
}

// Type-faithful encoding: JSON.stringify maps Infinity and NaN to null and -0
// to 0, which once hid a real divergence behind an agreeing comparison.
function encode(v) {
  if (v === null) return { t: "null" };
  if (typeof v === "number") {
    if (Number.isNaN(v)) return { t: "nan" };
    if (v === Infinity) return { t: "inf" };
    if (v === -Infinity) return { t: "-inf" };
    if (Object.is(v, -0)) return { t: "-0" };
    return { t: "num", v };
  }
  if (typeof v === "string") return { t: "str", v };
  if (typeof v === "boolean") return { t: "bool", v };
  if (Array.isArray(v)) return { t: "arr", v: v.map(encode) };
  if (typeof v === "object") {
    const keys = Object.keys(v);
    return { t: "obj", k: keys, v: keys.map((k) => encode(v[k])) };
  }
  return { t: typeof v, v: String(v) };
}

process.stdout.write(JSON.stringify(results));
