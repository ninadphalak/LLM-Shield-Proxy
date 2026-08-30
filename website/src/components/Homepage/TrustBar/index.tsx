import type {ReactNode} from 'react';
import Link from '@docusaurus/Link';
import styles from './styles.module.css';

const FRAMEWORKS: {label: string; to: string}[] = [
  {label: 'HIPAA', to: '/docs/compliance/hipaa'},
  {label: 'SOC 2', to: '/docs/compliance/soc2'},
  {label: 'GDPR', to: '/docs/compliance/gdpr'},
  {label: 'EU AI Act', to: '/docs/compliance/eu_ai_act'},
  {label: 'NIST / ISO / FIPS 140-3', to: '/docs/compliance/nist_iso_fips'},
];

export default function TrustBar(): ReactNode {
  return (
    <div className={styles.wrapper}>
      <div className="container">
        <div className={styles.row}>
          <span className={styles.caption}>Engineered around technical safeguards for</span>
          <div className={styles.chips}>
            {FRAMEWORKS.map((f) => (
              <Link key={f.label} to={f.to} className={styles.chip}>
                {f.label}
              </Link>
            ))}
          </div>
        </div>
        <p className={styles.disclaimer}>
          These are technical controls mapped to each framework's requirements - see how in the docs.
          Deploying this proxy is one control among many a full compliance program requires; it is not a
          certification.
        </p>
      </div>
    </div>
  );
}
