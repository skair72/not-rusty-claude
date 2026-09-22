// cell_segmenter_oracle.cjs - run CellSegmenter cases and print results as JSONL.
//
// Runs INSIDE the native binary - whose bun-internal runtime has the real
// Bun.ant - as `BUN_OPTIONS="--preload <this file>" claude --version` (the
// file exits before Claude's own main starts; scripts/harness.py does exactly
// this), and under stock Bun with the port preloaded:
// `bun --preload scripts/bun-ant.mjs <this file>`. Same cases, same output
// format, so the two outputs can be compared byte for byte.
//
// Input: NRC_CASES=<path to .json array or .jsonl of cases>
// Output: NRC_OUT=<path> (JSONL, one line per case) - or stdout if unset.
//
// Case shape:
// { "id": "...", "opts": {...optional CellSegmenter ctor override...},
//   "steps": [
//     {"op":"segment","text":"...","reordered":false,"cap":512},
//     {"op":"paint","w":20,"h":2,"x":0,"y":0,"arg7":null,
//      "charMap":"offset"|"identity", "wordMode":"distinct"|"zero",
//      "init":[...optional initial screen Int32 pairs...], "keep":false},
//     {"op":"setCell","w":20,"h":2,"x":3,"y":0,"char":98,"word":123,
//      "init":[...], "keep":false},
//     {"op":"tables"}
//   ]}
// `keep: true` reuses the screen left by the previous paint/setCell step.
const fs = require("fs");

const DEFAULT_OPTS = {
  ambiguousIsNarrow: true,
  substitute: [[1564, 1564], [8234, 8238], [8294, 8297]],
  screen: { widthMask: 3, narrow: 0, wide: 1, spacerTail: 2, spacerHead: 3,
            emptyCharIndex: 0, spacerCharIndex: 1, emptyWord: 0, tabWidth: 8 },
};

function loadCases(p) {
  const raw = fs.readFileSync(p, "utf8");
  if (p.endsWith(".jsonl")) return raw.split("\n").filter(Boolean).map((l) => JSON.parse(l));
  return JSON.parse(raw);
}

function errStr(e) {
  return e && typeof e === "object" ? `${e.name}: ${e.message}` : String(e);
}

function runCase(c) {
  const out = { id: c.id, results: [] };
  let seg;
  try {
    seg = new Bun.ant.CellSegmenter(c.opts ?? DEFAULT_OPTS);
  } catch (e) {
    out.ctorError = errStr(e);
    return out;
  }
  let cells = new Int32Array(1024), runs = new Int32Array(1024), count = 0;
  let screen = null;
  for (const st of c.steps) {
    const r = { op: st.op };
    try {
      if (st.op === "segment") {
        const cap = st.cap ?? 512;
        cells = new Int32Array(2 * cap); runs = new Int32Array(2 * cap);
        const n = seg.segment(st.text, cells, runs, !!st.reordered);
        r.ret = n;
        count = n;
        if (n > 0) {
          r.cells = Array.from(cells.subarray(0, 2 * n));
          let maxRun = 0;
          for (let i = 0; i < n; i++) maxRun = Math.max(maxRun, cells[2 * i + 1] >>> 10);
          r.runs = Array.from(runs.subarray(0, 2 * (maxRun + 1)));
        }
      } else if (st.op === "paint" || st.op === "setCell") {
        const w = st.w, h = st.h;
        if (!st.keep || !screen || screen.length !== w * h * 2) {
          screen = new Int32Array(w * h * 2);
          if (st.init) screen.set(st.init.slice(0, w * h * 2));
        }
        if (st.op === "paint") {
          const g = seg.graphemes.length;
          const charMap = new Int32Array(Math.max(g, 1));
          for (let i = 0; i < g; i++) charMap[i] = st.charMap === "identity" ? i : 1000 + i;
          let maxRun = 0;
          for (let i = 0; i < count; i++) maxRun = Math.max(maxRun, cells[2 * i + 1] >>> 10);
          const words = new Int32Array(Math.max(maxRun + 1, 1));
          for (let m = 0; m <= maxRun; m++)
            words[m] = st.wordMode === "zero" ? 0 : ((m + 1) << 17) | ((runs[2 * m + 1] & 32767) << 2);
          const arg7 = st.arg7 === undefined || st.arg7 === null ? undefined : st.arg7;
          r.ret = seg.paint(screen, w, st.x, st.y, cells, count, arg7, charMap, words);
        } else {
          r.ret = seg.setCell(screen, w, st.x, st.y, st.char, st.word);
        }
        if (typeof r.ret === "number") {
          r.lo = r.ret % 1048576;
          r.dmgStart = Math.floor(r.ret / 1048576) % 65536;
          r.dmgEnd = Math.floor(r.ret / 68719476736);
        }
        r.screen = Array.from(screen);
      } else if (st.op === "tables") {
        r.graphemes = seg.graphemes.slice(98);
        r.graphemesLen = seg.graphemes.length;
        r.sgrKeys = seg.sgrKeys.slice();
        r.sgrCloseKeys = seg.sgrCloseKeys.slice();
        r.uris = seg.uris.slice();
      } else {
        r.error = "unknown op";
      }
    } catch (e) {
      r.error = errStr(e);
    }
    out.results.push(r);
  }
  return out;
}

const cases = loadCases(process.env.NRC_CASES);
const lines = cases.map((c) => JSON.stringify(runCase(c)));
if (process.env.NRC_OUT) fs.writeFileSync(process.env.NRC_OUT, lines.join("\n") + "\n");
else console.log(lines.join("\n"));
process.exit(0);
