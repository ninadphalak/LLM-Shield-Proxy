import assert from "node:assert/strict";
import { test } from "node:test";

import { assertChunkInvariant, ChunkInvarianceError } from "../dist/esm/index.js";
import { designA, designB, holdBack, redact } from "../dist/esm/examples.js";

const TEXT = "use sk-AbCd1234 now";
const TWO_KEYS = "a sk-AAAAAAAA and sk-BBBBBBBBsk-CCCCCCCC then sk-DDDD";

test("holdBack passes every split of a short input", () => {
  assert.equal(assertChunkInvariant(holdBack, redact, "x sk-AbCd1234y", { splits: "all" }), 2 ** 13);
});

test("holdBack passes three-part splits with adjacent and trailing keys", () => {
  const n = TWO_KEYS.length;
  assert.equal(assertChunkInvariant(holdBack, redact, TWO_KEYS, { splits: 3 }), 1 + (n - 1) + ((n - 1) * (n - 2)) / 2);
});

test("designA fails and the message names the split", () => {
  assert.throws(
    () => assertChunkInvariant(designA, redact, TEXT),
    (error) => {
      assert.ok(error instanceof ChunkInvarianceError);
      assert.ok(error instanceof Error);
      assert.equal(error.name, "ChunkInvarianceError");
      assert.deepEqual(error.chunks, ["use s", "k-AbCd1234 now"]);
      assert.equal(error.expected, "use [KEY] now");
      assert.equal(error.actual, TEXT);
      assert.match(error.message, /split: {4}\["use s","k-AbCd1234 now"\] \(cut at 5\)/);
      assert.match(error.message, /whole: {4}"use \[KEY\] now"/);
      assert.match(error.message, /streamed: "use sk-AbCd1234 now"/);
      return true;
    },
  );
});

test("designB fails with a leak and a corrupted tail", () => {
  assert.throws(
    () => assertChunkInvariant(designB, redact, TEXT),
    (error) => {
      assert.deepEqual(error.chunks, ["use s", "k-AbCd1234 now"]);
      assert.equal(error.actual, "use sKEY] now");
      return true;
    },
  );
});

test("designB passes the unsplit input, which is why it looks fixed", () => {
  assert.equal(assertChunkInvariant(designB, redact, TEXT, { splits: [[TEXT]] }), 1);
});
