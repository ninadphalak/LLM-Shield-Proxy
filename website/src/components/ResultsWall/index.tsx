import {useCallback, useEffect, useMemo, useState} from 'react';
import type {CSSProperties, ReactNode} from 'react';
import clsx from 'clsx';
import {useHistory, useLocation} from '@docusaurus/router';
import useBaseUrl from '@docusaurus/useBaseUrl';
import styles from './styles.module.css';
import {
  ARCHITECTURE,
  PROVENANCE,
  ROWS,
  checkStates,
  checksPassed,
  firstIndependentPass,
  isOurs,
  passes,
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

/** Runs shown under a card before the rest fold behind "Show all N runs". */
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
 * These ids belong to the run, not the card, so grouping runs under one card changed none of
 * them: a link made when every run had a card of its own lands on the same run today.
 */
function assignIds(rows: ResultRow[]): Map<ResultRow, string> {
  const ids = new Map<ResultRow, string>();
  const taken = new Set<string>();
  for (const row of rows) {
    const {issue} = submissionOf(row);
    const id = issue ? `issue-${issue}` : undefined;
    if (id && !taken.has(id)) {
      ids.set(row, id);
      taken.add(id);
    }
  }
  for (const row of rows) {
    if (ids.has(row)) continue;
    const base = slug(`${row.project} ${row.version}`) || 'result';
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
 * its wording matches, so a doubtful pair shows as two cards rather than one merged claim.
 */
const CONFIG_ALIASES: Record<string, string> = {
  'request redaction on, response redaction on with an off-arm control': 'response scan on',
};

/**
 * Which runs of a card count as the same pinned version, for the dispute rule. A commit hash
 * is the version whatever words surround it ("fork main dceef23176de" and "commit
 * dceef23176de" are one build); otherwise the version text itself.
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

type Group = {
  id: string;
  name: string;
  /** The configuration as the card shows it, or empty when no run states one. */
  config: string;
  runs: Run[];
  /** The run the card's shield and pips come from: the newest with all three checks measured. */
  headline: Run;
  /** False when no run measured all three checks, so the headline is simply the newest. */
  headlineComplete: boolean;
  latest: string;
  independent: string[];
  replicated: boolean;
  /** Checks on which two runs of the SAME version disagree. The target is disputed. */
  disputed: {check: CheckKey; version: string}[];
  /** Checks that came out differently in different versions: revision history, not dispute. */
  changed: CheckKey[];
};

const CHECK_KEYS: CheckKey[] = ['request', 'whole', 'split'];

const newestFirst = (a: Run, b: Run) => b.row.date.localeCompare(a.row.date) || b.order - a.order;

/**
 * One card per gateway and configuration, with every run of it stacked underneath.
 *
 * THE GROUPING RULE. Two runs share a card when (1) their gateways are the same by
 * `gatewayKey`, the same normalisation the "Gateways tested" count uses, so "Portkey" and
 * "Portkey OSS Gateway" are one gateway and "LLM-Shield-Proxy (ours)" is LLM-Shield-Proxy;
 * and (2) the configuration half of their `version` field (see `splitVersion`) is the same
 * text, ignoring case, spacing and a trailing full stop, after `CONFIG_ALIASES`. The version
 * half is deliberately NOT part of the key: a new release or commit of the same setup is a
 * reproduction of it, which is what `submitting.md` counts toward replication. A run whose
 * configuration is not stated only groups with other unstated runs of the same gateway,
 * never with a configured one, because "not stated" is not evidence of "the same".
 *
 * A run with no gateway in it (the no-gateway control) groups by its full project name, so
 * it keeps a card of its own.
 */
function groupRuns(rows: ResultRow[], ids: Map<ResultRow, string>): Group[] {
  const byKey = new Map<string, Run[]>();
  rows.forEach((row, order) => {
    const {version, config} = splitVersion(row.version);
    const configKey = normalise(config);
    const canonical = CONFIG_ALIASES[configKey] ?? configKey;
    const gateway = gatewayKey(row.project) ?? normalise(row.project);
    const key = `${gateway}|${canonical}`;
    const {issue, submitter} = submissionOf(row);
    const run: Run = {
      row,
      id: ids.get(row) ?? `result-${order}`,
      order,
      version,
      config,
      issue,
      submitter,
      states: checkStates(row),
    };
    byKey.set(key, [...(byKey.get(key) ?? []), run]);
  });

  const groups: Group[] = [];
  // A card id must never take a run's id, or a bot link would land on the wrong thing.
  const taken = new Set(ids.values());
  for (const [key, unsorted] of byKey) {
    const runs = [...unsorted].sort(newestFirst);
    const complete = runs.find((run) => !Object.values(run.states).includes('unmeasured'));
    const headline = complete ?? runs[0];
    const headlineComplete = complete !== undefined;
    const configKey = key.slice(key.indexOf('|') + 1);
    const aliased = Object.values(CONFIG_ALIASES).includes(configKey);
    // An aliased card shows the shared wording; any other shows its newest run's own words.
    const config = aliased
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

    // Disagreements are kept and shown, never averaged (submitting.md). Only runs of the
    // same pinned version can dispute each other; a different result from a different
    // version is a change, and the runs list shows which version changed it.
    const disputed: Group['disputed'] = [];
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

    const base = `card-${slug(key.replaceAll('|', ' ')) || 'gateway'}`;
    let id = base;
    for (let n = 2; taken.has(id); n += 1) id = `${base}-${n}`;
    taken.add(id);

    groups.push({
      id,
      // The newest run's name. "(ours)" is dropped because the card says so in a chip.
      name: runs[0].row.project.replace(/\s*\(ours\)\s*$/i, ''),
      config,
      runs,
      headline,
      headlineComplete,
      latest: runs[0].row.date,
      independent,
      replicated: independent.length >= REPLICATED_AT,
      disputed,
      changed,
    });
  }
  return groups;
}

// --------------------------------------------------------------------------- the checks

const CHECK_LABEL: Record<CheckKey, string> = {
  request: 'Request path',
  whole: 'Value whole in response',
  split: 'Value split across chunks',
};

const CHECK_SHORT: Record<CheckKey, string> = {
  request: 'request',
  whole: 'whole',
  split: 'split',
};

/** Glyph and word for every state, so no state is carried by colour alone. */
const STATE: Record<CheckState, {glyph: string; word: string}> = {
  pass: {glyph: '✓', word: 'passed'},
  fail: {glyph: '✕', word: 'leaked'},
  incomplete: {glyph: '!', word: 'incomplete'},
  unmeasured: {glyph: '–', word: 'not measured'},
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

function leakDetail(label: string | undefined, state: CheckState, inconclusive: number): string {
  if (state === 'unmeasured' || label === undefined) return 'not measured';
  if (state === 'incomplete') return `${label} leaked, ${inconclusive} cases unjudged`;
  return `${label} leaked`;
}

/**
 * The three checks, as pips. Same rule as the README badge (`checkStates`), and each pip
 * says in words what it found, so a reader who cannot tell the colours apart loses nothing.
 */
function Checks({row}: {row: ResultRow}): ReactNode {
  const states = checkStates(row);
  const inconclusive = row.responseInconclusive ?? 0;
  const details: Record<CheckKey, ReactNode> = {
    request: row.sentN === 0 ? 'nothing reached the provider' : `${row.sent} reached the provider`,
    whole: (
      <>
        {leakDetail(row.leakWhole, states.whole, inconclusive)}
        {row.leakWhole && row.leakWholeN !== undefined && row.previous && (
          <Trend now={row.leakWholeN} before={row.previous.leakWholeN} label={row.leakWhole} />
        )}
      </>
    ),
    split: (
      <>
        {leakDetail(row.leakSplit, states.split, inconclusive)}
        {row.leakSplit && row.leakSplitN !== undefined && row.previous && (
          <Trend now={row.leakSplitN} before={row.previous.leakSplitN} label={row.leakSplit} />
        )}
      </>
    ),
  };
  return (
    <ul className={styles.checks}>
      {CHECK_KEYS.map((key) => (
        <li key={key} className={clsx(styles.check, styles[states[key]])}>
          <span className={styles.pip} aria-hidden="true">
            {STATE[states[key]].glyph}
          </span>
          <span className={styles.checkText}>
            <span className={styles.checkLabel}>
              {CHECK_LABEL[key]}
              <span className={styles.srOnly}>: {STATE[states[key]].word},</span>
            </span>
            <span className={styles.checkDetail}>{details[key]}</span>
          </span>
        </li>
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
          title={`${CHECK_LABEL[key]}: ${STATE[states[key]].word}`}>
          <span aria-hidden="true">{STATE[states[key]].glyph}</span>
          <span className={styles.srOnly}>
            {CHECK_LABEL[key]}: {STATE[states[key]].word}.{' '}
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
 * caller data came back the card says so next to any clean response pip, so `0 of 16` is
 * never read as the flattering half of a number with two readings.
 */
function Fidelity({row}: {row: ResultRow}): ReactNode {
  const all = row.restoredN === 1;
  const none = row.restoredN === 0;
  const states = checkStates(row);
  const cleanResponse = states.whole === 'pass' || states.split === 'pass';
  return (
    <p className={clsx(styles.fidelity, all ? styles.pass : none ? styles.fail : styles.incomplete)}>
      <span className={styles.pip} aria-hidden="true">
        {all ? '✓' : none ? '✕' : '!'}
      </span>
      <span>
        Gave the caller&apos;s own data back: <strong>{row.restored}</strong>
        {none && cleanResponse && (
          <span className={styles.caveat}>
            {' '}
            Nothing came back, so a low leak count may mean little was returned at all.
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
    <a
      className={styles.flag}
      href={safeHref(`${REPO}/issues/${issue}`)}
      target="_blank"
      rel="noreferrer"
      title={`${label} about this run. Click to read them.`}>
      {flags.count} open {flags.count === 1 ? 'question' : 'questions'}
    </a>
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
  'measured-here': 'benchmark maintainers',
  'submitted-main': "the project's own CI",
  'submitted-branch': "the project's own CI",
  'submitted-fork': 'a fork',
  'submitted-unverified': 'self-reported',
};

function RunLine({
  run,
  groupConfig,
  linked,
  milestone,
  hidden,
}: {
  run: Run;
  groupConfig: string;
  linked: boolean;
  milestone: boolean;
  hidden: boolean;
}): ReactNode {
  const {row, submitter, issue} = run;
  const provenance = PROVENANCE[row.provenance];
  const ciRun = safeHref(row.runUrl);
  const report = safeHref(row.reportUrl);
  const passed = checksPassed(row);
  const maintainer = submitter && BENCHMARK_MAINTAINERS.has(submitter.toLowerCase());
  // A run worded differently from its card (an alias) shows its own words, so grouping never
  // hides what a submitter wrote.
  const differs = run.config !== '' && normalise(run.config) !== normalise(groupConfig);
  return (
    <li
      id={run.id}
      tabIndex={-1}
      hidden={hidden}
      className={clsx(styles.run, linked && styles.runLinked)}>
      <span className={styles.runWho}>
        {submitter ? (
          <>
            <Avatar handle={submitter} size={22} />
            <a
              className={styles.handle}
              href={safeHref(`https://github.com/${submitter}`)}
              target="_blank"
              rel="noreferrer"
              title={maintainer ? 'A benchmark maintainer. Does not count toward replication.' : undefined}>
              @{submitter}
            </a>
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
      <span className={styles.runWhat}>
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
        <span className={styles.runCount} aria-hidden="true">
          {passed}/{CHECKS}
        </span>
        <span className={styles.chip} title={provenance.hint}>
          {provenance.label}
        </span>
      </span>
      <span className={styles.runLinks}>
        {ciRun ? (
          <a href={ciRun} target="_blank" rel="noreferrer" title="The CI run behind this result">
            CI run
          </a>
        ) : report ? (
          <a href={report} title="The report behind this result">
            report
          </a>
        ) : null}
        {issue && (
          <a
            href={safeHref(`${REPO}/issues/${issue}`)}
            target="_blank"
            rel="noreferrer"
            title="The submission issue">
            #{issue}
          </a>
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
        <a className={styles.permalink} href={safeHref(`#${run.id}`)} title="Link to this run">
          #<span className={styles.srOnly}>Link to this run</span>
        </a>
      </span>
    </li>
  );
}

/**
 * How far a card is from being called replicated, in the words `submitting.md` uses, with a
 * three-step meter beside the words for a reader scanning the wall.
 */
function Replication({group}: {group: Group}): ReactNode {
  const runs = group.runs.length;
  const people = group.independent.length;
  const shown = Math.min(people, REPLICATED_AT);
  return (
    <div
      className={clsx(styles.replication, group.replicated && styles.replicated)}
      title="A result is replicated when three different people, other than the benchmark maintainers, have each run the same gateway and configuration from their own fork. Runs in the gateway's own repository do not count.">
      <span className={styles.meter} aria-hidden="true">
        {Array.from({length: REPLICATED_AT}, (_, i) => (
          <span key={i} className={clsx(styles.meterStep, i < shown && styles.meterOn)} />
        ))}
      </span>
      <span>
        <strong>
          {runs} {runs === 1 ? 'run' : 'runs'}
        </strong>
        , {people} independent {people === 1 ? 'submitter' : 'submitters'}.{' '}
        {group.replicated ? (
          <strong>Replicated.</strong>
        ) : (
          <>
            <strong>Unreplicated:</strong> {shown} of the {REPLICATED_AT} independent submitters
            needed to call it replicated.
          </>
        )}
      </span>
    </div>
  );
}

function Card({
  group,
  linked,
  index,
  milestone,
  expanded,
  onToggle,
}: {
  group: Group;
  /** The id the address points at, when it is this card or one of its runs. */
  linked?: string;
  index: number;
  milestone?: ResultRow;
  expanded: boolean;
  onToggle: () => void;
}): ReactNode {
  const row = group.headline.row;
  const passed = checksPassed(row);
  const full = passes(row) && passed === CHECKS;
  const age = daysSince(group.latest);
  const stale = age !== null && age > STALE_AFTER_DAYS;
  // A property of the configuration, so the newest run that states it speaks for the card.
  const architecture =
    ARCHITECTURE[group.runs.find((run) => run.row.architecture !== 'not-stated')?.row.architecture ?? 'not-stated'];
  const licensed = group.runs[0].row;
  const pricing = safeHref(group.runs.find((run) => run.row.pricingUrl)?.row.pricingUrl);
  const state = passed === CHECKS ? styles.stateGold : passed > 0 ? styles.statePartial : styles.stateNone;
  const hasMilestone = milestone !== undefined && group.runs.some((run) => run.row === milestone);
  const listId = `${group.id}-runs`;
  const extra = group.runs.length - VISIBLE_RUNS;
  return (
    <li
      id={group.id}
      tabIndex={-1}
      className={clsx(styles.card, state, linked && styles.linked)}
      style={{'--i': Math.min(index, 12)} as CSSProperties}>
      {linked && (
        <span className={styles.ribbon} aria-hidden="true">
          Linked result
        </span>
      )}
      <div className={styles.cardHead}>
        <Medal passed={passed} />
        <div className={styles.identity}>
          <h3 className={styles.project}>{group.name}</h3>
          <p className={styles.version}>
            {group.config ? group.config : <em>Configuration not stated</em>}
          </p>
          <p className={styles.tags}>
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
            <span className={styles.chip} title="Licence">
              {pricing ? (
                <a href={pricing} target="_blank" rel="noreferrer">
                  {licensed.license}
                </a>
              ) : (
                licensed.license
              )}
            </span>
            {isOurs(group.name) && (
              <span className={clsx(styles.chip, styles.chipQuiet)} title="This project wrote this gateway.">
                ours
              </span>
            )}
          </p>
        </div>
        <a className={styles.permalink} href={safeHref(`#${group.id}`)} title="Link to this card">
          #<span className={styles.srOnly}>Link to this card</span>
        </a>
      </div>

      <Replication group={group} />

      {group.disputed.length > 0 && (
        <p className={styles.disputed}>
          <span className={styles.disputeMark} aria-hidden="true">
            ⚑
          </span>
          <span>
            <strong>Disputed.</strong> Runs of the same version disagree on{' '}
            {group.disputed.map((d) => `${CHECK_LABEL[d.check].toLowerCase()} (${d.version})`).join(', ')}.
            Both are kept below, not averaged.
          </span>
        </p>
      )}
      {group.changed.length > 0 && (
        <p className={styles.changed}>
          <span aria-hidden="true">↻</span>{' '}
          <span>
            <strong>Changed between versions</strong> on{' '}
            {group.changed.map((check) => CHECK_LABEL[check].toLowerCase()).join(', ')}. The runs
            below show which version did what.
          </span>
        </p>
      )}

      <div className={styles.headline}>
        <p className={styles.headlineCaption}>
          <span className={styles.captionLabel}>
            {group.runs.length === 1
              ? 'The only run'
              : group.headlineComplete
                ? 'Latest complete run'
                : 'Latest run'}
          </span>{' '}
          {row.date}, {group.headline.version}
          {row.harness && (
            <span
              title="Two runs measured with different harness versions were produced by different code.">
              , harness {row.harness}
            </span>
          )}
        </p>
        <Checks row={row} />
        <Fidelity row={row} />
        <p className={styles.note}>{row.note}</p>
      </div>

      <div className={styles.meta}>
        <span className={clsx(styles.chip, styles.chipQuiet)} title={architecture.hint}>
          Reads the stream: {architecture.label}
        </span>
        {stale && (
          <span className={styles.stale} title="Last measured a while ago. The project has probably shipped since.">
            worth rerunning
          </span>
        )}
      </div>

      <div className={styles.runsBlock}>
        <p className={styles.runsTitle} id={`${listId}-title`}>
          {group.runs.length === 1 ? 'Run' : `All ${group.runs.length} runs, newest first`}
        </p>
        <ol className={styles.runs} id={listId} aria-labelledby={`${listId}-title`}>
          {group.runs.map((run, i) => (
            <RunLine
              key={run.id}
              run={run}
              groupConfig={group.config}
              linked={run.id === linked}
              milestone={run.row === milestone}
              hidden={!expanded && i >= VISIBLE_RUNS}
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
            {expanded ? 'Show fewer runs' : `Show all ${group.runs.length} runs`}
          </button>
        )}
      </div>
    </li>
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
  groups,
  milestone,
  milestoneId,
}: {
  rows: ResultRow[];
  groups: Group[];
  milestone?: ResultRow;
  milestoneId?: string;
}): ReactNode {
  const gateways = new Set(rows.map((row) => gatewayKey(row.project)).filter(Boolean)).size;
  const people = contributors(rows);
  const leaks = rows.filter((row) => Object.values(checkStates(row)).includes('fail')).length;
  const gold = rows.filter((row) => checksPassed(row) === CHECKS).length;
  const replicated = groups.filter((group) => group.replicated).length;
  const measuredHere = rows.filter((row) => row.provenance === 'measured-here').length;
  const stats: {value: number; label: string; hint: string; tone?: string}[] = [
    {value: rows.length, label: 'Runs on the wall', hint: 'One gateway, one version, one configuration each.'},
    {value: gateways, label: 'Gateways tested', hint: 'Distinct gateways, however many versions each.'},
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
      hint: `Gateway and configuration cards run by ${REPLICATED_AT} independent submitters.`,
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
              <strong>Unclaimed.</strong> The first gateway we did not write to answer all three
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
            <span className={styles.person} title="Runs this project measured itself, ours and other gateways alike.">
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
 * The reader's order, never the site's. The page ships with the most recently run card first;
 * "Most checks passed" is there because a reader may reasonably want it, and choosing it
 * changes nothing but their own view. Every order works on whole cards: a card's runs always
 * stay newest first underneath it.
 */
const SORTS: {key: SortKey; label: string; compare: (a: Group, b: Group) => number}[] = [
  {
    key: 'newest',
    label: 'Most recent run',
    compare: (a, b) => b.latest.localeCompare(a.latest) || b.runs[0].order - a.runs[0].order,
  },
  {
    key: 'checks',
    label: 'Most checks passed',
    compare: (a, b) =>
      checksPassed(b.headline.row) - checksPassed(a.headline.row) ||
      Number(passes(b.headline.row)) - Number(passes(a.headline.row)) ||
      b.latest.localeCompare(a.latest),
  },
  {
    key: 'gateway',
    label: 'Gateway',
    compare: (a, b) =>
      `${a.name} ${a.config}`.toLowerCase().localeCompare(`${b.name} ${b.config}`.toLowerCase()),
  },
  {
    key: 'replication',
    label: 'Most independent runs',
    compare: (a, b) =>
      b.independent.length - a.independent.length ||
      b.runs.length - a.runs.length ||
      b.latest.localeCompare(a.latest),
  },
];

function Legend({readHref}: {readHref: string}): ReactNode {
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
      <span className={styles.legendOrder} title="The order of the three pips on every run line.">
        pip order: {CHECK_KEYS.map((key) => CHECK_SHORT[key]).join(', ')}
      </span>
      <a className={styles.legendLink} href={safeHref(readHref)}>
        How to read a card
      </a>
    </p>
  );
}

export default function ResultsWall({rows = ROWS}: Props): ReactNode {
  // Computed, so the mark is earned by the row rather than granted in a data file. It is
  // undefined until a gateway this project did not write answers all three questions.
  const milestone = useMemo(() => firstIndependentPass(rows), [rows]);
  const ids = useMemo(() => assignIds(rows), [rows]);
  const groups = useMemo(() => groupRuns(rows, ids), [rows, ids]);
  const [sort, setSort] = useState<SortKey>('newest');
  const [linked, setLinked] = useState<string | undefined>();
  const [missing, setMissing] = useState<string | undefined>();
  // A reader's own choice to open or close a card's runs. A card with no entry here is
  // closed, unless the address points at one of its folded runs.
  const [open, setOpen] = useState<Record<string, boolean>>({});
  // Bumped on every fragment followed, so following the same link twice scrolls twice.
  const [visit, setVisit] = useState(0);
  const location = useLocation();
  const history = useHistory();
  const readPage = useBaseUrl('/docs/conformance/reading-the-results-wall');
  const submitPage = useBaseUrl('/docs/conformance/add-your-result');

  const sorted = useMemo(() => {
    const compare = SORTS.find((option) => option.key === sort)?.compare ?? SORTS[0].compare;
    // Array sort is stable, so cards that tie keep the order they were grouped in.
    return [...groups].sort(compare);
  }, [groups, sort]);

  /** Every id a fragment may name, run or card, mapped to the card it lives in. */
  const owner = useMemo(() => {
    const map = new Map<string, Group>();
    for (const group of groups) {
      map.set(group.id, group);
      for (const run of group.runs) map.set(run.id, group);
    }
    return map;
  }, [groups]);

  /**
   * Follow the address's fragment to a run or a card: open the card if the run is folded,
   * mark both, and ask for a scroll once the page has rendered the run visible.
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
      const moved = MOVED_ANCHORS[target];
      if (moved) {
        history.replace(`${moved === 'read' ? readPage : submitPage}#${target}`);
        return;
      }
      const group = owner.get(target);
      setLinked(group ? target : undefined);
      setMissing(!group && /^issue-[1-9]\d*$/.test(target) ? target.slice('issue-'.length) : undefined);
      if (!group) return;
      // A link to a folded run opens its card, even one the reader closed earlier.
      setOpen((current) => {
        if (!(group.id in current)) return current;
        const next = {...current};
        delete next[group.id];
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
  useEffect(() => {
    if (!linked || visit === 0) return undefined;
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
    const scroll = (behavior: ScrollBehavior) => {
      const element = document.getElementById(linked);
      if (!element) return;
      element.scrollIntoView({block: 'center', behavior});
      element.focus({preventScroll: true});
    };
    const frame = window.requestAnimationFrame(() => scroll(reduce ? 'auto' : 'smooth'));
    const settle = window.setTimeout(() => {
      const element = document.getElementById(linked);
      if (!element) return;
      const box = element.getBoundingClientRect();
      if (box.bottom < 0 || box.top > window.innerHeight) scroll('auto');
    }, 900);
    return () => {
      window.cancelAnimationFrame(frame);
      window.clearTimeout(settle);
    };
  }, [linked, visit]);

  const milestoneId = milestone && ids.get(milestone);

  return (
    <div className={styles.wall} id="everything-we-have-tested">
      <Scoreboard rows={rows} groups={groups} milestone={milestone} milestoneId={milestoneId} />

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
        <Legend readHref={`${readPage}#reading-a-card`} />
      </div>

      <ol className={styles.cards}>
        {sorted.map((group, index) => {
          const mine = linked !== undefined && owner.get(linked) === group;
          const linkedRun = mine ? group.runs.findIndex((run) => run.id === linked) : -1;
          const expanded = open[group.id] ?? linkedRun >= VISIBLE_RUNS;
          return (
            <Card
              key={group.id}
              group={group}
              linked={mine ? linked : undefined}
              index={index}
              milestone={milestone}
              expanded={expanded}
              onToggle={() => setOpen((current) => ({...current, [group.id]: !expanded}))}
            />
          );
        })}
      </ol>
    </div>
  );
}
