// verify-tree.js - check a rewired code-split tree with BUN's parser.
//
//   bun scripts/verify-tree.js <extract-dir>
//
// tools/postprocess.py rewires every /$bunfs/root/ reference by looking at the
// text around it (docs/findings.md 14). This checks the result with a parser
// instead of the regexes that produced it, module by module:
//
//   1. the rewritten module parses (Bun.Transpiler.transformSync);
//   2. it has exactly the import records the ORIGINAL module had - same count,
//      same kinds, same order (Bun.Transpiler.scanImports on both) - so no
//      static import became a dynamic one and no path turned into an import;
//   3. every record that named /$bunfs/root/<X> now resolves to X's own file
//      under root/, and every other record is untouched.
//
// Our own files (the entry, the polyfill) and the text-module wrappers must
// parse too. Prints one JSON line; exits 1 on any problem. Run by
// scripts/build.sh before a build is swapped in, and by scripts/harness.py.
// It needs original/, which build.sh deletes once this has passed unless
// NRC_KEEP_ORIGINAL is set; without it, it exits 2 and says so.
"use strict";
const fs = require("fs");
const path = require("path");

const dir = path.resolve(process.argv[2] || ".");
const manifest = JSON.parse(fs.readFileSync(path.join(dir, "manifest.json"), "utf8"));
if (!fs.existsSync(path.join(dir, manifest.tree))) {
  // exit 2, not 1: nothing was checked, so nothing was found wrong either
  console.error(`verify-tree: ${path.join(dir, manifest.tree)} is gone - build.sh removes the ` +
    "verbatim extraction once this check has passed. Rebuild with NRC_KEEP_ORIGINAL=1 to " +
    "check the tree again (after editing root/, say).");
  process.exit(2);
}
const JS = new Set(["js", "jsx", "ts", "tsx"]);
const VFS = "/$bunfs/root/";
const transpiler = new Bun.Transpiler({ loader: "js" });
const byPath = new Map(manifest.modules.map((m) => [m.path, m]));

// tools/postprocess.py _out_path(), restated
function outPath(mod) {
  if (JS.has(mod.loader) && !path.posix.basename(mod.path).includes(".")) return mod.path + ".js";
  if (mod.loader === "text") return mod.path + ".cjs";
  return mod.path;
}
function decode(buf, enc) {
  if (enc === "latin1") return buf.toString("latin1");
  if (enc === "utf16le") return buf.toString("utf16le");
  return buf.toString("utf8");
}
const errText = (e) => String((e && e.message) || e).split("\n")[0].slice(0, 200);

let modules = 0, records = 0, parsed = 0;
const problems = [], rejected = [];
for (const mod of manifest.modules) {
  if (!JS.has(mod.loader)) continue;
  modules++;
  const rel = outPath(mod);
  const abs = path.join(dir, "root", rel);
  let now, before, after;
  try {
    now = fs.readFileSync(abs, "utf8");
  } catch (e) {
    problems.push(`${rel}: not written (${errText(e)})`);
    continue;
  }
  try {
    transpiler.transformSync(now);
    parsed++;
  } catch (e) {
    rejected.push(`${rel}: ${errText(e)}`);
    continue;
  }
  try {
    before = transpiler.scanImports(decode(fs.readFileSync(path.join(dir, manifest.tree, mod.path)), mod.encoding));
    after = transpiler.scanImports(now);
  } catch (e) {
    problems.push(`${rel}: scanImports failed (${errText(e)})`);
    continue;
  }
  // a realm entry (the hooks Worker) gains one leading import of the polyfill
  if (after.length === before.length + 1 && after[0].path.endsWith("/bun-ant.mjs")) after = after.slice(1);
  if (before.length !== after.length) {
    problems.push(`${rel}: ${before.length} import records before the rewrite, ${after.length} after`);
    continue;
  }
  for (let i = 0; i < before.length; i++) {
    records++;
    const a = before[i], b = after[i];
    if (a.kind !== b.kind) {
      problems.push(`${rel}: record ${i} (${a.path}) was ${a.kind}, is now ${b.kind}`);
    } else if (a.path.startsWith(VFS)) {
      const target = byPath.get(a.path.slice(VFS.length));
      const want = target && path.join(dir, "root", outPath(target));
      const got = path.resolve(path.dirname(abs), b.path);
      if (!want || got !== want) problems.push(`${rel}: record ${i} ${a.path} -> ${b.path} resolves to ${got}`);
    } else if (a.path !== b.path) {
      problems.push(`${rel}: record ${i} changed from ${a.path} to ${b.path}`);
    }
  }
}

// ours, and the text wrappers
const extra = ["cli.js", "bun-ant.mjs", "bun-ant-cell-segmenter.mjs"].map((f) => path.join(dir, f));
for (const mod of manifest.modules) if (mod.loader === "text") extra.push(path.join(dir, "root", outPath(mod)));
for (const f of extra) {
  try {
    transpiler.transformSync(fs.readFileSync(f, "utf8"));
    parsed++;
  } catch (e) {
    rejected.push(`${path.relative(dir, f)}: ${errText(e)}`);
  }
}

console.log(JSON.stringify({ modules, parsed, records, problemCount: problems.length,
  rejectedCount: rejected.length, problems: problems.slice(0, 40), rejected: rejected.slice(0, 40) }));
process.exit(problems.length || rejected.length ? 1 : 0);
