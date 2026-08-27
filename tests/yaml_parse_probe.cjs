// Run under Bun (native Bun.YAML.parse) and under Node + scripts/bun-shim.cjs.
// Prints one JSON array; tests/test_yaml_parse.py compares the two.
const cases = require("./yaml_parse_corpus.cjs");

const parse = globalThis.Bun && globalThis.Bun.YAML && globalThis.Bun.YAML.parse;
if (typeof parse !== "function") {
  console.error("no Bun.YAML.parse: run under Bun, or under Node with the shim preloaded");
  process.exit(2);
}

// JSON.stringify maps Infinity and NaN to null and -0 to 0, so comparing two
// JSON.stringify outputs is blind to exactly the values most likely to differ
// between a real YAML parser and a hand-written one. That blindness produced a
// false "measured" fact once already: the shim returned null for .inf and the
// differential agreed, because the channel had destroyed Bun's Infinity before
// the comparison saw it. So encode types explicitly.
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

const out = [];
for (const src of cases) {
  try { out.push({ ok: encode(parse(src)) }); }
  catch (e) { out.push({ err: String((e && e.message) || e).slice(0, 200) }); }
}
process.stdout.write(JSON.stringify(out));
