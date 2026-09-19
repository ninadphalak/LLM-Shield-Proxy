/**
 * Rows for the results wall.
 *
 * WHY THIS IS A DATA FILE. The rows used to be written inline in the page as JSX. That
 * is fine for eight rows a human adds by hand and impossible for rows a workflow
 * submits: a bot cannot safely edit JSX inside prose, and a reviewer cannot read the
 * diff. Keeping them here means a submitted result is a well-formed object in a list,
 * which is a diff a person can check in a few seconds.
 *
 * EVERY FIELD IS SELF-REPORTED unless `provenance` says otherwise. A row measured by
 * this project says so; a row a project submitted about itself says that instead. The
 * distinction is the whole value of the column, so it must never be quietly upgraded.
 *
 * Nothing here is ranked. See the page for why, and `ResultsWall` for the sort, which
 * is the reader's to choose and is never baked into this order.
 */

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
  /** Counts, not rates: "2 of 16" means more to a reader than 0.125. */
  leakWhole: string;
  leakWholeN: number;
  leakSplit: string;
  leakSplitN: number;
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
};

export const ROWS: ResultRow[] = [
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
