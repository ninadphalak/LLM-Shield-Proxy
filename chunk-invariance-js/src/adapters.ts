/** Turn common streaming-filter shapes into the filter the assertion calls. */

import type { AsyncStreamFilter, StreamFilter } from "./core.js";

export interface PerChunkOptions<S> {
  /** Called once per stream, so every split starts clean. Default: a new empty object. */
  state?: () => S;
  /** Called after the last chunk. Leave it out only if the filter has no end-of-stream step. */
  flush?: (state: S) => string;
}

/** Adapt `process(chunk, state) => output` plus an optional `flush(state) => output`. */
export function perChunk<S = Record<string, unknown>>(
  process: (chunk: string, state: S) => string,
  options: PerChunkOptions<S> = {},
): StreamFilter {
  const makeState = options.state ?? (() => ({}) as S);
  const flush = options.flush;
  return function* (chunks: Iterable<string>) {
    const state = makeState();
    for (const chunk of chunks) yield process(chunk, state);
    if (flush) yield flush(state);
  };
}

/**
 * Adapt a WHATWG `TransformStream<string, string>` factory for `assertChunkInvariantAsync`.
 * The factory is called once per split, so every split gets a fresh stream.
 */
export function fromTransformStream(factory: () => TransformStream<string, string>): AsyncStreamFilter {
  return async function* (chunks: AsyncIterable<string>) {
    const stream = factory();
    const writer = stream.writable.getWriter();
    const writing = (async () => {
      try {
        for await (const chunk of chunks) await writer.write(chunk);
        await writer.close();
      } catch (error) {
        // Error the readable side too, or the read loop below waits forever.
        await writer.abort(error).catch(() => undefined);
        throw error;
      }
    })();
    // Surfaced through the readable, and again by `await writing` below.
    writing.catch(() => undefined);
    const reader = stream.readable.getReader();
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      yield value;
    }
    await writing;
  };
}
