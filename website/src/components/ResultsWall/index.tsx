import {useMemo, useState} from 'react';
import type {ReactNode} from 'react';
import styles from './styles.module.css';

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
  /** 'measured' if this project ran it, 'submitted' if the project sent it in. */
  source: 'measured' | 'submitted';
  reportUrl?: string;
};

type Props = {rows: ResultRow[]};

type Col = {key: string; label: string; sortOn?: keyof ResultRow; numeric?: boolean};

const COLUMNS: Col[] = [
  {key: 'project', label: 'Gateway', sortOn: 'project'},
  {key: 'version', label: 'Version', sortOn: 'version'},
  {key: 'sent', label: "Sent the caller's data to the provider", sortOn: 'sentN', numeric: true},
  {key: 'restored', label: "Gave back the caller's own data", sortOn: 'restoredN', numeric: true},
  {key: 'leakWhole', label: 'Leaked, value sent whole', sortOn: 'leakWholeN', numeric: true},
  {key: 'leakSplit', label: 'Leaked, value split in two', sortOn: 'leakSplitN', numeric: true},
  {key: 'note', label: 'What happened', sortOn: 'note'},
  {key: 'date', label: 'Tested', sortOn: 'date'},
];

// Default view is newest first. The site never ships a ranked order; the reader can
// sort for themselves. See the page text for why that distinction matters.
export default function ResultsWall({rows}: Props): ReactNode {
  const [key, setKey] = useState<string>('date');
  const [ascending, setAscending] = useState(false);

  const sorted = useMemo(() => {
    const col = COLUMNS.find((c) => c.key === key);
    const field = (col?.sortOn ?? 'date') as keyof ResultRow;
    const copy = [...rows];
    copy.sort((a, b) => {
      const l = a[field];
      const r = b[field];
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
        Click any column heading to sort. This changes only your view of the table and
        nothing is scored or ranked.
      </p>
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
          {sorted.map((row) => (
            <tr key={`${row.project}-${row.version}`}>
              <td>
                {row.project}
                {row.source === 'submitted' && (
                  <span className={styles.badge} title="Submitted by the project itself">
                    submitted
                  </span>
                )}
              </td>
              <td className={styles.muted}>{row.version}</td>
              <td>{row.sent}</td>
              <td>{row.restored}</td>
              <td>{row.leakWhole}</td>
              <td>{row.leakSplit}</td>
              <td>{row.note}</td>
              <td className={styles.muted}>
                {row.reportUrl ? <a href={row.reportUrl}>{row.date}</a> : row.date}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
