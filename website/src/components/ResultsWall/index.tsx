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
 * A stable DOM id for every row, so any row can be linked to.
 *
 * A submitted row is `issue-<number>`, which is what the intake bot links to from the issue
 * (`row_url` in `scripts/process_conformance_submission.py`); change one and the other must
 * follow. A row with no issue is a slug of its gateway and version. Issue ids are assigned
 * first so that a slug can never take one, and a repeated slug gets a numeric suffix in the
 * order the rows are declared, which does not change when a reader sorts.
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

// --------------------------------------------------------------------------- the checks

const CHECK_LABEL: Record<CheckKey, string> = {
  request: 'Request path',
  whole: 'Value whole in response',
  split: 'Value split across chunks',
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
      {(Object.keys(CHECK_LABEL) as CheckKey[]).map((key) => (
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
      title={`${label} about this row. Click to read them.`}>
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
      aria-label={`${passed} of ${CHECKS} checks passed`}>
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
 * Who ran it, on the card's main line rather than in a tooltip.
 *
 * A submitted row names its submitter with their avatar, beside the run it links to, so a
 * reader can see a person ran it instead of inferring one from a URL. A row with no
 * submitter says in plain words who produced it, from its provenance.
 */
const NO_SUBMITTER: Record<ResultRow['provenance'], string> = {
  'measured-here': 'Run by the benchmark maintainers',
  'submitted-main': "Run in the project's own CI",
  'submitted-branch': "Run in the project's own CI",
  'submitted-fork': 'Run in a fork',
  'submitted-unverified': 'Self-reported, with no run to point at',
};

function RunBy({row}: {row: ResultRow}): ReactNode {
  const {issue, submitter} = submissionOf(row);
  const provenance = PROVENANCE[row.provenance];
  const run = safeHref(row.runUrl);
  return (
    <p className={styles.runBy}>
      {submitter ? (
        <>
          <Avatar handle={submitter} size={22} />
          <span>
            Run by{' '}
            <a
              className={styles.handle}
              href={safeHref(`https://github.com/${submitter}`)}
              target="_blank"
              rel="noreferrer">
              @{submitter}
            </a>
          </span>
        </>
      ) : (
        <>
          <span className={styles.houseMark} aria-hidden="true">
            ◆
          </span>
          <span>{NO_SUBMITTER[row.provenance]}</span>
        </>
      )}
      <span className={styles.chip} title={provenance.hint}>
        {run ? (
          <a href={run} target="_blank" rel="noreferrer">
            {provenance.label}, see the run
          </a>
        ) : (
          provenance.label
        )}
      </span>
      {issue && (
        <a
          className={styles.issueLink}
          href={safeHref(`${REPO}/issues/${issue}`)}
          target="_blank"
          rel="noreferrer"
          title="The submission issue">
          #{issue}
        </a>
      )}
    </p>
  );
}

function Card({
  row,
  id,
  linked,
  index,
  milestone,
}: {
  row: ResultRow;
  id: string;
  linked: boolean;
  index: number;
  milestone: boolean;
}): ReactNode {
  const passed = checksPassed(row);
  const full = passes(row) && passed === CHECKS;
  const age = daysSince(row.date);
  const stale = age !== null && age > STALE_AFTER_DAYS;
  const architecture = ARCHITECTURE[row.architecture];
  const pricing = safeHref(row.pricingUrl);
  const state = passed === CHECKS ? styles.stateGold : passed > 0 ? styles.statePartial : styles.stateNone;
  return (
    <li
      id={id}
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
          <h3 className={styles.project}>{row.project}</h3>
          <p className={styles.version}>{row.version}</p>
          <RunBy row={row} />
          <p className={styles.tags}>
            {full && (
              <span
                className={styles.fullPass}
                title="Nothing reached the provider, every value came back to the caller, and nothing it never sent got through, whole or split.">
                ★ Full pass
              </span>
            )}
            {milestone && (
              <span
                className={styles.milestone}
                title="The first gateway not written by this project to answer all three questions. Computed from the rows, not awarded.">
                <Trophy className={styles.inlineTrophy} /> First independent pass
              </span>
            )}
            <span className={styles.chip}>
              {pricing ? (
                <a href={pricing} target="_blank" rel="noreferrer">
                  {row.license}
                </a>
              ) : (
                row.license
              )}
            </span>
            <Flags flags={row.flags} />
          </p>
        </div>
        <a className={styles.permalink} href={safeHref(`#${id}`)} title="Link to this result">
          #<span className={styles.srOnly}>Link to this result</span>
        </a>
      </div>

      <Checks row={row} />
      <Fidelity row={row} />

      <p className={styles.note}>{row.note}</p>

      <div className={styles.meta}>
        <span className={clsx(styles.chip, styles.chipQuiet)} title={architecture.hint}>
          {architecture.label}
        </span>
        <span className={styles.date}>
          {safeHref(row.reportUrl) ? <a href={safeHref(row.reportUrl)}>{row.date}</a> : row.date}
        </span>
        {row.harness && (
          <span
            className={styles.harness}
            title={`Measured with pii-leak-benchmark ${row.harness}. Two rows measured with different harness versions were produced by different code.`}>
            harness {row.harness}
          </span>
        )}
        {stale && (
          <span className={styles.stale} title="Measured a while ago. The project has probably shipped since.">
            worth rerunning
          </span>
        )}
      </div>
    </li>
  );
}

// --------------------------------------------------------------------------- the scoreboard

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
  milestone,
  milestoneId,
}: {
  rows: ResultRow[];
  milestone?: ResultRow;
  milestoneId?: string;
}): ReactNode {
  const gateways = new Set(rows.map((row) => gatewayKey(row.project)).filter(Boolean)).size;
  const people = contributors(rows);
  const leaks = rows.filter((row) => Object.values(checkStates(row)).includes('fail')).length;
  const gold = rows.filter((row) => checksPassed(row) === CHECKS).length;
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

type SortKey = 'newest' | 'checks' | 'gateway' | 'provenance';

/**
 * The reader's order, never the site's. The page ships newest first; "Most checks passed"
 * is there because a reader may reasonably want it, and choosing it changes nothing but
 * their own view.
 */
const SORTS: {key: SortKey; label: string; compare: (a: ResultRow, b: ResultRow) => number}[] = [
  {key: 'newest', label: 'Newest', compare: (a, b) => b.date.localeCompare(a.date)},
  {
    key: 'checks',
    label: 'Most checks passed',
    compare: (a, b) =>
      checksPassed(b) - checksPassed(a) ||
      Number(passes(b)) - Number(passes(a)) ||
      b.date.localeCompare(a.date),
  },
  {
    key: 'gateway',
    label: 'Gateway',
    compare: (a, b) =>
      `${a.project} ${a.version}`.toLowerCase().localeCompare(`${b.project} ${b.version}`.toLowerCase()),
  },
  {
    key: 'provenance',
    label: 'Who ran it',
    compare: (a, b) =>
      PROVENANCE[b.provenance].rank - PROVENANCE[a.provenance].rank || b.date.localeCompare(a.date),
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
  const [sort, setSort] = useState<SortKey>('newest');
  const [linked, setLinked] = useState<string | undefined>();
  const [missing, setMissing] = useState<string | undefined>();
  const location = useLocation();
  const history = useHistory();
  const readPage = useBaseUrl('/docs/conformance/reading-the-results-wall');
  const submitPage = useBaseUrl('/docs/conformance/add-your-result');

  const sorted = useMemo(() => {
    const compare = SORTS.find((option) => option.key === sort)?.compare ?? SORTS[0].compare;
    // Array sort is stable, so rows that tie keep the order they are declared in.
    return [...rows].sort(compare);
  }, [rows, sort]);

  /**
   * Follow the address's fragment to a row: scroll it into view, mark it, and move focus
   * to it so a screen reader starts there too. Rows exist in the server-rendered page, but
   * the layout can still move after mount (fonts, avatars), so the scroll is checked again
   * once things settle.
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
      const known = [...ids.values()].includes(target);
      setLinked(known ? target : undefined);
      setMissing(!known && /^issue-[1-9]\d*$/.test(target) ? target.slice('issue-'.length) : undefined);
      if (!known) return;
      const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      const scroll = (behavior: ScrollBehavior) => {
        const element = document.getElementById(target);
        if (!element) return;
        element.scrollIntoView({block: 'center', behavior});
        element.focus({preventScroll: true});
      };
      const frame = window.requestAnimationFrame(() => scroll(reduce ? 'auto' : 'smooth'));
      const settle = window.setTimeout(() => {
        const element = document.getElementById(target);
        if (!element) return;
        const box = element.getBoundingClientRect();
        if (box.bottom < 0 || box.top > window.innerHeight) scroll('auto');
      }, 900);
      return () => {
        window.cancelAnimationFrame(frame);
        window.clearTimeout(settle);
      };
    },
    [history, ids, readPage, submitPage],
  );

  useEffect(() => follow(location.hash), [follow, location.hash]);

  useEffect(() => {
    // A plain fragment link (the `#` on a card) may not reach the router, so listen too.
    const onHash = () => follow(window.location.hash);
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, [follow]);

  return (
    <div className={styles.wall} id="everything-we-have-tested">
      <Scoreboard rows={rows} milestone={milestone} milestoneId={milestone && ids.get(milestone)} />

      {missing && (
        <p className={styles.missing} role="status">
          Result #{missing} is not on this copy of the wall yet. A new row takes a few minutes
          to appear; if it has been longer, the page is probably cached. Hard refresh with{' '}
          <kbd>Ctrl</kbd> + <kbd>Shift</kbd> + <kbd>R</kbd>, or <kbd>Cmd</kbd> + <kbd>Shift</kbd>{' '}
          + <kbd>R</kbd> on a Mac.
        </p>
      )}

      <div className={styles.toolbar}>
        <div className={styles.sorts} role="group" aria-label="Order the results">
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
        {sorted.map((row, index) => {
          const id = ids.get(row) ?? `result-${index}`;
          return (
            <Card
              key={id}
              row={row}
              id={id}
              linked={id === linked}
              index={index}
              milestone={row === milestone}
            />
          );
        })}
      </ol>
    </div>
  );
}
