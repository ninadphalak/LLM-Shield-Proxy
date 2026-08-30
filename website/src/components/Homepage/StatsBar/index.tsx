import type {ReactNode} from 'react';
import styles from './styles.module.css';

const STATS: {value: string; label: string}[] = [
  {value: 'Apache 2.0', label: 'No project license fee or paid tier'},
  {value: '7', label: 'Published conformance domains'},
  {value: '3', label: 'Offline assessment artifact formats'},
  {value: '0', label: 'Upstream calls made by the assessor'},
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
