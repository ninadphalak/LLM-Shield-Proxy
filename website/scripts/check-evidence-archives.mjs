import {existsSync, readFileSync} from 'node:fs';
import {dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const websiteRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const defaultSource = join(websiteRoot, 'src', 'data', 'submitted-rows.json');
const defaultRepoRoot = resolve(websiteRoot, '..');

/** The two files the intake writes for every row it archives. */
export const ARCHIVE_FILES = ['reports.json', 'provenance.json'];

/**
 * Refuse the build when a published row names a saved report that is not in the repository.
 *
 * The wall links each archived row to its saved report, and the row is only worth anything
 * once its run's artifacts have expired if that copy exists. A row may name only its own
 * issue's directory. Rows from before the archive existed name none and pass.
 */
export function checkEvidenceArchives(source = defaultSource, repoRoot = defaultRepoRoot) {
  const document = JSON.parse(readFileSync(source, 'utf8'));
  const problems = [];
  for (const row of document.entries ?? []) {
    if (row.status !== 'published') continue;
    const {issue, archive} = row._submission ?? {};
    if (archive === undefined) continue;
    if (archive !== `benchmarks/results/submitted/${issue}`) {
      problems.push(`issue ${issue}: archive "${archive}" is not this issue's directory`);
      continue;
    }
    for (const name of ARCHIVE_FILES) {
      if (!existsSync(join(repoRoot, archive, name))) {
        problems.push(`issue ${issue}: ${archive}/${name} does not exist`);
      }
    }
  }
  if (problems.length > 0) {
    throw new Error(`published rows name saved reports that are missing:\n${problems.join('\n')}`);
  }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  checkEvidenceArchives();
}
