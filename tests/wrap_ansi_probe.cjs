// Run under Bun (native Bun.wrapAnsi) and under Node + scripts/bun-shim.cjs.
// Prints one JSON array; tests/test_wrap_ansi.py compares the two byte for byte.
const { strings, widths, optionSets, cases } = require("./wrap_ansi_corpus.cjs");

const wrapAnsi = globalThis.Bun && globalThis.Bun.wrapAnsi;
if (typeof wrapAnsi !== "function") {
  console.error("no Bun.wrapAnsi: run under Bun, or under Node with the shim preloaded");
  process.exit(2);
}

const out = [];
for (const c of cases) {
  try {
    out.push({ ok: wrapAnsi(strings[c.s], widths[c.w], optionSets[c.o]) });
  } catch (e) {
    out.push({ err: String((e && e.message) || e).slice(0, 200) });
  }
}
process.stdout.write(JSON.stringify(out));
