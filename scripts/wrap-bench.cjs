// A CPU benchmark for Bun.wrapAnsi, runnable under either runtime:
//
//   /path/to/bun scripts/wrap-bench.cjs                  # the oracle
//   node --require "$PWD/scripts/bun-shim.cjs" scripts/wrap-bench.cjs
//
// (the --require path must be absolute or start with ./ - Node resolves a
// bare relative path there as a module NAME, not a file, and fails)
//
// or `make wrap-bench`, which runs both and prints them together.
//
// This exists because wrapAnsi is a TUI hot path - it runs on every render -
// and the shim has already regressed here once without any test noticing: a
// correctness fix (d730e94) doubled the per-frame cost by moving a row-build
// out from behind a guard, and only a hand-written benchmark caught it. The
// differential fuzzing next door proves the shim ANSWERS like Bun; nothing
// else here proves it answers in time.
//
// The inputs are fixed text, not random: this is a regression gauge, so two
// runs on the same revision must be comparable. Every case is shaped like
// something Claude Code actually renders - SGR-colorized prose, bullets and
// syntax-highlighted code - rather than a synthetic escape soup.
//
// The two halves matter separately, because the shim treats them completely
// differently:
//
//   "plain" cases carry SGRs and nothing else. No row can qualify for the
//   midline whitespace collapse (see wrap_lineCanGate in the shim), so the
//   whole collapse pass is skipped and cost is roughly linear.
//
//   "gating" cases carry one non-SGR CSI, which is all it takes to make the
//   collapse pass run. It rebuilds rows from scratch once per escape cluster,
//   so these are QUADRATIC in the shim and linear in Bun. This is the single
//   largest known performance gap and the reason the numbers below diverge so
//   violently as the line grows. Do not delete these cases to make the table
//   look better.
//
// Reference numbers, and how to read a change to them, are in
// docs/findings.md - a bare millisecond count here means nothing without the
// Bun column beside it, since the host and its load move both together.

const ESC = String.fromCharCode(27);
const SGR = (n) => ESC + "[" + n + "m";
const RESET = ESC + "[0m";
const DIM = ESC + "[2m";
// Device Status Report. Any non-SGR CSI does the same job here: it is the
// cheapest way to make a line eligible for the collapse pass.
const CSI = ESC + "[6n";

const WORDS = ("the quick brown fox jumps over lazy dog while parsing tokens and " +
  "emitting rows into a terminal buffer for review").split(" ");

// nWords of prose, every sgrEvery-th word wrapped in an SGR pair.
function prose(nWords, sgrEvery) {
  let out = "";
  for (let i = 0; i < nWords; i++) {
    const w = WORDS[i % WORDS.length];
    out += (i % sgrEvery === 0) ? SGR(31 + (i % 7)) + w + RESET : w;
    out += " ";
  }
  return out;
}

// One line of highlighted source: an SGR pair per token, as a syntax
// highlighter emits.
function codeLine() {
  const toks = ["const", "x", "=", "await", "foo", ".", "bar", "(", "a", ",", "b", ")", ";"];
  return "  " + toks.map((t, i) => SGR(31 + (i % 7)) + t + RESET).join(" ");
}

// A whole render: wrapped prose, then bullets, then a code block.
function frame() {
  const rows = [];
  for (let i = 0; i < 12; i++) rows.push(prose(18, 3));
  for (let i = 0; i < 12; i++) rows.push(DIM + "  - " + RESET + prose(10, 2));
  for (let i = 0; i < 16; i++) rows.push(codeLine());
  return rows.join("\n");
}

const CASES = [
  ["plain  40-line frame", frame()],
  ["plain  code line", codeLine()],
  ["plain  paragraph 120w", prose(120, 3)],
  ["gating line 20w", CSI + prose(20, 3)],
  ["gating line 60w", CSI + prose(60, 3)],
  ["gating line 120w", CSI + prose(120, 3)],
  ["gating line 240w", CSI + prose(240, 3)],
];

const COLS = Number(process.env.COLS || 100);
// Per case. Long enough to be stable, short enough that the whole run is well
// under a minute even on the shim's worst case.
const BUDGET = Number(process.env.BUDGET || 1000);
const MAX_ITERS = 20000;
// The shim's worst case takes ~600ms per call, so a pure time budget would
// time it exactly once and report whatever that single sample happened to be.
// A floor of three keeps the slowest rows meaningful rather than noise.
const MIN_ITERS = 3;

if (typeof Bun === "undefined" || typeof Bun.wrapAnsi !== "function") {
  console.error("no Bun.wrapAnsi in this runtime - run under bun, or under");
  console.error('node with --require "$PWD/scripts/bun-shim.cjs"');
  process.exit(2);
}

console.log("wrapAnsi bench  COLS=" + COLS + "  BUDGET=" + BUDGET + "ms/case");
console.log("");

for (const [label, input] of CASES) {
  for (let i = 0; i < 3; i++) Bun.wrapAnsi(input, COLS, {}); // warm
  const t0 = Date.now();
  let n = 0;
  while ((Date.now() - t0 < BUDGET || n < MIN_ITERS) && n < MAX_ITERS) {
    Bun.wrapAnsi(input, COLS, {});
    n++;
  }
  const ms = (Date.now() - t0) / n;
  console.log(
    "  " + label.padEnd(22) +
    ms.toFixed(3).padStart(10) + " ms/call" +
    ("  len=" + input.length).padEnd(12) +
    "n=" + n
  );
}
