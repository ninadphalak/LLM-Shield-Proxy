/**
 * Rows for the results wall.
 *
 * WHY THIS IS A DATA FILE. The rows used to be written inline in the page as JSX. That
 * is fine for eight rows a human adds by hand and impossible for rows a workflow
 * submits: a bot cannot safely edit JSX inside prose, and a reviewer cannot read the
 * diff. Keeping them here means a submitted result is a well-formed object in a list,
 * which is a diff a person can check in a few seconds.
 *
 * THE WORKFLOW DOES NOT EDIT THIS FILE. It appends to `submitted-rows.json`, which is
 * imported below. The same argument that moved these rows out of the page moves a
 * stranger's text out of TypeScript: in JSON it is data, here it would be source that
 * gets bundled. This file holds the types, the measured rows, and the validator.
 *
 * EVERY FIELD IS SELF-REPORTED unless `provenance` says otherwise. A row measured by
 * this project says so; a row a project submitted about itself says that instead. The
 * distinction is the whole value of the column, so it must never be quietly upgraded.
 *
 * Nothing here is ranked. See the page for why, and `ResultsWall` for the sort, which
 * is the reader's to choose and is never baked into this order.
 */

import submitted from './submitted-rows.json';

/**
 * Who ran it and where, ordered by how much a reader can check WITHOUT trusting the
 * submitter. The number is the sort key; the label is what appears in the table.
 *
 * `ours` is not the strongest, it is the most conflicted: we wrote the check. It sorts
 * with the self-reported rows because that is what it is.
 */
export type Provenance =
  | 'measured-here'
  | 'submitted-main'
  | 'submitted-branch'
  | 'submitted-fork'
  | 'submitted-unverified';

export const PROVENANCE: Record<Provenance, {label: string; rank: number; hint: string}> = {
  // Highest rank because a reader can rerun it from the published reports in this
  // repository, not because we are trustworthy about our own product.
  'measured-here': {
    label: 'measured here',
    rank: 4,
    hint: 'Run by this project. Reports are in the repository and can be rerun.',
  },
  'submitted-main': {
    label: 'CI, main',
    rank: 3,
    hint: "Submitted from a run on the project's own default branch.",
  },
  'submitted-branch': {
    label: 'CI, branch',
    rank: 2,
    hint: 'Submitted from a run on a branch, so it may not be shipping code.',
  },
  'submitted-fork': {
    label: 'CI, fork',
    rank: 1,
    hint: 'Submitted from a fork. Useful for testing a change, not a claim about a release.',
  },
  'submitted-unverified': {
    label: 'self-reported',
    rank: 0,
    hint: 'Sent in without a run to point at. Taken at face value.',
  },
};

/**
 * How the gateway inspects a streaming response. This is the honest substitute for a
 * latency column: the three strategies have real and different costs, and a reader
 * choosing a gateway wants to know which one they are buying. Timings from whatever
 * runner a submitter happened to use would be hardware noise published as a product
 * characteristic, so they are not collected.
 */
export type Architecture = 'per-chunk' | 'buffered' | 'held-tail' | 'none' | 'not-stated';

export const ARCHITECTURE: Record<Architecture, {label: string; rank: number; hint: string}> = {
  buffered: {
    label: 'waits for the whole response',
    rank: 3,
    hint: 'Sees every split value. The reader waits for the entire answer before any of it arrives.',
  },
  'held-tail': {
    label: 'holds back a short tail',
    rank: 2,
    hint: 'Keeps streaming and still catches most splits. Leaks if the tail is shorter than the value.',
  },
  'per-chunk': {
    label: 'checks each chunk alone',
    rank: 1,
    hint: 'Fastest, and cannot see a value split across two chunks.',
  },
  none: {
    label: 'does not inspect responses',
    rank: 0,
    hint: 'Nothing reads the response stream, so nothing in it can be caught.',
  },
  'not-stated': {
    label: 'not stated',
    rank: -1,
    hint: 'Not recorded for this run.',
  },
};

export type ResultRow = {
  date: string;
  project: string;
  version: string;
  /** Which configured types still reached the provider: "none", "phone", "all 4 types". */
  sent: string;
  sentN: number;
  /** "all" or "none", the plain reading of FidelityRate. */
  restored: string;
  restoredN: number;
  /**
   * Counts, not rates: "2 of 16" means more to a reader than 0.125.
   *
   * OPTIONAL, because only one profile measures them. These two columns come from the
   * response-split profile, which injects values into the RESPONSE and checks whether the
   * gateway catches them whole and then split across two chunks. The operator check that
   * runs in CI measures the request side and fidelity, and does not produce them. A row
   * from such a run leaves them unset and the table says "not measured".
   *
   * Never default an unset one to zero. `0 of 16` is the strongest claim this page makes,
   * and asserting it from a run that did not look would be the worst bug this file could
   * have.
   */
  leakWhole?: string;
  leakWholeN?: number;
  leakSplit?: string;
  leakSplitN?: number;
  note: string;
  provenance: Provenance;
  architecture: Architecture;
  /** SPDX identifier, or "proprietary". A fact a reader can check in a repository. */
  license: string;
  /** Link to the project's OWN pricing page. Never our characterisation of their tiers. */
  pricingUrl?: string;
  reportUrl?: string;
  /** Link to the CI run behind a submitted row, so the provenance claim is clickable. */
  runUrl?: string;
  /**
   * The same configuration's previous measurement, for the trend indicator. Set this
   * only when the earlier row is on this page too: the arrow is a comparison a reader
   * can verify by looking up, never a number we assert on its own.
   */
  previous?: {version: string; leakWholeN: number; leakSplitN: number};
  /**
   * Open disputes against this row, counted by `scripts/count_result_flags.py`.
   *
   * WHY A COUNT AND NOT A VERDICT. A reader deciding whether to trust a row is better
   * served by "two people have questioned this, here they are" than by us adjudicating in
   * a footnote. The number links to the issues so the argument is readable, and it goes
   * away when they are closed. A disputed row is never hidden or downranked: this page
   * publishes disagreements rather than resolving them, which is the same reason two runs
   * of the same target that disagree both stay up.
   */
  flags?: {count: number; issue: number};
};

/**
 * Rows this project measured itself. Every one of them has `provenance: 'measured-here'`
 * and a report in the repository behind it.
 *
 * Kept separate from the submitted rows below so the homepage can count the measured ones
 * without counting a claim somebody sent in. The page renders both together; only the
 * arithmetic distinguishes them.
 */
export const MEASURED_ROWS: ResultRow[] = [
  {
    date: '2026-09-09',
    project: 'LLM-Shield-Proxy (ours)',
    version: '1.6.0, default settings',
    sent: 'none',
    sentN: 0,
    restored: 'all',
    restoredN: 1.0,
    leakWhole: '16 of 16',
    leakWholeN: 1.0,
    leakSplit: '16 of 16',
    leakSplitN: 1.0,
    note: 'Kept everything out of the provider request, then handed every value back to the client.',
    provenance: 'measured-here',
    architecture: 'none',
    license: 'Apache-2.0',
    reportUrl: './results',
  },
  {
    date: '2026-09-10',
    project: 'LLM-Shield-Proxy (ours)',
    version: '1.6.0, response scan on',
    sent: 'none',
    sentN: 0,
    restored: 'all',
    restoredN: 1.0,
    leakWhole: '2 of 16',
    leakWholeN: 0.125,
    leakSplit: '4 of 16',
    leakSplitN: 0.25,
    note: 'Kept everything out of the provider request. Still leaks some back to the client, and twice as much once a value is split.',
    provenance: 'measured-here',
    architecture: 'held-tail',
    license: 'Apache-2.0',
    reportUrl: './results',
  },
  {
    date: '2026-09-10',
    project: 'LLM Guard',
    version: '0.3.16, scanned per chunk',
    sent: 'phone',
    sentN: 1,
    restored: 'all',
    restoredN: 1.0,
    leakWhole: '4 of 16',
    leakWholeN: 0.25,
    leakSplit: '12 of 16',
    leakSplitN: 0.75,
    note: 'Phone numbers reached the provider. Gave the caller their data back. Splitting a value tripled what leaked to the client.',
    provenance: 'measured-here',
    architecture: 'per-chunk',
    license: 'MIT',
    reportUrl: './results',
  },
  {
    date: '2026-09-10',
    project: 'LLM Guard',
    version: '0.3.16, whole response buffered',
    sent: 'phone',
    sentN: 1,
    restored: 'all',
    restoredN: 1.0,
    leakWhole: '5 of 16',
    leakWholeN: 0.3125,
    leakSplit: '5 of 16',
    leakSplitN: 0.3125,
    note: 'Phone numbers reached the provider. Splitting changed nothing, because it waits for the whole response before sending any of it.',
    provenance: 'measured-here',
    architecture: 'buffered',
    license: 'MIT',
    reportUrl: './results',
  },
  {
    date: '2026-09-10',
    project: 'Guardrails AI',
    version: '0.10.2, sentence retention',
    sent: 'all 4 types',
    sentN: 4,
    restored: 'none',
    restoredN: 0.0,
    leakWhole: '2 of 16',
    leakWholeN: 0.125,
    leakSplit: '2 of 16',
    leakSplitN: 0.125,
    note: 'All four data types reached the provider, and none of the caller’s own data came back.',
    provenance: 'measured-here',
    architecture: 'held-tail',
    license: 'Apache-2.0',
    reportUrl: './results',
  },
  {
    date: '2026-09-09',
    project: 'LiteLLM',
    version: '1.99 with Presidio',
    sent: 'all 4 types',
    sentN: 4,
    restored: 'none',
    restoredN: 0.0,
    leakWhole: '0 of 16',
    leakWholeN: 0.0,
    leakSplit: '0 of 16',
    leakSplitN: 0.0,
    note: 'All four data types reached the provider even though redaction was switched on, and none of the caller’s data came back.',
    provenance: 'measured-here',
    architecture: 'not-stated',
    license: 'MIT',
    reportUrl: './results',
  },
  {
    date: '2026-09-09',
    project: 'NeMo Guardrails',
    version: '0.24.0',
    sent: 'all 4 types',
    sentN: 4,
    restored: 'none',
    restoredN: 0.0,
    leakWhole: '0 of 12',
    leakWholeN: 0.0,
    leakSplit: '0 of 12',
    leakSplitN: 0.0,
    note: 'All four data types reached the provider. Nothing was restored, and 8 of 32 cases produced no stream at all.',
    provenance: 'measured-here',
    architecture: 'not-stated',
    license: 'Apache-2.0',
    reportUrl: './results',
  },
  {
    date: '2026-09-09',
    project: 'Portkey',
    version: 'OSS gateway',
    sent: 'all 4 types',
    sentN: 4,
    restored: 'all',
    restoredN: 1.0,
    leakWhole: '16 of 16',
    leakWholeN: 1.0,
    leakSplit: '16 of 16',
    leakSplitN: 1.0,
    note: 'All four data types reached the provider, and everything it should have withheld went through to the client.',
    provenance: 'measured-here',
    architecture: 'none',
    license: 'Apache-2.0',
    reportUrl: './results',
  },
];

/**
 * Rows submitted from outside, read from `submitted-rows.json`.
 *
 * WHY THE VALIDATOR. A submitted entry is written into that file by a workflow, from the
 * body of an issue anyone can open. Docusaurus will bundle whatever is in there, so the
 * check that it is a well-formed row has to happen somewhere, and the build is the right
 * place: a bad entry stops the deploy instead of reaching the page.
 *
 * WHAT `status` MEANS. The workflow writes `published` when it could read the measured
 * columns out of the linked run's build artifact, which is the normal case, and the row
 * goes up without anyone approving it. It writes nothing at all when it could not: a
 * submission whose artifact was unreadable is answered on the issue rather than parked
 * here, because a row nobody can see helps nobody. `draft` therefore exists for a row a
 * person is still working on by hand, and such a row renders nowhere.
 *
 * An earlier version of this comment described a manual flow where every submission
 * landed as a draft for a maintainer to complete. That was the design before the intake
 * read artifacts; it is not what the code does, and the two public data files saying so
 * were caught in review rather than by anything executable. If this paragraph and
 * `scripts/process_conformance_submission.py` ever disagree again, the script is right.
 *
 * The validator below is what keeps either path safe: a `published` entry that is missing
 * a field, or that carries a value outside one of the two unions, throws here rather than
 * shipping. Note what it does NOT require, and why. The four leak columns are optional
 * because only the response-split profile measures them, and there is no honest default
 * for a missing one: `0 of 16` is the strongest claim on this page and an absent count
 * would sort as though it were the weakest.
 */
type SubmittedEntry = {status?: unknown} & Partial<Record<keyof ResultRow, unknown>>;

const REQUIRED_TEXT: (keyof ResultRow)[] = [
  'date',
  'project',
  'version',
  'sent',
  'restored',
  'note',
  'license',
];

const REQUIRED_NUMBERS: (keyof ResultRow)[] = ['sentN', 'restoredN'];

/**
 * The two optional columns, each a string and a number that only make sense together.
 * A row with a count but no text, or the reverse, renders a number with no denominator.
 */
const PAIRED_OPTIONAL: [keyof ResultRow, keyof ResultRow][] = [
  ['leakWhole', 'leakWholeN'],
  ['leakSplit', 'leakSplitN'],
];

function publishedRows(entries: SubmittedEntry[]): ResultRow[] {
  return entries
    .filter((entry) => entry.status === 'published')
    .map((entry, index) => {
      const where = `submitted-rows.json entry ${index} (${String(entry.project)})`;
      for (const field of REQUIRED_TEXT) {
        const value = entry[field];
        if (typeof value !== 'string' || value.trim() === '') {
          throw new Error(`${where}: "${field}" must be a non-empty string to publish.`);
        }
      }
      for (const field of REQUIRED_NUMBERS) {
        const value = entry[field];
        if (typeof value !== 'number' || !Number.isFinite(value) || value < 0) {
          throw new Error(`${where}: "${field}" must be a finite number at or above zero.`);
        }
      }
      if (!(String(entry.provenance) in PROVENANCE)) {
        throw new Error(`${where}: "${String(entry.provenance)}" is not a provenance value.`);
      }
      // A submitted row may never claim to have been measured here. That claim is what
      // the column exists to protect, and the file header says it is never upgraded
      // quietly, so the upgrade is refused loudly instead.
      if (entry.provenance === 'measured-here') {
        throw new Error(`${where}: a submitted row cannot claim "measured-here".`);
      }
      if (!(String(entry.architecture) in ARCHITECTURE)) {
        throw new Error(`${where}: "${String(entry.architecture)}" is not an architecture value.`);
      }
      for (const [text, count] of PAIRED_OPTIONAL) {
        const hasText = entry[text] !== undefined && entry[text] !== null;
        const hasCount = entry[count] !== undefined && entry[count] !== null;
        if (hasText !== hasCount) {
          throw new Error(`${where}: "${text}" and "${count}" must be set together or not at all.`);
        }
        if (hasCount && (typeof entry[count] !== 'number' || !Number.isFinite(entry[count]))) {
          throw new Error(`${where}: "${count}" must be a finite number when it is set.`);
        }
      }
      return entry as unknown as ResultRow;
    });
}

export const SUBMITTED_ROWS: ResultRow[] = publishedRows(
  (submitted as {entries?: SubmittedEntry[]}).entries ?? [],
);

/**
 * What the page renders. Order here is insertion order and carries no meaning: the table
 * sorts client side and defaults to date, and nothing on this page is ranked.
 */
export const ROWS: ResultRow[] = [...MEASURED_ROWS, ...SUBMITTED_ROWS];
