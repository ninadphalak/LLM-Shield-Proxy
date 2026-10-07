/** The invariant, the splits it is checked over, and the assertion. */

export type StreamFilter = (chunks: Iterable<string>) => Iterable<string>;
export type AsyncStreamFilter = (chunks: AsyncIterable<string>) => AsyncIterable<string> | Iterable<string>;
export type WholeFilter = (text: string) => string;
export type Splits = "all-two-part" | "all" | number | Iterable<readonly string[]>;

export interface AssertOptions {
  /**
   * "all-two-part" (default): the whole input, then every single cut. "all": every split, for
   * inputs up to 16 characters. A number: every split into at most that many parts. Or an
   * iterable of explicit chunk lists, each of which must join back to the input.
   */
  splits?: Splits;
}

/** `splits: "all"` enumerates 2 ** (length - 1) splits; past this length that is too many. */
export const ALL_SPLITS_MAX_LENGTH = 16;

/** The streamed output for one split differs from filtering the whole input at once. */
export class ChunkInvarianceError extends Error {
  readonly text: string;
  readonly chunks: string[];
  readonly expected: string;
  readonly actual: string;
  readonly splitsTried: number;

  constructor(text: string, chunks: readonly string[], expected: string, actual: string, splitsTried: number) {
    const cuts = cutPoints(chunks);
    super(
      "stream filter is not chunk-invariant\n" +
        `  input:    ${JSON.stringify(text)}\n` +
        `  split:    ${JSON.stringify(chunks)} (cut at ${cuts.length ? cuts.join(", ") : "nothing: one chunk"})\n` +
        `  whole:    ${JSON.stringify(expected)}\n` +
        `  streamed: ${JSON.stringify(actual)}\n` +
        `  split #${splitsTried} in the order tried; generated splits run fewest parts first`,
    );
    this.name = "ChunkInvarianceError";
    this.text = text;
    this.chunks = [...chunks];
    this.expected = expected;
    this.actual = actual;
    this.splitsTried = splitsTried;
  }
}

/** Cut offsets in code points, matching how the splits are generated. */
function cutPoints(chunks: readonly string[]): number[] {
  const cuts: number[] = [];
  let offset = 0;
  for (const chunk of chunks.slice(0, -1)) {
    offset += Array.from(chunk).length;
    cuts.push(offset);
  }
  return cuts;
}

function* combinations(count: number, choose: number, start = 1): Generator<number[]> {
  if (choose === 0) {
    yield [];
    return;
  }
  for (let first = start; first <= count - choose + 1; first++) {
    for (const rest of combinations(count, choose - 1, first + 1)) {
      yield [first, ...rest];
    }
  }
}

function splitAt(points: readonly string[], cuts: readonly number[]): string[] {
  const bounds = [0, ...cuts, points.length];
  const parts: string[] = [];
  for (let i = 0; i < bounds.length - 1; i++) {
    parts.push(points.slice(bounds[i], bounds[i + 1]).join(""));
  }
  return parts;
}

/**
 * Every split of `text` into at most `maxParts` non-empty parts (all of them when omitted),
 * ordered by number of parts, then by cut positions. The single-part split comes first.
 * Splits fall between code points, never inside a surrogate pair.
 */
export function* allSplits(text: string, maxParts?: number): Generator<string[]> {
  if (maxParts !== undefined && (!Number.isInteger(maxParts) || maxParts < 1)) {
    throw new RangeError("maxParts must be an integer of at least 1");
  }
  const points = Array.from(text);
  const most = maxParts === undefined ? Math.max(points.length, 1) : Math.min(maxParts, Math.max(points.length, 1));
  for (let parts = 1; parts <= most; parts++) {
    for (const cuts of combinations(points.length - 1, parts - 1)) {
      yield splitAt(points, cuts);
    }
  }
}

/** Every split of `text` into two non-empty parts, left cut first. */
export function* twoPartSplits(text: string): Generator<string[]> {
  const points = Array.from(text);
  for (let cut = 1; cut < points.length; cut++) {
    yield [points.slice(0, cut).join(""), points.slice(cut).join("")];
  }
}

function resolveSplits(text: string, splits: Splits): Iterable<readonly string[]> {
  if (splits === "all-two-part") return allSplits(text, 2);
  if (splits === "all") {
    const length = Array.from(text).length;
    if (length > ALL_SPLITS_MAX_LENGTH) {
      throw new RangeError(
        `splits: "all" is 2 ** ${length - 1} splits for a ${length}-character input; use it up to ` +
          `${ALL_SPLITS_MAX_LENGTH} characters, or pass a number (the most parts per split)`,
      );
    }
    return allSplits(text);
  }
  if (typeof splits === "number") return allSplits(text, splits);
  if (typeof splits === "string") {
    throw new RangeError(`unknown splits value ${JSON.stringify(splits)}; use "all-two-part", "all", a number, or chunk lists`);
  }
  if (splits == null || typeof (splits as Iterable<readonly string[]>)[Symbol.iterator] !== "function") {
    throw new TypeError('splits must be "all-two-part", "all", a number, or an iterable of chunk lists');
  }
  return splits;
}

function checkJoin(text: string, chunks: readonly string[]): void {
  if (chunks.join("") !== text) {
    throw new RangeError(`explicit split ${JSON.stringify(chunks)} does not join back to the input`);
  }
}

/**
 * Assert that streaming `text` in any of the chosen splits gives what filtering it whole gives.
 *
 * `streamFilter` takes an iterable of input chunks and returns or yields output chunks; it is
 * called once per split, so it must start from fresh state each call. Throws
 * `ChunkInvarianceError` on the first split, fewest parts first, whose joined output differs.
 * Returns the number of splits checked.
 */
export function assertChunkInvariant(
  streamFilter: StreamFilter,
  wholeFilter: WholeFilter,
  text: string,
  options: AssertOptions = {},
): number {
  const expected = wholeFilter(text);
  let tried = 0;
  for (const split of resolveSplits(text, options.splits ?? "all-two-part")) {
    const chunks = [...split];
    checkJoin(text, chunks);
    tried++;
    const actual = [...streamFilter(chunks[Symbol.iterator]())].join("");
    if (actual !== expected) throw new ChunkInvarianceError(text, chunks, expected, actual, tried);
  }
  return tried;
}

async function* feed(chunks: readonly string[]): AsyncGenerator<string> {
  for (const chunk of chunks) yield chunk;
}

/**
 * `assertChunkInvariant` for an async filter: `streamFilter` takes an async iterable of chunks
 * and returns an async (or sync) iterable of output chunks. `wholeFilter` may return a promise.
 */
export async function assertChunkInvariantAsync(
  streamFilter: AsyncStreamFilter,
  wholeFilter: (text: string) => string | Promise<string>,
  text: string,
  options: AssertOptions = {},
): Promise<number> {
  const expected = await wholeFilter(text);
  let tried = 0;
  for (const split of resolveSplits(text, options.splits ?? "all-two-part")) {
    const chunks = [...split];
    checkJoin(text, chunks);
    tried++;
    const pieces: string[] = [];
    for await (const piece of streamFilter(feed(chunks))) pieces.push(piece);
    const actual = pieces.join("");
    if (actual !== expected) throw new ChunkInvarianceError(text, chunks, expected, actual, tried);
  }
  return tried;
}
