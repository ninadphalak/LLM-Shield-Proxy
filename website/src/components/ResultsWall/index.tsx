import {createContext, useCallback, useContext, useEffect, useMemo, useRef, useState} from 'react';
import type {CSSProperties, ReactNode} from 'react';
import clsx from 'clsx';
import {useHistory, useLocation} from '@docusaurus/router';
import useBaseUrl from '@docusaurus/useBaseUrl';
import useBrokenLinks from '@docusaurus/useBrokenLinks';
import styles from './styles.module.css';
import {
  ARCHITECTURE,
  ROWS,
  checkStates,
  checksPassed,
  firstIndependentPass,
  isOurs,
  passes,
  type Architecture,
  type CheckKey,
  type CheckState,
  type ResultRow,
} from '@site/src/data/results-wall';
import {safeHref} from '@site/src/utils/safeHref';

export type {ResultRow};

type Props = {rows?: ResultRow[]};

const REPO = 'https://github.com/ninadphalak/LLM-Shield-Proxy';
const CHECKS = 3;

/** Rows older than this read as worth rerunning rather than as current. */
const STALE_AFTER_DAYS = 180;

/** Runs shown under a configuration before the rest fold behind "Show all N runs". */
const VISIBLE_RUNS = 3;

/**
 * Distinct independent submitters a gateway and configuration needs before it is called
 * replicated. The number and the rule come from `website/docs/conformance/submitting.md`.
 */
const REPLICATED_AT = 3;

/**
 * People whose runs never count toward replication, whatever they ran.
 *
 * `submitting.md` excludes a gateway's own maintainers. The people who maintain this
 * benchmark are excluded as well, for every gateway: their runs in this repository are
 * already `measured-here` and do not count, and the same person submitting the same kind of
 * run from a fork is no more independent for having used a different button. Erring this way
 * can only undercount replication, never claim one that did not happen.
 */
const BENCHMARK_MAINTAINERS = new Set(['ninadphalak']);

/** The id of the annotated example card at the top of the wall, and the link that opens it. */
const HOW_TO_READ = 'how-to-read-a-card';

function daysSince(date: string): number | null {
  const then = Date.parse(date);
  if (Number.isNaN(then)) return null;
  return Math.floor((Date.now() - then) / 86_400_000);
}

// --------------------------------------------------------------------------- identity

/**
 * A GitHub login, exactly: letters, digits and single hyphens, 39 characters at most. The
 * submitter field is written by a workflow from the issue author, and is still checked here
 * before it can shape a profile link or an image address.
 */
const HANDLE = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;

function submissionOf(row: ResultRow): {issue?: number; submitter?: string} {
  const raw = row._submission;
  if (!raw || typeof raw !== 'object') return {};
  const issue = Number.parseInt(String(raw.issue), 10);
  const submitter = typeof raw.submitter === 'string' ? raw.submitter : '';
  return {
    issue: Number.isSafeInteger(issue) && issue > 0 ? issue : undefined,
    submitter: HANDLE.test(submitter) ? submitter : undefined,
  };
}

function slug(text: string): string {
  return text
    .toLowerCase()
    .normalize('NFKD')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 80);
}

/**
 * A stable DOM id for every run, so any run can be linked to.
 *
 * A submitted run is `issue-<number>`, which is what the intake bot links to from the issue
 * (`row_url` in `scripts/process_conformance_submission.py`); change one and the other must
 * follow. A run with no issue is a slug of its gateway and version. Issue ids are assigned
 * first so that a slug can never take one, and a repeated slug gets a numeric suffix in the
 * order the rows are declared, which does not change when a reader sorts.
 *
 * These ids belong to the run, not the card, so regrouping runs changes none of them: a link
 * made when every run had a card of its own lands on the same run today.
 *
 * `prefix` keeps the example card's ids apart from the wall's when both are on one page.
 */
function assignIds(rows: ResultRow[], prefix = ''): Map<ResultRow, string> {
  const ids = new Map<ResultRow, string>();
  const taken = new Set<string>();
  for (const row of rows) {
    const {issue} = submissionOf(row);
    const id = issue ? `${prefix}issue-${issue}` : undefined;
    if (id && !taken.has(id)) {
      ids.set(row, id);
      taken.add(id);
    }
  }
  for (const row of rows) {
    if (ids.has(row)) continue;
    const base = `${prefix}${slug(`${row.project} ${row.version}`) || 'result'}`;
    let id = base;
    for (let n = 2; taken.has(id); n += 1) id = `${base}-${n}`;
    ids.set(row, id);
    taken.add(id);
  }
  return ids;
}

/**
 * Headings that used to live on this page and moved when the explanation did. A link to
 * one of them, from anywhere, is sent on to where the section is now rather than landing
 * on a wall with no such heading.
 */
const MOVED_ANCHORS: Record<string, 'read' | 'submit'> = {
  'two-bugs': 'read',
  'detector-gap': 'read',
  'boundary-bug': 'read',
  'why-there-is-no-speed-column': 'read',
  'why-we-do-not-rank-these': 'read',
  'ours-did-not-pass-either-at-first': 'read',
  'if-you-think-a-row-is-wrong': 'read',
  credit: 'read',
  'try-it-in-one-line': 'submit',
  'add-your-gateway': 'submit',
  'tell-people': 'submit',
  'forks-and-branches-are-welcome': 'submit',
  'what-to-send': 'submit',
};

// --------------------------------------------------------------------------- grouping

/**
 * One name per gateway, so "Portkey" and "Portkey OSS Gateway" count once. A negative
 * control is a run with no gateway in it, so it is not counted as one.
 */
function gatewayKey(project: string): string | undefined {
  const key = project
    .toLowerCase()
    .replace(/\(.*?\)/g, '')
    .replace(/\b(oss )?gateway$/, '')
    .trim();
  if (!key || key.startsWith('no-gateway')) return undefined;
  return key;
}

/**
 * A row's `version` field, read as the version that ran and the configuration it ran in.
 *
 * Every row on the wall writes it the same way: the version first, then a comma, then the
 * configuration ("1.6.6, response scan on", "commit caba832d976f, OSS output guardrail,
 * unauthenticated call-out"). One older row writes "1.99 with Presidio", so " with " also
 * separates the two when there is no comma. A field with neither is a version alone, and its
 * configuration is not stated ("0.24.0", "OSS gateway").
 */
function splitVersion(text: string): {version: string; config: string} {
  const comma = text.indexOf(',');
  if (comma >= 0) {
    return {version: text.slice(0, comma).trim(), config: text.slice(comma + 1).trim()};
  }
  const withAt = text.search(/\swith\s/i);
  if (withAt > 0) {
    return {
      version: text.slice(0, withAt).trim(),
      config: text.slice(withAt).replace(/^\s*with\s+/i, '').trim(),
    };
  }
  return {version: text.trim(), config: ''};
}

function normalise(text: string): string {
  return text.toLowerCase().replace(/\s+/g, ' ').replace(/[.\s]+$/, '').trim();
}

/**
 * Two wordings of one configuration, written down so a reviewer can check each one.
 *
 * Kept to cases the rows themselves settle. LLM-Shield-Proxy redacts the request by default,
 * so "request redaction on, response redaction on" is the configuration the maintainers'
 * rows call "response scan on"; the off-arm is a control inside that run, and the row's
 * numbers are from the arm with redaction on. Anything not listed here is grouped only when
 * its wording matches, so a doubtful pair shows as two configurations rather than one merged
 * claim.
 */
const CONFIG_ALIASES: Record<string, string> = {
  'request redaction on, response redaction on with an off-arm control': 'response scan on',
};

/**
 * Which runs of a configuration count as the same pinned version, for the dispute rule. A
 * commit hash is the version whatever words surround it ("fork main dceef23176de" and
 * "commit dceef23176de" are one build); otherwise the version text itself.
 */
function versionKey(version: string): string {
  const hash = version.toLowerCase().match(/\b[0-9a-f]{7,40}\b/);
  return hash ? hash[0] : normalise(version);
}

type Run = {
  row: ResultRow;
  id: string;
  /** Position in the declared rows, which breaks date ties: a later row is the newer one. */
  order: number;
  version: string;
  config: string;
  issue?: number;
  submitter?: string;
  states: Record<CheckKey, CheckState>;
};

/** One gateway in one configuration, with every run of it. Shown inside its gateway's card. */
type Config = {
  /**
   * `card-<gateway>-<configuration>`: the id the whole card had when every configuration was
   * a card of its own. Kept on the configuration so a link made then still lands on it.
   */
  id: string;
  /** The configuration as the card shows it, or empty when no run states one. */
  label: string;
  runs: Run[];
  /** The run this configuration's pips come from: the newest with all three checks measured. */
  headline: Run;
  headlineComplete: boolean;
  latest: Run;
  independent: string[];
  replicated: boolean;
  /** Checks on which two runs of the SAME version disagree. The target is disputed. */
  disputed: {check: CheckKey; version: string}[];
  /** Checks that came out differently in different versions: revision history, not dispute. */
  changed: CheckKey[];
  /** A property of the configuration, so the newest run that states it speaks for it. */
  architecture: Architecture;
};

/** One proxy: exactly one card on the wall, however many configurations and runs it has. */
type Proxy = {
  id: string;
  /** The `gatewayKey`, which also picks the replication instructions. */
  key: string;
  name: string;
  /** Newest first, by their newest run. */
  configs: Config[];
  /** Every run of every configuration, newest first. */
  runs: Run[];
  /** The proxy's most recent complete run, which the shield and the headline speak for. */
  headline: Run;
  headlineConfig: Config;
  headlineComplete: boolean;
  latest: Run;
  /** The configuration closest to replicated, for the one-line summary in the header. */
  closest: Config;
};

const CHECK_KEYS: CheckKey[] = ['request', 'whole', 'split'];

const newestFirst = (a: Run, b: Run) => b.row.date.localeCompare(a.row.date) || b.order - a.order;

const isComplete = (run: Run) => !Object.values(run.states).includes('unmeasured');

function buildConfig(id: string, key: string, unsorted: Run[]): Config {
  const runs = [...unsorted].sort(newestFirst);
  const complete = runs.find(isComplete);
  const configKey = key.slice(key.indexOf('|') + 1);
  const aliased = Object.values(CONFIG_ALIASES).includes(configKey);
  // An aliased configuration shows the shared wording; any other its newest run's own words.
  const label = aliased
    ? (runs.find((run) => normalise(run.config) === configKey)?.config ?? configKey)
    : runs[0].config;

  const independent = [
    ...new Set(
      runs
        // Only a verified fork run can count. A run in the gateway's own repository
        // (`submitted-main`, `submitted-branch`) is the gateway team's CI, and an unverified
        // run proves nothing about who ran it. A fork owned by someone on the gateway's team
        // looks like any other fork from here, which is why submitters are asked to declare it.
        .filter((run) => run.row.provenance === 'submitted-fork' && run.submitter)
        .map((run) => (run.submitter as string).toLowerCase())
        .filter((handle) => !BENCHMARK_MAINTAINERS.has(handle)),
    ),
  ];

  // Disagreements are kept and shown, never averaged (submitting.md). Only runs of the same
  // pinned version can dispute each other; a different result from a different version is a
  // change, and the runs list shows which version changed it.
  const disputed: Config['disputed'] = [];
  const byVersion = new Map<string, Run[]>();
  for (const run of runs) {
    const vk = versionKey(run.version);
    byVersion.set(vk, [...(byVersion.get(vk) ?? []), run]);
  }
  for (const same of byVersion.values()) {
    if (same.length < 2) continue;
    for (const check of CHECK_KEYS) {
      const seen = new Set(same.map((run) => run.states[check]).filter((s) => s !== 'unmeasured'));
      if (seen.size > 1) disputed.push({check, version: same[0].version});
    }
  }
  const changed = CHECK_KEYS.filter((check) => {
    if (disputed.some((d) => d.check === check)) return false;
    const seen = new Set(runs.map((run) => run.states[check]).filter((s) => s !== 'unmeasured'));
    return seen.size > 1;
  });

  return {
    id,
    label,
    runs,
    headline: complete ?? runs[0],
    headlineComplete: complete !== undefined,
    latest: runs[0],
    independent,
    replicated: independent.length >= REPLICATED_AT,
    disputed,
    changed,
    architecture: runs.find((run) => run.row.architecture !== 'not-stated')?.row.architecture ?? 'not-stated',
  };
}

/**
 * One card per proxy, each holding every configuration of it, each holding every run.
 *
 * THE PROXY RULE. Runs share a card when their gateways are the same by `gatewayKey`, the
 * same normalisation the "Gateways tested" count uses, so "Portkey" and "Portkey OSS Gateway"
 * are one proxy and "LLM-Shield-Proxy (ours)" is LLM-Shield-Proxy.
 *
 * THE CONFIGURATION RULE, inside a card. Two runs are the same configuration when the
 * configuration half of their `version` field (see `splitVersion`) is the same text, ignoring
 * case, spacing and a trailing full stop, after `CONFIG_ALIASES`. The version half is
 * deliberately NOT part of the key: a new release or commit of the same setup is a
 * reproduction of it, which is what `submitting.md` counts toward replication. A run whose
 * configuration is not stated only groups with other unstated runs of the same gateway,
 * never with a configured one, because "not stated" is not evidence of "the same".
 * Replication, disputes and "changed between versions" are all per configuration, because
 * the replication rule in `submitting.md` is per gateway AND configuration.
 *
 * A run with no gateway in it (the no-gateway control) is not a proxy. It is returned apart,
 * grouped by its full project name, and the wall shows it as a baseline.
 */
function groupRuns(
  rows: ResultRow[],
  ids: Map<ResultRow, string>,
  prefix = '',
): {proxies: Proxy[]; controls: Config[]} {
  const byKey = new Map<string, Run[]>();
  rows.forEach((row, order) => {
    const {version, config} = splitVersion(row.version);
    const configKey = normalise(config);
    const canonical = CONFIG_ALIASES[configKey] ?? configKey;
    const gateway = gatewayKey(row.project);
    // The control keeps its whole name as its key and is marked so it never becomes a proxy.
    const key = gateway ? `${gateway}|${canonical}` : `\u0000${normalise(row.project)}|${canonical}`;
    const {issue, submitter} = submissionOf(row);
    const run: Run = {
      row,
      id: ids.get(row) ?? `${prefix}result-${order}`,
      order,
      version,
      config,
      issue,
      submitter,
      states: checkStates(row),
    };
    byKey.set(key, [...(byKey.get(key) ?? []), run]);
  });

  // No card, configuration or run may share an id, or a bot link would land on the wrong thing.
  const taken = new Set(ids.values());
  const claim = (base: string) => {
    let id = base;
    for (let n = 2; taken.has(id); n += 1) id = `${base}-${n}`;
    taken.add(id);
    return id;
  };

  const controls: Config[] = [];
  const byGateway = new Map<string, Config[]>();
  for (const [key, runs] of byKey) {
    const control = key.startsWith('\u0000');
    const readable = key.replace('\u0000', '');
    const config = buildConfig(
      claim(`${prefix}card-${slug(readable.replaceAll('|', ' ')) || 'gateway'}`),
      readable,
      runs,
    );
    if (control) {
      controls.push(config);
      continue;
    }
    const gateway = readable.slice(0, readable.indexOf('|'));
    byGateway.set(gateway, [...(byGateway.get(gateway) ?? []), config]);
  }

  const proxies: Proxy[] = [];
  for (const [key, unsorted] of byGateway) {
    const configs = [...unsorted].sort((a, b) => newestFirst(a.latest, b.latest));
    const runs = configs.flatMap((config) => config.runs).sort(newestFirst);
    const complete = runs.find(isComplete);
    const headline = complete ?? runs[0];
    const headlineConfig = configs.find((config) => config.runs.includes(headline)) ?? configs[0];
    // Closest to replicated; a tie goes to the configuration the header already speaks for.
    const closest = [...configs].sort(
      (a, b) =>
        b.independent.length - a.independent.length ||
        Number(b === headlineConfig) - Number(a === headlineConfig),
    )[0];
    proxies.push({
      id: claim(`${prefix}proxy-${slug(key) || 'gateway'}`),
      key,
      // The newest run's name. "(ours)" is dropped because the card says so in a chip.
      name: runs[0].row.project.replace(/\s*\(ours\)\s*$/i, ''),
      configs,
      runs,
      headline,
      headlineConfig,
      headlineComplete: complete !== undefined,
      latest: runs[0],
      closest,
    });
  }
  return {proxies, controls: controls.sort((a, b) => newestFirst(a.latest, b.latest))};
}

// --------------------------------------------------------------------------- replicate

/**
 * Where "Replicate this result" goes for each proxy, by `gatewayKey`.
 *
 * The four proxies with a ready-made source-build workflow land on their own row of the
 * "Test a proxy from your own fork" table, which carries a matching id. LLM Guard is a scanner
 * library rather than a gateway, and the CI page's LLM Guard section names the small wrapper
 * this project measured it through. Guardrails AI has no page of its own, so it goes to the
 * commands that measure any named gateway on both paths. Anything else goes to the general
 * "add your gateway" steps.
 */
const REPLICATE: Record<string, {path: string; hint: string}> = {
  portkey: {
    path: 'source-ci#replicate-portkey',
    hint: 'Fork Portkey, add one workflow file, click Run. No API key needed.',
  },
  litellm: {
    path: 'source-ci#replicate-litellm',
    hint: 'Fork LiteLLM, add one workflow file, click Run. No API key needed.',
  },
  'nemo guardrails': {
    path: 'source-ci#replicate-nemo-guardrails',
    hint: 'Fork NeMo Guardrails, add one workflow file, click Run. No API key needed.',
  },
  'llm-shield-proxy': {
    path: 'source-ci#replicate-llm-shield-proxy',
    hint: 'Fork it and click Run: the workflow is already in the repository.',
  },
  'llm guard': {
    path: 'ci#llm-guard',
    hint: 'Wrap LLM Guard in the same small gateway used here, then run the check.',
  },
  'guardrails ai': {
    path: 'reproduce-fragmentation#publishing-a-comparative-row-instead',
    hint: 'Run the request and response checks against it yourself.',
  },
};

const REPLICATE_ANY = {
  path: 'add-your-result#add-your-gateway',
  hint: 'Run the check in your own CI and send in the result.',
};

// --------------------------------------------------------------------------- the example

/**
 * Set on the annotated example card. Its links do not go anywhere, its avatar is a drawing
 * rather than a real person's picture, and every part the guide points at carries a number.
 */
const ExampleMode = createContext(false);

/** A numbered target for the annotated example. Outside the example it is nothing at all. */
function useMark(n: number): {attrs: Record<string, number>; mark: ReactNode; marked?: string} {
  const example = useContext(ExampleMode);
  if (!example) return {attrs: {}, mark: null};
  return {
    attrs: {'data-callout': n},
    mark: (
      <span className={styles.mark} aria-hidden="true">
        {n}
      </span>
    ),
    marked: styles.marked,
  };
}

/** A link on a real card, and inert text shaped like one on the example. */
function Link({
  href,
  external,
  className,
  title,
  children,
}: {
  href?: string;
  external?: boolean;
  className?: string;
  title?: string;
  children: ReactNode;
}): ReactNode {
  const example = useContext(ExampleMode);
  const target = safeHref(href);
  if (example || !target) {
    return (
      <span className={clsx(className, example && styles.fakeLink)} title={title}>
        {children}
      </span>
    );
  }
  return (
    <a
      className={className}
      href={target}
      title={title}
      {...(external ? {target: '_blank', rel: 'noreferrer'} : {})}>
      {children}
    </a>
  );
}

// --------------------------------------------------------------------------- the checks

/** Each check as the yes-or-no question it answers. "Yes" is the bad answer on all three. */
const CHECK_QUESTION: Record<CheckKey, string> = {
  request: 'Sent raw personal data to the provider?',
  whole: 'Leaked personal data to the user, in one piece?',
  split: 'Leaked personal data to the user, split in two?',
};

/** The same three, short enough for a tooltip on a small pip. */
const CHECK_SHORT: Record<CheckKey, string> = {
  request: 'Sent raw data to provider',
  whole: 'Leaked to user, whole',
  split: 'Leaked to user, split',
};

/** The names the legend gives the three small pips, in their order on every run line. */
const CHECK_PIP: Record<CheckKey, string> = {
  request: 'sent to provider',
  whole: 'leaked whole',
  split: 'leaked split',
};

/** Glyph and word for every state, so no state is carried by colour alone. */
const STATE: Record<CheckState, {glyph: string; word: string}> = {
  pass: {glyph: '✓', word: 'passed'},
  fail: {glyph: '✕', word: 'leaked'},
  incomplete: {glyph: '!', word: 'unclear'},
  unmeasured: {glyph: '–', word: 'not tested'},
};

const TIER = ['tier0', 'tier1', 'tier2', 'tier3'] as const;

/**
 * The change against this configuration's own previous measurement.
 *
 * Deliberately NOT a comparison between products. A delta against the same row's
 * earlier version is revision history: it says a team fixed something, which is the
 * behaviour this page exists to encourage. A delta across products would be a ranking,
 * which this page does not publish.
 *
 * `total` is recovered from the row's own "N of M" text rather than assumed to be 16,
 * because a run with inconclusive cases has a smaller denominator and the arrow must
 * not silently rescale it.
 */
function Trend({now, before, label}: {now: number; before: number; label: string}): ReactNode {
  const total = Number(label.split(' of ')[1]);
  if (!Number.isFinite(total)) return null;
  const moved = Math.round((now - before) * total);
  if (moved === 0) return null;
  const better = moved < 0;
  return (
    <span
      className={better ? styles.trendBetter : styles.trendWorse}
      title={
        better
          ? `${Math.abs(moved)} fewer than the previous version on this page`
          : `${moved} more than the previous version on this page`
      }>
      {better ? '↓' : '↑'}
      {Math.abs(moved)}
      <span className={styles.srOnly}>
        {better ? ' fewer than the previous version' : ' more than the previous version'}
      </span>
    </span>
  );
}

function leakAnswer(label: string | undefined, state: CheckState, inconclusive: number): string {
  if (state === 'unmeasured' || label === undefined) return 'Not tested';
  if (state === 'incomplete') return `Unclear: ${label} leaked, ${inconclusive} cases unjudged`;
  if (state === 'fail') return `Yes: ${label}`;
  return `No: ${label}`;
}

function CheckRow({
  check,
  state,
  answer,
  n,
}: {
  check: CheckKey;
  state: CheckState;
  answer: ReactNode;
  n: number;
}): ReactNode {
  const {attrs, mark, marked} = useMark(n);
  return (
    <li className={clsx(styles.check, styles[state], marked)} {...attrs}>
      {mark}
      <span className={styles.pip} aria-hidden="true">
        {STATE[state].glyph}
      </span>
      <span className={styles.checkQuestion}>{CHECK_QUESTION[check]}</span>
      <span className={styles.checkAnswer}>
        <span className={styles.srOnly}>{STATE[state].word}: </span>
        {answer}
      </span>
    </li>
  );
}

/**
 * The three checks, as labelled rows. Same rule as the README badge (`checkStates`), and each
 * row answers its question in words, so a reader who cannot tell the colours apart loses
 * nothing.
 */
function Checks({row}: {row: ResultRow}): ReactNode {
  const states = checkStates(row);
  const inconclusive = row.responseInconclusive ?? 0;
  const answers: Record<CheckKey, ReactNode> = {
    request: row.sentN === 0 ? 'No' : `Yes: ${row.sent}`,
    whole: (
      <>
        {leakAnswer(row.leakWhole, states.whole, inconclusive)}
        {row.leakWhole && row.leakWholeN !== undefined && row.previous && (
          <Trend now={row.leakWholeN} before={row.previous.leakWholeN} label={row.leakWhole} />
        )}
      </>
    ),
    split: (
      <>
        {leakAnswer(row.leakSplit, states.split, inconclusive)}
        {row.leakSplit && row.leakSplitN !== undefined && row.previous && (
          <Trend now={row.leakSplitN} before={row.previous.leakSplitN} label={row.leakSplit} />
        )}
      </>
    ),
  };
  return (
    <ul className={styles.checks}>
      {CHECK_KEYS.map((key, i) => (
        <CheckRow key={key} check={key} state={states[key]} answer={answers[key]} n={3 + i} />
      ))}
    </ul>
  );
}

/** One run's three checks in a line: a glyph each, a word each for a screen reader. */
function MiniChecks({states}: {states: Record<CheckKey, CheckState>}): ReactNode {
  return (
    <span className={styles.miniChecks}>
      {CHECK_KEYS.map((key) => (
        <span
          key={key}
          className={clsx(styles.mini, styles[states[key]])}
          title={`${CHECK_SHORT[key]}: ${STATE[states[key]].word}`}>
          <span aria-hidden="true">{STATE[states[key]].glyph}</span>
          <span className={styles.srOnly}>
            {CHECK_SHORT[key]}: {STATE[states[key]].word}.{' '}
          </span>
        </span>
      ))}
    </span>
  );
}

/**
 * The fourth question, shown beside the checks rather than folded into them.
 *
 * A gateway can leak nothing back to the client because it returned nothing at all. When no
 * caller data came back the card says so next to any clean response check, so `0 of 16` is
 * never read as the flattering half of a number with two readings.
 */
function Fidelity({row}: {row: ResultRow}): ReactNode {
  const {attrs, mark, marked} = useMark(6);
  const all = row.restoredN === 1;
  const none = row.restoredN === 0;
  const states = checkStates(row);
  const cleanResponse = states.whole === 'pass' || states.split === 'pass';
  return (
    <p
      className={clsx(styles.fidelity, all ? styles.pass : none ? styles.fail : styles.incomplete, marked)}
      {...attrs}>
      {mark}
      <span className={styles.pip} aria-hidden="true">
        {all ? '✓' : none ? '✕' : '!'}
      </span>
      <span>
        Gave callers their own data back? <strong>{all ? 'Yes' : none ? 'No' : 'Partly'}</strong>
        {none && cleanResponse && (
          <span className={styles.caveat}>
            {' '}
            Nothing came back, so a low leak count may only mean little was returned at all.
          </span>
        )}
      </span>
    </p>
  );
}

/**
 * Open disputes against a row, as a number a reader can click.
 *
 * A caveat on the whole row, not another measurement of the product. Zero renders nothing:
 * a row of noughts would read as a score.
 */
function Flags({flags}: {flags?: {count: number; issue: number}}): ReactNode {
  if (!flags || flags.count < 1) return null;
  // Both halves come out of a JSON file a workflow writes, so neither is trusted here even
  // though the workflow validates them. The issue number is forced to an integer before it
  // can shape a path, and the finished URL still goes through the same guard every other
  // link on this page uses.
  const issue = Number.parseInt(String(flags.issue), 10);
  if (!Number.isSafeInteger(issue) || issue < 1) return null;
  const label = flags.count === 1 ? '1 open question' : `${flags.count} open questions`;
  return (
    <Link
      className={styles.flag}
      href={safeHref(`${REPO}/issues/${issue}`)}
      external
      title={`${label} about this run. Click to read them.`}>
      {label}
    </Link>
  );
}

/** A trophy outline, drawn rather than an emoji so it takes the theme's colours. */
function Trophy({className}: {className?: string}): ReactNode {
  return (
    <svg className={className} viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        fill="currentColor"
        d="M7 3h10v2h3v3a4 4 0 0 1-4 4h-.35A5 5 0 0 1 13 14.9V17h3v2H8v-2h3v-2.1A5 5 0 0 1 8.35 12H8a4 4 0 0 1-4-4V5h3V3Zm0 4H6v1a2 2 0 0 0 1.2 1.83A5 5 0 0 1 7 9V7Zm10 0v2c0 .28-.02.56-.07.83A2 2 0 0 0 18 8V7h-1ZM6 20h12v2H6v-2Z"
      />
    </svg>
  );
}

/** The shield. Its metal is the number of checks passed, and it says the number too. */
function Medal({passed}: {passed: number}): ReactNode {
  return (
    <div
      className={clsx(styles.medal, styles[TIER[passed]])}
      role="img"
      aria-label={`${passed} of ${CHECKS} checks passed in the latest complete run`}>
      <span className={styles.medalFace}>
        <span className={styles.medalCount}>{passed}</span>
        <span className={styles.medalOf}>of {CHECKS}</span>
      </span>
    </div>
  );
}

function Avatar({handle, size = 28}: {handle: string; size?: number}): ReactNode {
  const example = useContext(ExampleMode);
  if (example) {
    // A drawn stand-in: the example must not show a real person's picture.
    return (
      <span
        className={clsx(styles.avatar, styles.fakeAvatar)}
        style={{width: size, height: size}}
        aria-hidden="true">
        {handle.charAt(0).toUpperCase()}
      </span>
    );
  }
  // The handle has already passed HANDLE, and the address still goes through the guard.
  const src = safeHref(`https://github.com/${handle}.png?size=${size * 2}`);
  if (!src) return null;
  return (
    <img
      className={styles.avatar}
      src={src}
      alt=""
      width={size}
      height={size}
      loading="lazy"
      decoding="async"
    />
  );
}

/**
 * Who ran it, in plain words when there is no submitter to name. A submitted run names its
 * submitter with their avatar instead, so a reader can see a person ran it rather than
 * inferring one from a URL.
 */
const NO_SUBMITTER: Record<ResultRow['provenance'], string> = {
  'measured-here': 'this project',
  'submitted-main': "the project's own CI",
  'submitted-branch': "the project's own CI",
  'submitted-fork': 'someone, from a fork',
  'submitted-unverified': 'self-reported',
};

/**
 * Where a run ran, in words a stranger reads without the guide. The data file's shorter
 * labels stay what they are; this is only how the card says them.
 */
const WHERE: Record<ResultRow['provenance'], {label: string; hint: string}> = {
  'measured-here': {
    label: 'Run by this project',
    hint: 'Run by the benchmark maintainers. Reports are in the repository and can be rerun.',
  },
  'submitted-main': {
    label: "Proxy's own CI",
    hint: "Submitted from a run on the proxy project's own default branch.",
  },
  'submitted-branch': {
    label: "Proxy's CI, a branch",
    hint: 'Submitted from a run on a branch, so it may not be shipping code.',
  },
  'submitted-fork': {
    label: 'From a fork',
    hint: 'Run from a fork of the proxy, and verified from its CI run.',
  },
  'submitted-unverified': {
    label: 'Self-reported',
    hint: 'Sent in without a run to point at. Taken at face value.',
  },
};

function whoRan(run: Run): string {
  return run.submitter ? `@${run.submitter}` : NO_SUBMITTER[run.row.provenance];
}

function RunLine({
  run,
  configLabel,
  linked,
  milestone,
  hidden,
  annotate,
}: {
  run: Run;
  configLabel: string;
  linked: boolean;
  milestone: boolean;
  hidden: boolean;
  /** On the example, this run carries the "who ran it" and "where it ran" numbers. */
  annotate?: boolean;
}): ReactNode {
  const {row, submitter, issue} = run;
  const where = WHERE[row.provenance];
  const passed = checksPassed(row);
  const maintainer = submitter && BENCHMARK_MAINTAINERS.has(submitter.toLowerCase());
  const who = useMark(9);
  const chip = useMark(10);
  const whoMark = annotate ? who : {attrs: {}, mark: null, marked: undefined};
  const chipMark = annotate ? chip : {attrs: {}, mark: null, marked: undefined};
  // A run worded differently from its configuration (an alias) shows its own words, so
  // grouping never hides what a submitter wrote.
  const differs = run.config !== '' && normalise(run.config) !== normalise(configLabel);
  return (
    <li
      id={run.id}
      tabIndex={-1}
      hidden={hidden}
      className={clsx(styles.run, linked && styles.runLinked)}>
      <span className={clsx(styles.runMain, whoMark.marked)} {...whoMark.attrs}>
        {whoMark.mark}
        <span className={styles.runWho}>
          {submitter ? (
            <>
              <Avatar handle={submitter} size={22} />
              <Link
                className={styles.handle}
                href={safeHref(`https://github.com/${submitter}`)}
                external
                title={maintainer ? 'A benchmark maintainer. Does not count toward replication.' : undefined}>
                @{submitter}
              </Link>
            </>
          ) : (
            <>
              <span className={styles.houseMark} aria-hidden="true">
                ◆
              </span>
              <span>{NO_SUBMITTER[row.provenance]}</span>
            </>
          )}
        </span>
        <time className={styles.date} dateTime={row.date}>
          {row.date}
        </time>
        <span
          className={styles.runVersion}
          title={row.harness ? `Measured with pii-leak-benchmark ${row.harness}.` : undefined}>
          {run.version}
          {differs && <span className={styles.runConfig}> ({run.config})</span>}
        </span>
      </span>
      <span className={styles.runResult}>
        <MiniChecks states={run.states} />
        <span className={styles.runCount}>
          {passed} of {CHECKS} passed
        </span>
        <span
          className={clsx(styles.chip, styles.whereChip, chipMark.marked)}
          title={where.hint}
          {...chipMark.attrs}>
          {chipMark.mark}
          {where.label}
        </span>
      </span>
      <span className={styles.runLinks}>
        {row.runUrl ? (
          <Link href={safeHref(row.runUrl)} external title="The CI run behind this result">
            CI run
          </Link>
        ) : row.reportUrl ? (
          <Link href={safeHref(row.reportUrl)} title="The report behind this result">
            report
          </Link>
        ) : null}
        {issue && (
          <Link href={safeHref(`${REPO}/issues/${issue}`)} external title="The submission issue">
            #{issue}
          </Link>
        )}
        <Flags flags={row.flags} />
        {milestone && (
          <span
            className={styles.milestone}
            title="The first gateway not written by this project to answer all three questions. Computed from the rows, not awarded.">
            <Trophy className={styles.inlineTrophy} /> First independent pass
          </span>
        )}
        {linked && <span className={styles.runTag}>Linked run</span>}
        <Link className={styles.permalink} href={safeHref(`#${run.id}`)} title="Link to this run">
          #<span className={styles.srOnly}>Link to this run</span>
        </Link>
      </span>
    </li>
  );
}

/** Three steps, filled by independent submitters. The words beside it carry the meaning. */
function Meter({count}: {count: number}): ReactNode {
  const shown = Math.min(count, REPLICATED_AT);
  return (
    <span className={styles.meter} aria-hidden="true">
      {Array.from({length: REPLICATED_AT}, (_, i) => (
        <span key={i} className={clsx(styles.meterStep, i < shown && styles.meterOn)} />
      ))}
    </span>
  );
}

const REPLICATION_RULE =
  'A result is replicated when three different people, other than the benchmark maintainers, have each run the same proxy and configuration from their own fork. Runs in the proxy’s own repository do not count.';

/** The proxy's one-line replication status, for the configuration closest to replicated. */
function ReplicationSummary({proxy}: {proxy: Proxy}): ReactNode {
  const {attrs, mark, marked} = useMark(7);
  const config = proxy.closest;
  const people = config.independent.length;
  const shown = Math.min(people, REPLICATED_AT);
  const multi = proxy.configs.length > 1;
  return (
    <div
      className={clsx(styles.replication, config.replicated && styles.replicated, marked)}
      title={REPLICATION_RULE}
      {...attrs}>
      {mark}
      <Meter count={people} />
      <span>
        {config.replicated ? (
          <strong>Replicated</strong>
        ) : (
          <>
            <strong>
              {shown} of {REPLICATED_AT}
            </strong>{' '}
            independent runs
          </>
        )}
        {multi && (
          <>
            {' '}
            for <em>{config.label || 'configuration not stated'}</em>
          </>
        )}
        {!config.replicated && <span className={styles.muted}>. {REPLICATED_AT} make it replicated.</span>}{' '}
        <span className={styles.muted}>
          {proxy.runs.length} {proxy.runs.length === 1 ? 'run' : 'runs'} in total.
        </span>
      </span>
    </div>
  );
}

/**
 * One configuration inside a proxy's card: its own pips, its own replication count and marks,
 * and its runs newest first. When it is the proxy's only configuration it has no chrome of its
 * own, because the card already says everything the section header would.
 */
function ConfigSection({
  config,
  index,
  solo,
  linked,
  milestone,
  expanded,
  onToggle,
}: {
  config: Config;
  index: number;
  solo: boolean;
  linked?: string;
  milestone?: ResultRow;
  expanded: boolean;
  onToggle: () => void;
}): ReactNode {
  const {attrs, mark, marked} = useMark(8);
  const listId = `${config.id}-runs`;
  const extra = config.runs.length - VISIBLE_RUNS;
  const people = config.independent.length;
  const architecture = ARCHITECTURE[config.architecture];
  const passed = checksPassed(config.headline.row);
  return (
    <section
      id={config.id}
      tabIndex={-1}
      className={clsx(styles.config, solo && styles.configSolo, linked === config.id && styles.configLinked)}
      aria-labelledby={`${config.id}-title`}>
      <div className={clsx(styles.configHead, marked)} {...attrs}>
        {mark}
        <p className={styles.configTitle} id={`${config.id}-title`}>
          <span className={styles.eyebrow}>{solo ? 'Configuration tested' : `Configuration ${index + 1}`}</span>
          <span className={styles.configName}>
            {config.label ? config.label : <em>Not stated by the run</em>}
          </span>
        </p>
        {!solo && (
          <span
            className={styles.configResult}
            title={
              config.headlineComplete
                ? 'The newest run of this configuration that measured all three checks.'
                : 'No run of this configuration measured all three checks. This is its newest run.'
            }>
            <MiniChecks states={config.headline.states} />
            <span className={styles.runCount}>
              {passed} of {CHECKS}
            </span>
          </span>
        )}
      </div>

      <p className={styles.configMeta}>
        {!solo && (
          <span
            className={clsx(styles.configReplication, config.replicated && styles.replicatedText)}
            title={REPLICATION_RULE}>
            <Meter count={people} />
            {config.replicated ? 'Replicated' : `${Math.min(people, REPLICATED_AT)} of ${REPLICATED_AT} independent runs`}
          </span>
        )}
        <span className={clsx(styles.chip, styles.chipQuiet)} title={architecture.hint}>
          Reads the reply: {architecture.label}
        </span>
      </p>

      {config.disputed.length > 0 && (
        <p className={styles.disputed}>
          <span className={styles.disputeMark} aria-hidden="true">
            ⚑
          </span>
          <span>
            <strong>Disputed.</strong> Runs of the same version disagree on{' '}
            {config.disputed.map((d) => `“${CHECK_PIP[d.check]}” (${d.version})`).join(', ')}.
            Both are kept below, not averaged.
          </span>
        </p>
      )}
      {config.changed.length > 0 && (
        <p className={styles.changed}>
          <span aria-hidden="true">↻</span>{' '}
          <span>
            <strong>Changed between versions</strong> on{' '}
            {config.changed.map((check) => `“${CHECK_PIP[check]}”`).join(' and ')}. The runs
            below show which version did what.
          </span>
        </p>
      )}

      <p className={styles.runsTitle} id={`${listId}-title`}>
        {config.runs.length === 1 ? 'The run' : `${config.runs.length} runs, newest first`}
      </p>
      <ol className={styles.runs} id={listId} aria-labelledby={`${listId}-title`}>
        {config.runs.map((run, i) => (
          <RunLine
            key={run.id}
            run={run}
            configLabel={config.label}
            linked={run.id === linked}
            milestone={run.row === milestone}
            hidden={!expanded && i >= VISIBLE_RUNS}
            annotate={i === 0}
          />
        ))}
      </ol>
      {extra > 0 && (
        <button
          type="button"
          className={styles.more}
          aria-expanded={expanded}
          aria-controls={listId}
          onClick={onToggle}>
          {expanded ? 'Show fewer runs' : `Show all ${config.runs.length} runs`}
        </button>
      )}
    </section>
  );
}

/** The headline for a proxy's latest run, in the words a stranger would use. */
function verdictOf(row: ResultRow, complete: boolean): {title: string; detail: string} {
  const states = Object.values(checkStates(row));
  const fails = states.filter((s) => s === 'fail').length;
  const passed = checksPassed(row);
  const untested = states.filter((s) => s === 'unmeasured').length;
  if (!complete) {
    return {
      title: fails > 0 ? 'Leaked in latest run' : 'Not fully tested yet',
      detail: `${passed} of ${CHECKS} checks passed, ${untested} not tested.`,
    };
  }
  if (fails > 0) {
    return {title: 'Leaked in latest run', detail: `${fails} of ${CHECKS} checks leaked.`};
  }
  if (passed === CHECKS) {
    return {
      title: 'Passed all 3 checks',
      detail:
        row.restoredN === 1
          ? 'Nothing leaked, and callers got their own data back.'
          : 'Nothing leaked, but callers did not get all their own data back.',
    };
  }
  return {
    title: 'No leak seen, but unclear',
    detail: `${CHECKS - passed} of ${CHECKS} checks had cases that could not be judged.`,
  };
}

function Verdict({proxy}: {proxy: Proxy}): ReactNode {
  const {attrs, mark, marked} = useMark(2);
  const run = proxy.headline;
  const {title, detail} = verdictOf(run.row, proxy.headlineComplete);
  const multi = proxy.configs.length > 1;
  const passed = checksPassed(run.row);
  return (
    <div className={clsx(styles.verdict, marked)} {...attrs}>
      {mark}
      <p className={styles.verdictTitle}>
        {title}
        <span className={styles.verdictDetail}> {detail}</span>
      </p>
      <p className={styles.verdictCaption}>
        <span className={styles.srOnly}>{passed} of {CHECKS} checks passed. </span>
        {proxy.headlineComplete ? 'Latest complete run' : 'Latest run'}: {run.row.date}, {run.version}
        {multi && (
          <>
            , <em>{proxy.headlineConfig.label || 'configuration not stated'}</em>
          </>
        )}
        , by {whoRan(run)}
        {run.row.harness && (
          <span title="Two runs measured with different harness versions were produced by different code.">
            , harness {run.row.harness}
          </span>
        )}
      </p>
    </div>
  );
}

function Replicate({proxy, docsBase}: {proxy: Proxy; docsBase: string}): ReactNode {
  const {attrs, mark, marked} = useMark(11);
  const target = REPLICATE[proxy.key] ?? REPLICATE_ANY;
  return (
    <div className={clsx(styles.replicate, marked)} {...attrs}>
      {mark}
      <Link className={styles.replicateButton} href={safeHref(`${docsBase}${target.path}`)}>
        Replicate this result <span aria-hidden="true">→</span>
      </Link>
      <span className={styles.replicateHint}>{target.hint}</span>
    </div>
  );
}

function ProxyCard({
  proxy,
  linked,
  index,
  milestone,
  isOpen,
  onToggle,
  docsBase,
}: {
  proxy: Proxy;
  /** The id the address points at, when it is this card, a configuration or a run in it. */
  linked?: string;
  index: number;
  milestone?: ResultRow;
  isOpen: (config: Config) => boolean;
  onToggle: (config: Config) => void;
  docsBase: string;
}): ReactNode {
  const example = useContext(ExampleMode);
  const medalMark = useMark(1);
  const row = proxy.headline.row;
  const passed = checksPassed(row);
  const full = passes(row) && passed === CHECKS;
  const age = daysSince(proxy.latest.row.date);
  const stale = !example && age !== null && age > STALE_AFTER_DAYS;
  // The licence and project link as the newest run states them.
  const licence = proxy.latest.row.license;
  const pricing = proxy.runs.find((run) => run.row.pricingUrl)?.row.pricingUrl;
  const state = passed === CHECKS ? styles.stateGold : passed > 0 ? styles.statePartial : styles.stateNone;
  const hasMilestone = milestone !== undefined && proxy.runs.some((run) => run.row === milestone);
  const solo = proxy.configs.length === 1;
  const Tag = example ? 'div' : 'li';
  return (
    <Tag
      id={proxy.id}
      tabIndex={-1}
      className={clsx(styles.card, state, linked && styles.linked, example && styles.exampleCard)}
      style={{'--i': Math.min(index, 12)} as CSSProperties}>
      {linked && (
        <span className={styles.ribbon} aria-hidden="true">
          Linked result
        </span>
      )}
      {example && (
        <span className={clsx(styles.ribbon, styles.exampleRibbon)} aria-hidden="true">
          Example
        </span>
      )}
      <div className={styles.cardHead}>
        <div className={clsx(styles.medalWrap, medalMark.marked)} {...medalMark.attrs}>
          {medalMark.mark}
          <Medal passed={passed} />
        </div>
        <div className={styles.identity}>
          <h3 className={styles.project}>
            {proxy.name}
            <Link className={styles.permalink} href={safeHref(`#${proxy.id}`)} title="Link to this card">
              #<span className={styles.srOnly}>Link to this card</span>
            </Link>
          </h3>
          <p className={styles.tags}>
            <span className={styles.chip} title="Licence">
              {pricing ? (
                <Link href={safeHref(pricing)} external>
                  {licence}
                </Link>
              ) : (
                licence
              )}
            </span>
            {isOurs(proxy.name) && (
              <span className={clsx(styles.chip, styles.chipQuiet)} title="This project wrote this proxy.">
                ours
              </span>
            )}
            {!solo && (
              <span className={clsx(styles.chip, styles.chipQuiet)}>
                {proxy.configs.length} configurations
              </span>
            )}
            {full && (
              <span
                className={styles.fullPass}
                title="In the latest complete run: nothing reached the provider, every value came back to the caller, and nothing it never sent got through, whole or split.">
                ★ Full pass
              </span>
            )}
            {hasMilestone && (
              <span
                className={styles.milestone}
                title="The first gateway not written by this project to answer all three questions. Computed from the rows, not awarded.">
                <Trophy className={styles.inlineTrophy} /> First independent pass
              </span>
            )}
            {stale && (
              <span className={styles.stale} title="Last measured a while ago. The project has probably shipped since.">
                worth rerunning
              </span>
            )}
          </p>
        </div>
      </div>

      <Verdict proxy={proxy} />

      <div className={styles.cardBody}>
        <div className={styles.summary}>
          <Checks row={row} />
          <Fidelity row={row} />
          <p className={styles.note}>{row.note}</p>
          <ReplicationSummary proxy={proxy} />
        </div>

        <div className={styles.configs}>
          {proxy.configs.map((config, i) => (
            <ConfigSection
              key={config.id}
              config={config}
              index={i}
              solo={solo}
              linked={linked}
              milestone={milestone}
              expanded={isOpen(config)}
              onToggle={() => onToggle(config)}
            />
          ))}
        </div>

        <Replicate proxy={proxy} docsBase={docsBase} />
      </div>
    </Tag>
  );
}

/**
 * The no-gateway control: the test app talking to the provider directly. It is not a proxy,
 * so it is a slim strip rather than a card. It exists to show the test sees a leak when there
 * is one, so leaking everything is the expected result.
 */
function ControlStrip({
  controls,
  linked,
}: {
  controls: Config[];
  linked?: string;
}): ReactNode {
  if (controls.length === 0) return null;
  return (
    <section className={styles.control} aria-labelledby="baseline-control-title">
      <p className={styles.controlTitle} id="baseline-control-title">
        <span className={styles.eyebrow}>Baseline, not a proxy</span> No proxy in the middle
      </p>
      <p className={styles.controlText}>
        The same test with nothing between the app and the provider. It is meant to leak
        everything: that shows the test can see a leak when there is one.
      </p>
      {controls.map((config) => (
        <ol key={config.id} id={config.id} className={styles.runs} aria-label={config.label || 'Baseline runs'}>
          {config.runs.map((run) => (
            <RunLine
              key={run.id}
              run={run}
              configLabel={config.label}
              linked={run.id === linked}
              milestone={false}
              hidden={false}
            />
          ))}
        </ol>
      ))}
    </section>
  );
}

// --------------------------------------------------------------------------- how to read

type Callout = {n: number; side: 'left' | 'right'; text: string};

/**
 * What each numbered part of the example card means, in a few words. Sides are picked so no
 * two neighbouring parts send their arrows the same way.
 */
const CALLOUTS: Callout[] = [
  {n: 1, side: 'left', text: 'Checks passed out of 3, in the latest complete run'},
  {n: 2, side: 'right', text: 'That run’s result, in plain words: when, which commit, who ran it'},
  {n: 3, side: 'left', text: 'Request path: did raw personal data reach the model provider?'},
  {n: 4, side: 'right', text: 'Value whole: did a value in one piece come back to the user?'},
  {n: 5, side: 'left', text: 'Value split: did a value split across two chunks come back?'},
  {n: 6, side: 'right', text: 'Did callers get their own data back? “No” makes a low leak count mean less'},
  {n: 7, side: 'left', text: 'Independent runs so far. 3 are needed to call it replicated'},
  {n: 8, side: 'right', text: 'The setup that was tested. A proxy with several gets one section each'},
  {n: 9, side: 'left', text: 'One run: who ran it, when, which commit'},
  {n: 10, side: 'right', text: 'Where it ran: this project, the proxy’s own CI, or a fork'},
  {n: 11, side: 'left', text: 'Step-by-step instructions to run it yourself'},
];

/** Sample rows for the example card. Nothing here is a measurement of a real product. */
const EXAMPLE_ROWS: ResultRow[] = [
  {
    date: '2026-09-20',
    project: 'Example Proxy',
    version: 'commit 1a2b3c4d5e6f, PII guardrail on',
    sent: 'none',
    sentN: 0,
    restored: 'all',
    restoredN: 1,
    leakWhole: '0 of 16',
    leakWholeN: 0,
    leakSplit: '4 of 16',
    leakSplitN: 0.25,
    note: 'Sample data. Kept everything out of the provider request and caught values sent whole, but let some through once a value was split across two chunks.',
    provenance: 'submitted-fork',
    architecture: 'per-chunk',
    license: 'MIT',
    runUrl: 'https://example.com',
    _submission: {issue: 123, submitter: 'a-contributor'},
  },
  {
    date: '2026-09-02',
    project: 'Example Proxy',
    version: '2.0.0, PII guardrail on',
    sent: 'none',
    sentN: 0,
    restored: 'all',
    restoredN: 1,
    leakWhole: '0 of 16',
    leakWholeN: 0,
    leakSplit: '4 of 16',
    leakSplitN: 0.25,
    note: 'Sample data.',
    provenance: 'measured-here',
    architecture: 'per-chunk',
    license: 'MIT',
    reportUrl: './results',
  },
];

/** Below this width the arrows have no room, and the example switches to numbered markers. */
const ARROWS_AT = 700;

type Placement = {
  top: Record<number, number>;
  lines: {n: number; d: string; x: number; y: number}[];
  height: number;
  width: number;
};

/**
 * Place each callout level with the part it points at, then push it down just far enough not
 * to overlap the one above, and draw a curve from the callout to the part.
 */
function place(stage: HTMLElement): Placement | null {
  const box = stage.getBoundingClientRect();
  const top: Record<number, number> = {};
  const lines: Placement['lines'] = [];
  let bottom = 0;
  for (const side of ['left', 'right'] as const) {
    const items = CALLOUTS.filter((c) => c.side === side)
      .map((c) => {
        const target = stage.querySelector<HTMLElement>(`[data-callout="${c.n}"]`);
        const label = stage.querySelector<HTMLElement>(`[data-callout-label="${c.n}"]`);
        if (!target || !label || !label.parentElement) return null;
        const t = target.getBoundingClientRect();
        const column = label.parentElement.getBoundingClientRect();
        return {c, t, label, column, y: t.top + t.height / 2 - box.top};
      })
      .filter((item): item is NonNullable<typeof item> => item !== null)
      .sort((a, b) => a.y - b.y);
    let floor = 0;
    for (const item of items) {
      const h = item.label.offsetHeight;
      const y = Math.max(item.y - h / 2, floor);
      top[item.c.n] = Math.round(y);
      floor = y + h + 10;
      bottom = Math.max(bottom, floor);
      const from = {
        x: side === 'left' ? item.column.right - box.left + 4 : item.column.left - box.left - 4,
        y: y + Math.min(h / 2, 12),
      };
      const to = {
        x: side === 'left' ? item.t.left - box.left - 3 : item.t.right - box.left + 3,
        y: item.y,
      };
      const bend = (to.x - from.x) * 0.5;
      lines.push({
        n: item.c.n,
        d: `M${from.x.toFixed(1)} ${from.y.toFixed(1)} C${(from.x + bend).toFixed(1)} ${from.y.toFixed(1)} ${(to.x - bend).toFixed(1)} ${to.y.toFixed(1)} ${to.x.toFixed(1)} ${to.y.toFixed(1)}`,
        x: from.x,
        y: from.y,
      });
    }
  }
  if (lines.length === 0) return null;
  const card = stage.querySelector<HTMLElement>('[data-example-card]');
  const cardBottom = card ? card.getBoundingClientRect().bottom - box.top : 0;
  return {top, lines, height: Math.ceil(Math.max(bottom, cardBottom)), width: Math.ceil(box.width)};
}

/**
 * The annotated example card: a card built from sample rows by the same code as the wall's,
 * with every part a reader asks about labelled. Wide, the labels sit either side with arrows
 * to what they name. Narrow, the arrows have no room, so the card carries numbered markers and
 * the labels become a numbered list underneath.
 */
export function AnnotatedExample(): ReactNode {
  const docsBase = useBaseUrl('/docs/conformance/');
  const {proxies} = useMemo(() => groupRuns(EXAMPLE_ROWS, assignIds(EXAMPLE_ROWS, 'example-'), 'example-'), []);
  const proxy = proxies[0];
  const stageRef = useRef<HTMLDivElement>(null);
  const [arrows, setArrows] = useState(false);
  const [placement, setPlacement] = useState<Placement | null>(null);

  useEffect(() => {
    const stage = stageRef.current;
    if (!stage) return undefined;
    let frame = 0;
    const measure = () => {
      window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        const wide = stage.clientWidth >= ARROWS_AT;
        setArrows(wide);
        if (!wide) {
          setPlacement(null);
          return;
        }
        const next = place(stage);
        setPlacement((current) => (JSON.stringify(current) === JSON.stringify(next) ? current : next));
      });
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(stage);
    const card = stage.querySelector('[data-example-card]');
    if (card) observer.observe(card);
    document.fonts?.ready.then(measure).catch(() => undefined);
    return () => {
      window.cancelAnimationFrame(frame);
      observer.disconnect();
    };
  }, [arrows]);

  if (!proxy) return null;
  const column = (side: 'left' | 'right') => (
    <ol className={clsx(styles.calloutColumn, styles[side])} aria-hidden={!arrows}>
      {CALLOUTS.filter((c) => c.side === side).map((c) => (
        <li
          key={c.n}
          data-callout-label={c.n}
          className={styles.callout}
          style={{top: placement?.top[c.n] ?? 0, visibility: placement ? 'visible' : 'hidden'}}>
          {c.text}
        </li>
      ))}
    </ol>
  );

  return (
    <figure className={clsx(styles.tokens, styles.example, arrows ? styles.arrows : styles.markers)}>
      <figcaption className={styles.exampleCaption}>
        <strong>An example card, with sample data.</strong>{' '}
        {arrows ? 'Each label points at the part it explains.' : 'Each number on the card is explained below it.'}
      </figcaption>
      <div
        ref={stageRef}
        className={styles.stage}
        style={arrows && placement ? {minHeight: placement.height} : undefined}>
        {arrows && column('left')}
        <div className={styles.exampleCardWrap} data-example-card="">
          <ExampleMode.Provider value={true}>
            <ProxyCard
              proxy={proxy}
              index={0}
              isOpen={() => true}
              onToggle={() => undefined}
              docsBase={docsBase}
            />
          </ExampleMode.Provider>
        </div>
        {arrows && column('right')}
        {arrows && placement && (
          <svg
            className={styles.leaders}
            width={placement.width}
            height={placement.height}
            aria-hidden="true"
            focusable="false">
            <defs>
              <marker
                id="wall-arrowhead"
                viewBox="0 0 10 10"
                refX="9"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse">
                <path d="M0 0 L10 5 L0 10 z" className={styles.arrowHead} />
              </marker>
            </defs>
            {placement.lines.map((line) => (
              <g key={line.n}>
                <path d={line.d} className={styles.leader} markerEnd="url(#wall-arrowhead)" />
                <circle cx={line.x} cy={line.y} r="2.5" className={styles.arrowHead} />
              </g>
            ))}
          </svg>
        )}
      </div>
      {!arrows && (
        <ol className={styles.exampleLegend}>
          {CALLOUTS.map((c) => (
            <li key={c.n}>
              <span className={styles.legendNumber} aria-hidden="true">
                {c.n}
              </span>
              <span>
                <span className={styles.srOnly}>{c.n}. </span>
                {c.text}
              </span>
            </li>
          ))}
        </ol>
      )}
    </figure>
  );
}

// --------------------------------------------------------------------------- the scoreboard

type Contributor = {handle: string; runs: number; first: string};

function contributors(rows: ResultRow[]): Contributor[] {
  const byHandle = new Map<string, Contributor>();
  for (const row of rows) {
    const {submitter} = submissionOf(row);
    if (!submitter || row.provenance === 'measured-here') continue;
    const key = submitter.toLowerCase();
    const seen = byHandle.get(key);
    if (seen) {
      seen.runs += 1;
      if (row.date < seen.first) seen.first = row.date;
    } else {
      byHandle.set(key, {handle: submitter, runs: 1, first: row.date});
    }
  }
  return [...byHandle.values()].sort(
    (a, b) => b.runs - a.runs || a.first.localeCompare(b.first) || a.handle.localeCompare(b.handle),
  );
}

function Scoreboard({
  rows,
  proxies,
  milestone,
  milestoneId,
}: {
  rows: ResultRow[];
  proxies: Proxy[];
  milestone?: ResultRow;
  milestoneId?: string;
}): ReactNode {
  const gateways = new Set(rows.map((row) => gatewayKey(row.project)).filter(Boolean)).size;
  const people = contributors(rows);
  const leaks = rows.filter((row) => Object.values(checkStates(row)).includes('fail')).length;
  const gold = rows.filter((row) => checksPassed(row) === CHECKS).length;
  const replicated = proxies.flatMap((proxy) => proxy.configs).filter((config) => config.replicated).length;
  const measuredHere = rows.filter((row) => row.provenance === 'measured-here').length;
  const stats: {value: number; label: string; hint: string; tone?: string}[] = [
    {value: rows.length, label: 'Runs on the wall', hint: 'One proxy, one version, one configuration each, plus the baseline.'},
    {value: gateways, label: 'Proxies tested', hint: 'Distinct proxies, one card each, however many versions and configurations.'},
    {value: people.length, label: 'Contributors', hint: 'People who submitted a run from their own CI.'},
    {
      value: leaks,
      label: 'Leaks on the record',
      hint: 'Runs with at least one leaking check. Each is a bug someone can now fix.',
      tone: styles.statLeak,
    },
    {value: gold, label: 'Gold shields', hint: 'Runs with all three checks passed.', tone: styles.statGold},
    {
      value: replicated,
      label: 'Replicated',
      hint: `Proxy configurations run by ${REPLICATED_AT} independent people.`,
    },
  ];
  return (
    <section className={styles.scoreboard} aria-label="Scoreboard">
      <ul className={styles.stats}>
        {stats.map((stat) => (
          <li key={stat.label} className={clsx(styles.stat, stat.tone)} title={stat.hint}>
            <span className={styles.statValue}>{stat.value}</span>
            <span className={styles.statLabel}>{stat.label}</span>
          </li>
        ))}
      </ul>

      <div className={clsx(styles.trophy, milestone && styles.trophyClaimed)}>
        <Trophy className={styles.trophyIcon} />
        <div>
          <p className={styles.trophyTitle}>First independent pass</p>
          {milestone && milestoneId ? (
            <p className={styles.trophyText}>
              Held by{' '}
              <a href={safeHref(`#${milestoneId}`)}>
                {milestone.project} {milestone.version}
              </a>
              , for good. Computed from the rows, not awarded.
            </p>
          ) : (
            <p className={styles.trophyText}>
              <strong>Unclaimed.</strong> The first proxy we did not write to answer all three
              questions takes it, permanently. Ours does not count.
            </p>
          )}
        </div>
      </div>

      <div className={styles.people}>
        <p className={styles.peopleTitle}>Contributors</p>
        <ul className={styles.peopleList}>
          {people.map((person) => (
            <li key={person.handle}>
              <a
                className={styles.person}
                href={safeHref(`https://github.com/${person.handle}`)}
                target="_blank"
                rel="noreferrer"
                title={`${person.runs} ${person.runs === 1 ? 'run' : 'runs'} submitted, first on ${person.first}`}>
                <Avatar handle={person.handle} size={36} />
                <span className={styles.personName}>@{person.handle}</span>
                <span className={styles.personRuns}>
                  {person.runs} {person.runs === 1 ? 'run' : 'runs'}
                </span>
              </a>
            </li>
          ))}
          <li>
            <span className={styles.person} title="Runs this project measured itself, ours and other proxies alike.">
              <span className={clsx(styles.avatar, styles.houseAvatar)} aria-hidden="true">
                ◆
              </span>
              <span className={styles.personName}>This project</span>
              <span className={styles.personRuns}>
                {measuredHere} {measuredHere === 1 ? 'run' : 'runs'}
              </span>
            </span>
          </li>
        </ul>
      </div>
    </section>
  );
}

// --------------------------------------------------------------------------- the wall

type SortKey = 'newest' | 'checks' | 'gateway' | 'replication';

/**
 * The reader's order, never the site's. The page ships with the most recently run proxy
 * first; "Most checks passed" is there because a reader may reasonably want it, and choosing
 * it changes nothing but their own view. Every order works on whole cards: configurations and
 * runs always stay newest first inside a card.
 */
const SORTS: {key: SortKey; label: string; compare: (a: Proxy, b: Proxy) => number}[] = [
  {
    key: 'newest',
    label: 'Most recent run',
    compare: (a, b) => newestFirst(a.latest, b.latest),
  },
  {
    key: 'checks',
    label: 'Most checks passed',
    compare: (a, b) =>
      checksPassed(b.headline.row) - checksPassed(a.headline.row) ||
      Number(passes(b.headline.row)) - Number(passes(a.headline.row)) ||
      newestFirst(a.latest, b.latest),
  },
  {
    key: 'gateway',
    label: 'Name',
    compare: (a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()),
  },
  {
    key: 'replication',
    label: 'Most independent runs',
    compare: (a, b) =>
      b.closest.independent.length - a.closest.independent.length ||
      b.runs.length - a.runs.length ||
      newestFirst(a.latest, b.latest),
  },
];

function Legend(): ReactNode {
  return (
    <p className={styles.legend}>
      {(['pass', 'fail', 'incomplete', 'unmeasured'] as CheckState[]).map((state) => (
        <span key={state} className={clsx(styles.legendItem, styles[state])}>
          <span className={styles.pip} aria-hidden="true">
            {STATE[state].glyph}
          </span>
          {STATE[state].word}
        </span>
      ))}
      <span className={styles.legendOrder} title="The order of the three small pips on every run line.">
        Small pips, left to right: {CHECK_KEYS.map((key) => CHECK_PIP[key]).join(', ')}
      </span>
    </p>
  );
}

type Owner = {proxy?: Proxy; config: Config};

export default function ResultsWall({rows = ROWS}: Props): ReactNode {
  // Computed, so the mark is earned by the row rather than granted in a data file. It is
  // undefined until a gateway this project did not write answers all three questions.
  const milestone = useMemo(() => firstIndependentPass(rows), [rows]);
  const ids = useMemo(() => assignIds(rows), [rows]);
  const {proxies, controls} = useMemo(() => groupRuns(rows, ids), [rows, ids]);
  const [sort, setSort] = useState<SortKey>('newest');
  const [linked, setLinked] = useState<string | undefined>();
  const [missing, setMissing] = useState<string | undefined>();
  const [howOpen, setHowOpen] = useState(false);
  // A reader's own choice to open or close a configuration's runs. A configuration with no
  // entry here is closed, unless the address points at one of its folded runs.
  const [open, setOpen] = useState<Record<string, boolean>>({});
  // Bumped on every fragment followed, so following the same link twice scrolls twice.
  const [visit, setVisit] = useState(0);
  const location = useLocation();
  const history = useHistory();
  const readPage = useBaseUrl('/docs/conformance/reading-the-results-wall');
  const submitPage = useBaseUrl('/docs/conformance/add-your-result');
  const docsBase = useBaseUrl('/docs/conformance/');
  // The toggle's id is rendered here rather than by a heading, so tell the link checker it exists.
  useBrokenLinks().collectAnchor(HOW_TO_READ);

  const sorted = useMemo(() => {
    const compare = SORTS.find((option) => option.key === sort)?.compare ?? SORTS[0].compare;
    // Array sort is stable, so cards that tie keep the order they were grouped in.
    return [...proxies].sort(compare);
  }, [proxies, sort]);

  /** Every id a fragment may name, proxy, configuration or run, mapped to where it lives. */
  const owner = useMemo(() => {
    const map = new Map<string, Owner>();
    for (const proxy of proxies) {
      map.set(proxy.id, {proxy, config: proxy.headlineConfig});
      for (const config of proxy.configs) {
        map.set(config.id, {proxy, config});
        for (const run of config.runs) map.set(run.id, {proxy, config});
      }
    }
    for (const config of controls) {
      map.set(config.id, {config});
      for (const run of config.runs) map.set(run.id, {config});
    }
    return map;
  }, [proxies, controls]);

  /**
   * Follow the address's fragment to a run, a configuration or a card: open the folded runs
   * if the run is among them, mark it, and ask for a scroll once the page has rendered it.
   */
  const follow = useCallback(
    (hash: string) => {
      let target = '';
      try {
        target = decodeURIComponent(hash.replace(/^#/, ''));
      } catch {
        return;
      }
      if (!target) {
        setLinked(undefined);
        setMissing(undefined);
        return;
      }
      if (target === HOW_TO_READ) {
        setHowOpen(true);
        setLinked(undefined);
        setMissing(undefined);
        setVisit((n) => n + 1);
        return;
      }
      const moved = MOVED_ANCHORS[target];
      if (moved) {
        history.replace(`${moved === 'read' ? readPage : submitPage}#${target}`);
        return;
      }
      const found = owner.get(target);
      setLinked(found ? target : undefined);
      setMissing(!found && /^issue-[1-9]\d*$/.test(target) ? target.slice('issue-'.length) : undefined);
      if (!found) return;
      // A link to a folded run opens its configuration, even one the reader closed earlier.
      setOpen((current) => {
        if (!(found.config.id in current)) return current;
        const next = {...current};
        delete next[found.config.id];
        return next;
      });
      setVisit((n) => n + 1);
    },
    [history, owner, readPage, submitPage],
  );

  useEffect(() => follow(location.hash), [follow, location.hash]);

  useEffect(() => {
    // A plain fragment link (the `#` on a card) may not reach the router, so listen too.
    const onHash = () => follow(window.location.hash);
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, [follow]);

  /**
   * Scroll after React has committed, so a run that was folded is already visible. Runs exist
   * in the server-rendered page, but the layout can still move after mount (fonts, avatars),
   * so the scroll is checked again once things settle. Focus moves too, so a screen reader
   * starts at the run.
   */
  const scrollTarget = linked ?? (howOpen && visit > 0 ? HOW_TO_READ : undefined);
  useEffect(() => {
    if (!scrollTarget || visit === 0) return undefined;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const block: ScrollLogicalPosition = scrollTarget === HOW_TO_READ ? 'start' : 'center';
    const scroll = (behavior: ScrollBehavior) => {
      const element = document.getElementById(scrollTarget);
      if (!element) return;
      element.scrollIntoView({block, behavior});
      element.focus({preventScroll: true});
    };
    const frame = window.requestAnimationFrame(() => scroll(reduce ? 'auto' : 'smooth'));
    const settle = window.setTimeout(() => {
      const element = document.getElementById(scrollTarget);
      if (!element) return;
      const box = element.getBoundingClientRect();
      if (box.bottom < 0 || box.top > window.innerHeight) scroll('auto');
    }, 900);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(settle);
    };
  }, [scrollTarget, visit]);

  const milestoneId = milestone && ids.get(milestone);
  const linkedOwner = linked ? owner.get(linked) : undefined;

  const isOpen = (config: Config) => {
    if (config.id in open) return open[config.id];
    if (linkedOwner?.config !== config || !linked) return false;
    return config.runs.findIndex((run) => run.id === linked) >= VISIBLE_RUNS;
  };

  return (
    <div className={clsx(styles.tokens, styles.wall)} id="everything-we-have-tested">
      <section id={HOW_TO_READ} tabIndex={-1} className={clsx(styles.howTo, howOpen && styles.howToOpen)}>
        <button
          type="button"
          className={styles.howToToggle}
          aria-expanded={howOpen}
          aria-controls={`${HOW_TO_READ}-body`}
          onClick={() => setHowOpen((value) => !value)}>
          <span className={styles.chevron} aria-hidden="true">
            ▸
          </span>
          <span className={styles.howToLabel}>How to read a card</span>
          <span className={styles.howToHint}>
            {howOpen ? 'Hide the example' : 'An example card with every part labelled'}
          </span>
        </button>
        <div id={`${HOW_TO_READ}-body`} hidden={!howOpen} className={styles.howToBody}>
          {howOpen && <AnnotatedExample />}
          <p className={styles.howToMore}>
            Want the reasoning behind each check?{' '}
            <a href={safeHref(readPage)}>How to read the results wall</a>.
          </p>
        </div>
      </section>

      <Scoreboard rows={rows} proxies={proxies} milestone={milestone} milestoneId={milestoneId} />

      {missing && (
        <p className={styles.missing} role="status">
          Result #{missing} is not on this copy of the wall yet. A new row takes a few minutes
          to appear; if it has been longer, the page is probably cached. Hard refresh with{' '}
          <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>R</kbd>, or <kbd>Cmd</kbd> + <kbd>Shift</kbd>{' '}
          + <kbd>R</kbd> on a Mac.
        </p>
      )}

      <div className={styles.toolbar}>
        <div className={styles.sorts} role="group" aria-label="Order the cards">
          <span className={styles.sortLabel}>Order</span>
          {SORTS.map((option) => (
            <button
              key={option.key}
              type="button"
              className={clsx(styles.sort, sort === option.key && styles.sortActive)}
              aria-pressed={sort === option.key}
              onClick={() => setSort(option.key)}>
              {option.label}
            </button>
          ))}
        </div>
        <Legend />
      </div>

      <ol className={styles.cards}>
        {sorted.map((proxy, index) => {
          const mine = linkedOwner?.proxy === proxy;
          return (
            <ProxyCard
              key={proxy.id}
              proxy={proxy}
              linked={mine ? linked : undefined}
              index={index}
              milestone={milestone}
              isOpen={isOpen}
              onToggle={(config) =>
                setOpen((current) => ({...current, [config.id]: !isOpen(config)}))
              }
              docsBase={docsBase}
            />
          );
        })}
      </ol>

      <ControlStrip controls={controls} linked={linkedOwner && !linkedOwner.proxy ? linked : undefined} />
    </div>
  );
}
