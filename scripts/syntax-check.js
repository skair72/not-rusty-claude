// Compile-only syntax check. new Function() parses the source and throws on a
// syntax error, but never runs it — so this is safe on a 23 MB CLI bundle.
// Runs under Bun because Node rejects the `using` / `await using` declarations
// the real cli.js contains.
const fs = require("fs");

const target = process.argv[2];
if (!target) {
  console.error("usage: bun scripts/syntax-check.js <file>");
  process.exit(2);
}

try {
  new Function(fs.readFileSync(target, "utf8"));
  console.log("SYNTAX OK");
} catch (err) {
  console.error("SYNTAX FAIL:", err.message);
  process.exit(1);
}
