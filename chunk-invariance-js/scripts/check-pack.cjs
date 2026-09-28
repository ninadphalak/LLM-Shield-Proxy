// Reads `npm pack --dry-run --json` output and fails if the tarball holds anything but the
// two builds, the README, the licence and the manifest.
const fs = require("node:fs");

const [pack] = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const files = pack.files.map((f) => f.path).sort();
console.log(files.join("\n"));
const allowed = /^(dist\/(esm|cjs)\/[a-z]+\.(js|d\.ts)|dist\/(esm|cjs)\/package\.json|README\.md|LICENSE|package\.json)$/;
const stray = files.filter((f) => !allowed.test(f));
const missing = ["dist/cjs/index.js", "dist/cjs/index.d.ts", "dist/esm/index.js", "dist/esm/index.d.ts"].filter(
  (f) => !files.includes(f),
);
if (stray.length || missing.length) {
  console.error("unexpected:", stray, "missing:", missing);
  process.exit(1);
}
