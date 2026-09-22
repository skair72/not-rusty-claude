// cell_segmenter_fuzz.cjs - deterministic random case generator for tests/cell_segmenter_oracle.cjs (native vs candidate).
//   bun tests/cell_segmenter_fuzz.cjs START COUNT OUT.json [family]
// Case i is generated from an RNG seeded by the case index i alone, so any case can be
// regenerated in isolation. family (optional) forces one family for every case.
"use strict";
const fs = require("fs");

function rngFor(i) {
  // splitmix32 of the index -> mulberry32 state
  let s = (i + 0x9e3779b9) | 0;
  s = Math.imul(s ^ (s >>> 16), 0x85ebca6b); s = Math.imul(s ^ (s >>> 13), 0xc2b2ae35); s ^= s >>> 16;
  let a = s >>> 0;
  const next = () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const R = {
    f: next,
    int: (n) => Math.floor(next() * n),
    range: (lo, hi) => lo + Math.floor(next() * (hi - lo + 1)),
    pick: (arr) => arr[Math.floor(next() * arr.length)],
    chance: (p) => next() < p,
    weighted(pairs) { let tot = 0; for (const [w] of pairs) tot += w; let x = next() * tot; for (const [w, v] of pairs) { if ((x -= w) < 0) return v; } return pairs[pairs.length - 1][1]; },
  };
  return R;
}

const cp = (c) => String.fromCodePoint(c);
const ESC = "\x1b";
// ---------------------------------------------------------------- text atoms
const ASCII_WORDS = ["the", "Read", "file", "src/index.ts", "ok", "x", "=", "->", "(a)", "[1]", "{}", "#", "*", "0", "42", "a,b", "v1.2.3", "~", "|", "`code`", "$HOME", "100%", " ", "  ", "if", "!", "?", "..."];
const CJK = [0x4e2d, 0x6587, 0x5b57, 0x4e00, 0x3042, 0x30ab, 0xac00, 0xd55c, 0xff21, 0x3000, 0x20000, 0x2fffd, 0x4dc0, 0x9fff, 0x3400];
const EMOJI = [0x1f600, 0x1f44d, 0x1f468, 0x1f469, 0x1f467, 0x1f9d1, 0x1f4bb, 0x2764, 0x1f525, 0x231a, 0x2705, 0x26a0, 0x1f680, 0x1f3f4, 0x261d, 0x1f466, 0x1f3f3, 0x2b50, 0x1f308];
const COMBINING = [0x301, 0x302, 0x308, 0x20dd, 0x20e3, 0x5b0, 0x64b, 0x93f, 0x94d, 0x941, 0x903, 0xe33, 0x302e, 0x1ab0, 0x1ae0, 0xfe0e, 0xfe0f, 0x200d, 0x200c, 0x1f3fb, 0x1f3fd, 0xe0067, 0xe007f, 0x11b61, 0x16d63];
const PREPEND = [0x600, 0x603, 0x605, 0x6dd, 0x70f, 0x890, 0x8e2, 0x110bd, 0x111c2, 0x11a3a, 0x113d1];
const INDIC = [0x915, 0x937, 0x924, 0x930, 0x995, 0xa95, 0xd15, 0xd3a, 0xe01, 0x1780];
const HANGUL = [0x1100, 0x1161, 0x11a8, 0xac00, 0xac01, 0xd7b0, 0x1160];
const ZW = [0x200b, 0xad, 0xfeff, 0x2060, 0x200e, 0x200f, 0x180e, 0x2028, 0x2029, 0xe0001, 0x1d173];
const SUBST = [0x61c, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069];
const CONTROLS = [0x0, 0x1, 0x7, 0x8, 0xa, 0xd, 0x18, 0x1a, 0x1c, 0x1f, 0x7f, 0x80, 0x85, 0x9c, 0x9a];
const MISC = [0xfffd, 0xfffe, 0xffff, 0x10ffff, 0xe000, 0xf0000, 0x378, 0xa0, 0xa7, 0xb1, 0xe9, 0x3b1, 0x410, 0x2500, 0x2502, 0x256d, 0x25cf, 0x23bf, 0x2026, 0x2192, 0xa9, 0xae, 0x2122, 0x1f1e6, 0x1f1fa, 0x1f1f8, 0x1f1ef, 0x50000];
const HEB = [0x5d0, 0x5d1, 0x5d2, 0x5d3, 0x5e9, 0x5dc, 0x5d5, 0x5dd, 0x5b4, 0x5bc];
const ARA = [0x627, 0x644, 0x628, 0x62a, 0x645, 0x660, 0x661, 0x669, 0x6f1, 0x60c, 0x60d, 0x64b, 0x670, 0x6dd, 0x600];
const RTL_OTHER = [0x7c1, 0x7ca, 0x710, 0x712, 0x780, 0x800, 0x840, 0x1e900, 0x10800, 0x10d00, 0x10ec5, 0x10ed0, 0x870, 0x8a0, 0xfb1d, 0xfb50, 0xfe70];
const BIDI_NEUTRAL = [0x20, 0x21, 0x22, 0x28, 0x29, 0x5b, 0x5d, 0x7b, 0x7d, 0x3c, 0x3e, 0x2b, 0x2d, 0x24, 0x25, 0x23, 0x2c, 0x2e, 0x3a, 0x2f, 0x30, 0x31, 0x39, 0xb0, 0x2030, 0x20ac, 0x2329, 0x232a, 0x3008, 0x3009, 0xff08, 0xff09, 0x2e55, 0x2e56, 0x9, 0x3000, 0x2029, 0x1f600];

function atom(R) {
  return R.weighted([
    [14, () => R.pick(ASCII_WORDS)],
    [3, () => { let s = ""; for (let k = R.range(1, 12); k > 0; k--) s += String.fromCharCode(R.range(0x20, 0x7e)); return s; }],
    [5, () => cp(R.pick(CJK))],
    [5, () => { // emoji, ZWJ sequences, modifiers, flags, keycaps
      return R.weighted([
        [4, () => cp(R.pick(EMOJI))],
        [2, () => cp(R.pick(EMOJI)) + "\u200d" + cp(R.pick(EMOJI)) + (R.chance(0.4) ? "\u200d" + cp(R.pick(EMOJI)) : "")],
        [1, () => cp(R.pick([0x1f44d, 0x261d, 0x1f466, 0x1f468])) + cp(R.range(0x1f3fb, 0x1f3ff))],
        [1, () => cp(R.range(0x1f1e6, 0x1f1ff)) + cp(R.range(0x1f1e6, 0x1f1ff))],
        [1, () => R.pick(["#", "1", "*", "9"]) + (R.chance(0.6) ? "\ufe0f" : "") + "\u20e3"],
        [1, () => cp(R.pick([0x2764, 0xa9, 0xae, 0x231a, 0x2122])) + R.pick(["\ufe0f", "\ufe0e"])],
        [1, () => "\u{1f3f4}\u{e0067}\u{e0062}\u{e0065}\u{e006e}\u{e0067}\u{e007f}"],
      ])();
    }],
    [4, () => cp(R.pick(COMBINING))],
    [2, () => cp(R.pick(PREPEND))],
    [2, () => cp(R.pick(INDIC)) + (R.chance(0.5) ? "\u094d" + cp(R.pick(INDIC)) : "")],
    [2, () => cp(R.pick(HANGUL))],
    [2, () => cp(R.pick(ZW))],
    [2, () => cp(R.pick(SUBST))],
    [2, () => cp(R.pick(CONTROLS))],
    [3, () => "\t"],
    [3, () => cp(R.pick(MISC))],
    [2, () => cp(R.pick(HEB))],
    [2, () => cp(R.pick(ARA))],
    [1, () => cp(R.pick(RTL_OTHER))],
    [1, () => String.fromCharCode(R.range(0xd800, 0xdfff))], // lone surrogate
    [2, () => { // random code point from anywhere
      const c = R.weighted([[3, () => R.range(0xa0, 0x2fff)], [2, () => R.range(0x3000, 0xffff)], [2, () => R.range(0x10000, 0x1ffff)], [1, () => R.range(0x20000, 0x10ffff)]])();
      return c >= 0xd800 && c <= 0xdfff ? "x" : cp(c);
    }],
  ])();
}

// ---------------------------------------------------------------- escapes
function sgrParams(R) {
  return R.weighted([
    [6, () => String(R.pick([0, 1, 2, 3, 4, 5, 7, 8, 9, 21, 22, 23, 24, 27, 29, 31, 32, 33, 34, 39, 41, 42, 49, 53, 55, 90, 97, 100, 107]))],
    [2, () => ""],
    [3, () => `38;5;${R.int(256)}`],
    [3, () => `38;2;${R.int(256)};${R.int(256)};${R.int(256)}`],
    [2, () => `48;2;${R.int(256)};${R.int(256)};${R.int(256)}`],
    [1, () => `58;5;${R.int(300)}`],
    [2, () => `1;${R.pick([31, 32, 38])}${R.chance(0.5) ? ";5;" + R.int(256) : ""}`],
    [2, () => R.pick(["4:3", "38:2::10:20:30", "38:5:1", "58:2::1:2:3", "4:0", "0:1", "22:1", ":4", ":", "1:2", "39:1", "038:2::1:2:3"])],
    [1, () => Array.from({ length: R.range(30, 36) }, () => R.pick(["1", "", "2", "0"])).join(";")],
    [1, () => R.pick(["256", "1;256", "256;1", "0256", "0255", "65536", "0018446744073709551616", "300:1", "999"])],
    [2, () => Array.from({ length: R.range(2, 6) }, () => R.pick(["", "0", "01", "1", "5", "6", "10", "11", "20", "38", "5", "2", "73", "74", "75", "51", "52", "54", "59", "128", "255", "31", "39"])).join(";")],
    [1, () => R.pick(["38", "38;5", "38;2;1;2", "38;5;", "38;2;;;", "38;;1", "38;5;1;", "48;5", "58"])],
  ])();
}
function escape(R) {
  return R.weighted([
    [16, () => ESC + "[" + sgrParams(R) + "m"],
    [2, () => "\x9b" + sgrParams(R) + "m"],
    [4, () => { // OSC 8
      const term = R.pick(["\x07", "\x07", ESC + "\\", "\x9c"]);
      const uri = R.weighted([[5, () => "https://example.com/" + R.int(5)], [2, () => ""], [1, () => "u://" + cp(R.pick(CJK)) + ";x"], [1, () => "http://a b\t" + cp(0x85)]])();
      const params = R.pick(["", "", "id=1", "id=x:y", "a;b"]);
      return R.weighted([[8, () => (R.chance(0.9) ? ESC + "]" : "\x9d") + "8;" + params + ";" + uri + term], [1, () => ESC + "]8;" + uri + term], [1, () => ESC + "]8" + term], [1, () => ESC + "]08;;x" + term], [1, () => ESC + "]8;;" + uri + R.pick(["\x18", ESC + "[", ""])]])();
    }],
    [2, () => ESC + "]" + R.pick(["0;title", "2;t", "52;c;AAA", "133;A", "9;4;1", "1337;x", ""]) + R.pick(["\x07", ESC + "\\", "\x9c", "\x18", ""])],
    [4, () => ESC + "[" + R.pick(["2J", "H", "1;1H", "K", "?25l", "?25h", "?2026h", "?2026l", ">1u", "<u", "200~", "201~", "8;1;1t", "1A", "3C", ">4;2m", "?1m", "1 q", "1$m", "=1m", "<1m", "1;?2m", "1 2m", "3\n1m", "31\\", "3中m", "31\x9d8;;x\x07m", "31\x18m"])],
    [2, () => ESC + R.pick(["P", "X", "^", "_"]) + R.pick(["abc", "q", "1;2|x", "a\x07b"]) + R.pick([ESC + "\\", "\x9c", "", "\x18", ESC + "x"])],
    [1, () => cp(R.pick([0x90, 0x98, 0x9e, 0x9f])) + "data" + R.pick(["\x9c", ESC + "\\", ""])],
    [2, () => ESC + R.pick(["(B", ")0", "7", "8", "=", ">", "c", "M", "\\", " X", "(", "#8", "%G", "\x01", "\t", "\x7f", "中", "\x9b", ESC])],
    [1, () => R.pick([ESC, ESC + "[", ESC + "[31", ESC + "]", ESC + "]8;;http://x", ESC + "P", "\x9b", "\x9d", ESC + "("])], // truncated
  ])();
}

function randomText(R, opts = {}) {
  const n = opts.len ?? R.weighted([[4, () => R.range(0, 6)], [4, () => R.range(4, 20)], [2, () => R.range(15, 60)], [1, () => R.range(60, 200)]])();
  const escRate = opts.escRate ?? R.pick([0, 0.05, 0.15, 0.3, 0.5]);
  let s = "";
  for (let k = 0; k < n; k++) s += R.chance(escRate) ? escape(R) : atom(R);
  return s;
}
function bidiText(R) {
  const n = R.range(1, 14);
  let s = "";
  for (let k = 0; k < n; k++) {
    s += R.weighted([
      [4, () => cp(R.pick(HEB))], [3, () => cp(R.pick(ARA))], [1, () => cp(R.pick(RTL_OTHER))],
      [4, () => R.pick(["a", "b", "abc", "Z", "x1"])], [5, () => cp(R.pick(BIDI_NEUTRAL))],
      [2, () => cp(R.pick(ZW))], [2, () => cp(R.pick(SUBST))], [2, () => cp(R.pick(PREPEND))],
      [2, () => cp(R.pick(COMBINING))], [1, () => cp(R.pick(CONTROLS))], [1, () => ESC + "[" + sgrParams(R) + "m"],
      [1, () => cp(R.pick(CJK))], [1, () => cp(R.pick(MISC))], [1, () => cp(R.pick([0x10ffff, 0xfdd0, 0xe0001, 0x1d173, 0x110bd]))],
      [1, () => String.fromCharCode(R.range(0xd800, 0xdfff))],
    ])();
  }
  return s;
}
function claudeLine(R) {
  const fg = () => ESC + "[38;2;" + R.int(256) + ";" + R.int(256) + ";" + R.int(256) + "m";
  return R.weighted([
    [2, () => fg() + ESC + "[1m● " + ESC + "[22mRead" + ESC + "[39m(" + "src/" + R.pick(["a", "bb", "中文"]) + ".ts)"],
    [2, () => "  ⎿  Read " + R.int(999) + " lines" + ESC + "[0m"],
    [2, () => ESC + "[48;2;0;64;0m" + fg() + "+ " + randomText(R, { len: R.range(3, 20), escRate: 0 }) + ESC + "[49m" + ESC + "[39m"],
    [2, () => "╭" + "─".repeat(R.range(1, 100)) + "╮"],
    [2, () => "│ " + randomText(R, { len: R.range(2, 12), escRate: 0.1 }) + " │"],
    [2, () => ESC + "]8;;https://docs.example/" + R.int(9) + "\x07" + ESC + "[4mlink" + ESC + "[24m" + ESC + "]8;;\x07 and **" + ESC + "[1mbold" + ESC + "[22m** `" + ESC + "[3mcode" + ESC + "[23m`"],
    [1, () => R.pick(["✻", "✽", "✶", "·", "✢"]) + " " + fg() + "Thinking…" + ESC + "[39m " + ESC + "[2m(" + R.int(60) + "s · esc to interrupt)" + ESC + "[22m"],
    [1, () => "\t" + "x".repeat(R.int(10)) + "\t|"],
    [1, () => "a".repeat(R.range(200, 700))],
  ])();
}

// ---------------------------------------------------------------- screens
function initScreen(R, w, h) {
  if (R.chance(0.25)) return undefined;
  const out = [];
  for (let row = 0; row < h; row++) {
    let c = 0;
    while (c < w) {
      const kind = R.weighted([[5, "n"], [3, "w"], [1, "e"], [1, "h"], [1, "r"]]);
      if (kind === "w" && c + 1 < w) { out.push(R.range(100, 900), (R.int(20) << 17) | (R.int(4) << 2) | 1, 1, 2); c += 2; continue; }
      if (kind === "e") out.push(0, 0);
      else if (kind === "h") out.push(0, 3);
      else if (kind === "r") out.push(R.range(0, 2000), R.int(0x7fffffff) * (R.chance(0.2) ? -1 : 1) | 0);
      else out.push(R.range(100, 900), (R.int(20) << 17) | (R.int(4) << 2));
      c++;
    }
  }
  return out;
}
function paintStep(R, keep) {
  const w = R.weighted([[3, () => R.range(1, 12)], [3, () => R.range(10, 40)], [1, () => R.range(80, 220)], [1, () => 0]])();
  const h = R.range(1, 3);
  const st = { op: "paint", w, h, x: R.weighted([[6, () => R.range(0, Math.max(0, w))], [2, () => R.range(-10, -1)], [1, () => R.range(w, w + 10)], [1, () => R.pick([-9, -8, -16, 7, 8, 9, 15, 16])]])(),
    y: R.weighted([[8, () => R.range(0, h - 1)], [1, () => h], [1, () => -1]])(), charMap: R.pick(["offset", "identity"]), wordMode: R.pick(["distinct", "distinct", "zero"]) };
  const init = initScreen(R, w, h);
  if (init) st.init = init;
  if (keep) st.keep = true;
  if (R.chance(0.02)) st.arg7 = R.pick([true, 0, "x"]);
  return st;
}
function setCellStep(R, keep) {
  const w = R.range(1, 12), h = R.range(1, 3);
  const st = { op: "setCell", w, h, x: R.weighted([[8, () => R.range(0, w - 1)], [1, () => R.range(-3, -1)], [1, () => R.range(w, w + 3)]])(),
    y: R.weighted([[8, () => R.range(0, h - 1)], [1, () => h], [1, () => -1]])(),
    char: R.pick([0, 1, 98, 500, 1000, R.int(100000)]),
    word: R.weighted([[4, () => (R.int(8) << 17) | (R.int(4) << 2)], [2, () => (R.int(8) << 17) | 1], [1, () => 2], [1, () => 3], [1, () => R.int(0x7fffffff) | 0], [1, () => -R.int(0x7fffffff)]])() };
  const init = initScreen(R, w, h);
  if (init) st.init = init;
  if (keep) st.keep = true;
  return st;
}

// ---------------------------------------------------------------- cases
const FAMILIES = {
  text(R) { // single / few segment steps over random mixed text, tables at the end
    const steps = [];
    for (let k = R.range(1, 3); k > 0; k--) steps.push({ op: "segment", text: randomText(R), reordered: R.chance(0.25) });
    steps.push({ op: "tables" });
    return steps;
  },
  escapes(R) {
    const steps = [];
    for (let k = R.range(1, 4); k > 0; k--) steps.push({ op: "segment", text: randomText(R, { escRate: R.pick([0.3, 0.5, 0.7]) }), reordered: R.chance(0.1) });
    steps.push({ op: "tables" });
    return steps;
  },
  bidi(R) {
    const steps = [];
    for (let k = R.range(1, 3); k > 0; k--) steps.push({ op: "segment", text: bidiText(R), reordered: true });
    steps.push({ op: "tables" });
    return steps;
  },
  paint(R) {
    const steps = [];
    let prevPaint = false;
    for (let k = R.range(1, 4); k > 0; k--) {
      steps.push({ op: "segment", text: R.chance(0.3) ? claudeLine(R) : randomText(R, { escRate: R.pick([0, 0.1, 0.3]) }), reordered: R.chance(0.15), ...(R.chance(0.1) ? { cap: R.range(0, 8) } : {}) });
      steps.push(paintStep(R, prevPaint && R.chance(0.4)));
      prevPaint = true;
      if (R.chance(0.3)) steps.push(setCellStep(R, R.chance(0.5)));
    }
    return steps;
  },
  setCell(R) {
    const steps = [];
    for (let k = R.range(1, 6); k > 0; k--) steps.push(setCellStep(R, k > 1 && R.chance(0.5)));
    return steps;
  },
  capacity(R) { // short buffers: return value, interning order, later ids
    const steps = [];
    for (let k = R.range(1, 4); k > 0; k--) {
      const text = R.chance(0.3) ? claudeLine(R) : randomText(R, { escRate: R.pick([0, 0.1, 0.3]) });
      steps.push({ op: "segment", text, cap: R.weighted([[3, () => R.range(0, 6)], [3, () => R.range(3, 40)], [1, () => 256]])(), reordered: R.chance(0.2) });
      if (R.chance(0.5)) steps.push({ op: "tables" });
      steps.push({ op: "segment", text, reordered: R.chance(0.2), cap: 1024 });
    }
    steps.push({ op: "tables" });
    return steps;
  },
  lifecycle(R) { // one instance, many lines, table growth, paints in between
    const steps = [];
    for (let k = R.range(5, 20); k > 0; k--) {
      const r = R.f();
      if (r < 0.6) steps.push({ op: "segment", text: R.chance(0.5) ? claudeLine(R) : randomText(R), reordered: R.chance(0.2), ...(R.chance(0.1) ? { cap: R.range(0, 30) } : {}) });
      else if (r < 0.85) steps.push(paintStep(R, R.chance(0.3)));
      else if (r < 0.95) steps.push({ op: "tables" });
      else steps.push(setCellStep(R, false));
    }
    steps.push({ op: "tables" });
    return steps;
  },
  claude(R) {
    const steps = [];
    for (let k = R.range(1, 5); k > 0; k--) {
      steps.push({ op: "segment", text: claudeLine(R), reordered: R.chance(0.3) });
      if (R.chance(0.6)) steps.push(paintStep(R, false));
    }
    steps.push({ op: "tables" });
    return steps;
  },
};
const KERNELISH = [0x4e00, 0x4e8c, 0x4e09, 0x6587, 0x5b57, 0x2500, 0x2502, 0x256d, 0x25cf, 0x23bf, 0xe9, 0xff, 0x3b1, 0x436, 0x5d0, 0x627, 0x3042, 0xff21, 0x2026, 0x2192, 0xa7, 0xfffd];
function kernelRun(R) {
  let s = "";
  for (let k = R.range(1, 12); k > 0; k--) s += cp(R.pick(KERNELISH));
  return s;
}
function kernelText(R) {
  let s = "";
  for (let k = R.range(1, 10); k > 0; k--) {
    s += R.weighted([
      [6, () => kernelRun(R)], [3, () => R.pick(["abc", "a", "ab", "hello world", " ", "x1"])],
      [2, () => cp(R.pick([0x301, 0x94d, 0x903, 0x200d, 0xfe0f, 0x5b4]))], [2, () => ESC + "[" + sgrParams(R) + "m"],
      [1, () => ESC + "]8;;u://" + R.int(3) + "\x07"], [1, () => cp(R.pick([0x600, 0x70f, 0x110bd]))],
      [1, () => "\t"], [1, () => cp(R.pick(CONTROLS))], [1, () => String.fromCharCode(R.range(0xd800, 0xdfff))],
      [1, () => cp(R.pick(EMOJI))], [1, () => cp(R.pick([0xac00, 0x1100]))], [1, () => cp(R.pick(SUBST))],
    ])();
  }
  return s;
}
FAMILIES.kernelcap = (R) => {
  const steps = [];
  for (let k = R.range(1, 4); k > 0; k--) {
    const text = kernelText(R);
    steps.push({ op: "segment", text, cap: R.weighted([[4, () => R.range(0, 12)], [2, () => R.range(10, 60)]])(), reordered: R.chance(0.2) });
    if (R.chance(0.6)) steps.push({ op: "tables" });
    steps.push({ op: "segment", text, reordered: R.chance(0.2) });
  }
  steps.push({ op: "tables" });
  return steps;
};
FAMILIES.long256 = (R) => { // Claude: 256-cell buffers, retry with a bigger buffer on a negative return
  const steps = [];
  for (let k = R.range(1, 3); k > 0; k--) {
    let text = "";
    while (text.length < R.range(260, 900)) text += R.chance(0.5) ? kernelText(R) : R.chance(0.5) ? claudeLine(R) : randomText(R, { len: 20 });
    steps.push({ op: "segment", text, cap: 256, reordered: R.chance(0.2) });
    steps.push({ op: "tables" });
    steps.push({ op: "segment", text, cap: 2048, reordered: R.chance(0.2) });
    if (R.chance(0.5)) steps.push(paintStep(R, false));
  }
  steps.push({ op: "tables" });
  return steps;
};
const EXPLICIT = [0x202a, 0x202b, 0x202c, 0x202d, 0x202e, 0x2066, 0x2067, 0x2068, 0x2069, 0x061c, 0x200e, 0x200f];
FAMILIES.bidiexplicit = (R) => { // non-Claude config: explicit embeddings/overrides/isolates reach ICU
  const steps = [];
  for (let k = R.range(1, 3); k > 0; k--) {
    let t = "";
    for (let q = R.range(1, 16); q > 0; q--) {
      t += R.weighted([[5, () => cp(R.pick(EXPLICIT))], [4, () => cp(R.pick(HEB))], [3, () => cp(R.pick(ARA))], [4, () => R.pick(["a", "b", "xyz", "1", "23", " "])],
        [4, () => cp(R.pick(BIDI_NEUTRAL))], [1, () => cp(R.pick(PREPEND))], [1, () => cp(R.pick(COMBINING))], [1, () => cp(R.pick(ZW))],
        [1, () => cp(R.pick(CONTROLS))], [1, () => ESC + "[" + sgrParams(R) + "m"], [1, () => cp(R.pick([0x10ffff, 0xfffe, 0xe0001]))], [1, () => cp(R.pick(RTL_OTHER))]])();
    }
    steps.push({ op: "segment", text: t, reordered: true });
  }
  steps.push({ op: "tables" });
  return steps;
};
// The reorder gate: a written cell starting with R/AL (RLM included) AND a raw UTF-16 unit in the
// RTL blocks anywhere in the input (escape payloads, substitutes, lone lead surrogates included).
const RLM_FOLLOW = [0x903, 0x302e, 0x20e3, 0xe33, 0x301, 0x94d, 0x200d, 0x1f3fb, 0x5b4, 0x64b, 0x93f];
const BLOCK_NONSTRONG = [0x591, 0x5bd, 0x606, 0x609, 0x60c, 0x60e, 0x610, 0x64b, 0x66a, 0x6de, 0x6f0, 0x6f9, 0x7f6, 0x7fd, 0x8ff, 0xfb1e, 0xfb29, 0xfd3e, 0xfdd0, 0xfdfd, 0xfeff, 0x1091f, 0x10a01, 0x10b39, 0x10d6e, 0x10ed0, 0x10f46, 0x1e8d0, 0x1e944, 0x1eef0];
const EDGE_UNITS = [0x58f, 0x590, 0x5d0, 0x660, 0x8ff, 0x900, 0xfb1c, 0xfb1d, 0xfdff, 0xfe00, 0xfe6f, 0xfe70, 0xfefe, 0xfeff, 0x200f, 0xd801, 0xd802, 0xd803, 0xd804, 0xd839, 0xd83a, 0xd83b, 0xd83c, 0xdc00, 0xdd1f];
function gateText(R) {
  let t = "";
  const u = () => String.fromCharCode(R.pick(EDGE_UNITS));
  for (let q = R.range(1, 12); q > 0; q--) {
    t += R.weighted([
      [5, () => "\u200f" + (R.chance(0.8) ? cp(R.pick(RLM_FOLLOW)) : "")],
      [4, () => cp(R.pick(BLOCK_NONSTRONG))],
      [2, () => ESC + "]0;" + u() + R.pick(["\x07", ESC + "\\", "\x9c"])],
      [2, () => ESC + "]8;;http://x/" + u() + "\x07L" + ESC + "]8;;\x07"],
      [1, () => ESC + "[" + u() + "m"], [1, () => ESC + "P" + u() + ESC + "\\"], [1, () => ESC + u()],
      [2, () => u()],
      [5, () => R.pick(["a", "b", "xyz", "1", "23", " ", "  ", "(", ")", "\t"])],
      [2, () => cp(R.pick(BIDI_NEUTRAL))], [1, () => cp(R.pick(ARA))], [1, () => cp(R.pick(HEB))], [1, () => cp(R.pick(RTL_OTHER))],
      [1, () => cp(R.pick(PREPEND))], [1, () => cp(R.pick(COMBINING))], [1, () => cp(R.pick(ZW))], [1, () => cp(R.pick(CONTROLS))],
      [1, () => cp(R.pick(EXPLICIT))], [1, () => ESC + "[" + sgrParams(R) + "m"],
    ])();
  }
  return t;
}
FAMILIES.bidigate = (R) => {
  const steps = [];
  for (let k = R.range(1, 4); k > 0; k--) {
    const text = gateText(R);
    steps.push({ op: "segment", text, reordered: true, ...(R.chance(0.15) ? { cap: R.range(0, 8) } : {}) });
    if (R.chance(0.3)) steps.push({ op: "segment", text, reordered: false });
  }
  steps.push({ op: "tables" });
  return steps;
};
// Paint with wide and extra-wide cells (a base + U+302E sums to width 3, CJK + U+302E to 4) at
// negative / edge x over screens full of WIDE/TAIL/HEAD pairs: clipping and orphan cleanup.
const WIDE_ATOMS = ["a\u302e", "\u4e2d\u302e", "\u4e2d", "\u6587", "a", "b", "\u302e\u302e", "x\u302e\u302e", "\t", "\u{1f600}", " ", "\u0301", "\x1b[31m", "\x1b[0m"];
function wideScreen(R, w, h) {
  const out = [];
  for (let row = 0; row < h; row++) {
    let c = 0;
    while (c < w) {
      const k = R.weighted([[5, "w"], [2, "n"], [1, "t"], [1, "h"], [1, "e"]]);
      if (k === "w" && c + 1 < w) { out.push(R.range(100, 900), (R.int(20) << 17) | 1, 1, 2); c += 2; continue; }
      if (k === "t") out.push(1, 2); else if (k === "h") out.push(0, 3); else if (k === "e") out.push(0, 0);
      else out.push(R.range(100, 900), R.int(20) << 17);
      c++;
    }
  }
  return out;
}
FAMILIES.paintwide = (R) => {
  const steps = [];
  let prev = false;
  for (let k = R.range(1, 4); k > 0; k--) {
    let text = "";
    for (let q = R.range(1, 8); q > 0; q--) text += R.pick(WIDE_ATOMS);
    steps.push({ op: "segment", text, reordered: false });
    const w = R.range(1, 16), h = R.range(1, 2);
    const st = { op: "paint", w, h, x: R.weighted([[4, () => R.range(-8, -1)], [3, () => R.range(0, w)], [1, () => R.range(w - 4, w + 2)]])(), y: R.range(0, h - 1),
      charMap: R.pick(["offset", "identity"]), wordMode: R.pick(["distinct", "zero"]) };
    if (prev && R.chance(0.3)) st.keep = true; else st.init = wideScreen(R, w, h);
    steps.push(st);
    prev = true;
  }
  return steps;
};
const WEIGHTS = [[5, "text"], [3, "escapes"], [2, "bidi"], [4, "paint"], [1, "setCell"], [2, "capacity"], [2, "lifecycle"], [2, "claude"], [2, "kernelcap"], [1, "long256"], [1, "bidiexplicit"], [1, "bidigate"], [1, "paintwide"]];

function genCase(i, family) {
  const R = rngFor(i);
  const fam = family || R.weighted(WEIGHTS);
  const c = { id: `fz${i}.${fam}`, steps: FAMILIES[fam](R) };
  if (fam === "bidiexplicit") { c.opts = { ambiguousIsNarrow: true, substitute: R.pick([[], [], [[0x202e, 0x202e]]]) }; return c; }
  if (fam === "bidigate") { c.opts = { ambiguousIsNarrow: true, substitute: R.pick([[], [], [[0x5d0, 0x5ea]], [[0x600, 0x6ff]], [[0x200f, 0x200f]], [[1564, 1564], [8234, 8238], [8294, 8297]]]) }; return c; }
  if (R.chance(0.01)) c.opts = { ambiguousIsNarrow: R.chance(0.5), substitute: R.pick([[], null, [[1564, 1564], [8234, 8238], [8294, 8297]], [[0x4e2d, 0x4e2d], [0x301, 0x301]]]), screen: { widthMask: 3, narrow: 0, wide: 1, spacerTail: 2, spacerHead: 3, emptyCharIndex: 0, spacerCharIndex: 1, emptyWord: 0, tabWidth: 8 } };
  return c;
}

if (require.main === module) {
  const [start, count, out, family] = process.argv.slice(2);
  const cases = [];
  for (let i = +start; i < +start + +count; i++) cases.push(genCase(i, family));
  fs.writeFileSync(out, JSON.stringify(cases));
}
module.exports = { genCase, rngFor, randomText, bidiText, claudeLine };
