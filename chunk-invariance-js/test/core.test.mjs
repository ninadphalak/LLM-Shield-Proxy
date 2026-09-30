import assert from "node:assert/strict";
import { test } from "node:test";

import {
  allSplits,
  assertChunkInvariant,
  assertChunkInvariantAsync,
  ChunkInvarianceError,
  fromTransformStream,
  perChunk,
  twoPartSplits,
} from "../dist/esm/index.js";
import { designA, holdBack, redact } from "../dist/esm/examples.js";

const TEXT = "use sk-AbCd1234 now";

test("twoPartSplits is every single cut in order", () => {
  assert.deepEqual([...twoPartSplits("abcd")], [["a", "bcd"], ["ab", "cd"], ["abc", "d"]]);
  assert.deepEqual([...twoPartSplits("a")], []);
});

test("allSplits counts and orders by fewest parts", () => {
  const splits = [...allSplits("abcde")];
  assert.equal(splits.length, 2 ** 4);
  assert.deepEqual(splits[0], ["abcde"]);
  const lengths = splits.map((s) => s.length);
  assert.deepEqual(lengths, [...lengths].sort((a, b) => a - b));
  assert.ok(splits.every((s) => s.join("") === "abcde" && s.every((part) => part.length > 0)));
  assert.equal([...allSplits("abcde", 2)].length, 1 + 4);
  assert.deepEqual([...allSplits("", 3)], [[""]]);
  assert.throws(() => [...allSplits("abc", 0)], RangeError);
});

test("splits fall between code points, never inside a surrogate pair", () => {
  const splits = [...twoPartSplits("a\u{1F600}b")];
  assert.deepEqual(splits, [["a", "\u{1F600}b"], ["a\u{1F600}", "b"]]);
});

test("explicit splits must join back to the input", () => {
  assert.throws(() => assertChunkInvariant(holdBack, redact, TEXT, { splits: [["use", "sk"]] }), RangeError);
});

test("all is refused for long inputs, unknown modes are refused", () => {
  assert.throws(() => assertChunkInvariant(holdBack, redact, "x".repeat(17), { splits: "all" }), /pass a number/);
  assert.throws(() => assertChunkInvariant(holdBack, redact, TEXT, { splits: "every" }), RangeError);
  assert.throws(() => assertChunkInvariant(holdBack, redact, TEXT, { splits: true }), TypeError);
});

test("a missing flush is caught", () => {
  const noFlush = perChunk((chunk) => chunk.slice(0, -1));
  assert.throws(() => assertChunkInvariant(noFlush, (t) => t.slice(0, -1), "abc"), ChunkInvarianceError);
});

test("perChunk gives every stream fresh state", () => {
  const seen = [];
  const filter = perChunk((chunk, state) => {
    state.count = (state.count ?? 0) + 1;
    seen.push(state.count);
    return chunk;
  });
  assertChunkInvariant(filter, (t) => t, "abc");
  assert.deepEqual(seen, [1, 1, 2, 1, 2]);
});

async function* asyncDesignA(chunks) {
  for await (const chunk of chunks) yield redact(chunk);
}

test("async assertion: a buffering TransformStream passes, a per-chunk one fails", async () => {
  const correct = () => {
    let buffered = "";
    return new TransformStream({
      transform(chunk) {
        buffered += chunk;
      },
      flush(controller) {
        controller.enqueue(redact(buffered));
      },
    });
  };
  assert.equal(await assertChunkInvariantAsync(fromTransformStream(correct), async (t) => redact(t), TEXT), TEXT.length);
  await assert.rejects(assertChunkInvariantAsync(asyncDesignA, redact, TEXT), ChunkInvarianceError);
  const perChunkStream = () =>
    new TransformStream({
      transform(chunk, controller) {
        controller.enqueue(redact(chunk));
      },
    });
  await assert.rejects(assertChunkInvariantAsync(fromTransformStream(perChunkStream), redact, TEXT), ChunkInvarianceError);
});

test("a source that throws errors the TransformStream instead of hanging", async () => {
  const identity = () => new TransformStream();
  async function* broken() {
    yield "a";
    throw new Error("source broke");
  }
  const filter = fromTransformStream(identity);
  await assert.rejects(
    (async () => {
      for await (const piece of filter(broken())) void piece;
    })(),
    /source broke/,
  );
});

test("designA's failure names the smallest split", () => {
  assert.throws(
    () => assertChunkInvariant(designA, redact, TEXT, { splits: 4 }),
    (error) => {
      assert.ok(error instanceof ChunkInvarianceError);
      assert.deepEqual(error.chunks, ["use s", "k-AbCd1234 now"]);
      return true;
    },
  );
});
