// The package is "type": "module", so dist/cjs needs its own package.json saying commonjs, or
// Node loads the CommonJS build as ESM. The ESM build gets one too, for symmetry.
const fs = require("node:fs");
const path = require("node:path");

for (const [dir, type] of [["dist/cjs", "commonjs"], ["dist/esm", "module"]]) {
  fs.writeFileSync(path.join(__dirname, "..", dir, "package.json"), JSON.stringify({ type }) + "\n");
}
