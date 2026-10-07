// The CommonJS build loads through `require` and behaves like the ESM one.
const assert = require("node:assert/strict");
const { test } = require("node:test");

const { assertChunkInvariant, ChunkInvarianceError } = require("chunk-invariance");
const { designA, holdBack, redact } = require("chunk-invariance/examples");

test("require() resolves the CommonJS build", () => {
  const resolved = require.resolve("chunk-invariance").split("\\").join("/");
  assert.match(resolved, /dist\/cjs\/index\.js$/);
  assert.equal(assertChunkInvariant(holdBack, redact, "use sk-AbCd1234 now"), 19);
  assert.throws(() => assertChunkInvariant(designA, redact, "use sk-AbCd1234 now"), ChunkInvarianceError);
});
