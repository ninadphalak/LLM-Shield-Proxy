/**
 * Drop a link target that is not safe to put in an `href`.
 *
 * WHY THIS EXISTS. The results wall and the corpus credits are built to render rows
 * submitted by other people. Every link on them -- a project's pricing page, a CI run, a
 * contributor's profile -- is a string that arrived from outside. React escapes text, but
 * it does NOT validate URLs: a `javascript:` target interpolated into an `href` runs on
 * click, and React only warns about it in development. On a page about other people's
 * security defects that is the one bug we cannot ship.
 *
 * Returns the URL when it is safe to link, or `undefined` when the caller should render
 * plain text instead. Never throws, and never silently rewrites a legitimate target.
 */

/** A scheme is everything before the first colon, and only if it looks like a scheme. */
const SCHEME = /^([a-z][a-z0-9+.-]*):/i;

/**
 * `mailto` is included because a contributor may reasonably want to be reachable.
 * `data` and `blob` are NOT: both can carry a document that runs script in this origin.
 */
const SAFE_SCHEMES = new Set(['http', 'https', 'mailto']);

export function safeHref(raw?: string): string | undefined {
  if (!raw) return undefined;

  // Strip control characters and whitespace BEFORE looking for the scheme. Browsers
  // ignore tabs and newlines inside a URL, so `java\nscript:alert(1)` is parsed as
  // `javascript:` while a naive check sees a scheme of `java\nscript` and no match at
  // all. Stripping first means the check sees what the browser will see.
  const value = raw.replace(/[\u0000-\u0020]/g, '');
  if (!value) return undefined;

  const scheme = SCHEME.exec(value);
  // No scheme means a relative target: `./results`, `/docs/x`, `#anchor`. Those resolve
  // against this site and cannot execute, so they pass through unchanged.
  if (!scheme) return value;

  return SAFE_SCHEMES.has(scheme[1].toLowerCase()) ? value : undefined;
}
