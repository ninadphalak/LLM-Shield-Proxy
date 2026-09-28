/**
 * Three streaming redactors for one pattern: one correct, two that leak across a chunk boundary.
 *
 * The pattern is a fixed-length key, `sk-` plus eight letters or digits, replaced by `[KEY]`.
 * `redact` is the whole-input filter all three are compared against.
 *
 * - `holdBack` is correct. It holds back the last `KEY_LENGTH - 1` characters, the longest tail
 *   that could still be the start of a key, and redacts before it emits.
 * - `designA` redacts each chunk on its own. A key cut in two is never seen whole.
 * - `designB` prepends a fixed carryover from the previous chunk and redacts the join. The match
 *   fires, but the start of the key already left with the previous chunk.
 */

import { perChunk } from "./adapters.js";
import type { StreamFilter } from "./core.js";

export const KEY_LENGTH = 11;
export const MASK = "[KEY]";
const KEY_SOURCE = "sk-[A-Za-z0-9]{8}";

/** The whole-input filter: every key replaced. */
export function redact(text: string): string {
  return text.replace(new RegExp(KEY_SOURCE, "g"), MASK);
}

/** Correct: holds back a possible key prefix and redacts before emitting. */
export const holdBack: StreamFilter = perChunk<{ buffer: string }>(
  (chunk, state) => {
    const buffer = state.buffer + chunk;
    // A key that starts before `safe` ends inside the buffer, so it is already complete.
    const safe = buffer.length - (KEY_LENGTH - 1);
    const out: string[] = [];
    let emitted = 0;
    for (const match of buffer.matchAll(new RegExp(KEY_SOURCE, "g"))) {
      const start = match.index ?? 0;
      if (start >= safe) break;
      out.push(buffer.slice(emitted, start), MASK);
      emitted = start + match[0].length;
    }
    const keepFrom = Math.max(emitted, safe);
    out.push(buffer.slice(emitted, keepFrom));
    state.buffer = buffer.slice(keepFrom);
    return out.join("");
  },
  {
    state: () => ({ buffer: "" }),
    flush: (state) => {
      const rest = state.buffer;
      state.buffer = "";
      return redact(rest);
    },
  },
);

/** Leaks: each chunk is redacted alone, so a key split across two chunks passes. */
export function* designA(chunks: Iterable<string>): Generator<string> {
  for (const chunk of chunks) yield redact(chunk);
}

/**
 * Leaks: prepends the last `carry` characters of the previous chunk and redacts the join. The
 * carried characters were already emitted, so the start of a split key has gone out unredacted,
 * and cutting the carry off the redacted join corrupts what follows.
 */
export function* designB(chunks: Iterable<string>, carry = KEY_LENGTH - 1): Generator<string> {
  let previous = "";
  for (const chunk of chunks) {
    const joined = previous + chunk;
    yield redact(joined).slice(previous.length);
    previous = carry ? joined.slice(-carry) : "";
  }
}
