// Run under Bun (native Bun.YAML.parse) and under Node + scripts/bun-shim.cjs.
// Prints one JSON array; tests/test_yaml_parse.py compares the two.
const cases = require("./yaml_parse_corpus.cjs");

const parse = globalThis.Bun && globalThis.Bun.YAML && globalThis.Bun.YAML.parse;
if (typeof parse !== "function") {
  console.error("no Bun.YAML.parse: run under Bun, or under Node with the shim preloaded");
  process.exit(2);
}

const out = [];
for (const src of cases) {
  try { out.push({ ok: parse(src) }); }
  catch (e) { out.push({ err: String((e && e.message) || e).slice(0, 200) }); }
}
process.stdout.write(JSON.stringify(out));
