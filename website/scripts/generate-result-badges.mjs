import {readFileSync, writeFileSync, mkdirSync, readdirSync, unlinkSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const websiteRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const defaultSource = join(websiteRoot, 'src', 'data', 'submitted-rows.json');
const defaultOutput = join(websiteRoot, 'build', 'conformance-badges');

export const LABEL = 'pii-leak-benchmark';
const CHECKS = 3;

/**
 * How many of the three checks a row passed: request path, response with the value whole,
 * response with the value split across two chunks. A check passes only when it was measured
 * in full and nothing leaked. An unmeasured check, or a response check with inconclusive
 * cases, is not a pass, so no row reaches 3 of 3 on less than the whole measurement.
 */
export function checksPassed(row) {
  const values = [row.sentN, row.leakWholeN, row.leakSplitN];
  for (const value of values) {
    if (value !== undefined && value !== null
        && (typeof value !== 'number' || !Number.isFinite(value) || value < 0)) {
      throw new Error(`published row ${row._submission?.issue} has invalid measured counts`);
    }
  }
  if (!values.some((value) => typeof value === 'number')) {
    throw new Error(`published row ${row._submission?.issue} has invalid measured counts`);
  }
  const inconclusive = row.responseInconclusive ?? 0;
  if (!Number.isSafeInteger(inconclusive) || inconclusive < 0) {
    throw new Error(`published row ${row._submission?.issue} has invalid measured counts`);
  }
  // A response check with unjudged cases was not shown clean, whatever its leak rate.
  const counted = inconclusive > 0 ? [values[0]] : values;
  return counted.filter((value) => value === 0).length;
}

// Verdana 11px advance widths, rounded, for the only characters a badge ever carries.
const WIDTHS = {' ': 3.9, '-': 4.6, '✓': 8.2};
const textWidth = (text) =>
  [...text].reduce((sum, ch) => sum + (WIDTHS[ch] ?? (/[0-9]/.test(ch) ? 7 : 6.6)), 0);

/** A badge SVG. Only fixed strings and counts reach it, never submitter text. */
export function badgeSvg(passed) {
  const full = passed === CHECKS;
  const message = `${full ? '✓ ' : ''}${passed} of ${CHECKS} passed`;
  const labelWidth = Math.round(textWidth(LABEL) + 14);
  const messageWidth = Math.round(textWidth(message) + 14);
  const width = labelWidth + messageWidth;
  const fill = full ? 'url(#gold)' : passed > 0 ? '#1f6feb' : '#6e7781';
  const ink = full ? '#2b1d00' : '#fff';
  const title = `${LABEL}: ${passed} of ${CHECKS} checks passed`;
  const text = (x, w, value, color) =>
    `<text x="${x}" y="15" fill="#010101" fill-opacity=".25" textLength="${w}" lengthAdjust="spacingAndGlyphs">${value}</text>` +
    `<text x="${x}" y="14" fill="${color}" textLength="${w}" lengthAdjust="spacingAndGlyphs">${value}</text>`;
  // The full pass gets a gold face and a slow shimmer: the badge worth putting in a README.
  const shimmer = full
    ? `<rect y="0" width="28" height="20" fill="url(#shine)" transform="skewX(-20)">` +
      `<animate attributeName="x" values="${labelWidth - 40};${width + 20};${width + 20}" keyTimes="0;0.3;1" dur="6s" repeatCount="indefinite"/></rect>`
    : '';
  return [
    `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="20" role="img" aria-label="${title}">`,
    `<title>${title}</title>`,
    '<defs>',
    '<linearGradient id="gloss" x2="0" y2="100%"><stop offset="0" stop-color="#fff" stop-opacity=".14"/><stop offset="1" stop-opacity=".12"/></linearGradient>',
    '<linearGradient id="gold" x1="0" x2="1"><stop offset="0" stop-color="#c9971c"/><stop offset=".5" stop-color="#f7d154"/><stop offset="1" stop-color="#d9a520"/></linearGradient>',
    '<linearGradient id="shine" x1="0" x2="1"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".5" stop-color="#fff" stop-opacity=".6"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>',
    `<clipPath id="round"><rect width="${width}" height="20" rx="3"/></clipPath>`,
    '</defs>',
    '<g clip-path="url(#round)">',
    `<rect width="${labelWidth}" height="20" fill="#24292f"/>`,
    `<rect x="${labelWidth}" width="${messageWidth}" height="20" fill="${fill}"/>`,
    `<rect width="${width}" height="20" fill="url(#gloss)"/>`,
    shimmer,
    '</g>',
    '<g font-family="Verdana,Geneva,DejaVu Sans,sans-serif" font-size="11">',
    text(7, Math.round(textWidth(LABEL)), LABEL, '#fff'),
    text(labelWidth + 7, Math.round(textWidth(message)), message, ink),
    '</g>',
    '</svg>',
  ].join('') + '\n';
}

/** A Shields endpoint file, kept so badge links posted before the SVG existed keep working. */
export function badgeJson(passed) {
  return {
    schemaVersion: 1,
    label: LABEL,
    message: `${passed} of ${CHECKS} passed`,
    color: passed === CHECKS ? 'd9a520' : passed > 0 ? 'blue' : 'lightgrey',
  };
}

/** Write one SVG and one endpoint file per published row, keyed by its issue number. */
export function generateResultBadges(source = defaultSource, output = defaultOutput) {
  const document = JSON.parse(readFileSync(source, 'utf8'));
  if (!Array.isArray(document.entries)) {
    throw new Error('submitted-rows.json must contain an entries array');
  }
  mkdirSync(output, {recursive: true});
  for (const name of readdirSync(output)) {
    if (/^issue-[1-9]\d*\.(json|svg)$/.test(name)) unlinkSync(join(output, name));
  }

  const seen = new Set();
  for (const row of document.entries) {
    if (row.status !== 'published') continue;
    const issue = row._submission?.issue;
    if (!Number.isSafeInteger(issue) || issue <= 0 || seen.has(issue)) {
      throw new Error(`published row has an invalid or repeated issue number: ${issue}`);
    }
    seen.add(issue);
    const passed = checksPassed(row);
    writeFileSync(join(output, `issue-${issue}.svg`), badgeSvg(passed));
    writeFileSync(join(output, `issue-${issue}.json`), JSON.stringify(badgeJson(passed)) + '\n');
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  generateResultBadges();
}
