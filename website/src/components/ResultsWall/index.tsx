import {useMemo, useState} from 'react';
import type {ReactNode} from 'react';
import styles from './styles.module.css';
import {
  ARCHITECTURE,
  PROVENANCE,
  ROWS,
  type ResultRow,
} from '@site/src/data/results-wall';
import {safeHref} from '@site/src/utils/safeHref';

export type {ResultRow};

type Props = {rows?: ResultRow[]};

type Col = {
  key: string;
  label: string;
  sortOn?: (row: ResultRow) => string | number;
  numeric?: boolean;
};

// Every column is sortable, including the two that are judgements about the project
// rather than measurements of it. A reader who wants to see all the fork runs together,
// or every gateway that buffers, can have that; the site still ships none of those
// orders as the default. See the page for why that distinction is load-bearing.
const COLUMNS: Col[] = [
  {key: 'project', label: 'Gateway', sortOn: (r) => r.project},
  {key: 'version', label: 'Version', sortOn: (r) => r.version},
  {key: 'sent', label: "Sent the caller's data to the provider", sortOn: (r) => r.sentN, numeric: true},
  {key: 'restored', label: "Gave back the caller's own data", sortOn: (r) => r.restoredN, numeric: true},
  // `?? -1` sorts an unmeasured row below every measured one, in both directions, rather
  // than mixing `undefined` into a numeric compare where it would land arbitrarily. A row
  // that did not look is not a row that found nothing, and the two must not interleave.
  {key: 'leakWhole', label: 'Leaked, value sent whole', sortOn: (r) => r.leakWholeN ?? -1, numeric: true},
  {key: 'leakSplit', label: 'Leaked, value split in two', sortOn: (r) => r.leakSplitN ?? -1, numeric: true},
  {
    key: 'architecture',
    label: 'How it reads the stream',
    sortOn: (r) => ARCHITECTURE[r.architecture].rank,
    numeric: true,
  },
  {key: 'license', label: 'Licence', sortOn: (r) => r.license},
  {
    key: 'provenance',
    label: 'Who ran it',
    sortOn: (r) => PROVENANCE[r.provenance].rank,
    numeric: true,
  },
  {key: 'note', label: 'What happened', sortOn: (r) => r.note},
  {key: 'date', label: 'Tested', sortOn: (r) => r.date},
];

/** Rows older than this read as worth rerunning rather than as current. */
const STALE_AFTER_DAYS = 180;

function daysSince(date: string): number | null {
  const then = Date.parse(date);
  if (Number.isNaN(then)) return null;
  return Math.floor((Date.now() - then) / 86_400_000);
}

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
function Trend({
  now,
  before,
  label,
}: {
  now: number;
  before: number;
  label: string;
}): ReactNode {
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
    </span>
  );
}

const REPO = 'https://github.com/ninadphalak/LLM-Shield-Proxy';

/**
 * Open disputes against a row, as a number a reader can click.
 *
 * It is next to the gateway's name rather than in a column of its own because it is a
 * caveat on the whole row, not another measurement of the product. Zero renders nothing:
 * a column of noughts would read as a score, and this page does not publish one.
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
      {flags.count} open
    </a>
  );
}

/**
 * One leak cell, which may have nothing in it.
 *
 * The two leak columns come from the response-split profile. The operator check that runs
 * in a gateway's own CI measures the request side and fidelity and never produces them, so
 * a row submitted from such a run has no number here. It says so, in words, rather than
 * showing a blank a reader would read as zero.
 */
function Leak({
  label,
  now,
  before,
}: {
  label?: string;
  now?: number;
  before?: number;
}): ReactNode {
  if (label === undefined || now === undefined) {
    return (
      <span
        className={styles.muted}
        title="This run measured what the gateway sent upstream. The response-split profile, which produces this number, was not part of it.">
        not measured
      </span>
    );
  }
  return (
    <>
      {label}
      {before !== undefined && <Trend now={now} before={before} label={label} />}
    </>
  );
}

export default function ResultsWall({rows = ROWS}: Props): ReactNode {
  const [key, setKey] = useState<string>('date');
  const [ascending, setAscending] = useState(false);

  const sorted = useMemo(() => {
    const col = COLUMNS.find((c) => c.key === key);
    const read = col?.sortOn ?? ((r: ResultRow) => r.date);
    const copy = [...rows];
    copy.sort((a, b) => {
      const l = read(a);
      const r = read(b);
      if (typeof l === 'number' && typeof r === 'number') {
        return (l - r) * (ascending ? 1 : -1);
      }
      const ls = String(l).toLowerCase();
      const rs = String(r).toLowerCase();
      if (ls === rs) return 0;
      return (ls < rs ? -1 : 1) * (ascending ? 1 : -1);
    });
    return copy;
  }, [rows, key, ascending]);

  function pick(next: string) {
    if (next === key) {
      setAscending(!ascending);
      return;
    }
    const col = COLUMNS.find((c) => c.key === next);
    setKey(next);
    setAscending(!col?.numeric && next !== 'date');
  }

  return (
    <div className={styles.wrap}>
      <p className={styles.hint}>
        <strong>Click any column heading to sort</strong>, including "Who ran it" and "How
        it reads the stream". That changes only your view: nothing here is scored or ranked,
        and the order the page ships in is the date. An arrow next to a leak count compares
        a gateway with its own earlier version on this page, never with another project.
      </p>
      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              {COLUMNS.map((column) => {
                const active = column.key === key;
                return (
                  <th
                    key={column.key}
                    aria-sort={active ? (ascending ? 'ascending' : 'descending') : 'none'}>
                    <button
                      type="button"
                      className={active ? `${styles.head} ${styles.active}` : styles.head}
                      onClick={() => pick(column.key)}
                      title={`Sort by ${column.label.toLowerCase()}`}>
                      {column.label}
                      <span className={styles.arrow} aria-hidden="true">
                        {active ? (ascending ? '↑' : '↓') : '↕'}
                      </span>
                    </button>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {sorted.map((row) => {
              const age = daysSince(row.date);
              const stale = age !== null && age > STALE_AFTER_DAYS;
              const provenance = PROVENANCE[row.provenance];
              const architecture = ARCHITECTURE[row.architecture];
              return (
                <tr key={`${row.project}-${row.version}`}>
                  <td>
                    {row.project}
                    <Flags flags={row.flags} />
                  </td>
                  <td className={styles.muted}>{row.version}</td>
                  <td>{row.sent}</td>
                  <td>{row.restored}</td>
                  <td>
                    <Leak
                      label={row.leakWhole}
                      now={row.leakWholeN}
                      before={row.previous?.leakWholeN}
                    />
                  </td>
                  <td>
                    <Leak
                      label={row.leakSplit}
                      now={row.leakSplitN}
                      before={row.previous?.leakSplitN}
                    />
                  </td>
                  <td className={styles.muted} title={architecture.hint}>
                    {architecture.label}
                  </td>
                  <td className={styles.muted}>
                    {safeHref(row.pricingUrl) ? (
                      <a href={safeHref(row.pricingUrl)} target="_blank" rel="noreferrer">
                        {row.license}
                      </a>
                    ) : (
                      row.license
                    )}
                  </td>
                  <td>
                    <span className={styles.badge} title={provenance.hint}>
                      {safeHref(row.runUrl) ? (
                        <a href={safeHref(row.runUrl)} target="_blank" rel="noreferrer">
                          {provenance.label}
                        </a>
                      ) : (
                        provenance.label
                      )}
                    </span>
                  </td>
                  <td>{row.note}</td>
                  <td className={styles.muted}>
                    {safeHref(row.reportUrl) ? (
                      <a href={safeHref(row.reportUrl)}>{row.date}</a>
                    ) : (
                      row.date
                    )}
                    {stale && (
                      <span
                        className={styles.stale}
                        title="Measured a while ago. The project has probably shipped since.">
                        worth rerunning
                      </span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
