// Secondary, lightweight syntax check. new Function() invokes JavaScriptCore's
// Function-constructor parser, NOT Bun's own module transpiler — the two
// demonstrably disagree in both directions on Bun 1.3.14 (e.g. a top-level
// legacy HTML comment `<!--` is accepted here but rejected by Bun's real
// loader; a missing IIFE invocation, `(function(){...})` with no trailing
// `()`, is valid JS and is NOT caught here even though it would be a broken
// post-process output). Never runs the source — so this is safe on a 23 MB
// CLI bundle. Runs under Bun because Node rejects the `using` / `await using`
// declarations the real cli.js contains.
//
// The primary, stronger check is Bun's own parser/transpiler:
//   bun build --no-bundle --target=bun <file> --outfile=/dev/null
// Prefer that as the authoritative L3 rung; treat this script as a
// secondary, quick sanity check only.
const fs = require("fs");

const target = process.argv[2];
if (!target) {
  console.error("usage: bun scripts/syntax-check.js <file>");
  process.exit(2);
}

let src;
try {
  src = fs.readFileSync(target, "utf8");
} catch (err) {
  console.error("read failed:", err.code || err.message);
  process.exit(2);
}

try {
  new Function(src);
  console.log("SYNTAX OK");
} catch (err) {
  console.error("SYNTAX FAIL:", err.message);
  process.exit(1);
}
