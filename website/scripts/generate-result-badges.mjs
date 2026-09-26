import {readFileSync, writeFileSync, mkdirSync, readdirSync, unlinkSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const websiteRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const defaultSource = join(websiteRoot, 'src', 'data', 'submitted-rows.json');
const defaultOutput = join(websiteRoot, 'build', 'conformance-badges');

/** Build stable Shields endpoint files only from rows the wall actually publishes. */
export function generateResultBadges(source = defaultSource, output = defaultOutput) {
  const document = JSON.parse(readFileSync(source, 'utf8'));
  if (!Array.isArray(document.entries)) {
    throw new Error('submitted-rows.json must contain an entries array');
  }
  mkdirSync(output, {recursive: true});
  for (const name of readdirSync(output)) {
    if (/^issue-[1-9]\d*\.json$/.test(name)) unlinkSync(join(output, name));
  }

  const seen = new Set();
  for (const row of document.entries) {
    if (row.status !== 'published') continue;
    const issue = row._submission?.issue;
    if (!Number.isSafeInteger(issue) || issue <= 0 || seen.has(issue)) {
      throw new Error(`published row has an invalid or repeated issue number: ${issue}`);
    }
    seen.add(issue);
    const counts = [row.sentN, row.leakWholeN, row.leakSplitN]
      .filter((value) => value !== undefined && value !== null);
    if (counts.length === 0 || counts.some((value) =>
      typeof value !== 'number' || !Number.isFinite(value) || value < 0)) {
      throw new Error(`published row ${issue} has invalid measured counts`);
    }
    const leaked = counts.some((value) => value > 0);
    // A summary row without a leak is not proof that every profile passed.
    const badge = {
      schemaVersion: 1,
      label: 'PII leak check',
      message: leaked ? 'leaked' : 'benchmarked',
      color: leaked ? 'critical' : 'blue',
    };
    writeFileSync(join(output, `issue-${issue}.json`), JSON.stringify(badge) + '\n');
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  generateResultBadges();
}
