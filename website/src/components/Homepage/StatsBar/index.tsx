import type {ReactNode} from 'react';
import styles from './styles.module.css';

const STATS: {value: string; label: string}[] = [
  {value: '<85 MB', label: 'Resident memory (verified RSS)'},
  {value: '<5 µs', label: 'Added latency per SSE chunk'},
  {value: '170+', label: 'Automated tests in CI, and growing'},
  {value: '0', label: 'Bytes of PII persisted to disk'},
];

export default function StatsBar(): ReactNode {
  return (
    <div className={styles.wrapper}>
      <div className="container">
        <div className={styles.grid}>
          {STATS.map((s) => (
            <div key={s.label} className={styles.stat}>
              <div className={styles.value}>{s.value}</div>
              <div className={styles.label}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
