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
  // Single-parameter forms: these are the ones that carry across a row break.
  ESC + "[31m", ESC + "[39m", ESC + "[1m", ESC + "[22m", ESC + "[4m",
  ESC + "[24m", ESC + "[41m", ESC + "[49m", ESC + "[0m", ESC + "[m",
  ESC + "[7m", ESC + "[27m", ESC + "[9m", ESC + "[29m", ESC + "[53m",
  ESC + "[55m", ESC + "[2m", ESC + "[3m", ESC + "[23m", ESC + "[90m",
  ESC + "[97m", ESC + "[100m", ESC + "[107m",
  // Multi-parameter: 256-colour, truecolor and combined forms. A themed TUI
  // emits these constantly, and the first corpus contained not one of them -
  // which is how a carry model that fabricated a nonexistent code survived a
  // "byte-identical over 2,800 cases" claim.
  ESC + "[38;5;208m", ESC + "[48;5;20m", ESC + "[38;2;215;119;87m",
  ESC + "[48;2;0;0;0m", ESC + "[1;31m", ESC + "[0;32;1m", ESC + "[4;38;5;9m",
  ESC + "[1;4;7m", ESC + "[39;49m", ESC + "[0;0m",
  // Non-SGR CSI: zero width, but not colour. They must not be mistaken for
  // carry candidates just because they are escapes.
  ESC + "[2K", ESC + "[1A", ESC + "[?25l", ESC + "[6n", ESC + "[H",
];

const WORDS = [
  "a", "ab", "abc", "word", "longer", "hyphen-ated", "x", "the", "quick",
  "supercalifragilistic", "a-very-long-unbroken-token-here",
  // East Asian wide characters: two columns each, so they land differently
  // against every break rule than ASCII does.
  "\u65e5\u672c", "\u65e5\u672c\u8a9e", "\ud55c\uad6d\uc5b4",
  "\u4e2d\u6587\u5b57", "\uff71\uff72\uff73",
  // Emoji and clusters. The inner word-breaker works on code points while
  // measurement works on clusters, so these break differently from how they
  // measure - which is exactly where the model has been wrong.
  "\ud83d\udc4d", "\ud83d\udc68\u200d\ud83d\udc69\u200d\ud83d\udc67",
  "\ud83c\uddfa\ud83c\uddf8", "1\ufe0f\u20e3", "\ud83d\udc4b\ud83c\udffd",
  "\ud83c\udff3\ufe0f\u200d\ud83c\udf08", "\ud83d\ude00",
  // Combining marks: zero width, attaching to what precedes them.
  "caf\u00e9", "\u00e9", "e\u0301", "a\u0300\u0301\u0302",
  // Formatting characters that are not escapes and not spaces.
  "tab\ttab", "\u00a0nbsp", "zero\u200bwidth", "bidi\u202e",
  "under_score", "CAPS", "1234", ".", "-", "--", "a.b", "'quoted'",
];

const SEPARATORS = [
  " ", "  ", "   ", "    ", "\n", "\n\n", "\r", "\r\n", "\t", " \t ",
  "\u00a0", " \n ", "\n ", " \n",
];

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

// Narrow widths are where the break rules interact hardest; the wide ones are
// what a real terminal uses. Both belong: a rule that only holds at width 80
// is a rule that has not been tested.
const WIDTHS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 12, 16, 20, 40, 80, 120];
const OPTIONS = [
  undefined, {}, { hard: true }, { hard: false }, { trim: false },
  { trim: true }, { wordWrap: false }, { hard: true, trim: false },
  { hard: true, wordWrap: false }, { trim: false, wordWrap: false },
];

// --- YAML inputs -------------------------------------------------------------

const SCALARS = [
  "1", "-1", "0x10", "-0x10", "+0x10", "0o17", "-0o17", "1.5", ".5", "+.5",
  "1e3", "1E3", "1e-3", "-0", "0", "00", "007", "1_000", "0b101", "1.",
  ".inf", "-.inf", ".nan", ".INF", "12345678901234567890",
  "true", "TRUE", "True", "false", "yes", "no", "on", "off", "null", "~",
  "Null", "NULL", "text", "two words", "with-dash", "a#b", "a #b", "a # b",
  "'quoted'", '"double"', "'it''s'", '"a\\nb"', "''", '""',
  "12:30", "2026-08-26", "don't", 'say "hi"', "trailing   ", "  leading",
  "-", "--", "-x", "x:", ":x", "a: b", "@at", "%pct", "`tick",
  "[1, 2]", "{a: 1}", "{a:1}", "[]", "{}", "",
]

const KEYS = [
  "name", "description", "tools", "allowed-tools", "model", "a", "b",
  '"quoted key"', "'single'", "key_under", "UPPER", "a b", "1", "true",
  "null", "0", "a-b", "a.b", "x", "argument-hint", "disable-model-invocation",
]

const BLOCK_HEADERS = ["|", ">", "|-", ">-", "|+", ">+"];

const FLOW_VALUES = [
  "[1, 2]", "{a: 1}", "{a:1}", "[]", "{}", "[a, b]", "{x: y, z: 1}",
  "[[1], [2]]", "{a: {b: 1}}", "[{a: 1}]", "[ 1 , 2 ]", "{ x : 1 }",
];

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
    } else if (roll < 0.96) {
      lines.push(key + ": " + pick(FLOW_VALUES));
    } else {
      lines.push("# " + pick(WORDS));
    }
  }
  if (random() < 0.08) lines.unshift("---");
  if (random() < 0.05) lines.push("");
  if (random() < 0.05) lines.unshift("# leading comment");
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
