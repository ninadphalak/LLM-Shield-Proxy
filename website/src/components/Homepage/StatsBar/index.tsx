import type {ReactNode} from 'react';
import styles from './styles.module.css';

// This count includes only qualifying reproductions submitted by an independent
// operator. No published row has one yet.
const STATS: {value: string; label: string}[] = [
  {value: '6', label: 'Gateway configurations measured, raw artifacts published'},
  {value: '0', label: 'Independent reproductions — every row reads unreplicated'},
  {value: '1', label: 'Third-party dependency to run the benchmark (httpx)'},
  {value: 'Apache 2.0', label: 'No license fee, no paid tier, no hosted service'},
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
